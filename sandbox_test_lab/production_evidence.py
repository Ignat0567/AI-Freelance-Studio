from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import stat
from typing import Any

from .production_self_test import (
    BACKEND_ENDPOINT,
    INSTALLER_SHA256,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    PRODUCTION_PROTOCOL,
    PRODUCTION_SCHEMA_VERSION,
    STABLE_DURATION_SECONDS,
    TRUSTED_PROFILE_NAME,
    WINDOW_TITLE,
)
from .workspace import REPARSE_POINT_ATTRIBUTE


EXPECTED_PRODUCTION_EVIDENCE_FILES = frozenset({
    "status.json", "heartbeat.json", "completion.json", "guest-system.json", "lifecycle.log",
    "production-evidence.json",
})
MAX_JSON_BYTES = 128 * 1024
MAX_LOG_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 576 * 1024
_TEMPORARY = re.compile(r"^(status|heartbeat|completion|guest-system|production-evidence)\.json\.[0-9a-f]{32}\.tmp$")
_SAFE_MESSAGE = re.compile(r"^[A-Za-z0-9_]{1,192}$")
_SAFE_SYSTEM = re.compile(r"^[A-Za-z0-9 ._()\-]{1,256}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEX8 = re.compile(r"^[0-9a-f]{8}$")
_SAFE_HEX = re.compile(r"^[0-9a-f]{1,16}$")
_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,127}$")
_READINESS = {"installation_failed", "gui_launch_failed", "backend_not_ready", "fully_ready"}
_BACKEND_STATES = {"ready", "not_ready", "not_applicable"}
_PROCESS_ROLES = {"root", "electron_child", "backend_candidate", "system_helper", "unknown"}
_IMAGE_ORIGINS = {
    "verified_install_root", "canonical_system32", "canonical_syswow64", "other_windows_directory",
    "outside_untrusted", "unavailable",
}
_AUTHENTICODE_STATUSES = {"valid", "not_signed", "hash_mismatch", "not_trusted", "unknown_error", "unavailable"}
_WER_EVENT_TYPES = {"unavailable", "appcrash", "bex", "bex64"}
_INSTALLER_DIAGNOSTIC_SOURCES = {"none", "application_error_1000", "windows_error_reporting_1001"}
_DIAGNOSTIC_FIELDS = {
    "process_role", "image_basename", "image_origin", "image_regular_file", "image_reparse_free", "image_hash",
    "authenticode_status", "microsoft_signed", "parent_relation_verified", "creation_after_launch",
    "eligible_for_gui_verification", "eligible_for_backend_verification", "eligible_for_cleanup",
}
MAX_OWNED_DESCENDANT_DIAGNOSTICS = 64
_STATUS_FIELDS = {
    "schema_version", "protocol", "run_id", "profile_name", "product", "version", "status", "outcome",
    "overall_readiness", "started_at", "installer_started_at", "process_start_returned_at", "installer_finished_at", "launch_started_at",
    "process_started_at", "window_detected_at", "stable_started_at", "stable_verified_at", "backend_started_at",
    "backend_ready_at", "cleanup_started_at", "cleanup_finished_at", "completed_at", "artifact_name",
    "artifact_size", "artifact_sha256_host", "artifact_sha256_guest", "host_artifact_verified",
    "guest_artifact_verified", "network_disabled", "installer_exit_code", "installer_exit_code_hex",
    "install_duration_seconds", "installer_process_started", "installer_process_exited", "crash_event_found",
    "faulting_application_basename", "faulting_module_basename", "exception_code", "fault_offset",
    "wer_event_type", "installer_diagnostic_source", "reboot_required", "installed_exe_found",
    "installed_exe_sha256", "installed_exe_regular", "installed_exe_non_reparse", "install_root_verified",
    "installation_passed", "launch_root_pid", "owned_tree_count", "all_images_contained", "root_process_retained",
    "window_handle", "window_title", "window_visible", "window_owner_pid", "window_owner_in_owned_tree",
    "stable_duration_seconds", "first_launch_verified", "gui_passed", "backend_required", "backend_process_tracked",
    "backend_image_contained", "backend_status", "backend_http_status", "backend_response_status",
    "backend_response_service", "backend_endpoint_verified", "backend_pid", "backend_pid_ownership_verified",
    "backend_image_verified", "fully_ready", "production_cleanup_method", "production_taskkill_observed", "graceful_close_attempted",
    "graceful_cleanup_succeeded", "fallback_kill_used", "owned_processes_exited", "auxiliary_processes_exited", "cleanup_complete", "errors", "warnings",
    "owned_descendant_diagnostics",
    "guest_terminal_deadline_utc",
}


class ProductionEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedProductionEvidence:
    run_id: str
    status: str
    outcome: str
    overall_readiness: str
    fully_ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    raw: dict[str, Any]


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _regular_size(path: Path) -> int:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProductionEvidenceError("required production evidence file is missing") from exc
    if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        raise ProductionEvidenceError("production evidence must be regular and non-reparse")
    if path.suffix.lower() in {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".hta"}:
        raise ProductionEvidenceError("production evidence may not contain executable or script files")
    return info.st_size


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionEvidenceError("production evidence contains duplicate JSON keys")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    if _regular_size(path) > MAX_JSON_BYTES:
        raise ProductionEvidenceError("production evidence JSON exceeds its size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionEvidenceError("production evidence is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProductionEvidenceError("production evidence JSON root must be an object")
    return value


def _timestamp(value: Any, field: str, *, optional: bool = False) -> datetime | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise ProductionEvidenceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionEvidenceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ProductionEvidenceError(f"{field} must include a timezone")
    return parsed


def _messages(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 20 or any(not isinstance(v, str) or not _SAFE_MESSAGE.fullmatch(v) for v in value):
        raise ProductionEvidenceError(f"{field} must be a bounded safe-message list")
    return tuple(value)


def _reject_sensitive(value: Any, field: str = "") -> None:
    lowered = field.lower()
    if any(word in lowered for word in ("path", "command", "argument", "environment", "header", "cookie", "password", "secret", "token", "credential", "user_data")):
        raise ProductionEvidenceError("production evidence contains path-bearing or secret-like fields")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_sensitive(item, key)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive(item, field)
    elif isinstance(value, str) and ("\\" in value or ":/" in value or value.startswith(("/", "~"))):
        if field != "protocol":
            raise ProductionEvidenceError("production evidence contains a path or URL value")


def _owned_diagnostics(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) > MAX_OWNED_DESCENDANT_DIAGNOSTICS:
        raise ProductionEvidenceError("owned descendant diagnostics must be a bounded list")
    diagnostics: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != _DIAGNOSTIC_FIELDS:
            raise ProductionEvidenceError("owned descendant diagnostic fields do not match the exact schema")
        if item["process_role"] not in _PROCESS_ROLES or item["image_origin"] not in _IMAGE_ORIGINS:
            raise ProductionEvidenceError("owned descendant diagnostic role or origin is invalid")
        if not isinstance(item["image_basename"], str) or not _SAFE_BASENAME.fullmatch(item["image_basename"]):
            raise ProductionEvidenceError("owned descendant diagnostic basename is invalid")
        if item["authenticode_status"] not in _AUTHENTICODE_STATUSES:
            raise ProductionEvidenceError("owned descendant diagnostic Authenticode status is invalid")
        image_hash = item["image_hash"]
        if not isinstance(image_hash, str) or (image_hash and not _SHA256.fullmatch(image_hash)):
            raise ProductionEvidenceError("owned descendant diagnostic hash is invalid")
        bool_fields = _DIAGNOSTIC_FIELDS - {
            "process_role", "image_basename", "image_origin", "image_hash", "authenticode_status", "microsoft_signed",
        }
        if any(not isinstance(item[name], bool) for name in bool_fields):
            raise ProductionEvidenceError("owned descendant diagnostic booleans are invalid")
        if item["microsoft_signed"] is not None and not isinstance(item["microsoft_signed"], bool):
            raise ProductionEvidenceError("owned descendant diagnostic Microsoft signature flag is invalid")
        if item["image_reparse_free"] and not item["image_regular_file"]:
            raise ProductionEvidenceError("owned descendant reparse proof lacks a regular file")
        if not item["parent_relation_verified"] or not item["creation_after_launch"]:
            raise ProductionEvidenceError("owned descendant lacks parent and creation proof")
        if item["image_origin"] == "verified_install_root" and not (
            item["image_regular_file"] and item["image_reparse_free"]
        ):
            raise ProductionEvidenceError("install-root diagnostic lacks regular non-reparse proof")
        if item["process_role"] in {"root", "electron_child", "backend_candidate"} and item["image_origin"] != "verified_install_root":
            raise ProductionEvidenceError("application process role must originate in the install root")
        if item["process_role"] == "system_helper" and item["image_origin"] not in {"canonical_system32", "canonical_syswow64"}:
            raise ProductionEvidenceError("system helper origin is invalid")
        external = item["image_origin"] != "verified_install_root"
        if external and any(item[name] for name in (
            "eligible_for_gui_verification", "eligible_for_backend_verification", "eligible_for_cleanup",
        )):
            raise ProductionEvidenceError("external owned descendant cannot be proof or cleanup eligible")
        if item["process_role"] == "system_helper" and any(item[name] for name in (
            "eligible_for_gui_verification", "eligible_for_backend_verification", "eligible_for_cleanup",
        )):
            raise ProductionEvidenceError("system helper cannot be proof or cleanup eligible")
        if item["eligible_for_gui_verification"] != (
            item["image_origin"] == "verified_install_root" and item["process_role"] in {"root", "electron_child"}
        ):
            raise ProductionEvidenceError("owned descendant GUI eligibility is inconsistent")
        if item["eligible_for_backend_verification"] != (
            item["image_origin"] == "verified_install_root" and item["process_role"] == "backend_candidate"
        ):
            raise ProductionEvidenceError("owned descendant backend eligibility is inconsistent")
        if item["eligible_for_cleanup"] != (item["image_origin"] == "verified_install_root"):
            raise ProductionEvidenceError("owned descendant cleanup eligibility is inconsistent")
        diagnostics.append(item)
    return tuple(diagnostics)


def _is_trusted_auxiliary(item: dict[str, Any]) -> bool:
    return (
        item["image_basename"].casefold() == "conhost.exe"
        and item["image_origin"] == "canonical_system32"
        and item["image_regular_file"]
        and item["image_reparse_free"]
        and item["authenticode_status"] == "valid"
        and item["microsoft_signed"] is True
        and item["parent_relation_verified"]
        and item["creation_after_launch"]
        and item["process_role"] == "system_helper"
        and not item["eligible_for_gui_verification"]
        and not item["eligible_for_backend_verification"]
        and not item["eligible_for_cleanup"]
    )


def validate_partial_production_evidence_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
    except OSError as exc:
        raise ProductionEvidenceError("production evidence directory is missing") from exc
    if directory.is_symlink() or _is_reparse(directory) or not stat.S_ISDIR(info.st_mode):
        raise ProductionEvidenceError("production evidence directory must be regular and non-reparse")
    total = 0
    for entry in directory.iterdir():
        if entry.name not in EXPECTED_PRODUCTION_EVIDENCE_FILES and not _TEMPORARY.fullmatch(entry.name):
            raise ProductionEvidenceError("production evidence contains an unexpected file")
        size = _regular_size(entry)
        if size > (MAX_LOG_BYTES if entry.name == "lifecycle.log" else MAX_JSON_BYTES):
            raise ProductionEvidenceError("production evidence file exceeds its size limit")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ProductionEvidenceError("production evidence exceeds its total size limit")


def _validate_status(
    payload: dict[str, Any], run_id: str, *, expected_guest_terminal_deadline_utc: str | None = None,
) -> ValidatedProductionEvidence:
    if set(payload) != _STATUS_FIELDS:
        raise ProductionEvidenceError("production evidence fields do not match the exact schema")
    _reject_sensitive(payload)
    if payload.get("schema_version") != PRODUCTION_SCHEMA_VERSION or payload.get("protocol") != PRODUCTION_PROTOCOL or payload.get("run_id") != run_id:
        raise ProductionEvidenceError("production evidence identity is invalid")
    if (payload.get("profile_name"), payload.get("product"), payload.get("version")) != (TRUSTED_PROFILE_NAME, PRODUCT_NAME, PRODUCT_VERSION):
        raise ProductionEvidenceError("production evidence profile identity is invalid")
    readiness = payload.get("overall_readiness")
    status, outcome = payload.get("status"), payload.get("outcome")
    if readiness not in _READINESS or status not in {"passed", "failed"} or outcome not in {"passed", "failed", "timed_out", "reboot_required", "infrastructure_error"}:
        raise ProductionEvidenceError("production evidence terminal state is invalid")
    if (status == "passed") != (outcome == "passed") or (status == "passed") != (readiness == "fully_ready"):
        raise ProductionEvidenceError("production terminal state is inconsistent")
    if payload.get("artifact_name") != "artifact.exe" or payload.get("artifact_size") != 215224643:
        raise ProductionEvidenceError("production artifact identity is invalid")
    for field in ("artifact_sha256_host", "artifact_sha256_guest"):
        if not isinstance(payload.get(field), str) or (field == "artifact_sha256_host" and not _SHA256.fullmatch(payload[field])) or (field == "artifact_sha256_guest" and payload[field] != "" and not _SHA256.fullmatch(payload[field])):
            raise ProductionEvidenceError(f"{field} is invalid")
    if payload["artifact_sha256_host"] != INSTALLER_SHA256:
        raise ProductionEvidenceError("host artifact hash is not trusted")
    installed_hash = payload.get("installed_exe_sha256")
    if installed_hash != "" and (not isinstance(installed_hash, str) or not _SHA256.fullmatch(installed_hash)):
        raise ProductionEvidenceError("installed executable diagnostic hash is invalid")
    booleans = [name for name in _STATUS_FIELDS if name in {
        "host_artifact_verified", "guest_artifact_verified", "network_disabled", "reboot_required", "installed_exe_found",
        "installed_exe_regular", "installed_exe_non_reparse", "install_root_verified", "installation_passed",
        "all_images_contained", "root_process_retained", "window_visible", "window_owner_in_owned_tree",
        "first_launch_verified", "gui_passed", "backend_required", "backend_process_tracked", "backend_image_contained",
        "backend_endpoint_verified", "backend_pid_ownership_verified", "backend_image_verified", "fully_ready",
        "production_taskkill_observed", "graceful_close_attempted", "graceful_cleanup_succeeded",
        "fallback_kill_used", "owned_processes_exited", "auxiliary_processes_exited", "cleanup_complete",
        "installer_process_started", "installer_process_exited", "crash_event_found",
    }]
    if any(not isinstance(payload.get(name), bool) for name in booleans):
        raise ProductionEvidenceError("production evidence boolean fields are invalid")
    for field in ("installer_exit_code", "launch_root_pid", "owned_tree_count", "window_handle", "window_owner_pid", "backend_http_status", "backend_pid"):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ProductionEvidenceError(f"{field} must be an integer or null")
    duration = payload.get("stable_duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration <= 120):
        raise ProductionEvidenceError("stable duration is invalid")
    install_duration = payload.get("install_duration_seconds")
    if install_duration is not None and (
        isinstance(install_duration, bool) or not isinstance(install_duration, (int, float))
        or not 0 <= install_duration <= 610 or round(install_duration, 3) != install_duration
    ):
        raise ProductionEvidenceError("install duration is invalid")
    if status == "passed" and install_duration is not None and install_duration >= 600:
        raise ProductionEvidenceError("passed install duration exceeds the fixed timeout")
    exit_code = payload.get("installer_exit_code")
    exit_hex = payload.get("installer_exit_code_hex")
    if not isinstance(exit_hex, str) or (exit_hex and not _HEX8.fullmatch(exit_hex)):
        raise ProductionEvidenceError("installer exit-code hex is invalid")
    expected_exit_hex = "" if exit_code is None else (exit_code & 0xFFFFFFFF).to_bytes(4, "big").hex()
    if exit_hex != expected_exit_hex:
        raise ProductionEvidenceError("installer exit code and hex contradict")
    started_process = payload.get("installer_process_started")
    exited_process = payload.get("installer_process_exited")
    if exited_process and not started_process:
        raise ProductionEvidenceError("exited installer was not started")
    start_attempted = payload.get("installer_started_at") is not None
    start_returned = payload.get("process_start_returned_at") is not None
    if started_process != start_returned or start_returned and not start_attempted:
        raise ProductionEvidenceError("installer lifecycle start state contradicts its timestamps")
    if exited_process != (payload.get("installer_finished_at") is not None):
        raise ProductionEvidenceError("installer lifecycle exit state contradicts its timestamp")
    if exited_process != (install_duration is not None and exit_code is not None):
        raise ProductionEvidenceError("installer exit state contradicts duration or code")
    crash_found = payload.get("crash_event_found")
    app_basename = payload.get("faulting_application_basename")
    module_basename = payload.get("faulting_module_basename")
    exception_code = payload.get("exception_code")
    fault_offset = payload.get("fault_offset")
    wer_type = payload.get("wer_event_type")
    diagnostic_source = payload.get("installer_diagnostic_source")
    if app_basename not in {"artifact.exe", "unavailable"}:
        raise ProductionEvidenceError("faulting application basename is invalid")
    if not isinstance(module_basename, str) or not _SAFE_BASENAME.fullmatch(module_basename):
        raise ProductionEvidenceError("faulting module basename is invalid")
    if not isinstance(exception_code, str) or (exception_code and not _HEX8.fullmatch(exception_code)):
        raise ProductionEvidenceError("installer exception code is invalid")
    if not isinstance(fault_offset, str) or (fault_offset and not _SAFE_HEX.fullmatch(fault_offset)):
        raise ProductionEvidenceError("installer fault offset is invalid")
    if wer_type not in _WER_EVENT_TYPES or diagnostic_source not in _INSTALLER_DIAGNOSTIC_SOURCES:
        raise ProductionEvidenceError("installer crash diagnostic enum is invalid")
    if crash_found:
        if exit_code in (None, 0) or app_basename != "artifact.exe" or diagnostic_source == "none":
            raise ProductionEvidenceError("installer crash diagnostic contradicts installer outcome")
        if diagnostic_source == "application_error_1000" and wer_type != "unavailable":
            raise ProductionEvidenceError("Application Error diagnostic contains WER-only data")
        if diagnostic_source == "windows_error_reporting_1001" and (
            module_basename != "unavailable" or exception_code or fault_offset
        ):
            raise ProductionEvidenceError("WER diagnostic contains Application Error-only data")
    elif (app_basename, module_basename, exception_code, fault_offset, wer_type, diagnostic_source) != (
        "unavailable", "unavailable", "", "", "unavailable", "none"
    ):
        raise ProductionEvidenceError("unavailable installer crash diagnostics are inconsistent")
    times = {name: _timestamp(payload.get(name), name, optional=name not in {"started_at", "completed_at"}) for name in (
        "started_at", "installer_started_at", "process_start_returned_at", "installer_finished_at", "launch_started_at", "process_started_at",
        "window_detected_at", "stable_started_at", "stable_verified_at", "backend_started_at", "backend_ready_at",
        "cleanup_started_at", "cleanup_finished_at", "completed_at",
    )}
    started_at, completed_at = times["started_at"], times["completed_at"]
    guest_terminal_deadline = _timestamp(payload.get("guest_terminal_deadline_utc"), "guest_terminal_deadline_utc")
    canonical_terminal_deadline = guest_terminal_deadline.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if payload["guest_terminal_deadline_utc"] != canonical_terminal_deadline:
        raise ProductionEvidenceError("guest terminal deadline must be canonical UTC")
    if expected_guest_terminal_deadline_utc is not None and payload["guest_terminal_deadline_utc"] != expected_guest_terminal_deadline_utc:
        raise ProductionEvidenceError("guest terminal deadline does not match host authority")
    if completed_at > guest_terminal_deadline:
        raise ProductionEvidenceError("production completed after the guest terminal deadline")
    present = [value for value in times.values() if value is not None]
    if any(value < started_at or value > completed_at for value in present):
        raise ProductionEvidenceError("production lifecycle timestamp is outside the run interval")
    if present != sorted(present):
        raise ProductionEvidenceError("production lifecycle timestamps are inconsistent")
    if exited_process:
        elapsed = (times["installer_finished_at"] - times["installer_started_at"]).total_seconds()
        if elapsed < 0 or abs(elapsed - install_duration) > 0.01:
            raise ProductionEvidenceError("install duration contradicts installer timestamps")
    errors = _messages(payload.get("errors"), "errors")
    warnings = _messages(payload.get("warnings"), "warnings")
    diagnostics = _owned_diagnostics(payload.get("owned_descendant_diagnostics"))
    external_diagnostics = tuple(item for item in diagnostics if item["image_origin"] != "verified_install_root")
    trusted_auxiliaries = tuple(item for item in external_diagnostics if _is_trusted_auxiliary(item))
    unauthorized_external = tuple(item for item in external_diagnostics if not _is_trusted_auxiliary(item))
    owned_count = payload.get("owned_tree_count")
    if not (owned_count is None and not diagnostics) and owned_count != len(diagnostics):
        raise ProductionEvidenceError("owned tree count does not match descendant diagnostics")
    if payload.get("root_process_retained") and (
        not diagnostics or sum(item["process_role"] == "root" for item in diagnostics) != 1
    ):
        raise ProductionEvidenceError("retained root lacks one root diagnostic")
    if unauthorized_external:
        if payload.get("all_images_contained") or any(payload.get(name) for name in (
            "first_launch_verified", "gui_passed", "backend_process_tracked", "backend_image_contained",
            "backend_endpoint_verified", "backend_pid_ownership_verified", "backend_image_verified", "fully_ready",
        )):
            raise ProductionEvidenceError("external owned descendant contradicts readiness proof")
        if status != "failed" or "owned_process_image_outside_install_root" not in errors:
            raise ProductionEvidenceError("external owned descendant requires structured failure")
    if diagnostics and payload.get("all_images_contained") != (not unauthorized_external):
        raise ProductionEvidenceError("all-images containment or auxiliary authorization contradicts first launch")
    if not payload.get("host_artifact_verified"):
        raise ProductionEvidenceError("host artifact verification is required")
    if payload.get("guest_artifact_verified") and payload.get("artifact_sha256_guest") != INSTALLER_SHA256:
        raise ProductionEvidenceError("guest artifact verification contradicts its hash")
    if payload.get("installed_exe_found") and not (
        installed_hash and payload.get("installed_exe_regular") and payload.get("installed_exe_non_reparse")
        and payload.get("install_root_verified")
    ):
        raise ProductionEvidenceError("installed executable claim lacks type, hash, or containment proof")
    if payload.get("gui_passed") != payload.get("first_launch_verified"):
        raise ProductionEvidenceError("GUI and first-launch claims are inconsistent")
    if status == "failed" and not errors:
        raise ProductionEvidenceError("failed production evidence requires a safe error code")
    if outcome == "reboot_required" and not (payload.get("reboot_required") and payload.get("installer_exit_code") in {1641, 3010}):
        raise ProductionEvidenceError("reboot-required outcome lacks a reboot exit code")
    if payload.get("reboot_required") and payload.get("installer_exit_code") not in {1641, 3010}:
        raise ProductionEvidenceError("reboot claim lacks a reboot exit code")
    backend_status = payload.get("backend_status")
    if backend_status not in _BACKEND_STATES:
        raise ProductionEvidenceError("backend status is invalid")
    if backend_status == "not_applicable":
        if payload.get("backend_required") or any(payload.get(name) not in (False, None, "") for name in (
            "backend_process_tracked", "backend_image_contained", "backend_http_status", "backend_response_status",
            "backend_response_service", "backend_endpoint_verified", "backend_pid", "backend_pid_ownership_verified",
            "backend_image_verified", "backend_started_at", "backend_ready_at",
        )):
            raise ProductionEvidenceError("not-applicable backend evidence is inconsistent")
    if backend_status == "ready" and not (
        payload.get("backend_required") and payload.get("backend_process_tracked") and payload.get("backend_image_contained")
        and payload.get("backend_pid_ownership_verified") and payload.get("backend_image_verified")
        and isinstance(payload.get("backend_pid"), int) and payload["backend_pid"] > 0
        and payload.get("backend_endpoint_verified") and payload.get("backend_http_status") == 200
        and payload.get("backend_response_status") == "ok" and payload.get("backend_response_service") == "FreelancerStudio"
        and times["backend_started_at"] is not None and times["backend_ready_at"] is not None
    ):
        raise ProductionEvidenceError("ready backend lacks process and exact endpoint proof")
    if payload.get("installation_passed") and not (
        payload.get("host_artifact_verified") and payload.get("guest_artifact_verified") and payload["artifact_sha256_guest"] == INSTALLER_SHA256
        and payload.get("network_disabled") and payload.get("installer_exit_code") == 0
        and payload.get("installer_exit_code_hex") == "00000000" and payload.get("installer_process_started")
        and payload.get("installer_process_exited") and not payload.get("crash_event_found") and not payload.get("reboot_required")
        and payload.get("installed_exe_found") and installed_hash and payload.get("installed_exe_regular")
        and payload.get("installed_exe_non_reparse") and payload.get("install_root_verified")
    ):
        raise ProductionEvidenceError("installation_passed lacks artifact, installer, and executable proof")
    if payload.get("installation_passed") and any(times[name] is None for name in ("installer_started_at", "installer_finished_at")):
        raise ProductionEvidenceError("installation_passed lacks installer lifecycle timestamps")
    if payload.get("first_launch_verified"):
        if not all(payload.get(name) is True for name in (
            "installation_passed", "installed_exe_found", "installed_exe_regular", "installed_exe_non_reparse",
            "install_root_verified", "root_process_retained", "all_images_contained", "window_visible",
            "window_owner_in_owned_tree", "gui_passed",
        )):
            raise ProductionEvidenceError("first launch lacks lifecycle and identity proof")
        if not all(isinstance(payload.get(name), int) and payload[name] > 0 for name in ("launch_root_pid", "owned_tree_count", "window_handle", "window_owner_pid")):
            raise ProductionEvidenceError("first launch lacks owned PID or window proof")
        if payload.get("window_title") != WINDOW_TITLE or duration is None or duration < STABLE_DURATION_SECONDS:
            raise ProductionEvidenceError("first launch title or stable interval is invalid")
        if times["stable_started_at"] is None or times["stable_verified_at"] is None or (times["stable_verified_at"] - times["stable_started_at"]).total_seconds() < STABLE_DURATION_SECONDS:
            raise ProductionEvidenceError("first launch stable timestamps are insufficient")
        if any(times[name] is None for name in ("launch_started_at", "process_started_at", "window_detected_at")):
            raise ProductionEvidenceError("first launch lacks launch and window timestamps")
    if payload.get("fully_ready") != (readiness == "fully_ready"):
        raise ProductionEvidenceError("fully_ready contradicts overall readiness")
    if payload.get("fully_ready") and not (payload.get("first_launch_verified") and backend_status == "ready" and payload.get("cleanup_complete")):
        raise ProductionEvidenceError("fully ready lacks GUI, backend, or cleanup proof")
    if backend_status == "ready" and not payload.get("first_launch_verified"):
        raise ProductionEvidenceError("ready backend lacks a verified first launch")
    if readiness == "installation_failed" and any(payload.get(name) for name in ("installation_passed", "gui_passed", "fully_ready")):
        raise ProductionEvidenceError("installation_failed readiness contradicts lifecycle claims")
    if readiness == "gui_launch_failed" and not (
        payload.get("installation_passed") and not payload.get("gui_passed") and not payload.get("fully_ready")
    ):
        raise ProductionEvidenceError("gui_launch_failed readiness contradicts lifecycle claims")
    if readiness == "backend_not_ready" and not (
        payload.get("installation_passed") and payload.get("gui_passed") and not payload.get("fully_ready")
    ):
        raise ProductionEvidenceError("backend_not_ready readiness contradicts lifecycle claims")
    if payload.get("cleanup_complete") and not (
        payload.get("owned_processes_exited") and payload.get("auxiliary_processes_exited")
        and payload.get("cleanup_started_at") and payload.get("cleanup_finished_at")
    ):
        raise ProductionEvidenceError("cleanup completion lacks owned-process proof")
    if trusted_auxiliaries and status == "passed" and not payload.get("auxiliary_processes_exited"):
        raise ProductionEvidenceError("passed auxiliary evidence lacks natural-exit proof")
    if payload.get("fallback_kill_used") and payload.get("graceful_cleanup_succeeded"):
        raise ProductionEvidenceError("graceful and fallback cleanup are mutually exclusive")
    if payload.get("fallback_kill_used") != ("owned_tree_fallback_kill_used" in warnings):
        raise ProductionEvidenceError("cleanup fallback warning is inconsistent")
    cleanup_method = payload.get("production_cleanup_method")
    if cleanup_method not in {"", "owned_backend_pid_tree"}:
        raise ProductionEvidenceError("production cleanup method is invalid")
    if cleanup_method == "owned_backend_pid_tree" and not (
        payload.get("backend_pid_ownership_verified") and payload.get("backend_image_verified")
        and isinstance(payload.get("backend_pid"), int) and payload["backend_pid"] > 0
    ):
        raise ProductionEvidenceError("production cleanup method lacks backend ownership proof")
    if payload.get("graceful_close_attempted") and cleanup_method != "owned_backend_pid_tree":
        raise ProductionEvidenceError("graceful close lacks verified production cleanup method")
    if payload.get("production_taskkill_observed") and not (
        payload.get("graceful_close_attempted") and cleanup_method == "owned_backend_pid_tree"
    ):
        raise ProductionEvidenceError("production taskkill observation lacks owned cleanup proof")
    if status == "passed" and (errors or not all(payload.get(name) is True for name in (
        "installation_passed", "gui_passed", "first_launch_verified", "backend_pid_ownership_verified",
        "backend_image_verified", "fully_ready", "cleanup_complete",
    ))):
        raise ProductionEvidenceError("passed production evidence lacks complete proof")
    if status == "passed" and cleanup_method != "owned_backend_pid_tree":
        raise ProductionEvidenceError("passed production evidence lacks the owned backend cleanup method")
    if status == "passed" and (not diagnostics or unauthorized_external):
        raise ProductionEvidenceError("passed production evidence contains an unauthorized external diagnostic")
    if status == "passed" and not (
        any(item["eligible_for_gui_verification"] for item in diagnostics)
        and any(item["eligible_for_backend_verification"] for item in diagnostics)
    ):
        raise ProductionEvidenceError("passed production evidence lacks eligible process diagnostics")
    return ValidatedProductionEvidence(run_id, status, outcome, readiness, payload["fully_ready"], errors, warnings, payload)


def read_production_heartbeat(path: Path, run_id: str) -> dict[str, Any]:
    payload = _load_json(path)
    if set(payload) != {"schema_version", "protocol", "run_id", "phase", "updated_at"}:
        raise ProductionEvidenceError("production heartbeat fields are invalid")
    if payload.get("schema_version") != PRODUCTION_SCHEMA_VERSION or payload.get("protocol") != PRODUCTION_PROTOCOL or payload.get("run_id") != run_id:
        raise ProductionEvidenceError("production heartbeat identity is invalid")
    if payload.get("phase") not in {"installing", "launching", "window_detecting", "backend_checking", "cleanup", "completed"}:
        raise ProductionEvidenceError("production heartbeat phase is invalid")
    _timestamp(payload.get("updated_at"), "heartbeat.updated_at")
    return payload


def validate_production_evidence_directory(
    directory: Path,
    run_id: str,
    *,
    expected_guest_terminal_deadline_utc: str | None = None,
) -> ValidatedProductionEvidence:
    validate_partial_production_evidence_directory(directory)
    entries = tuple(directory.iterdir())
    if {entry.name for entry in entries} != EXPECTED_PRODUCTION_EVIDENCE_FILES or len(entries) != len(EXPECTED_PRODUCTION_EVIDENCE_FILES):
        raise ProductionEvidenceError("production evidence contains missing or unexpected files")
    status = _load_json(directory / "status.json")
    evidence = _load_json(directory / "production-evidence.json")
    if status != evidence:
        raise ProductionEvidenceError("production status and evidence do not match")
    validated = _validate_status(
        status, run_id, expected_guest_terminal_deadline_utc=expected_guest_terminal_deadline_utc,
    )
    completion = _load_json(directory / "completion.json")
    if set(completion) != {"schema_version", "protocol", "run_id", "completed", "status", "outcome", "overall_readiness", "completion_created_at"}:
        raise ProductionEvidenceError("production completion fields are invalid")
    if completion != {
        "schema_version": PRODUCTION_SCHEMA_VERSION, "protocol": PRODUCTION_PROTOCOL, "run_id": run_id, "completed": True,
        "status": validated.status, "outcome": validated.outcome, "overall_readiness": validated.overall_readiness,
        "completion_created_at": completion["completion_created_at"],
    }:
        raise ProductionEvidenceError("production completion does not match terminal evidence")
    completed_at = _timestamp(status["completed_at"], "completed_at")
    completion_created_at = _timestamp(completion["completion_created_at"], "completion_created_at")
    if not completed_at <= completion_created_at <= completed_at + timedelta(seconds=5):
        raise ProductionEvidenceError("production completion timestamp is outside the terminal grace interval")
    heartbeat = read_production_heartbeat(directory / "heartbeat.json", run_id)
    if heartbeat["phase"] != "completed" or _timestamp(heartbeat["updated_at"], "heartbeat.updated_at") > completed_at:
        raise ProductionEvidenceError("production heartbeat does not match completion")
    guest = _load_json(directory / "guest-system.json")
    if set(guest) != {"os_version", "architecture", "powershell_version"} or any(not isinstance(v, str) or not _SAFE_SYSTEM.fullmatch(v) for v in guest.values()):
        raise ProductionEvidenceError("production guest-system evidence is invalid")
    try:
        lines = (directory / "lifecycle.log").read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionEvidenceError("production lifecycle log is invalid") from exc
    terminal = "passed" if validated.status == "passed" else "failed"
    if len(lines) != 2 or not lines[0].endswith(" production_self_test_started") or not lines[1].endswith(f" production_self_test_{terminal}"):
        raise ProductionEvidenceError("production lifecycle log contradicts status")
    log_times = [_timestamp(line.partition(" ")[0], "lifecycle timestamp") for line in lines]
    if not _timestamp(status["started_at"], "started_at") <= log_times[0] <= log_times[1] <= _timestamp(status["completed_at"], "completed_at"):
        raise ProductionEvidenceError("production lifecycle timestamps contradict evidence")
    terminal_deadline = _timestamp(status["guest_terminal_deadline_utc"], "guest_terminal_deadline_utc")
    if completion_created_at > terminal_deadline or log_times[1] > terminal_deadline:
        raise ProductionEvidenceError("terminal evidence was emitted after the guest terminal deadline")
    return validated
