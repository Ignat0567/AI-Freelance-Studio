from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PureWindowsPath
import re
import stat
from typing import Any

from .fixture_builder import FIXTURE_GUI_EXECUTABLE_NAME, TRUSTED_FIXTURE_GUI_SHA256
from .fixture_launch import (
    CONTROLLED_LAUNCH_PROFILE,
    INSTALL_LAUNCH_PROTOCOL,
    INSTALL_LAUNCH_SCHEMA_VERSION,
    MINIMUM_STABLE_DURATION_SECONDS,
    FIXTURE_WINDOW_TITLE,
)
from .workspace import REPARSE_POINT_ATTRIBUTE


EXPECTED_LAUNCH_EVIDENCE_FILES = frozenset({
    "status.json", "heartbeat.json", "completion.json", "guest-system.json", "lifecycle.log", "launch-evidence.json",
})
MAX_JSON_BYTES = 128 * 1024
MAX_LOG_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 640 * 1024
_PHASES = {
    "installed", "launching", "process_started", "window_detecting", "window_visible",
    "first_launch_verified", "cleanup", "completed",
}
_SUCCESS_TRANSITIONS = (
    "installed", "launching", "process_started", "window_detecting", "window_visible",
    "first_launch_verified", "cleanup", "completed",
)
_OUTCOMES = {"passed", "failed", "timed_out", "reboot_required", "infrastructure_error"}
_ATOMIC_TEMPORARY = re.compile(r"^(status|heartbeat|completion|guest-system|launch-evidence)\.json\.[0-9a-f]{32}\.tmp$")
_SAFE_SYSTEM_VALUE = re.compile(r"^[A-Za-z0-9 ._()\-]{1,256}$")
_LOG_MESSAGE = re.compile(r"^(controlled_install_launch_started|controlled_install_launch_passed|controlled_install_launch_failed)$")
_SAFE_MESSAGE = re.compile(r"^[A-Za-z0-9_]{1,256}$")
_STATUS_FIELDS = {
    "schema_version", "protocol", "run_id", "launch_profile", "status", "phase", "outcome", "started_at", "updated_at",
    "completed_at", "artifact", "installer_kind", "installer_started_at", "installer_finished_at", "installer_exit_code",
    "install_duration_seconds", "reboot_required", "network_disabled", "artifact_sha256_host", "artifact_sha256_guest",
    "hash_verified", "installation_passed", "installed_marker_verified", "installed_payload_verified", "installed_executable_found",
    "installed_executable_sha256", "installed_executable_hash_verified", "launch_started_at", "process_started_at",
    "window_detection_started_at", "window_detected_at", "stable_started_at", "stable_verified_at", "cleanup_started_at",
    "cleanup_finished_at", "process_started", "owned_process_id", "process_image_name", "process_image_verified", "window_handle",
    "window_handle_present", "window_title", "window_title_verified", "window_owned_by_process", "window_visible",
    "stable_window_duration_seconds", "first_launch_verified", "graceful_close_succeeded", "forced_owned_process_cleanup",
    "cleanup_process_exited", "phase_transitions", "errors", "warnings",
}


class LaunchEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedLaunchEvidence:
    run_id: str
    status: str
    outcome: str
    first_launch_verified: bool
    forced_owned_process_cleanup: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    raw: dict[str, Any]


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _regular_size(path: Path) -> int:
    try:
        info = path.lstat()
    except OSError as exc:
        raise LaunchEvidenceError("required launch evidence file is missing") from exc
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise LaunchEvidenceError("launch evidence must contain only regular non-reparse files")
    if path.suffix.lower() in {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".hta"}:
        raise LaunchEvidenceError("launch evidence may not contain executable or script files")
    return info.st_size


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LaunchEvidenceError("launch evidence contains duplicate JSON keys")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    if _regular_size(path) > MAX_JSON_BYTES:
        raise LaunchEvidenceError("launch evidence JSON exceeds its size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchEvidenceError("launch evidence is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LaunchEvidenceError("launch evidence JSON root must be an object")
    return value


def _timestamp(value: Any, field: str, *, optional: bool = False) -> datetime | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise LaunchEvidenceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaunchEvidenceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LaunchEvidenceError(f"{field} must include a timezone")
    return parsed


def _sha256(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise LaunchEvidenceError(f"{field} must be a lowercase SHA-256")
    return value


def _messages(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise LaunchEvidenceError(f"{field} must be a bounded list")
    if any(not isinstance(item, str) or not _SAFE_MESSAGE.fullmatch(item) for item in value):
        raise LaunchEvidenceError(f"{field} contains path-bearing or unsupported data")
    return tuple(value)


def _identity(payload: dict[str, Any], run_id: str) -> None:
    if payload.get("schema_version") != INSTALL_LAUNCH_SCHEMA_VERSION or payload.get("protocol") != INSTALL_LAUNCH_PROTOCOL:
        raise LaunchEvidenceError("launch evidence schema or protocol is unsupported")
    if payload.get("run_id") != run_id:
        raise LaunchEvidenceError("launch evidence run_id does not match")


def _reject_path_or_secret_data(value: Any, field: str = "") -> None:
    lowered = field.lower()
    if any(token in lowered for token in ("path", "command", "argument", "environment", "password", "secret", "token", "credential", "api_key")):
        raise LaunchEvidenceError("launch evidence contains path-bearing or secret-like fields")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_path_or_secret_data(item, key)
    elif isinstance(value, list):
        for item in value:
            _reject_path_or_secret_data(item, field)
    elif isinstance(value, str) and ("\\" in value or ":/" in value or value.startswith(("/", "~"))):
        raise LaunchEvidenceError("launch evidence contains path-bearing values")


def validate_partial_launch_evidence_directory(evidence_directory: Path) -> None:
    try:
        info = evidence_directory.lstat()
    except OSError as exc:
        raise LaunchEvidenceError("launch evidence directory is missing") from exc
    if evidence_directory.is_symlink() or _is_reparse(evidence_directory) or not stat.S_ISDIR(info.st_mode):
        raise LaunchEvidenceError("launch evidence directory must be a regular non-reparse directory")
    total = 0
    for entry in evidence_directory.iterdir():
        if entry.name not in EXPECTED_LAUNCH_EVIDENCE_FILES and not _ATOMIC_TEMPORARY.fullmatch(entry.name):
            raise LaunchEvidenceError("launch evidence contains an unexpected file")
        size = _regular_size(entry)
        if size > (MAX_LOG_BYTES if entry.name == "lifecycle.log" else MAX_JSON_BYTES):
            raise LaunchEvidenceError("launch evidence file exceeds its size limit")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise LaunchEvidenceError("launch evidence exceeds its total size limit")


def _validate_status(payload: dict[str, Any], run_id: str, artifact_sha256: str) -> ValidatedLaunchEvidence:
    if set(payload) != _STATUS_FIELDS:
        raise LaunchEvidenceError("launch evidence fields do not match the exact schema")
    _identity(payload, run_id)
    _reject_path_or_secret_data(payload)
    status = payload.get("status")
    outcome = payload.get("outcome")
    if status not in {"passed", "failed"} or outcome not in _OUTCOMES or payload.get("phase") != "completed":
        raise LaunchEvidenceError("launch evidence terminal status is invalid")
    if (status == "passed") != (outcome == "passed"):
        raise LaunchEvidenceError("launch status and outcome are inconsistent")
    if payload.get("launch_profile") != CONTROLLED_LAUNCH_PROFILE or payload.get("artifact") != "artifact.exe" or len(PureWindowsPath(str(payload.get("artifact"))).parts) != 1:
        raise LaunchEvidenceError("launch evidence controlled identity is invalid")
    if payload.get("installer_kind") != "nsis_exe":
        raise LaunchEvidenceError("launch evidence installer kind is invalid")

    started = _timestamp(payload.get("started_at"), "started_at")
    updated = _timestamp(payload.get("updated_at"), "updated_at")
    completed = _timestamp(payload.get("completed_at"), "completed_at")
    if not started <= updated <= completed:
        raise LaunchEvidenceError("launch evidence terminal timestamps are inconsistent")
    timestamp_names = (
        "installer_started_at", "installer_finished_at", "launch_started_at", "process_started_at", "window_detection_started_at",
        "window_detected_at", "stable_started_at", "stable_verified_at", "cleanup_started_at", "cleanup_finished_at",
    )
    times = {name: _timestamp(payload.get(name), name, optional=True) for name in timestamp_names}

    booleans = (
        "reboot_required", "network_disabled", "hash_verified", "installation_passed", "installed_marker_verified",
        "installed_payload_verified", "installed_executable_found", "installed_executable_hash_verified", "process_started",
        "process_image_verified", "window_handle_present", "window_title_verified", "window_owned_by_process",
        "window_visible", "first_launch_verified", "graceful_close_succeeded", "forced_owned_process_cleanup", "cleanup_process_exited",
    )
    if any(not isinstance(payload.get(name), bool) for name in booleans):
        raise LaunchEvidenceError("launch evidence boolean fields are invalid")
    host_hash = _sha256(payload.get("artifact_sha256_host"), "artifact_sha256_host")
    guest_hash = _sha256(payload.get("artifact_sha256_guest"), "artifact_sha256_guest", optional=status == "failed")
    gui_hash = _sha256(payload.get("installed_executable_sha256"), "installed_executable_sha256", optional=status == "failed")
    errors = _messages(payload.get("errors"), "errors")
    warnings = _messages(payload.get("warnings"), "warnings")

    exit_code = payload.get("installer_exit_code")
    process_id = payload.get("owned_process_id")
    window_handle = payload.get("window_handle")
    for name, value in (("installer_exit_code", exit_code), ("owned_process_id", process_id), ("window_handle", window_handle)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise LaunchEvidenceError(f"{name} must be an integer or null")
    for name in ("install_duration_seconds", "stable_window_duration_seconds"):
        value = payload.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 1800):
            raise LaunchEvidenceError(f"{name} is invalid")

    transitions = payload.get("phase_transitions")
    if not isinstance(transitions, list) or len(transitions) > len(_SUCCESS_TRANSITIONS):
        raise LaunchEvidenceError("phase transitions are invalid")
    transition_names: list[str] = []
    transition_times: list[datetime] = []
    for transition in transitions:
        if not isinstance(transition, dict) or set(transition) != {"phase", "at"} or transition.get("phase") not in _PHASES:
            raise LaunchEvidenceError("phase transition entry is invalid")
        transition_names.append(transition["phase"])
        transition_times.append(_timestamp(transition.get("at"), "phase_transition.at"))
    if transition_names != list(_SUCCESS_TRANSITIONS[:len(transition_names)]) and status == "passed":
        raise LaunchEvidenceError("passed launch phase transitions are invalid")
    if status == "passed" and tuple(transition_names) != _SUCCESS_TRANSITIONS:
        raise LaunchEvidenceError("passed launch requires the complete phase sequence")
    if transition_times and (transition_times != sorted(transition_times) or transition_times[0] < started or transition_times[-1] > completed):
        raise LaunchEvidenceError("phase transition timestamps are inconsistent")

    transition_at = dict(zip(transition_names, transition_times, strict=True))
    if status == "passed":
        if not (
            transition_at["launching"] <= times["launch_started_at"]
            and times["process_started_at"] <= transition_at["process_started"] <= times["window_detection_started_at"]
            and transition_at["window_detecting"] == times["window_detection_started_at"]
            and transition_at["window_visible"] == times["window_detected_at"]
            and transition_at["first_launch_verified"] == times["stable_verified_at"]
            and transition_at["cleanup"] <= times["cleanup_started_at"]
            and transition_at["completed"] == times["cleanup_finished_at"]
        ):
            raise LaunchEvidenceError("phase transitions contradict lifecycle timestamps")

    if status == "passed":
        required_true = (
            "network_disabled", "hash_verified", "installation_passed", "installed_marker_verified", "installed_payload_verified",
            "installed_executable_found", "installed_executable_hash_verified", "process_started", "process_image_verified",
            "window_handle_present", "window_title_verified", "window_owned_by_process", "window_visible",
            "first_launch_verified", "cleanup_process_exited",
        )
        if any(payload.get(name) is not True for name in required_true) or payload.get("reboot_required") is not False:
            raise LaunchEvidenceError("passed launch requires installation, association, stability, and cleanup verification")
        if exit_code != 0 or host_hash != artifact_sha256.lower() or guest_hash != artifact_sha256.lower() or gui_hash != TRUSTED_FIXTURE_GUI_SHA256:
            raise LaunchEvidenceError("passed launch hash or installation verification is invalid")
        if process_id is None or process_id <= 0 or window_handle is None or window_handle <= 0:
            raise LaunchEvidenceError("passed launch requires a nonzero PID and window handle")
        if payload.get("process_image_name") != FIXTURE_GUI_EXECUTABLE_NAME or payload.get("window_title") != FIXTURE_WINDOW_TITLE:
            raise LaunchEvidenceError("passed launch process image or exact title is invalid")
        ordered = [times[name] for name in timestamp_names]
        if any(value is None for value in ordered) or not started <= ordered[0] <= ordered[1] <= ordered[2] <= ordered[3] <= ordered[4] <= ordered[5] <= ordered[6] <= ordered[7] <= ordered[8] <= ordered[9] <= completed:
            raise LaunchEvidenceError("passed launch lifecycle timestamps are inconsistent")
        stable_duration = payload.get("stable_window_duration_seconds")
        if stable_duration < MINIMUM_STABLE_DURATION_SECONDS or (times["stable_verified_at"] - times["stable_started_at"]).total_seconds() < MINIMUM_STABLE_DURATION_SECONDS:
            raise LaunchEvidenceError("passed launch stable interval is too short")
        if not payload.get("graceful_close_succeeded") and not payload.get("forced_owned_process_cleanup"):
            raise LaunchEvidenceError("passed launch cleanup was not requested")
        fallback = payload.get("forced_owned_process_cleanup")
        if fallback and payload.get("graceful_close_succeeded"):
            raise LaunchEvidenceError("graceful and forced cleanup claims are mutually exclusive")
        if fallback != ("owned_gui_fallback_kill_used" in warnings):
            raise LaunchEvidenceError("cleanup fallback warning is inconsistent")
        if errors:
            raise LaunchEvidenceError("passed launch may not contain errors")
    elif outcome == "passed":
        raise LaunchEvidenceError("failed launch evidence contains inconsistent success claims")

    if payload.get("first_launch_verified"):
        launch_claims = (
            "installation_passed", "installed_executable_found", "installed_executable_hash_verified", "process_started",
            "process_image_verified", "window_handle_present", "window_title_verified", "window_owned_by_process", "window_visible",
        )
        if any(payload.get(name) is not True for name in launch_claims):
            raise LaunchEvidenceError("first_launch_verified lacks supporting fields")
        if process_id is None or process_id <= 0 or window_handle is None or window_handle <= 0:
            raise LaunchEvidenceError("first_launch_verified lacks owned process or window evidence")
        if gui_hash != TRUSTED_FIXTURE_GUI_SHA256 or payload.get("process_image_name") != FIXTURE_GUI_EXECUTABLE_NAME or payload.get("window_title") != FIXTURE_WINDOW_TITLE:
            raise LaunchEvidenceError("first_launch_verified identity is inconsistent")
        stable_duration = payload.get("stable_window_duration_seconds")
        if stable_duration is None or stable_duration < MINIMUM_STABLE_DURATION_SECONDS:
            raise LaunchEvidenceError("first_launch_verified stable interval is insufficient")

    return ValidatedLaunchEvidence(run_id, status, outcome, payload["first_launch_verified"], payload["forced_owned_process_cleanup"], errors, warnings, payload)


def read_launch_heartbeat(path: Path, run_id: str) -> dict[str, Any]:
    payload = _load_json(path)
    if set(payload) != {"schema_version", "protocol", "run_id", "phase", "updated_at"}:
        raise LaunchEvidenceError("launch heartbeat fields are invalid")
    _identity(payload, run_id)
    if payload.get("phase") not in _PHASES:
        raise LaunchEvidenceError("launch heartbeat phase is invalid")
    _timestamp(payload.get("updated_at"), "heartbeat.updated_at")
    return payload


def validate_launch_evidence_directory(evidence_directory: Path, run_id: str, artifact_sha256: str) -> ValidatedLaunchEvidence:
    validate_partial_launch_evidence_directory(evidence_directory)
    entries = tuple(evidence_directory.iterdir())
    if {entry.name for entry in entries} != EXPECTED_LAUNCH_EVIDENCE_FILES or len(entries) != len(EXPECTED_LAUNCH_EVIDENCE_FILES):
        raise LaunchEvidenceError("launch evidence contains missing or unexpected files")
    sizes = {entry.name: _regular_size(entry) for entry in entries}
    if sizes["lifecycle.log"] > MAX_LOG_BYTES or sum(sizes.values()) > MAX_TOTAL_BYTES:
        raise LaunchEvidenceError("launch evidence exceeds its size limits")
    try:
        log = (evidence_directory / "lifecycle.log").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise LaunchEvidenceError("launch lifecycle log is not valid UTF-8") from exc
    lines = log.splitlines()
    if len(lines) != 2:
        raise LaunchEvidenceError("launch lifecycle log is invalid")
    log_messages: list[str] = []
    log_times: list[datetime] = []
    for line in lines:
        timestamp, separator, message = line.partition(" ")
        if not separator or not _LOG_MESSAGE.fullmatch(message):
            raise LaunchEvidenceError("launch lifecycle log contains unsupported data")
        log_times.append(_timestamp(timestamp, "lifecycle.log timestamp"))
        log_messages.append(message)

    status = _load_json(evidence_directory / "status.json")
    launch = _load_json(evidence_directory / "launch-evidence.json")
    if status != launch:
        raise LaunchEvidenceError("status and launch evidence do not match")
    validated = _validate_status(status, run_id, artifact_sha256)
    expected_terminal_log = (
        "controlled_install_launch_passed" if validated.status == "passed" else "controlled_install_launch_failed"
    )
    if log_messages != ["controlled_install_launch_started", expected_terminal_log]:
        raise LaunchEvidenceError("launch lifecycle log contradicts terminal status")
    status_started = _timestamp(status.get("started_at"), "started_at")
    status_completed = _timestamp(status.get("completed_at"), "completed_at")
    if not status_started <= log_times[0] <= log_times[1] <= status_completed:
        raise LaunchEvidenceError("launch lifecycle log timestamps contradict terminal evidence")

    completion = _load_json(evidence_directory / "completion.json")
    if set(completion) != {"schema_version", "protocol", "run_id", "completed", "status", "outcome", "phase", "completed_at"}:
        raise LaunchEvidenceError("launch completion fields are invalid")
    _identity(completion, run_id)
    if completion.get("completed") is not True or completion.get("phase") != "completed" or completion.get("status") != validated.status or completion.get("outcome") != validated.outcome:
        raise LaunchEvidenceError("launch completion does not match status")
    if _timestamp(completion.get("completed_at"), "completion.completed_at") != _timestamp(status.get("completed_at"), "completed_at"):
        raise LaunchEvidenceError("launch completion timestamp does not match status")

    heartbeat = read_launch_heartbeat(evidence_directory / "heartbeat.json", run_id)
    if heartbeat.get("phase") != "completed":
        raise LaunchEvidenceError("launch heartbeat is not terminal")
    heartbeat_time = _timestamp(heartbeat.get("updated_at"), "heartbeat.updated_at")
    transitions = validated.raw["phase_transitions"]
    completed_transition = _timestamp(transitions[-1]["at"], "completed transition")
    if heartbeat_time != completed_transition or heartbeat_time > status_completed:
        raise LaunchEvidenceError("launch heartbeat timestamp contradicts terminal evidence")
    guest = _load_json(evidence_directory / "guest-system.json")
    if set(guest) != {"os_version", "architecture", "powershell_version"} or any(not isinstance(value, str) or not _SAFE_SYSTEM_VALUE.fullmatch(value) for value in guest.values()):
        raise LaunchEvidenceError("guest system launch evidence is invalid")
    return validated
