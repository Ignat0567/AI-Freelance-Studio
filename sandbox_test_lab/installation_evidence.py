from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PureWindowsPath
import re
import stat
from typing import Any

from .fixture_installation import (
    FIXTURE_INSTALL_ROOT,
    INSTALL_EXECUTION_PROTOCOL,
    INSTALL_EXECUTION_SCHEMA_VERSION,
)
from .workspace import REPARSE_POINT_ATTRIBUTE


EXPECTED_INSTALLATION_EVIDENCE_FILES = frozenset(
    {
        "status.json",
        "heartbeat.json",
        "completion.json",
        "guest-system.json",
        "installer.log",
        "installation-evidence.json",
    }
)
MAX_JSON_BYTES = 128 * 1024
MAX_INSTALLER_LOG_BYTES = 256 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 768 * 1024
_PHASES = {"artifact_verified", "installing", "verifying", "completed"}
_OUTCOMES = {"passed", "failed", "timed_out", "reboot_required", "infrastructure_error"}
_ATOMIC_TEMPORARY = re.compile(
    r"^(status|heartbeat|completion|guest-system|installation-evidence)\.json\.[0-9a-f]{32}\.tmp$"
)
_SAFE_GUEST_SYSTEM_VALUE = re.compile(r"^[A-Za-z0-9 ._()\-]{1,256}$")
_INSTALLER_LOG_MESSAGE = re.compile(
    r"^(controlled_fixture_bootstrap_started|installer_started|installer_finished exit_code_-?[0-9]+|"
    r"controlled_fixture_installation_passed|controlled_fixture_installation_failed|evidence_finalization_failed)$"
)
_STATUS_FIELDS = {
    "schema_version",
    "protocol",
    "run_id",
    "status",
    "phase",
    "outcome",
    "started_at",
    "updated_at",
    "completed_at",
    "artifact",
    "installer_kind",
    "installer_started_at",
    "installer_finished_at",
    "installer_exit_code",
    "install_duration_seconds",
    "reboot_required",
    "artifact_sha256_host",
    "artifact_sha256_guest",
    "hash_verified",
    "expected_install_root",
    "installed_marker_found",
    "installed_payload_found",
    "installed_executable_found",
    "installed_executable_sha256",
    "first_launch_verified",
    "errors",
    "warnings",
}


class InstallationEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedInstallationEvidence:
    run_id: str
    status: str
    outcome: str
    installer_exit_code: int | None
    reboot_required: bool
    installed_marker_found: bool
    installed_payload_found: bool
    artifact_sha256_host: str
    artifact_sha256_guest: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    raw: dict[str, Any]


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _validate_regular_file(path: Path) -> int:
    try:
        info = path.lstat()
    except OSError as exc:
        raise InstallationEvidenceError("required installation evidence file is missing") from exc
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise InstallationEvidenceError("installation evidence must contain only regular non-reparse files")
    return info.st_size


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InstallationEvidenceError("installation evidence contains duplicate JSON keys")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    size = _validate_regular_file(path)
    if size > MAX_JSON_BYTES:
        raise InstallationEvidenceError("installation evidence JSON exceeds its size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallationEvidenceError("installation evidence is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise InstallationEvidenceError("installation evidence JSON root must be an object")
    return payload


def _timestamp(value: Any, field: str, *, optional: bool = False) -> datetime | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise InstallationEvidenceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstallationEvidenceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InstallationEvidenceError(f"{field} must include a timezone")
    return parsed


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise InstallationEvidenceError(f"{field} must be a SHA-256")
    return value.lower()


def _messages(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 50:
        raise InstallationEvidenceError(f"{field} must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, str) or len(item) > 256 or "\\" in item or ":/" in item:
            raise InstallationEvidenceError(f"{field} contains an invalid entry")
        result.append(item)
    return tuple(result)


def _validate_identity(payload: dict[str, Any], run_id: str) -> None:
    if payload.get("schema_version") != INSTALL_EXECUTION_SCHEMA_VERSION:
        raise InstallationEvidenceError("installation evidence schema_version is unsupported")
    if payload.get("protocol") != INSTALL_EXECUTION_PROTOCOL:
        raise InstallationEvidenceError("installation evidence protocol is unsupported")
    if payload.get("run_id") != run_id:
        raise InstallationEvidenceError("installation evidence run_id does not match")


def validate_partial_installation_evidence_directory(evidence_directory: Path) -> None:
    try:
        directory_info = evidence_directory.lstat()
    except OSError as exc:
        raise InstallationEvidenceError("installation evidence directory is missing") from exc
    if evidence_directory.is_symlink() or _is_reparse(evidence_directory) or not stat.S_ISDIR(directory_info.st_mode):
        raise InstallationEvidenceError("installation evidence directory must be a regular non-reparse directory")
    total_size = 0
    for entry in evidence_directory.iterdir():
        if entry.name not in EXPECTED_INSTALLATION_EVIDENCE_FILES and not _ATOMIC_TEMPORARY.fullmatch(entry.name):
            raise InstallationEvidenceError("installation evidence contains an unexpected file")
        size = _validate_regular_file(entry)
        limit = MAX_INSTALLER_LOG_BYTES if entry.name == "installer.log" else MAX_JSON_BYTES
        if size > limit:
            raise InstallationEvidenceError("installation evidence file exceeds its size limit")
        total_size += size
    if total_size > MAX_TOTAL_EVIDENCE_BYTES:
        raise InstallationEvidenceError("installation evidence exceeds its total size limit")


def _validate_status(payload: dict[str, Any], run_id: str, artifact_sha256: str) -> ValidatedInstallationEvidence:
    if set(payload) != _STATUS_FIELDS:
        raise InstallationEvidenceError("installation evidence fields do not match the schema")
    _validate_identity(payload, run_id)
    status = payload.get("status")
    outcome = payload.get("outcome")
    if status not in {"passed", "failed"} or outcome not in _OUTCOMES:
        raise InstallationEvidenceError("installation status or outcome is invalid")
    if payload.get("phase") != "completed":
        raise InstallationEvidenceError("installation evidence must be terminal")
    if status == "passed" and outcome != "passed":
        raise InstallationEvidenceError("passed status requires passed outcome")
    if status == "failed" and outcome == "passed":
        raise InstallationEvidenceError("failed status may not have passed outcome")
    if payload.get("artifact") != "artifact.exe" or len(PureWindowsPath(str(payload.get("artifact"))).parts) != 1:
        raise InstallationEvidenceError("installation artifact identity is invalid")
    if payload.get("installer_kind") != "nsis_exe":
        raise InstallationEvidenceError("installation evidence installer_kind is invalid")
    if payload.get("expected_install_root") != FIXTURE_INSTALL_ROOT:
        raise InstallationEvidenceError("installation evidence install root is invalid")
    installed_executable_found = payload.get("installed_executable_found")
    if not isinstance(installed_executable_found, bool):
        raise InstallationEvidenceError("Phase 2B installed executable evidence is invalid")
    installed_hash_value = payload.get("installed_executable_sha256")
    installed_executable_sha256 = None if status == "failed" and installed_hash_value == "" else _sha256(installed_hash_value, "installed_executable_sha256")
    from .fixture_builder import TRUSTED_FIXTURE_GUI_SHA256
    if status == "passed" and (not installed_executable_found or installed_executable_sha256 != TRUSTED_FIXTURE_GUI_SHA256):
        raise InstallationEvidenceError("Phase 2B installed executable hash is invalid")
    if payload.get("first_launch_verified") is not False:
        raise InstallationEvidenceError("Phase 2B may not report first launch verification")

    started = _timestamp(payload.get("started_at"), "started_at")
    updated = _timestamp(payload.get("updated_at"), "updated_at")
    completed = _timestamp(payload.get("completed_at"), "completed_at")
    installer_started = _timestamp(payload.get("installer_started_at"), "installer_started_at", optional=True)
    installer_finished = _timestamp(payload.get("installer_finished_at"), "installer_finished_at", optional=True)
    if not started <= updated <= completed:
        raise InstallationEvidenceError("installation evidence timestamps are inconsistent")
    if installer_started is not None and installer_started < started:
        raise InstallationEvidenceError("installer start timestamp is inconsistent")
    if installer_finished is not None and (installer_started is None or installer_finished < installer_started or installer_finished > completed):
        raise InstallationEvidenceError("installer finish timestamp is inconsistent")

    exit_code = payload.get("installer_exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise InstallationEvidenceError("installer_exit_code must be an integer or null")
    duration = payload.get("install_duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0 or duration > 1805):
        raise InstallationEvidenceError("install_duration_seconds is invalid")
    if duration is not None and (installer_started is None or installer_finished is None):
        raise InstallationEvidenceError("installer duration requires start and finish timestamps")

    reboot_required = payload.get("reboot_required")
    marker_found = payload.get("installed_marker_found")
    payload_found = payload.get("installed_payload_found")
    hash_verified = payload.get("hash_verified")
    if not all(isinstance(value, bool) for value in (reboot_required, marker_found, payload_found, hash_verified)):
        raise InstallationEvidenceError("installation evidence boolean fields are invalid")
    host_hash = _sha256(payload.get("artifact_sha256_host"), "artifact_sha256_host")
    guest_hash = _sha256(payload.get("artifact_sha256_guest"), "artifact_sha256_guest")
    expected_hash = artifact_sha256.lower()
    errors = _messages(payload.get("errors"), "errors")
    warnings = _messages(payload.get("warnings"), "warnings")

    if outcome == "reboot_required" and (not reboot_required or exit_code not in {1641, 3010}):
        raise InstallationEvidenceError("reboot_required outcome is inconsistent")
    if outcome != "reboot_required" and reboot_required:
        raise InstallationEvidenceError("reboot_required flag is inconsistent")
    if status == "passed":
        if exit_code != 0 or installer_started is None or installer_finished is None or duration is None:
            raise InstallationEvidenceError("passed installation requires successful controlled execution")
        if not hash_verified or host_hash != expected_hash or guest_hash != expected_hash:
            raise InstallationEvidenceError("passed installation requires matching artifact hashes")
        if not marker_found or not payload_found:
            raise InstallationEvidenceError("passed installation requires marker and payload evidence")
        if errors:
            raise InstallationEvidenceError("passed installation may not contain errors")
    return ValidatedInstallationEvidence(
        run_id,
        status,
        outcome,
        exit_code,
        reboot_required,
        marker_found,
        payload_found,
        host_hash,
        guest_hash,
        errors,
        warnings,
        payload,
    )


def read_installation_heartbeat(path: Path, run_id: str) -> dict[str, Any]:
    payload = _load_json(path)
    if set(payload) != {"schema_version", "protocol", "run_id", "phase", "updated_at"}:
        raise InstallationEvidenceError("installation heartbeat fields are invalid")
    _validate_identity(payload, run_id)
    if payload.get("phase") not in _PHASES:
        raise InstallationEvidenceError("installation heartbeat phase is invalid")
    _timestamp(payload.get("updated_at"), "heartbeat.updated_at")
    return payload


def validate_installation_evidence_directory(
    evidence_directory: Path,
    run_id: str,
    artifact_sha256: str,
) -> ValidatedInstallationEvidence:
    try:
        directory_info = evidence_directory.lstat()
    except OSError as exc:
        raise InstallationEvidenceError("installation evidence directory is missing") from exc
    if evidence_directory.is_symlink() or _is_reparse(evidence_directory) or not stat.S_ISDIR(directory_info.st_mode):
        raise InstallationEvidenceError("installation evidence directory must be a regular non-reparse directory")
    entries = tuple(evidence_directory.iterdir())
    names = {entry.name for entry in entries}
    if names != EXPECTED_INSTALLATION_EVIDENCE_FILES or len(entries) != len(EXPECTED_INSTALLATION_EVIDENCE_FILES):
        raise InstallationEvidenceError("installation evidence contains missing or unexpected files")
    sizes = {entry.name: _validate_regular_file(entry) for entry in entries}
    if sizes["installer.log"] > MAX_INSTALLER_LOG_BYTES or sum(sizes.values()) > MAX_TOTAL_EVIDENCE_BYTES:
        raise InstallationEvidenceError("installation evidence exceeds its size limits")
    try:
        installer_log = (evidence_directory / "installer.log").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InstallationEvidenceError("installer log is not valid UTF-8") from exc
    if "\x00" in installer_log:
        raise InstallationEvidenceError("installer log contains invalid data")
    log_lines = installer_log.splitlines()
    if not log_lines or len(log_lines) > 100:
        raise InstallationEvidenceError("installer log must contain bounded controlled messages")
    for line in log_lines:
        timestamp_value, separator, message = line.partition(" ")
        if not separator or not _INSTALLER_LOG_MESSAGE.fullmatch(message):
            raise InstallationEvidenceError("installer log contains an unsupported message")
        _timestamp(timestamp_value, "installer.log timestamp")

    status_payload = _load_json(evidence_directory / "status.json")
    installation_payload = _load_json(evidence_directory / "installation-evidence.json")
    if status_payload != installation_payload:
        raise InstallationEvidenceError("status and installation evidence do not match")
    validated = _validate_status(status_payload, run_id, artifact_sha256)

    completion = _load_json(evidence_directory / "completion.json")
    if set(completion) != {"schema_version", "protocol", "run_id", "completed", "status", "outcome", "phase", "completed_at"}:
        raise InstallationEvidenceError("installation completion fields are invalid")
    _validate_identity(completion, run_id)
    if completion.get("completed") is not True or completion.get("phase") != "completed":
        raise InstallationEvidenceError("installation completion marker is incomplete")
    if completion.get("status") != validated.status or completion.get("outcome") != validated.outcome:
        raise InstallationEvidenceError("installation completion does not match status")
    if _timestamp(completion.get("completed_at"), "completion.completed_at") != _timestamp(status_payload.get("completed_at"), "completed_at"):
        raise InstallationEvidenceError("installation completion timestamp does not match status")

    heartbeat = _load_json(evidence_directory / "heartbeat.json")
    if set(heartbeat) != {"schema_version", "protocol", "run_id", "phase", "updated_at"}:
        raise InstallationEvidenceError("installation heartbeat fields are invalid")
    _validate_identity(heartbeat, run_id)
    if heartbeat.get("phase") not in _PHASES or heartbeat.get("phase") != "completed":
        raise InstallationEvidenceError("installation heartbeat is not terminal")
    _timestamp(heartbeat.get("updated_at"), "heartbeat.updated_at")

    guest_system = _load_json(evidence_directory / "guest-system.json")
    if set(guest_system) != {"os_version", "architecture", "powershell_version"}:
        raise InstallationEvidenceError("guest system evidence fields are invalid")
    if any(not isinstance(value, str) or not _SAFE_GUEST_SYSTEM_VALUE.fullmatch(value) for value in guest_system.values()):
        raise InstallationEvidenceError("guest system evidence values are invalid")
    return validated
