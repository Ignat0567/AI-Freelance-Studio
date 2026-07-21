from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Sequence

from .capability import detect_sandbox_capability
from .fixture_launch import FixtureLaunchRequest, FixtureLaunchWorkspaceManager
from .launch_evidence import (
    LaunchEvidenceError,
    read_launch_heartbeat,
    validate_launch_evidence_directory,
    validate_partial_launch_evidence_directory,
)
from .models import RunStatus, SandboxCapability, SandboxRunResult, validate_transition
from .runner import ProcessHandle, _stop_owned_process, launch_sandbox, utc_now
from .workspace import WorkspaceError, atomic_write_json
from .wsb_config import WsbConfigError, write_wsb_config


def _stop_broker(process: ProcessHandle, warnings: list[str]) -> None:
    try:
        _stop_owned_process(process)
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append(f"owned_process_cleanup_failed:{type(exc).__name__}")


class InstallLaunchSandboxRunner:
    def __init__(
        self,
        *,
        workspace_manager: FixtureLaunchWorkspaceManager | None = None,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.5,
    ):
        self.workspace_manager = workspace_manager or FixtureLaunchWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval

    def run(self, request: FixtureLaunchRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        started_at = utc_now()
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        status = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        process: ProcessHandle | None = None
        paths = None
        launcher_return_code: int | None = None
        launcher_exited_at: str | None = None
        launcher_exit_elapsed_seconds: float | None = None

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
            self.workspace_manager.write_guest_request(request, paths, artifact, digest)
            write_wsb_config(paths, network_enabled=False)
            if not capability.executable_path:
                raise RuntimeError("capability did not provide WindowsSandbox.exe path")
            self.workspace_manager.validate_for_launch(request, paths)
            if cancellation is not None and cancellation.is_set():
                transition(RunStatus.CANCELLED)
                return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled_before_launch", None, errors, warnings)

            transition(RunStatus.LAUNCHING)
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            completion = paths.evidence_directory / "completion.json"
            heartbeat = paths.evidence_directory / "heartbeat.json"
            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    _stop_broker(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_process_termination")
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, "cancelled", None, errors, warnings)
                validate_partial_launch_evidence_directory(paths.evidence_directory)
                if completion.is_file():
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_broker(process, warnings)
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout", None, [*errors, "launch_completion_after_deadline"], warnings)
                    evidence = validate_launch_evidence_directory(paths.evidence_directory, request.run_id, digest)
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_broker(process, warnings)
                        errors.append("launch_evidence_validation_exceeded_host_deadline")
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "timeout", None, errors, warnings)
                    snapshot = paths.logs_directory / "validated-launch-evidence.json"
                    atomic_write_json(snapshot, evidence.raw)
                    if status is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    warnings.extend(evidence.warnings)
                    errors.extend(evidence.errors)
                    if evidence.status == "passed" and evidence.outcome == "passed":
                        transition(RunStatus.PASSED)
                        return self._finish(paths, request.run_id, status, started_at, started_monotonic, "install_launch_completed", str(snapshot), errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
                    if evidence.outcome == "timed_out":
                        transition(RunStatus.TIMED_OUT)
                        reason = "guest_launch_timed_out"
                    else:
                        transition(RunStatus.FAILED)
                        reason = "install_launch_failed"
                    _stop_broker(process, warnings)
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, reason, str(snapshot), errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
                if heartbeat.is_file() and status is RunStatus.LAUNCHING:
                    read_launch_heartbeat(heartbeat, request.run_id)
                    transition(RunStatus.RUNNING)
                return_code = process.poll()
                if return_code is not None and launcher_return_code is None:
                    launcher_return_code = return_code
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - started_monotonic), 3)
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    _stop_broker(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_process_termination")
                    reason = "launcher_exited_without_launch_completion" if launcher_return_code is not None else "timeout"
                    return self._finish(paths, request.run_id, status, started_at, started_monotonic, reason, None, errors, warnings, launcher_return_code, launcher_exited_at, launcher_exit_elapsed_seconds)
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if status in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            if process is not None:
                _stop_broker(process, warnings)
            return self._finish(paths, request.run_id, status, started_at, started_monotonic, "keyboard_interrupt", None, errors, warnings)
        except LaunchEvidenceError as exc:
            errors.append(str(exc))
            if status in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            if process is not None:
                _stop_broker(process, warnings)
            return self._finish(paths, request.run_id, status, started_at, started_monotonic, "invalid_launch_evidence", None, errors, warnings)
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
            (paths.logs_directory / "host.log").write_text(f"run_id={run_id}\nstatus={status.value}\nexit_reason={reason}\n", encoding="utf-8")
        return result
