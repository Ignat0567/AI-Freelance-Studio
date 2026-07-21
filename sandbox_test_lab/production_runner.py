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
    validate_production_deadline_configuration,
)
from .runner import ProcessHandle, _stop_owned_process, launch_sandbox, utc_now
from .workspace import WorkspaceError, atomic_write_json
from .wsb_config import WsbConfigError, write_wsb_config


TERMINAL_EVIDENCE_GRACE_SECONDS = 30


def _stop_broker(process: ProcessHandle, warnings: list[str]) -> None:
    try:
        _stop_owned_process(process)
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append(f"owned_sandbox_broker_cleanup_failed_{type(exc).__name__}")


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
    ):
        self.workspace_manager = workspace_manager or ProductionSelfTestWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval
        self.utc_clock = utc_clock

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
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            completion = paths.evidence_directory / "completion.json"
            heartbeat = paths.evidence_directory / "heartbeat.json"
            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    _stop_broker(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_tree_termination")
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled", None, errors, warnings)
                validate_partial_production_evidence_directory(paths.evidence_directory)
                if completion.is_file():
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_broker(process, warnings)
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout", None, [*errors, "production_completion_after_deadline"], warnings)
                    evidence = validate_production_evidence_directory(
                        paths.evidence_directory, request.run_id,
                        expected_guest_terminal_deadline_utc=guest_terminal_deadline_utc,
                    )
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_broker(process, warnings)
                        errors.append("production_evidence_validation_exceeded_host_deadline")
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout", None, errors, warnings)
                    snapshot = paths.logs_directory / "validated-production-evidence.json"
                    atomic_write_json(snapshot, evidence.raw)
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    errors.extend(evidence.errors)
                    warnings.extend(evidence.warnings)
                    if evidence.status == "passed" and evidence.outcome == "passed" and evidence.fully_ready:
                        transition(RunStatus.PASSED)
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "production_self_test_fully_ready", str(snapshot), errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
                    transition(RunStatus.TIMED_OUT if evidence.outcome == "timed_out" else RunStatus.FAILED)
                    _stop_broker(process, warnings)
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, evidence.overall_readiness, str(snapshot), errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
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
                            return self._finish(
                                paths, request.run_id, status, started_at, started_monotonic,
                                "terminal_evidence_missing_after_completed_heartbeat", None, errors, warnings,
                                launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds,
                            )
                return_code = process.poll()
                if return_code is not None and launcher_return_code is None:
                    launcher_return_code = return_code
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - started_monotonic), 3)
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    _stop_broker(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_tree_termination")
                    reason = "launcher_exited_without_production_completion" if launcher_return_code is not None else "timeout"
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, reason, None, errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            if process is not None:
                _stop_broker(process, warnings)
            return self._finish(paths, request.run_id, status, started_at, started_monotonic, "keyboard_interrupt", None, errors, warnings)
        except ProductionEvidenceError as exc:
            errors.append(str(exc))
            if status in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            if process is not None:
                _stop_broker(process, warnings)
            return self._finish(paths, request.run_id, status, started_at, started_monotonic, "invalid_production_evidence", None, errors, warnings)
        except (WorkspaceError, WsbConfigError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            if process is not None:
                _stop_broker(process, warnings)
            return self._finish(paths, request.run_id, status, started_at, started_monotonic, "infrastructure_error", None, errors, warnings)

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
