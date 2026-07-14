"""Durable, per-project Studio state and acceptance-evidence ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".freelancerstudio"
STATE_FILE_NAME = "project_state.json"
LEDGER_FILE_NAME = "evidence_ledger.json"
RECOVERY_FILE_NAME = "recovery_report.json"
AUDIT_RUNS_DIR_NAME = "audit_runs"
STATE_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1
_LOCK = threading.RLock()
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s'\"]+|\bsk-[A-Za-z0-9_-]{16,}\b")
_IGNORED_DIRS = {".git", ".freelancerstudio", "evidence_artifacts", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache", "__pycache__"}
_IGNORED_EXTS = {".pyc", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".glb"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def studio_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), STATE_DIR_NAME)


def state_path(root: str) -> str:
    return os.path.join(studio_dir(root), STATE_FILE_NAME)


def ledger_path(root: str) -> str:
    return os.path.join(studio_dir(root), LEDGER_FILE_NAME)


def recovery_path(root: str) -> str:
    return os.path.join(studio_dir(root), RECOVERY_FILE_NAME)


def atomic_write_json(path: str, value: Any) -> None:
    """Write JSON durably; a crash leaves the prior completed file intact."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{Path(path).name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def read_json(path: str) -> tuple[dict[str, Any] | None, str]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return (value, "") if isinstance(value, dict) else (None, "JSON root is not an object")
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"invalid_json: {exc}"


def project_snapshot_fingerprint(root: str) -> str:
    """Fingerprint user deliverables, excluding Studio outputs and runtime artifacts."""
    digest = hashlib.sha256()
    root = os.path.abspath(root)
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in _IGNORED_DIRS and not name.endswith(".egg-info"))
        for name in sorted(files):
            if name == "DELIVERY_REPORT.json" or os.path.splitext(name)[1].lower() in _IGNORED_EXTS:
                continue
            path = os.path.join(current, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            digest.update(relative.encode("utf-8", errors="replace"))
            try:
                digest.update(Path(path).read_bytes())
            except OSError as exc:
                digest.update(str(exc).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def criterion_semantic_fingerprint(criterion: dict[str, Any]) -> str:
    meaning = {key: criterion.get(key, "") for key in ("title", "description", "expected_result", "trace", "verification_method", "verifier_plan")}
    return canonical_fingerprint(meaning)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_RE.sub("<redacted>", value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): "<redacted>" if re.search(r"(?i)(api[_-]?key|token|secret|password)", str(key)) else _redact(item) for key, item in value.items()}
    return value


def _criteria_for_state(project: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = []
    for source in project.get("acceptance_criteria", []):
        if not isinstance(source, dict):
            continue
        criterion = {key: value for key, value in source.items() if key != "evidence"}
        criteria.append(_redact(criterion))
    return criteria


def persist_project_state(project: dict[str, Any], root: str | None = None) -> dict[str, Any] | None:
    root = root or project.get("target_path")
    if not root or not os.path.isdir(root):
        return None
    with _LOCK:
        previous, _ = read_json(state_path(root))
        now = utc_now()
        previous = previous or {}
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "project_id": str(project.get("project_id") or project.get("id") or Path(root).name),
            "project_name": str(project.get("title") or project.get("jobTitle") or Path(root).name),
            "project_path": os.path.abspath(root),
            "original_request": _redact(str(project.get("original_request") or project.get("description") or project.get("project_spec", {}).get("original_user_request") or "")),
            "created_at": previous.get("created_at", now),
            "updated_at": now,
            "revision": int(previous.get("revision", 0)) + 1,
            "current_state": project.get("status", "created"),
            "pipeline_stage": project.get("_phase", ""),
            "project_spec": _redact(project.get("project_spec", {})),
            "acceptance_criteria": _criteria_for_state(project),
            "acceptance_criteria_source": project.get("acceptance_criteria_source", "project_contract"),
            "contract_migration": _redact(project.get("contract_migration", previous.get("contract_migration", {}))),
            "audit_finding_history": _redact(project.get("audit_finding_history", previous.get("audit_finding_history", []))),
            "effective_project_profile": _redact(project.get("project_profiles", [])),
            "product_runtime_profile": _redact(project.get("product_runtime_profile", {})),
            "latest_project_snapshot": project_snapshot_fingerprint(root),
            "latest_evidence_snapshot": project.get("latest_evidence_snapshot", previous.get("latest_evidence_snapshot", "")),
            "latest_final_audit_status": (project.get("final_delivery_report") or {}).get("status", previous.get("latest_final_audit_status", "")),
            "latest_delivery_report_path": os.path.join(root, "DELIVERY_REPORT.json"),
            "gates": {key: bool(project.get(key, False)) for key in ("_generation_finished", "_qa_passed", "_final_audit_passed", "_product_judge_passed")},
            "issues": _redact(project.get("issues", [])),
            "repair_attempts": _redact(project.get("repair_attempts", previous.get("repair_attempts", []))),
            "qa_summary": _redact(project.get("latest_qa_result", {})),
            "recovery_status": project.get("recovery_status", "native"),
        }
        atomic_write_json(state_path(root), payload)
        return payload


def load_project_state(root: str) -> tuple[dict[str, Any] | None, str]:
    state, error = read_json(state_path(root))
    if not state:
        return state, error
    if state.get("schema_version") != STATE_SCHEMA_VERSION or not state.get("project_id") or not state.get("project_path"):
        return None, "corrupted_metadata"
    return state, ""


def _empty_ledger(root: str) -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA_VERSION, "project_path": os.path.abspath(root), "history": {}, "latest": {}, "updated_at": utc_now()}


def load_evidence_ledger(root: str) -> tuple[dict[str, Any] | None, str]:
    ledger, error = read_json(ledger_path(root))
    if not ledger:
        return (_empty_ledger(root), "missing") if error == "missing" else (None, error)
    if ledger.get("schema_version") != LEDGER_SCHEMA_VERSION or not isinstance(ledger.get("history"), dict):
        return None, "corrupted_metadata"
    return ledger, ""


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_references(root: str, value: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def visit(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and (key.endswith("path") or key in {"screenshots", "artifact_path"}):
            path = item
            if not os.path.isabs(path):
                # Browser adapters can return a workspace-relative artifact path
                # while the ledger receives a project-relative root.
                workspace_path = os.path.abspath(path)
                path = workspace_path if os.path.isfile(workspace_path) else os.path.join(root, path)
            if os.path.isfile(path):
                try:
                    relative = os.path.relpath(path, root).replace(os.sep, "/")
                except ValueError:
                    # Runtime commands may contain an interpreter path on another
                    # drive. It is not a project artifact and must not block a
                    # durable evidence write.
                    return
                if relative == ".." or relative.startswith("../"):
                    return
                found[relative] = {"path": relative, "sha256": _hash_file(path), "artifact_type": "screenshot" if path.lower().endswith((".png", ".jpg", ".jpeg")) else "file"}

    visit(value)
    return [found[key] for key in sorted(found)]


def _record_hash(record: dict[str, Any]) -> str:
    return canonical_fingerprint({key: value for key, value in record.items() if key != "record_hash"})


def append_evidence_record(project: dict[str, Any], criterion: dict[str, Any], evidence: dict[str, Any], root: str | None = None) -> dict[str, Any] | None:
    root = root or project.get("target_path")
    if not root or not os.path.isdir(root):
        return None
    criterion_id = str(criterion.get("id") or evidence.get("criterion_id") or "")
    if not criterion_id:
        return None
    with _LOCK:
        ledger, error = load_evidence_ledger(root)
        if ledger is None:
            raise ValueError(f"Cannot append evidence to invalid ledger: {error}")
        history = ledger.setdefault("history", {}).setdefault(criterion_id, [])
        prior = ledger.setdefault("latest", {}).get(criterion_id, "")
        record = {
            "event_id": f"evidence-{uuid.uuid4().hex}",
            "criterion_id": criterion_id,
            "criterion_text": " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result")).strip(),
            "criterion_semantic_fingerprint": criterion_semantic_fingerprint(criterion),
            "project_path": os.path.abspath(root),
            "project_snapshot_fingerprint": project_snapshot_fingerprint(root),
            "verifier_type": evidence.get("verifier_type", evidence.get("method", "")),
            "adapter_type": ((evidence.get("collected_evidence") or {}).get("runtime_ui_adapter") or {}).get("adapter_type", ""),
            "evidence_level": "direct" if evidence.get("status") not in ("not_verified", "pending") else "unverified",
            "verdict": evidence.get("verdict", evidence.get("status", "not_verified")),
            "status": evidence.get("status", "not_verified"),
            "classification": evidence.get("classification", ""),
            "assertions": _redact(evidence.get("assertions", [])),
            "failure_reason": _redact(evidence.get("failure_reason", "")),
            "tooling_status": evidence.get("failure_kind", ""),
            "artifact_references": _artifact_references(root, evidence),
            "evidence_snapshot_fingerprint": canonical_fingerprint(evidence),
            "evidence": _redact(evidence),
            "supersedes": prior,
            "created_at": utc_now(),
        }
        judge = ((evidence.get("collected_evidence") or {}).get("INDEPENDENT_PRODUCT_JUDGE_REVIEW") or {})
        if isinstance(judge, dict):
            record["product_judge"] = _redact({key: judge.get(key) for key in ("provider", "model", "independence_level", "verdict", "findings", "blocking_findings", "objective_evidence_fingerprint", "project_snapshot_fingerprint", "evidence_references", "timestamp")})
        record["record_hash"] = _record_hash(record)
        history.append(record)
        ledger["latest"][criterion_id] = record["event_id"]
        ledger["updated_at"] = utc_now()
        atomic_write_json(ledger_path(root), ledger)
        project["latest_evidence_snapshot"] = record["project_snapshot_fingerprint"]
        persist_project_state(project, root)
        return record


def _record_artifacts_valid(root: str, record: dict[str, Any]) -> bool:
    if record.get("record_hash") != _record_hash(record):
        return False
    for artifact in record.get("artifact_references", []):
        path = os.path.join(root, artifact.get("path", ""))
        if not os.path.isfile(path) or artifact.get("sha256") != _hash_file(path):
            return False
    return True


def latest_valid_evidence(root: str, criterion: dict[str, Any], ledger: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    ledger = ledger or load_evidence_ledger(root)[0]
    if not ledger:
        return None, "missing_ledger"
    criterion_id = str(criterion.get("id") or "")
    snapshot = project_snapshot_fingerprint(root)
    semantic = criterion_semantic_fingerprint(criterion)
    for record in reversed(ledger.get("history", {}).get(criterion_id, [])):
        if record.get("project_path") != os.path.abspath(root) or record.get("criterion_semantic_fingerprint") != semantic:
            continue
        if record.get("project_snapshot_fingerprint") != snapshot:
            continue
        if not _record_artifacts_valid(root, record):
            continue
        return record, ""
    return None, "stale_or_missing"


def evidence_ledger_fingerprint(root: str) -> str:
    ledger, error = load_evidence_ledger(root)
    return canonical_fingerprint(ledger) if ledger and not error else ""


def report_staleness(root: str) -> dict[str, Any]:
    report, error = read_json(os.path.join(root, "DELIVERY_REPORT.json"))
    current_snapshot = project_snapshot_fingerprint(root)
    current_ledger = evidence_ledger_fingerprint(root)
    if not report:
        return {"stale": True, "reason": error, "current_snapshot": current_snapshot, "current_ledger_fingerprint": current_ledger}
    reasons = []
    if report.get("project_snapshot_fingerprint") != current_snapshot:
        reasons.append("project_snapshot_mismatch" if report.get("project_snapshot_fingerprint") else "missing_project_snapshot_provenance")
    if report.get("evidence_ledger_fingerprint") != current_ledger:
        reasons.append("evidence_ledger_mismatch" if report.get("evidence_ledger_fingerprint") else "missing_evidence_ledger_provenance")
    return {"stale": bool(reasons), "reason": "; ".join(reasons), "current_snapshot": current_snapshot, "current_ledger_fingerprint": current_ledger, "report": report}


def persist_audit_run(project: dict[str, Any], root: str, report: dict[str, Any]) -> str:
    run_id = f"audit-{uuid.uuid4().hex}"
    report["report_version"] = 2
    report["generated_at"] = utc_now()
    report["project_snapshot_fingerprint"] = project_snapshot_fingerprint(root)
    report["evidence_ledger_fingerprint"] = evidence_ledger_fingerprint(root)
    run = {"run_id": run_id, "project_id": project.get("project_id") or project.get("id"), "report": _redact(report)}
    path = os.path.join(studio_dir(root), AUDIT_RUNS_DIR_NAME, f"{run_id}.json")
    atomic_write_json(path, run)
    project["latest_audit_run_path"] = path
    persist_project_state(project, root)
    return path


def replay_final_audit(root: str) -> dict[str, Any]:
    state, state_error = load_project_state(root)
    if not state:
        return {"status": "not_reproducible", "reason": state_error, "criteria": [], "project_path": os.path.abspath(root)}
    ledger, ledger_error = load_evidence_ledger(root)
    if ledger is None:
        return {"status": "not_reproducible", "reason": ledger_error, "criteria": [], "project_path": os.path.abspath(root)}
    criteria = []
    valid = stale = missing = 0
    for criterion in state.get("acceptance_criteria", []):
        record, reason = latest_valid_evidence(root, criterion, ledger)
        if record:
            status = str(record.get("status") or "not_verified")
            valid += 1
        else:
            status = "not_verified"
            if ledger.get("history", {}).get(str(criterion.get("id") or "")):
                stale += 1
            else:
                missing += 1
        criteria.append({"id": criterion.get("id"), "status": status, "reason": reason if not record else ""})
    mandatory = [item for item in criteria if next((c for c in state.get("acceptance_criteria", []) if c.get("id") == item["id"]), {}).get("priority") in ("high", "critical")]
    issues = [issue for issue in state.get("issues", []) if isinstance(issue, dict) and issue.get("status", "open") == "open" and issue.get("severity") in ("critical", "high")]
    passed = bool(mandatory) and all(item["status"] == "passed" for item in mandatory) and not issues and bool(state.get("gates", {}).get("_qa_passed"))
    return {
        "status": "passed" if passed else "failed",
        "replay": True,
        "project_path": os.path.abspath(root),
        "project_snapshot_fingerprint": project_snapshot_fingerprint(root),
        "evidence_ledger_fingerprint": canonical_fingerprint(ledger),
        "criteria": criteria,
        "valid_evidence_count": valid,
        "stale_evidence_count": stale,
        "missing_evidence_count": missing,
        "open_blocking_issue_ids": [issue.get("id") for issue in issues],
        "report_staleness": report_staleness(root),
    }


def recover_legacy_project(root: str, persist: bool = False) -> dict[str, Any]:
    root = os.path.abspath(root)
    report, report_error = read_json(os.path.join(root, "DELIVERY_REPORT.json"))
    has_sources = os.path.isfile(os.path.join(root, "README.md")) or os.path.isdir(os.path.join(root, "tests"))
    if not report or not isinstance(report.get("acceptance_criteria_summary"), list) or not has_sources:
        return {"recovery_status": "insufficient_metadata", "project_path": root, "metadata_sources": [], "sources_missing": ["durable_project_state", "valid_delivery_report_or_project_sources"]}
    criteria = [
        {"id": item.get("id"), "title": item.get("title"), "priority": item.get("priority", "high"), "status": "not_verified", "verification_method": "feature_trace_static_or_smoke"}
        for item in report["acceptance_criteria_summary"] if isinstance(item, dict) and item.get("id") and item.get("title")
    ]
    project = {
        "project_id": f"legacy-{canonical_fingerprint(root)[:16]}",
        "title": report.get("project_name") or Path(root).name,
        "description": "",
        "target_path": root,
        "status": "recovery_required",
        "_phase": "",
        "project_spec": {"project_type": report.get("project_type", "generic"), "project_profiles": report.get("detected_profiles", [])},
        "project_profiles": report.get("detected_profiles", []),
        "acceptance_criteria": criteria,
        "acceptance_criteria_source": "legacy_delivery_report_definitions",
        "issues": [],
        "recovery_status": "legacy_project",
    }
    staleness = report_staleness(root)
    recovery = {
        "recovery_status": "legacy_project",
        "project_discovered": True,
        "project_id": project["project_id"],
        "project_path": root,
        "metadata_sources": ["DELIVERY_REPORT.json", "README.md" if os.path.isfile(os.path.join(root, "README.md")) else "", "tests/" if os.path.isdir(os.path.join(root, "tests")) else ""],
        "sources_missing": ["durable_project_state", "acceptance_evidence_ledger", "structured_browser_evidence"],
        "acceptance_criteria_recovered": len(criteria),
        "evidence_records_recovered": 0,
        "artifact_hashes_verified": 0,
        "stale_evidence_count": 0,
        "valid_evidence_count": 0,
        "unverifiable_evidence_count": len(criteria),
        "current_snapshot": project_snapshot_fingerprint(root),
        "persisted_report_stale": staleness["stale"],
        "audit_reproducibility_status": "not_reproducible_without_direct_evidence",
    }
    recovery["metadata_sources"] = [item for item in recovery["metadata_sources"] if item]
    if persist:
        persist_project_state(project, root)
        ledger, _ = load_evidence_ledger(root)
        atomic_write_json(ledger_path(root), ledger or _empty_ledger(root))
        atomic_write_json(recovery_path(root), recovery)
    return {"project": project, "recovery": recovery}


def discover_projects(generated_root: str) -> list[dict[str, Any]]:
    if not os.path.isdir(generated_root):
        return []
    discovered = []
    for entry in sorted(Path(generated_root).iterdir()):
        if not entry.is_dir():
            continue
        state, error = load_project_state(str(entry))
        if state:
            discovered.append({"classification": "fully_recovered", "project": state})
        elif error == "missing":
            legacy = recover_legacy_project(str(entry), persist=False)
            discovered.append({"classification": legacy.get("recovery_status", "insufficient_metadata"), "recovery": legacy})
        else:
            discovered.append({"classification": "corrupted_metadata", "project_path": str(entry), "reason": error})
    return discovered
