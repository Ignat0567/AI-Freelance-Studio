from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PureWindowsPath
from typing import Any


SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 1024 * 1024
GUEST_FINAL_STATUSES = {"passed", "failed"}


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    run_id: str
    status: str
    phase: str
    started_at: str
    updated_at: str
    completed_at: str
    artifact: str
    artifact_sha256_host: str
    artifact_sha256_guest: str | None
    hash_verified: bool
    guest_system: dict[str, Any]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    raw: dict[str, Any]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("evidence contains duplicate JSON keys")
        result[key] = value
    return result


def _load_json(path: Path, *, max_bytes: int = MAX_EVIDENCE_BYTES) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvidenceError("evidence file is missing") from exc
    if size > max_bytes:
        raise EvidenceError("evidence file exceeds the size limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("evidence is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("evidence root must be an object")
    return payload


def _safe_messages(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 100:
        raise EvidenceError(f"{field} must be a bounded list")
    messages = []
    for item in value:
        if not isinstance(item, str):
            raise EvidenceError(f"{field} entries must be strings")
        messages.append(item[:1000])
    return tuple(messages)


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def _validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{field} must be a timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{field} must be an ISO-8601 timestamp") from exc
    return value


def _validate_artifact_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise EvidenceError("artifact must be a bounded filename")
    path = PureWindowsPath(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {".", ".."} or ":" in value:
        raise EvidenceError("artifact may not contain a path")
    return value


def validate_completion(path: Path, expected_run_id: str) -> dict[str, Any]:
    payload = _load_json(path, max_bytes=64 * 1024)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceError("completion schema_version is unsupported")
    if payload.get("run_id") != expected_run_id:
        raise EvidenceError("completion run_id does not match")
    if payload.get("completed") is not True:
        raise EvidenceError("completion marker is incomplete")
    if payload.get("status") not in GUEST_FINAL_STATUSES:
        raise EvidenceError("completion status is invalid")
    _validate_timestamp(payload.get("completed_at"), "completed_at")
    return payload


def validate_guest_evidence(path: Path, expected_run_id: str) -> ValidatedEvidence:
    payload = _load_json(path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceError("evidence schema_version is unsupported")
    if payload.get("run_id") != expected_run_id:
        raise EvidenceError("evidence run_id does not match")
    status = payload.get("status")
    if status not in GUEST_FINAL_STATUSES:
        raise EvidenceError("evidence status is invalid")
    phase = payload.get("phase")
    if not isinstance(phase, str) or not phase or len(phase) > 100:
        raise EvidenceError("evidence phase is invalid")
    host_hash = payload.get("artifact_sha256_host")
    guest_hash = payload.get("artifact_sha256_guest")
    if not _valid_sha256(host_hash):
        raise EvidenceError("evidence contains an invalid host SHA-256")
    if status == "passed" and not _valid_sha256(guest_hash):
        raise EvidenceError("passed evidence requires a valid guest SHA-256")
    if status == "failed" and guest_hash not in (None, "") and not _valid_sha256(guest_hash):
        raise EvidenceError("failed evidence contains an invalid guest SHA-256")
    hash_verified = payload.get("hash_verified")
    if not isinstance(hash_verified, bool):
        raise EvidenceError("hash_verified must be boolean")
    guest_system = payload.get("guest_system")
    if not isinstance(guest_system, dict) or len(guest_system) > 20:
        raise EvidenceError("guest_system must be a bounded object")
    allowed_system_keys = {"os_version", "architecture", "powershell_version"}
    if set(guest_system) - allowed_system_keys:
        raise EvidenceError("guest_system contains unsupported fields")
    if any(not isinstance(value, str) or len(value) > 256 for value in guest_system.values()):
        raise EvidenceError("guest_system fields must be bounded strings")
    return ValidatedEvidence(
        run_id=expected_run_id,
        status=status,
        phase=phase,
        started_at=_validate_timestamp(payload.get("started_at"), "started_at"),
        updated_at=_validate_timestamp(payload.get("updated_at"), "updated_at"),
        completed_at=_validate_timestamp(payload.get("completed_at"), "completed_at"),
        artifact=_validate_artifact_name(payload.get("artifact")),
        artifact_sha256_host=host_hash.lower(),
        artifact_sha256_guest=guest_hash.lower() if guest_hash else None,
        hash_verified=hash_verified,
        guest_system=dict(guest_system),
        errors=_safe_messages(payload.get("errors"), "errors"),
        warnings=_safe_messages(payload.get("warnings"), "warnings"),
        raw=payload,
    )


def read_heartbeat(path: Path, expected_run_id: str) -> dict[str, Any]:
    payload = _load_json(path, max_bytes=64 * 1024)
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("run_id") != expected_run_id:
        raise EvidenceError("heartbeat identity is invalid")
    _validate_timestamp(payload.get("updated_at"), "updated_at")
    if not isinstance(payload.get("phase"), str):
        raise EvidenceError("heartbeat phase is invalid")
    return payload
