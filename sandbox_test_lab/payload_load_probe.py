from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
from typing import Callable, Sequence

from .capability import detect_sandbox_capability
from .models import RunStatus, SandboxCapability, SandboxRunRequest, SandboxRunResult
from .production_self_test import (
    HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    PRODUCTION_PROTOCOL,
    PRODUCTION_SCHEMA_VERSION,
    TRUSTED_PROFILE,
    TRUSTED_PROFILE_NAME,
    ensure_no_active_windows_sandbox_session,
)
from .runner import ProcessHandle, complete_owned_sandbox_session, launch_sandbox, utc_now
from .sandbox_session import OwnedSandboxSession, capture_owned_sandbox_session
from .screenshot_runner import ENTRY_MARKER_TIMEOUT_SECONDS, PAYLOAD_ENTRY_TIMEOUT_SECONDS
from .screenshot_self_test import (
    ENTRY_SCRIPT_FILENAME,
    ENTRY_STARTUP_STAGES,
    PAYLOAD_SCRIPT_FILENAME,
    PAYLOAD_SHA256,
    entry_payload_result_exists,
    read_entry_payload_result,
    read_payload_initialization_failure,
    read_startup_marker,
    startup_marker_filename,
)
from .workspace import REPARSE_POINT_ATTRIBUTE, SandboxWorkspaceManager, WorkspaceError, atomic_write_json, sha256_file
from .wsb_config import SCREENSHOT_ENTRY_COMMAND, validate_wsb_config, write_wsb_config


PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PAYLOAD_LOAD_PROBE_EXTERNAL"
PAYLOAD_LOAD_PROBE_TIMEOUT_SECONDS = 90.0
PAYLOAD_LOAD_PROBE_COMPLETION = "payload-load-probe-completion.json"
PROBE_PAYLOAD_STAGES = (
    "payload_interpreter_entered",
    "core_functions_loading_started",
    "core_functions_loading_completed",
    "production_api_add_type_started",
    "production_api_add_type_completed",
    "drawing_assembly_loading_started",
    "drawing_assembly_loading_completed",
    "screenshot_api_add_type_started",
    "screenshot_api_add_type_completed",
    "request_loading_started",
    "request_loading_completed",
    "initialization_completed",
    "completed",
)


def _strict_completion_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise WorkspaceError("payload load probe completion contains duplicate JSON keys")
        value[key] = item
    return value


def read_payload_load_probe_completion(path: Path, run_id: str) -> dict[str, object]:
    try:
        info = path.lstat()
        resolved_parent = path.resolve(strict=True).parent
        expected_parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceError("payload load probe completion is missing or inaccessible") from exc
    if (
        path.is_symlink()
        or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)
        or not stat.S_ISREG(info.st_mode)
        or resolved_parent != expected_parent
    ):
        raise WorkspaceError("payload load probe completion must be a contained regular non-reparse file")
    if not 1 <= info.st_size <= 4096:
        raise WorkspaceError("payload load probe completion size is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_completion_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("payload load probe completion is not strict UTF-8 JSON") from exc
    expected_fields = {"schema_version", "run_id", "status", "timestamp"}
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise WorkspaceError("payload load probe completion fields are invalid")
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
        or value.get("run_id") != run_id
        or value.get("status") != "passed"
    ):
        raise WorkspaceError("payload load probe completion identity is invalid")
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{7}Z",
        timestamp,
    ):
        raise WorkspaceError("payload load probe completion timestamp is invalid")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkspaceError("payload load probe completion timestamp is invalid") from exc
    return value


def payload_load_probe_external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == "external"


def payload_load_probe_request() -> SandboxRunRequest:
    harmless_source = Path(__file__).parent.parent / "docs" / "interactive-sandbox-test-lab-phase-3b.md"
    return SandboxRunRequest(source_artifact=harmless_source, timeout_seconds=PAYLOAD_LOAD_PROBE_TIMEOUT_SECONDS)


class PayloadLoadProbeWorkspaceManager(SandboxWorkspaceManager):
    def __init__(self, runtime_root: Path | None = None):
        guest_root = Path(__file__).with_name("guest")
        super().__init__(runtime_root, guest_root / ENTRY_SCRIPT_FILENAME, ENTRY_SCRIPT_FILENAME)
        self.payload_source = guest_root / PAYLOAD_SCRIPT_FILENAME

    def create(self, request: SandboxRunRequest):
        paths, staged, digest = super().create(request)
        if staged.suffix.casefold() == ".exe":
            raise WorkspaceError("payload load probe may not stage an executable")
        if sha256_file(self.payload_source) != PAYLOAD_SHA256:
            raise WorkspaceError("tracked Phase 3B payload hash mismatch")
        shutil.copyfile(self.payload_source, paths.guest_directory / PAYLOAD_SCRIPT_FILENAME, follow_symlinks=False)
        return paths, staged, digest

    def write_guest_request(
        self,
        request: SandboxRunRequest,
        paths,
        staged_artifact: Path,
        artifact_sha256: str,
        *,
        guest_terminal_deadline_utc: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(request_path, {
            "schema_version": PRODUCTION_SCHEMA_VERSION,
            "protocol": PRODUCTION_PROTOCOL,
            "run_id": request.run_id,
            "profile_name": TRUSTED_PROFILE_NAME,
            "product": PRODUCT_NAME,
            "version": PRODUCT_VERSION,
            "artifact_name": staged_artifact.name,
            "artifact_size": staged_artifact.stat().st_size,
            "artifact_sha256_host": artifact_sha256,
            "guest_terminal_deadline_utc": guest_terminal_deadline_utc,
            "profile": dict(TRUSTED_PROFILE),
            "execution_mode": "payload_load_probe",
            "payload_sha256": PAYLOAD_SHA256,
        })
        return request_path

    def validate_for_launch(self, request: SandboxRunRequest, paths) -> None:
        super().validate_for_launch(request, paths)
        if tuple(paths.evidence_directory.iterdir()):
            raise WorkspaceError("payload load probe evidence directory must be empty before launch")
        inputs = tuple(paths.input_directory.iterdir())
        if len(inputs) != 1 or inputs[0].suffix.casefold() == ".exe":
            raise WorkspaceError("payload load probe input must be one non-executable")
        if sha256_file(paths.guest_directory / ENTRY_SCRIPT_FILENAME) != sha256_file(self.bootstrap_source):
            raise WorkspaceError("payload load probe entry script changed")
        if sha256_file(paths.guest_directory / PAYLOAD_SCRIPT_FILENAME) != PAYLOAD_SHA256:
            raise WorkspaceError("payload load probe payload changed")
        validate_wsb_config(paths, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
        ensure_no_active_windows_sandbox_session()


class PayloadLoadProbeRunner:
    def __init__(
        self,
        *,
        workspace_manager: PayloadLoadProbeWorkspaceManager,
        capability_detector: Callable[[], SandboxCapability] = detect_sandbox_capability,
        launcher: Callable[[Sequence[str]], ProcessHandle] = launch_sandbox,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        session_guard: Callable[[], None] = ensure_no_active_windows_sandbox_session,
        session_factory: Callable[[int, datetime], OwnedSandboxSession] = capture_owned_sandbox_session,
    ):
        self.workspace_manager = workspace_manager
        self.capability_detector = capability_detector
        self.launcher = launcher
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.utc_clock = utc_clock
        self.session_guard = session_guard
        self.session_factory = session_factory

    def run(self, request: SandboxRunRequest, cancellation: threading.Event | None = None) -> SandboxRunResult:
        started_at = utc_now()
        started = self.monotonic()
        capability = self.capability_detector()
        if not capability.available or not capability.executable_path:
            return self._result(request.run_id, RunStatus.UNAVAILABLE, started_at, started, "sandbox_unavailable", tuple(capability.blockers))
        paths = None
        broker_code = None
        broker_exited_at = None
        broker_elapsed = None
        process: ProcessHandle | None = None
        launch_started: float | None = None
        cleanup_attempted = False
        owned_session: OwnedSandboxSession | None = None

        def complete_launcher() -> tuple[bool, str | None]:
            nonlocal broker_code, broker_exited_at, broker_elapsed, cleanup_attempted
            if process is None or cleanup_attempted:
                complete = process is None or (broker_code is not None and owned_session is not None)
                return complete, None
            cleanup_attempted = True
            if owned_session is None:
                return False, "owned_sandbox_session_identity_missing"
            try:
                broker_code, broker_exited_at = complete_owned_sandbox_session(
                    process,
                    session=owned_session,
                    session_guard=self.session_guard,
                    monotonic=self.monotonic,
                    sleeper=self.sleeper,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                observed_code = process.poll()
                if observed_code is not None and broker_code is None:
                    broker_code = observed_code
                    broker_exited_at = utc_now()
                    broker_elapsed = round(max(0.0, self.monotonic() - (launch_started or started)), 3)
                return False, f"owned_sandbox_session_cleanup_failed_{type(exc).__name__}"
            broker_elapsed = round(max(0.0, self.monotonic() - (launch_started or started)), 3)
            return True, None

        def finish(
            reason: str,
            *,
            error: str | None = None,
            status: RunStatus = RunStatus.INFRASTRUCTURE_ERROR,
        ) -> SandboxRunResult:
            _, cleanup_error = complete_launcher()
            return self._finish(
                paths, request, started_at, started, reason, broker_code, broker_exited_at, broker_elapsed,
                error=cleanup_error or error, status=status,
            )

        try:
            if cancellation is not None and cancellation.is_set():
                return self._result(request.run_id, RunStatus.CANCELLED, started_at, started, "cancelled_before_launch", ())
            paths, artifact, digest = self.workspace_manager.create(request)
            guest_deadline = (
                self.utc_clock().astimezone(timezone.utc)
                + timedelta(seconds=PAYLOAD_LOAD_PROBE_TIMEOUT_SECONDS - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS)
            ).isoformat(timespec="microseconds").replace("+00:00", "Z")
            self.workspace_manager.write_guest_request(
                request, paths, artifact, digest, guest_terminal_deadline_utc=guest_deadline,
            )
            write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
            self.workspace_manager.validate_for_launch(request, paths)
            if cancellation is not None and cancellation.is_set():
                return self._result(request.run_id, RunStatus.CANCELLED, started_at, started, "cancelled_before_launch", ())
            launch_started = self.monotonic()
            launcher_started_utc = self.utc_clock().astimezone(timezone.utc)
            entry_deadline = launch_started + ENTRY_MARKER_TIMEOUT_SECONDS
            overall_deadline = launch_started + PAYLOAD_LOAD_PROBE_TIMEOUT_SECONDS
            payload_deadline = None
            process = self.launcher([capability.executable_path, str(paths.config_file)])
            owned_session = self.session_factory(process.pid, launcher_started_utc)
            entry_marker = paths.evidence_directory / startup_marker_filename("entry", "entry_script_started")
            payload_marker = paths.evidence_directory / startup_marker_filename("payload", "payload_interpreter_entered")
            completion = paths.evidence_directory / PAYLOAD_LOAD_PROBE_COMPLETION
            initialization_failure = paths.evidence_directory / "payload-initialization-failure.json"
            payload_result = paths.evidence_directory / "entry-payload-result.json"
            while self.monotonic() < overall_deadline:
                if cancellation is not None and cancellation.is_set():
                    return finish("cancelled", status=RunStatus.CANCELLED)
                if entry_marker.is_file() and payload_deadline is None:
                    read_startup_marker(entry_marker, request.run_id, allow_pending_run_id=True)
                    payload_deadline = min(overall_deadline, self.monotonic() + PAYLOAD_ENTRY_TIMEOUT_SECONDS)
                if payload_marker.is_file():
                    read_startup_marker(payload_marker, request.run_id)
                if initialization_failure.is_file():
                    failure = read_payload_initialization_failure(initialization_failure, request.run_id)
                    return finish(
                        "payload_initialization_failed",
                        error=f"payload_initialization_failed_{failure['stage']}",
                    )
                if entry_payload_result_exists(payload_result):
                    result_payload = read_entry_payload_result(payload_result, request.run_id)
                    if not payload_marker.is_file() or result_payload["exit_code"] != 0:
                        reason = "payload_process_exited_before_entry" if not payload_marker.is_file() else "payload_initialization_failed"
                        return finish(reason)
                try:
                    completion.lstat()
                except FileNotFoundError:
                    completion_exists = False
                else:
                    completion_exists = True
                if completion_exists:
                    timeline = self._validate_timeline(paths.evidence_directory, request.run_id)
                    snapshot = paths.logs_directory / "validated-payload-load-timeline.json"
                    atomic_write_json(snapshot, {"schema_version": 1, "run_id": request.run_id, "timeline": timeline})
                    cleanup_ok, cleanup_error = complete_launcher()
                    if not cleanup_ok:
                        return self._finish(
                            paths, request, started_at, started, "sandbox_session_cleanup_failed",
                            broker_code, broker_exited_at, broker_elapsed, error=cleanup_error,
                        )
                    result = self._result(
                        request.run_id, RunStatus.PASSED, started_at, started, "payload_load_probe_completed", (),
                        broker_code, broker_exited_at, broker_elapsed,
                    )
                    atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
                    return result
                code = process.poll()
                if code is not None and broker_code is None:
                    broker_code = code
                    broker_exited_at = utc_now()
                    broker_elapsed = round(self.monotonic() - launch_started, 3)
                if payload_deadline is None and self.monotonic() >= entry_deadline:
                    return finish("logon_command_not_observed")
                if payload_deadline is not None and not payload_marker.is_file() and self.monotonic() >= payload_deadline:
                    return finish("payload_start_timeout")
                self.sleeper(0.25)
            return finish("payload_load_probe_timeout")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _, cleanup_error = complete_launcher()
            result = self._result(
                request.run_id, RunStatus.INFRASTRUCTURE_ERROR, started_at, started, "infrastructure_error",
                tuple(item for item in (str(exc), cleanup_error) if item),
                broker_code, broker_exited_at, broker_elapsed,
            )
            if paths is not None:
                atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
            return result

    def _validate_timeline(self, directory: Path, run_id: str) -> list[dict]:
        timeline = []
        for stage in ENTRY_STARTUP_STAGES:
            value = read_startup_marker(
                directory / startup_marker_filename("entry", stage),
                run_id,
                allow_pending_run_id=stage in {"entry_script_started", "request_found"},
            )
            timeline.append(value)
        for stage in PROBE_PAYLOAD_STAGES:
            value = read_startup_marker(directory / startup_marker_filename("payload", stage), run_id)
            timeline.append(value)
        timestamps = [datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")) for item in timeline]
        if timestamps != sorted(timestamps):
            raise WorkspaceError("payload load probe startup timeline is out of order")
        completion = read_payload_load_probe_completion(directory / PAYLOAD_LOAD_PROBE_COMPLETION, run_id)
        completion_timestamp = datetime.fromisoformat(str(completion["timestamp"]).replace("Z", "+00:00"))
        if completion_timestamp < timestamps[-1]:
            raise WorkspaceError("payload load probe completion precedes its startup timeline")
        return timeline

    def _finish(
        self, paths, request, started_at, started, reason, broker_code, broker_exited_at,
        broker_elapsed, *, error=None, status=RunStatus.INFRASTRUCTURE_ERROR,
    ):
        result = self._result(
            request.run_id, status, started_at, started, reason, (error or reason,),
            broker_code, broker_exited_at, broker_elapsed,
        )
        atomic_write_json(paths.run_root / "host-result.json", result.to_dict())
        return result

    def _result(self, run_id, status, started_at, started, reason, errors, broker_code=None, broker_exited_at=None, broker_elapsed=None):
        return SandboxRunResult(
            run_id=run_id, status=status, started_at=started_at, finished_at=utc_now(),
            duration_seconds=round(max(0.0, self.monotonic() - started), 3), exit_reason=reason,
            evidence_path=None, errors=errors, launcher_return_code=broker_code,
            launcher_exited_at=broker_exited_at, launcher_exit_elapsed_seconds=broker_elapsed,
        )
