from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Sequence

from .capability import detect_sandbox_capability
from .models import RunStatus, SandboxCapability, SandboxRunResult, validate_transition
from .production_runner import TERMINAL_EVIDENCE_GRACE_SECONDS
from .production_self_test import (
    HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    ensure_no_active_windows_sandbox_session,
    validate_production_deadline_configuration,
)
from .runner import ProcessHandle, _stop_owned_process, complete_owned_sandbox_session, launch_sandbox, utc_now
from .sandbox_session import OwnedSandboxSession, capture_owned_sandbox_session
from .screenshot_self_test import (
    ENTRY_SCRIPT_FILENAME,
    ScreenshotEvidenceError,
    ScreenshotSelfTestRequest,
    ScreenshotSelfTestWorkspaceManager,
    entry_payload_result_exists,
    read_entry_payload_result,
    read_payload_initialization_failure,
    read_startup_marker,
    startup_marker_filename,
    validate_partial_screenshot_evidence_directory,
    validate_screenshot_evidence_directory,
    write_validated_screenshot_snapshot,
)
from .workspace import WorkspaceError, atomic_write_json
from .wsb_config import SCREENSHOT_ENTRY_COMMAND, WsbConfigError, validate_wsb_config, write_wsb_config


ENTRY_MARKER_TIMEOUT_SECONDS = 30.0
PAYLOAD_ENTRY_TIMEOUT_SECONDS = 30.0


class ScreenshotSelfTestRunner:
    def __init__(
        self,
        *,
        workspace_manager: ScreenshotSelfTestWorkspaceManager | None = None,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.5,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        session_guard: Callable[[], None] = ensure_no_active_windows_sandbox_session,
        session_factory: Callable[[int, datetime], OwnedSandboxSession] = capture_owned_sandbox_session,
        launch_guard: Callable[[], None] = lambda: None,
    ):
        self.workspace_manager = workspace_manager or ScreenshotSelfTestWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval
        self.utc_clock = utc_clock
        self.session_guard = session_guard
        self.session_factory = session_factory
        self.launch_guard = launch_guard

    def run(self, request: ScreenshotSelfTestRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        validate_production_deadline_configuration()
        host_started_utc = self.utc_clock().astimezone(timezone.utc)
        started_at = host_started_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        guest_deadline = (
            host_started_utc + timedelta(seconds=request.timeout_seconds - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        status = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        process: ProcessHandle | None = None
        paths = None
        terminal_heartbeat_seen_at: float | None = None
        launcher_return_code: int | None = None
        launcher_exited_at: str | None = None
        launcher_exit_elapsed_seconds: float | None = None
        launch_started_monotonic: float | None = None
        launcher_cleanup_attempted = False
        owned_session: OwnedSandboxSession | None = None

        def complete_launcher() -> bool:
            nonlocal launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds, launcher_cleanup_attempted
            if process is None or launcher_cleanup_attempted:
                return process is None or (launcher_return_code is not None and owned_session is not None)
            launcher_cleanup_attempted = True
            if paths is not None:
                try:
                    (paths.logs_directory / "cleanup-phase.json").write_text(
                        "launcher_cleanup_started", encoding="utf-8",
                    )
                except OSError:
                    pass
            if owned_session is None:
                errors.append("owned_sandbox_session_identity_missing")
                try:
                    _stop_owned_process(process)
                except (OSError, subprocess.SubprocessError):
                    errors.append("owned_sandbox_launcher_cleanup_failed")
                return False
            try:
                launcher_return_code, launcher_exited_at = complete_owned_sandbox_session(
                    process,
                    session=owned_session,
                    session_guard=self.session_guard,
                    monotonic=self.monotonic,
                    sleeper=self.sleeper,
                )
            except (WorkspaceError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                observed_code = process.poll()
                if observed_code is not None and launcher_return_code is None:
                    launcher_return_code = observed_code
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(
                        max(0.0, self.monotonic() - (launch_started_monotonic or started_monotonic)), 3,
                    )
                errors.append(f"owned_sandbox_session_cleanup_failed_{type(exc).__name__}")
                return False
            launcher_exit_elapsed_seconds = round(
                max(0.0, self.monotonic() - (launch_started_monotonic or started_monotonic)), 3,
            )
            if paths is not None:
                try:
                    (paths.logs_directory / "cleanup-phase.json").write_text(
                        "launcher_cleanup_succeeded", encoding="utf-8",
                    )
                except OSError:
                    pass
            return True

        def finish(reason: str, evidence_path: str | None = None) -> SandboxRunResult:
            complete_launcher()
            return self._finish(
                paths, request.run_id, status, started_at, started_monotonic, reason, evidence_path,
                errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds,
            )

        def transition(target: RunStatus) -> None:
            nonlocal status
            validate_transition(status, target)
            status = target

        try:
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._result(request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            capability = self.capability_detector()
            warnings.extend(capability.warnings)
            if not capability.available:
                transition(RunStatus.UNAVAILABLE)
                errors.extend(capability.blockers)
                return self._result(request.run_id, status, started_at, started_monotonic, "sandbox_unavailable", None, errors, warnings)
            if self.monotonic() >= deadline:
                transition(RunStatus.TIMED_OUT)
                return self._result(request.run_id, status, started_at, started_monotonic, "timeout_before_launch", None, errors, warnings)
            paths, artifact, digest = self.workspace_manager.create(request)
            self.workspace_manager.write_guest_request(
                request, paths, artifact, digest, guest_terminal_deadline_utc=guest_deadline,
            )
            write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
            if not capability.executable_path:
                raise RuntimeError("capability did not provide WindowsSandbox.exe path")
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            self.workspace_manager.validate_for_launch(
                request, paths, guest_terminal_deadline_utc=guest_deadline,
            )
            validate_wsb_config(paths, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            if deadline - self.monotonic() <= HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS:
                transition(RunStatus.TIMED_OUT)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "insufficient_execution_budget_before_launch", None, errors, warnings)
            transition(RunStatus.LAUNCHING)
            launch_started_monotonic = self.monotonic()
            launcher_started_utc = self.utc_clock().astimezone(timezone.utc)
            entry_deadline = min(deadline, launch_started_monotonic + ENTRY_MARKER_TIMEOUT_SECONDS)
            self.launch_guard()
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            owned_session = self.session_factory(process.pid, launcher_started_utc)
            completion = paths.evidence_directory / "completion.json"
            heartbeat = paths.evidence_directory / "heartbeat.json"
            entry_marker = paths.evidence_directory / startup_marker_filename("entry", "entry_script_started")
            payload_marker = paths.evidence_directory / startup_marker_filename("payload", "payload_interpreter_entered")
            entry_failure = paths.evidence_directory / "entry-failure.json"
            payload_result = paths.evidence_directory / "entry-payload-result.json"
            initialization_failure = paths.evidence_directory / "payload-initialization-failure.json"
            payload_process_started = paths.evidence_directory / startup_marker_filename("entry", "payload_process_started")
            entry_seen = False
            payload_seen = False
            payload_deadline: float | None = None
            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_tree_termination")
                    return finish("cancelled")
                validate_partial_screenshot_evidence_directory(paths.evidence_directory)
                if entry_marker.is_file():
                    read_startup_marker(entry_marker, request.run_id, allow_pending_run_id=True)
                    if not entry_seen:
                        entry_seen = True
                        payload_deadline = min(
                            deadline,
                            launch_started_monotonic + ENTRY_MARKER_TIMEOUT_SECONDS + PAYLOAD_ENTRY_TIMEOUT_SECONDS,
                            self.monotonic() + PAYLOAD_ENTRY_TIMEOUT_SECONDS,
                        )
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                if payload_marker.is_file():
                    read_startup_marker(payload_marker, request.run_id)
                    payload_seen = True
                if entry_failure.is_file():
                    try:
                        failure = json.loads(entry_failure.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise ScreenshotEvidenceError("entry failure evidence is invalid") from exc
                    failure_reason = failure.get("reason") if isinstance(failure, dict) else None
                    reason_map = {
                        "payload_missing": "payload_missing",
                        "payload_hash_mismatch": "payload_hash_mismatch",
                        "payload_start_failed": "payload_parse_or_start_failure",
                    }
                    terminal_reason = reason_map.get(failure_reason, "entry_script_failed")
                    transition(RunStatus.INFRASTRUCTURE_ERROR)
                    errors.append(str(failure_reason or "entry_script_failed"))
                    return finish(terminal_reason)
                if initialization_failure.is_file():
                    failure = read_payload_initialization_failure(initialization_failure, request.run_id)
                    transition(RunStatus.INFRASTRUCTURE_ERROR)
                    errors.append(f"payload_initialization_failed_{failure['stage']}")
                    return finish("payload_initialization_failed")
                if entry_payload_result_exists(payload_result):
                    early_result = read_entry_payload_result(payload_result, request.run_id)
                    if not payload_seen or early_result["exit_code"] != 0:
                        terminal_reason = "payload_process_exited_before_entry" if not payload_seen else "payload_parse_or_start_failure"
                        transition(RunStatus.INFRASTRUCTURE_ERROR)
                        errors.append(terminal_reason)
                        return finish(terminal_reason)
                if completion.is_file():
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        errors.append("screenshot_completion_after_deadline")
                        return finish("timeout")
                    validated = validate_screenshot_evidence_directory(
                        paths.evidence_directory,
                        request.run_id,
                        run_started_at=host_started_utc,
                        expected_guest_terminal_deadline_utc=guest_deadline,
                    )
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        errors.append("screenshot_evidence_validation_exceeded_host_deadline")
                        return finish("timeout")
                    snapshot = paths.logs_directory / "validated-screenshot-evidence.json"
                    write_validated_screenshot_snapshot(snapshot, validated)
                    self._write_phase_marker(paths.logs_directory, "screenshot_snapshot_written")
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    self._write_host_state(
                        paths, request.run_id, status, started_at, started_monotonic,
                        "cleanup_in_progress", str(snapshot), errors, warnings,
                        launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds,
                    )
                    self._write_phase_marker(paths.logs_directory, "provisional_host_state_written")
                    if not complete_launcher():
                        transition(RunStatus.INFRASTRUCTURE_ERROR)
                        return finish("sandbox_session_cleanup_failed", str(snapshot))
                    transition(RunStatus.PASSED)
                    return finish("production_window_screenshot_validated", str(snapshot))
                if heartbeat.is_file():
                    try:
                        heartbeat_payload = json.loads(heartbeat.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise ScreenshotEvidenceError("screenshot heartbeat is invalid") from exc
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    if heartbeat_payload.get("phase") == "completed":
                        if terminal_heartbeat_seen_at is None:
                            terminal_heartbeat_seen_at = self.monotonic()
                        elif self.monotonic() - terminal_heartbeat_seen_at >= TERMINAL_EVIDENCE_GRACE_SECONDS:
                            transition(RunStatus.INFRASTRUCTURE_ERROR)
                            errors.append("terminal_evidence_missing_after_completed_heartbeat")
                            return finish("terminal_evidence_missing_after_completed_heartbeat")
                return_code = process.poll()
                if return_code is not None and launcher_return_code is None:
                    launcher_return_code = return_code
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - launch_started_monotonic), 3)
                if not entry_seen and self.monotonic() >= entry_deadline:
                    transition(RunStatus.INFRASTRUCTURE_ERROR)
                    errors.append("entry_script_marker_absent")
                    diagnostics = {
                        "schema_version": 1,
                        "run_id": request.run_id,
                        "exit_reason": "logon_command_not_observed",
                        "entry_timeout_seconds": ENTRY_MARKER_TIMEOUT_SECONDS,
                        "entry_marker_absent": True,
                        "payload_marker_absent": True,
                        "wsb_validation": "passed",
                        "mapping_validation": "passed",
                        "entry_script_present": (paths.guest_directory / ENTRY_SCRIPT_FILENAME).is_file(),
                        "request_present": (paths.guest_directory / "request.json").is_file(),
                        "broker_exit_code": launcher_return_code,
                        "broker_exited_at": launcher_exited_at,
                        "broker_exit_elapsed_seconds": launcher_exit_elapsed_seconds,
                    }
                    atomic_write_json(paths.logs_directory / "startup-diagnostics.json", diagnostics)
                    return finish("logon_command_not_observed")
                if entry_seen and not payload_seen and payload_deadline is not None and self.monotonic() >= payload_deadline:
                    transition(RunStatus.INFRASTRUCTURE_ERROR)
                    terminal_reason = "payload_start_timeout"
                    if not payload_process_started.is_file():
                        terminal_reason = "entry_script_failed"
                    errors.append("payload_interpreter_entered_marker_absent")
                    return finish(terminal_reason)
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_tree_termination")
                    return finish("timeout")
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            if process is not None:
                complete_launcher()
            return finish("keyboard_interrupt")
        except ScreenshotEvidenceError as exc:
            errors.append(str(exc))
            if status in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            if process is not None:
                complete_launcher()
            return finish("invalid_screenshot_evidence")
        except (WorkspaceError, WsbConfigError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            if process is not None:
                complete_launcher()
            return finish("infrastructure_error")

    def _result(self, run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code=None, launcher_exited_at=None, launcher_exit_elapsed_seconds=None):
        return SandboxRunResult(
            run_id=run_id, status=status, started_at=started_at, finished_at=utc_now(),
            duration_seconds=round(max(0.0, self.monotonic() - started_monotonic), 3), exit_reason=reason,
            evidence_path=evidence_path, errors=tuple(errors), warnings=tuple(dict.fromkeys(warnings)),
            launcher_return_code=launcher_return_code, launcher_exited_at=launcher_exited_at,
            launcher_exit_elapsed_seconds=launcher_exit_elapsed_seconds,
        )

    @staticmethod
    def _write_phase_marker(logs_dir: Path | None, phase: str, *, extra: dict | None = None) -> None:
        if logs_dir is not None:
            marker = {"phase": phase, "timestamp": utc_now()}
            marker["thread_name"] = threading.current_thread().name
            if extra:
                marker.update(extra)
            atomic_write_json(logs_dir / "phase-marker.json", marker)

    def _write_host_state(self, paths, run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code=None, launcher_exited_at=None, launcher_exit_elapsed_seconds=None):
        result = self._result(run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
        if paths is not None:
            try:
                atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            except (OSError, RuntimeError, ValueError):
                pass
            try:
                (paths.logs_directory / "host.log").write_text(
                    f"run_id={run_id}\nstatus={status.value}\nexit_reason={reason}\n", encoding="utf-8",
                )
            except OSError:
                pass
        return result

    def _finish(self, paths, run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code=None, launcher_exited_at=None, launcher_exit_elapsed_seconds=None):
        self._write_phase_marker(paths.logs_directory if paths is not None else None, "runner_finish_started")
        result = self._result(run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
        if paths is not None:
            try:
                atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            except (OSError, RuntimeError, ValueError):
                pass
            try:
                (paths.logs_directory / "host.log").write_text(
                    f"run_id={run_id}\nstatus={status.value}\nexit_reason={reason}\n", encoding="utf-8",
                )
            except OSError:
                pass
        return result
