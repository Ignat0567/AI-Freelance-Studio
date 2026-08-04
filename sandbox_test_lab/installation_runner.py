from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Sequence

from .capability import detect_sandbox_capability
from .fixture_installation import FixtureInstallationRequest, FixtureInstallationWorkspaceManager
from .installation_evidence import (
    InstallationEvidenceError,
    read_installation_heartbeat,
    validate_installation_evidence_directory,
    validate_partial_installation_evidence_directory,
)
from .models import RunStatus, SandboxCapability, SandboxRunResult, validate_transition
from .runner import ProcessHandle, _stop_owned_process, launch_sandbox, utc_now
from .workspace import WorkspaceError, atomic_write_json
from .wsb_config import WsbConfigError, write_wsb_config


def _stop_owned_process_safely(process: ProcessHandle, warnings: list[str]) -> None:
    try:
        _stop_owned_process(process)
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append(f"owned_process_cleanup_failed:{type(exc).__name__}")


class InstallationSandboxRunner:
    def __init__(
        self,
        *,
        workspace_manager: FixtureInstallationWorkspaceManager | None = None,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.5,
    ):
        self.workspace_manager = workspace_manager or FixtureInstallationWorkspaceManager()
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.poll_interval = poll_interval

    def run(
        self,
        request: FixtureInstallationRequest,
        cancellation: threading.Event | None = None,
    ) -> SandboxRunResult:
        started_at = utc_now()
        started_monotonic = self.monotonic()
        deadline = started_monotonic + request.timeout_seconds
        current = RunStatus.CREATED
        errors: list[str] = []
        warnings: list[str] = []
        process: ProcessHandle | None = None
        paths = None
        launcher_return_code: int | None = None
        launcher_exited_at: str | None = None
        launcher_exit_elapsed_seconds: float | None = None

        def transition(target: RunStatus) -> None:
            nonlocal current
            validate_transition(current, target)
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
            write_wsb_config(paths, network_enabled=False)
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
            heartbeat_path = paths.evidence_directory / "heartbeat.json"
            while True:
                if cancellation is not None and cancellation.is_set():
                    transition(RunStatus.CANCELLED)
                    _stop_owned_process_safely(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_installer_termination")
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
                validate_partial_installation_evidence_directory(paths.evidence_directory)
                if completion_path.is_file():
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_owned_process_safely(process, warnings)
                        errors.append("installation_completion_arrived_after_host_deadline")
                        return self._finish(paths, request.run_id, current, started_at, started_monotonic, "timeout", None, errors, warnings)
                    evidence = validate_installation_evidence_directory(paths.evidence_directory, request.run_id, artifact_hash)
                    if self.monotonic() >= deadline:
                        transition(RunStatus.TIMED_OUT)
                        _stop_owned_process_safely(process, warnings)
                        errors.append("installation_evidence_validation_exceeded_host_deadline")
                        return self._finish(paths, request.run_id, current, started_at, started_monotonic, "timeout", None, errors, warnings)
                    snapshot_path = paths.logs_directory / "validated-installation-evidence.json"
                    atomic_write_json(snapshot_path, evidence.raw)
                    if current is RunStatus.LAUNCHING:
                        transition(RunStatus.RUNNING)
                    warnings.extend(evidence.warnings)
                    errors.extend(evidence.errors)
                    if evidence.status == "passed" and evidence.outcome == "passed":
                        transition(RunStatus.PASSED)
                        return self._finish(
                            paths,
                            request.run_id,
                            current,
                            started_at,
                            started_monotonic,
                            "installation_completed",
                            str(snapshot_path),
                            errors,
                            warnings,
                            launcher_return_code,
                            launcher_exited_at,
                            launcher_exit_elapsed_seconds,
                        )
                    if evidence.outcome == "timed_out":
                        transition(RunStatus.TIMED_OUT)
                        reason = "installer_timed_out"
                    else:
                        transition(RunStatus.FAILED)
                        reason = "installer_reboot_required" if evidence.outcome == "reboot_required" else "installation_failed"
                    _stop_owned_process_safely(process, warnings)
                    return self._finish(
                        paths,
                        request.run_id,
                        current,
                        started_at,
                        started_monotonic,
                        reason,
                        str(snapshot_path),
                        errors,
                        warnings,
                        launcher_return_code,
                        launcher_exited_at,
                        launcher_exit_elapsed_seconds,
                    )
                if heartbeat_path.is_file() and current is RunStatus.LAUNCHING:
                    read_installation_heartbeat(heartbeat_path, request.run_id)
                    transition(RunStatus.RUNNING)
                returncode = process.poll()
                if returncode is not None and launcher_return_code is None:
                    launcher_return_code = returncode
                    launcher_exited_at = utc_now()
                    launcher_exit_elapsed_seconds = round(max(0.0, self.monotonic() - started_monotonic), 3)
                if self.monotonic() >= deadline:
                    transition(RunStatus.TIMED_OUT)
                    _stop_owned_process_safely(process, warnings)
                    warnings.append("sandbox_broker_termination_does_not_prove_guest_installer_termination")
                    if launcher_return_code is None:
                        errors.append("installation_completion_was_not_observed_before_host_timeout")
                        reason = "timeout"
                    else:
                        errors.append("launcher_exited_and_installation_completion_was_not_observed")
                        reason = "launcher_exited_without_installation_completion"
                    return self._finish(
                        paths,
                        request.run_id,
                        current,
                        started_at,
                        started_monotonic,
                        reason,
                        None,
                        errors,
                        warnings,
                        launcher_return_code,
                        launcher_exited_at,
                        launcher_exit_elapsed_seconds,
                    )
                self.sleeper(self.poll_interval)
        except KeyboardInterrupt:
            if current in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.CANCELLED)
            if process is not None:
                _stop_owned_process_safely(process, warnings)
                warnings.append("sandbox_broker_termination_does_not_prove_guest_installer_termination")
            return self._finish(paths, request.run_id, current, started_at, started_monotonic, "keyboard_interrupt", None, errors, warnings)
        except InstallationEvidenceError as exc:
            errors.append(str(exc))
            if current in {RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.FAILED)
            if process is not None:
                _stop_owned_process_safely(process, warnings)
            return self._finish(
                paths,
                request.run_id,
                current,
                started_at,
                started_monotonic,
                "invalid_installation_evidence",
                None,
                errors,
                warnings,
                launcher_return_code,
                launcher_exited_at,
                launcher_exit_elapsed_seconds,
            )
        except (WorkspaceError, WsbConfigError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            if current in {RunStatus.CREATED, RunStatus.LAUNCHING, RunStatus.RUNNING}:
                transition(RunStatus.INFRASTRUCTURE_ERROR)
            if process is not None:
                _stop_owned_process_safely(process, warnings)
            return self._finish(
                paths,
                request.run_id,
                current,
                started_at,
                started_monotonic,
                "infrastructure_error",
                None,
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
        paths,
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
            atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            (paths.logs_directory / "host.log").write_text(
                f"run_id={run_id}\nstatus={status.value}\nexit_reason={exit_reason}\n",
                encoding="utf-8",
            )
        return result
