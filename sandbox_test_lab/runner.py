from __future__ import annotations

from datetime import datetime, timezone
import logging
import ntpath
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Protocol, Sequence

from .capability import detect_sandbox_capability
from .evidence import EvidenceError, read_heartbeat, validate_completion, validate_guest_evidence
from .models import RunStatus, SandboxCapability, SandboxRunRequest, SandboxRunResult, validate_transition
from .sandbox_session import OwnedSandboxSession
from .workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json
from .wsb_config import WsbConfigError, write_wsb_config


logger = logging.getLogger(__name__)

GUEST_SANDBOX_SHUTDOWN_GRACE_SECONDS = 15.0
SOFT_SANDBOX_SESSION_EXIT_TIMEOUT_SECONDS = 5.0
FORCED_SANDBOX_SESSION_EXIT_TIMEOUT_SECONDS = 15.0
SANDBOX_SESSION_INACTIVE_STABLE_SECONDS = 1.0
DIAGNOSTICS_RETENTION_RUNS = 20
_DIAGNOSTIC_LOG_FILENAMES = (
    "host.log",
    "validated-production-evidence.json",
    "validated-screenshot-evidence.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def archive_run_diagnostics(
    diagnostics_root: Path | None,
    run_id: str,
    paths: Any,
    *,
    retain: int = DIAGNOSTICS_RETENTION_RUNS,
) -> None:
    """Best-effort copy of a completed run's safe diagnostic files into a durable,
    bounded directory, since the source workspace under ``paths.run_root`` is ephemeral
    and nothing else retains it. Never raises: diagnostics are a debugging aid, not part
    of run correctness."""
    if diagnostics_root is None or paths is None:
        return
    try:
        destination = Path(diagnostics_root) / run_id
        destination.mkdir(parents=True, exist_ok=True)
        candidates = [paths.run_root / "host-result.json"]
        candidates.extend(paths.logs_directory / name for name in _DIAGNOSTIC_LOG_FILENAMES)
        for source in candidates:
            if source.is_file():
                shutil.copy2(source, destination / source.name)
        _prune_diagnostics_root(Path(diagnostics_root), retain)
    except OSError:
        pass


def _prune_diagnostics_root(diagnostics_root: Path, retain: int) -> None:
    try:
        entries = [entry for entry in diagnostics_root.iterdir() if entry.is_dir()]
    except OSError:
        return
    if len(entries) <= retain:
        return
    try:
        entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in entries[retain:]:
        shutil.rmtree(stale, ignore_errors=True)


class ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def validate_sandbox_argv(argv: Sequence[str]) -> list[str]:
    if not argv or isinstance(argv, (str, bytes)):
        raise ValueError("sandbox launch requires argv")
    validated = list(argv)
    if any(ntpath.basename(str(token)).casefold() in {"taskkill", "taskkill.exe"} for token in validated):
        raise ValueError("sandbox launcher may not invoke taskkill")
    return validated


def launch_sandbox(argv: Sequence[str]) -> ProcessHandle:
    validated = validate_sandbox_argv(argv)
    return subprocess.Popen(
        validated,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_owned_process(process: ProcessHandle) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def complete_owned_sandbox_session(
    process: ProcessHandle,
    *,
    session: OwnedSandboxSession,
    session_guard: Callable[[], None],
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[int, str]:
    """Wait for guest shutdown, then close only the exact sandbox session owned by this run.

    Supports both legacy_client (WindowsSandboxClient) and remote_session
    (WindowsSandboxRemoteSession + WindowsSandboxServer) process models.
    """

    def wait_until_inactive(timeout_seconds: float) -> bool:
        deadline = monotonic() + timeout_seconds
        inactive_since: float | None = None
        while True:
            try:
                session_guard()
            except WorkspaceError as exc:
                if str(exc) != "active_windows_sandbox_session":
                    raise
                inactive_since = None
            else:
                if inactive_since is None:
                    inactive_since = monotonic()
                if monotonic() - inactive_since >= SANDBOX_SESSION_INACTIVE_STABLE_SECONDS:
                    return True
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            stable_remaining = (
                SANDBOX_SESSION_INACTIVE_STABLE_SECONDS
                if inactive_since is None
                else SANDBOX_SESSION_INACTIVE_STABLE_SECONDS - (monotonic() - inactive_since)
            )
            sleeper(min(0.25, remaining, max(0.0, stable_remaining)))

    _stop_owned_process(process)
    return_code = process.poll()
    if return_code is None:
        raise RuntimeError("Sandbox launcher diagnostic exit was not observed")
    if not wait_until_inactive(GUEST_SANDBOX_SHUTDOWN_GRACE_SECONDS):
        try:
            session.request_close()
        except WorkspaceError:
            pass
        if not wait_until_inactive(SOFT_SANDBOX_SESSION_EXIT_TIMEOUT_SECONDS):
            session.terminate()
            if not wait_until_inactive(FORCED_SANDBOX_SESSION_EXIT_TIMEOUT_SECONDS):
                if hasattr(session, 'model') and session.model == "remote_session" and session.server_pid is not None:
                    try:
                        session.terminate_server()
                    except WorkspaceError:
                        pass
                    if not wait_until_inactive(FORCED_SANDBOX_SESSION_EXIT_TIMEOUT_SECONDS):
                        raise WorkspaceError("owned_windows_sandbox_session_did_not_exit")
                else:
                    raise WorkspaceError("owned_windows_sandbox_session_did_not_exit")
    return return_code, utc_now()


class SandboxRunner:
    def __init__(
        self,
        *,
        workspace_manager: SandboxWorkspaceManager | None = None,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.5,
    ):
        self.workspace_manager = workspace_manager or SandboxWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval

    def run(self, request: SandboxRunRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        started_at = utc_now()
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        current = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        evidence_path: str | None = None
        process: ProcessHandle | None = None
        paths = None
        launcher_return_code: int | None = None
        launcher_exited_at: str | None = None
        launcher_exit_elapsed_seconds: float | None = None

        def transition(target: RunStatus) -> None:
            nonlocal current
            validate_transition(current, target)
            logger.info("Sandbox run %s: %s -> %s", request.run_id, current.value, target.value)
            current = target

        try:
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._result(request.run_id, current, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            capability = self.capability_detector()
            warnings.extend(capability.warnings)
            if not capability.available:
                transition(RunStatus.UNAVAILABLE)
                errors.extend(capability.blockers)
                return self._result(request.run_id, current, started_at, started_monotonic, "sandbox_unavailable", None, errors, warnings)
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._result(request.run_id, current, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            if self.monotonic() >= deadline:
                transition(RunStatus.TIMED_OUT)
                errors.append("host_deadline_elapsed_before_workspace_creation")
                return self._result(request.run_id, current, started_at, started_monotonic, "timeout_before_launch", None, errors, warnings)

            paths, staged_artifact, artifact_hash = self.workspace_manager.create(request)
            self.workspace_manager.write_guest_request(request, paths, staged_artifact, artifact_hash)
            write_wsb_config(paths, network_enabled=request.network_enabled)
            if not capability.executable_path:
                raise RuntimeError("capability did not provide WindowsSandbox.exe path")
            self.workspace_manager.validate_for_launch(request, paths)
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, current, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            if self.monotonic() >= deadline:
                transition(RunStatus.TIMED_OUT)
                errors.append("host_deadline_elapsed_before_launch")
                return self._finish(paths, request.run_id, current, started_at, started_monotonic, "timeout_before_launch", None, errors, warnings)
            transition(RunStatus.LAUNCHING)
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            completion_path = paths.evidence_directory / "completion.json"
            guest_evidence_path = paths.evidence_directory / "status.json"
            heartbeat_path = paths.evidence_directory / "heartbeat.json"
            saw_heartbeat = False

            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    _stop_owned_process(process)
                    return self._finish(
                        paths,
                        request.run_id,
                        current,
                        started_at,
                        started_monotonic,
                        "cancelled",
                        None,
                        errors,
                        warnings,
                        launcher_return_code,
                        launcher_exited_at,
                        launcher_exit_elapsed_seconds,
                    )
                if completion_path.is_file():
                    completion = validate_completion(completion_path, request.run_id)
                    evidence = validate_guest_evidence(guest_evidence_path, request.run_id)
                    evidence_path = str(guest_evidence_path)
                    if completion.get("status") != evidence.status:
                        raise EvidenceError("completion and evidence status do not match")
                    if evidence.artifact != staged_artifact.name:
                        raise EvidenceError("evidence artifact does not match the staged artifact")
                    if current == RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    if evidence.status == "passed" and evidence.hash_verified and evidence.artifact_sha256_host == artifact_hash and evidence.artifact_sha256_guest == artifact_hash:
                        transition(RunStatus.PASSED)
                        return self._finish(
                            paths,
                            request.run_id,
                            current,
                            started_at,
                            started_monotonic,
                            "guest_completed",
                            evidence_path,
                            errors,
                            warnings + list(evidence.warnings),
                            launcher_return_code,
                            launcher_exited_at,
                            launcher_exit_elapsed_seconds,
                        )
                    errors.extend(evidence.errors or ("guest_bootstrap_failed",))
                    transition(RunStatus.FAILED)
                    return self._finish(
                        paths,
                        request.run_id,
                        current,
                        started_at,
                        started_monotonic,
                        "guest_failed",
                        evidence_path,
                        errors,
                        warnings + list(evidence.warnings),
                        launcher_return_code,
                        launcher_exited_at,
                        launcher_exit_elapsed_seconds,
                    )
                if heartbeat_path.is_file():
                    try:
                        read_heartbeat(heartbeat_path, request.run_id)
                        if not saw_heartbeat:
                            transition(RunStatus.RUNNING)
                            saw_heartbeat = True
                    except EvidenceError as exc:
                        warnings.append(str(exc))
                returncode = process.poll()
                if returncode is not None and launcher_return_code is None:
                    launcher_return_code = returncode
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - started_monotonic), 3)
                    logger.info(
                        "Sandbox launcher for run %s exited with code %s after %.3fs; continuing evidence polling",
                        request.run_id,
                        returncode,
                        launcher_exit_elapsed_seconds,
                    )
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    _stop_owned_process(process)
                    if launcher_return_code is not None:
                        errors.append("launcher_exited_and_guest_completion_was_not_observed")
                        exit_reason = "launcher_exited_without_guest_completion"
                    else:
                        errors.append("guest_completion_was_not_observed_before_timeout")
                        exit_reason = "timeout"
                    return self._finish(
                        paths,
                        request.run_id,
                        current,
                        started_at,
                        started_monotonic,
                        exit_reason,
                        None,
                        errors,
                        warnings,
                        launcher_return_code,
                        launcher_exited_at,
                        launcher_exit_elapsed_seconds,
                    )
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if current in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            if process is not None:
                _stop_owned_process(process)
            return self._finish(
                paths,
                request.run_id,
                current,
                started_at,
                started_monotonic,
                "keyboard_interrupt",
                evidence_path,
                errors,
                warnings,
                launcher_return_code,
                launcher_exited_at,
                launcher_exit_elapsed_seconds,
            )
        except EvidenceError as exc:
            errors.append(str(exc))
            if current in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            return self._finish(
                paths,
                request.run_id,
                current,
                started_at,
                started_monotonic,
                "invalid_evidence",
                evidence_path,
                errors,
                warnings,
                launcher_return_code,
                launcher_exited_at,
                launcher_exit_elapsed_seconds,
            )
        except (WorkspaceError, WsbConfigError, OSError, RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            if current in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            if process is not None:
                try:
                    _stop_owned_process(process)
                except (OSError, subprocess.SubprocessError) as stop_error:
                    warnings.append(f"owned_process_cleanup_failed:{type(stop_error).__name__}")
            return self._finish(
                paths,
                request.run_id,
                current,
                started_at,
                started_monotonic,
                "infrastructure_error",
                evidence_path,
                errors,
                warnings,
                launcher_return_code,
                launcher_exited_at,
                launcher_exit_elapsed_seconds,
            )

    def _result(
        self,
        run_id: str,
        status: RunStatus,
        started_at: str,
        started_monotonic: float,
        exit_reason: str,
        evidence_path: str | None,
        errors: list[str],
        warnings: list[str],
        launcher_return_code: int | None = None,
        launcher_exited_at: str | None = None,
        launcher_exit_elapsed_seconds: float | None = None,
    ) -> SandboxRunResult:
        return SandboxRunResult(
            run_id=run_id,
            status=status,
            started_at=started_at,
            finished_at=utc_now(),
            duration_seconds=round(max(0.0, self.monotonic() - started_monotonic), 3),
            exit_reason=exit_reason,
            evidence_path=evidence_path,
            errors=tuple(errors),
            warnings=tuple(dict.fromkeys(warnings)),
            launcher_return_code=launcher_return_code,
            launcher_exited_at=launcher_exited_at,
            launcher_exit_elapsed_seconds=launcher_exit_elapsed_seconds,
        )

    def _finish(
        self,
        paths: Any,
        run_id: str,
        status: RunStatus,
        started_at: str,
        started_monotonic: float,
        exit_reason: str,
        evidence_path: str | None,
        errors: list[str],
        warnings: list[str],
        launcher_return_code: int | None = None,
        launcher_exited_at: str | None = None,
        launcher_exit_elapsed_seconds: float | None = None,
    ) -> SandboxRunResult:
        result = self._result(
            run_id,
            status,
            started_at,
            started_monotonic,
            exit_reason,
            evidence_path,
            errors,
            warnings,
            launcher_return_code,
            launcher_exited_at,
            launcher_exit_elapsed_seconds,
        )
        if paths is not None:
            try:
                atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            except (OSError, RuntimeError, ValueError):
                pass
            try:
                log_path = paths.logs_directory / "host.log"
                log_path.write_text(
                    f"run_id={run_id}\nstatus={status.value}\nexit_reason={exit_reason}\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        return result
