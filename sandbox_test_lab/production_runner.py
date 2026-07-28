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
from .production_evidence import (
    ProductionEvidenceError,
    read_production_heartbeat,
    validate_partial_production_evidence_directory,
    validate_production_evidence_directory,
)
from .production_self_test import (
    HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    ProductionSelfTestRequest,
    ProductionSelfTestWorkspaceManager,
    ensure_no_active_windows_sandbox_session,
    validate_production_deadline_configuration,
)
from .runner import ProcessHandle, _stop_owned_process, complete_owned_sandbox_session, launch_sandbox, utc_now
from .sandbox_session import OwnedSandboxSession, capture_owned_sandbox_session
from .workspace import WorkspaceError, atomic_write_json
from .wsb_config import WsbConfigError, write_wsb_config


TERMINAL_EVIDENCE_GRACE_SECONDS = 30


class ProductionSelfTestRunner:
    def __init__(
        self,
        *,
        workspace_manager: ProductionSelfTestWorkspaceManager | None = None,
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
        self.workspace_manager = workspace_manager or ProductionSelfTestWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval
        self.utc_clock = utc_clock
        self.session_guard = session_guard
        self.session_factory = session_factory
        self.launch_guard = launch_guard

    def run(self, request: ProductionSelfTestRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        validate_production_deadline_configuration()
        host_started_utc = self.utc_clock().astimezone(timezone.utc)
        started_at = host_started_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        host_deadline_utc = host_started_utc + timedelta(seconds=request.timeout_seconds)
        guest_terminal_deadline_utc = (
            host_deadline_utc - timedelta(seconds=HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        status = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        process: ProcessHandle | None = None
        paths = None
        launcher_return_code: int | None = None
        launcher_exited_at: str | None = None
        launcher_exit_elapsed_seconds: float | None = None
        terminal_heartbeat_seen_at: float | None = None
        launch_started_monotonic: float | None = None
        launcher_cleanup_attempted = False
        owned_session: OwnedSandboxSession | None = None

        def complete_launcher() -> bool:
            nonlocal launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds, launcher_cleanup_attempted
            if process is None or launcher_cleanup_attempted:
                return process is None or (launcher_return_code is not None and owned_session is not None)
            launcher_cleanup_attempted = True
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
                request, paths, artifact, digest,
                guest_terminal_deadline_utc=guest_terminal_deadline_utc,
            )
            write_wsb_config(paths, network_enabled=False)
            if not capability.executable_path:
                raise RuntimeError("capability did not provide WindowsSandbox.exe path")
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            if self.monotonic() >= deadline:
                transition(RunStatus.TIMED_OUT)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout_before_launch", None, errors, warnings)

            # This is intentionally the final substantive operation before the controlled launcher/Popen.
            self.workspace_manager.validate_for_launch(
                request, paths, guest_terminal_deadline_utc=guest_terminal_deadline_utc,
            )
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)
            remaining_host_budget = deadline - self.monotonic()
            if remaining_host_budget <= 0:
                transition(RunStatus.TIMED_OUT)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout_before_launch", None, errors, warnings)
            if remaining_host_budget <= HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS:
                transition(RunStatus.TIMED_OUT)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "insufficient_execution_budget_before_launch", None, errors, warnings)
            transition(RunStatus.LAUNCHING)
            launch_started_monotonic = self.monotonic()
            launcher_started_utc = self.utc_clock().astimezone(timezone.utc)
            self.launch_guard()
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            owned_session = self.session_factory(process.pid, launcher_started_utc)
            completion = paths.evidence_directory / "completion.json"
            heartbeat = paths.evidence_directory / "heartbeat.json"
            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    return finish("cancelled")
                validate_partial_production_evidence_directory(paths.evidence_directory)
                if completion.is_file():
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        errors.append("production_completion_after_deadline")
                        return finish("timeout")
                    evidence = validate_production_evidence_directory(
                        paths.evidence_directory, request.run_id,
                        expected_guest_terminal_deadline_utc=guest_terminal_deadline_utc,
                    )
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        errors.append("production_evidence_validation_exceeded_host_deadline")
                        return finish("timeout")
                    snapshot = paths.logs_directory / "validated-production-evidence.json"
                    atomic_write_json(snapshot, evidence.raw)
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    errors.extend(evidence.errors)
                    warnings.extend(evidence.warnings)
                    if evidence.status == "passed" and evidence.outcome == "passed" and evidence.fully_ready:
                        if not complete_launcher():
                            transition(RunStatus.INFRASTRUCTURE_ERROR)
                            return finish("sandbox_session_cleanup_failed", str(snapshot))
                        transition(RunStatus.PASSED)
                        return finish("production_self_test_fully_ready", str(snapshot))
                    transition(RunStatus.TIMED_OUT if evidence.outcome == "timed_out" else RunStatus.FAILED)
                    return finish(evidence.overall_readiness, str(snapshot))
                if heartbeat.is_file():
                    heartbeat_payload = read_production_heartbeat(heartbeat, request.run_id)
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    if heartbeat_payload["phase"] == "completed":
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
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - started_monotonic), 3)
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    reason = "launcher_exited_without_production_completion" if launcher_return_code is not None else "timeout"
                    return finish(reason)
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            return finish("keyboard_interrupt")
        except ProductionEvidenceError as exc:
            errors.append(str(exc))
            if status in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            return finish("invalid_production_evidence")
        except (WorkspaceError, WsbConfigError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            return finish("infrastructure_error")

    def _result(self, run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code=None, launcher_exited_at=None, launcher_exit_elapsed_seconds=None):
        return SandboxRunResult(
            run_id=run_id, status=status, started_at=started_at, finished_at=utc_now(),
            duration_seconds=round(max(0.0, self.monotonic() - started_monotonic), 3), exit_reason=reason,
            evidence_path=evidence_path, errors=tuple(errors), warnings=tuple(dict.fromkeys(warnings)),
            launcher_return_code=launcher_return_code, launcher_exited_at=launcher_exited_at,
            launcher_exit_elapsed_seconds=launcher_exit_elapsed_seconds,
        )

    def _finish(self, paths, run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code=None, launcher_exited_at=None, launcher_exit_elapsed_seconds=None):
        result = self._result(run_id, status, started_at, started_monotonic, reason, evidence_path, errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
        if paths is not None:
            atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            (paths.logs_directory / "host.log").write_text(
                f"run_id={run_id}\nstatus={status.value}\nexit_reason={reason}\n", encoding="utf-8",
            )
        return result
