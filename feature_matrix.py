"""Durable feature completeness matrix for generated projects."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from quality_profiles import ensure_quality_settings


MATRIX_SCHEMA_VERSION = 1
FEATURE_MATRIX_FILE_NAME = "feature_matrix.json"
DIMENSIONS = (
    "specification",
    "backend",
    "customer_ui",
    "manager_ui",
    "admin_ui",
    "persistence",
    "authorization",
    "automated_tests",
    "direct_runtime",
    "interaction",
    "e2e",
    "native_runtime",
    "packaged_artifact",
    "documentation",
)
DIMENSION_STATUSES = {"not_required", "missing", "implemented_unverified", "passed", "failed", "blocked", "not_verified"}
BLOCKING_STATUSES = {"missing", "failed", "blocked", "not_verified", "implemented_unverified"}
MATRIX_KINDS = ("critical_scenarios", "rbac", "persistence", "concurrency", "security")
SCENARIO_TYPES = ("happy_path", "validation_failure", "authorization_failure", "persistence_behavior", "conflicting_concurrent_behavior", "recovery_restart_behavior")
SECURITY_CHECKS = (
    "password_hashing",
    "authentication_failure_behavior",
    "session_token_safety",
    "rate_limiting",
    "rbac_idor",
    "input_validation",
    "secret_redaction",
    "cors_policy",
    "upload_validation",
    "demo_credential_isolation",
    "dependency_vulnerability_visibility",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_fingerprint(text: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    normalized = re.sub(r"\b(ac|req)-\d+\b", "", normalized)
    return canonical_fingerprint(normalized)


def feature_matrix_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), ".freelancerstudio", FEATURE_MATRIX_FILE_NAME)


def _atomic_write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_feature_matrix(root: str) -> tuple[dict[str, Any] | None, str]:
    try:
        with open(feature_matrix_path(root), "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or value.get("schema_version") != MATRIX_SCHEMA_VERSION:
            return None, "corrupted_feature_matrix"
        return value, ""
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"invalid_json: {exc}"


def persist_feature_matrix(root: str, matrix: dict[str, Any]) -> dict[str, Any]:
    matrix["schema_version"] = MATRIX_SCHEMA_VERSION
    matrix["updated_at"] = utc_now()
    _atomic_write_json(feature_matrix_path(root), matrix)
    return matrix


def _text_blob(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, dict):
            parts.extend(str(value.get(key, "")) for key in ("title", "description", "expected_result", "trace", "verification_method", "source_trace"))
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value or ""))
    return "\n".join(part for part in parts if part).lower()


def _actors(text: str) -> list[str]:
    actors = []
    for token, actor in (("customer", "customer"), ("client", "customer"), ("manager", "manager"), ("admin", "administrator"), ("staff", "staff"), ("user", "user")):
        if token in text and actor not in actors:
            actors.append(actor)
    return actors or ["user"]


def _required_dimensions(text: str, settings: dict[str, Any], mandatory: bool) -> dict[str, str]:
    required = {dimension: "not_required" for dimension in DIMENSIONS}
    required["specification"] = "missing"
    required["documentation"] = "missing"
    if mandatory:
        required["automated_tests"] = "missing" if settings.get("quality_profile") != "prototype" else "not_required"
        required["direct_runtime"] = "missing"
    if any(token in text for token in ("api", "backend", "endpoint", "create", "update", "delete", "store", "persist", "database", "request", "ticket", "favorite", "review")):
        required["backend"] = "missing"
    if any(token in text for token in ("customer", "client", "user", "ui", "screen", "button", "browser", "web")):
        required["customer_ui"] = "missing"
        required["interaction"] = "missing" if mandatory else "not_required"
    if "manager" in text:
        required["manager_ui"] = "missing"
    if "admin" in text:
        required["admin_ui"] = "missing"
    if any(token in text for token in ("persist", "restart", "database", "store", "save", "favorite", "review")):
        required["persistence"] = "missing"
    if any(token in text for token in ("auth", "login", "role", "rbac", "private", "another customer", "admin", "manager")):
        required["authorization"] = "missing"
    if settings.get("require_real_e2e") and mandatory:
        required["e2e"] = "missing"
    if settings.get("require_native_runtime"):
        required["native_runtime"] = "missing"
    if settings.get("require_packaged_artifact"):
        required["packaged_artifact"] = "missing"
    return required


def _feature_id(fingerprint: str) -> str:
    return f"FEATURE-{fingerprint[:12].upper()}"


def _feature_from_requirement(requirement: dict[str, Any], project: dict[str, Any], criteria: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    description = str(requirement.get("description") or requirement.get("title") or "Requested feature")
    name = str(requirement.get("title") or description).strip()[:96] or "Requested feature"
    fingerprint = semantic_fingerprint({"description": description, "source": requirement.get("source", "user_requirement")})
    linked = []
    req_id = str(requirement.get("id") or "")
    for criterion in criteria:
        ids = criterion.get("requirement_ids", [])
        if isinstance(ids, str):
            ids = [ids]
        if req_id and req_id in [str(item) for item in ids]:
            linked.append({"criterion_id": criterion.get("id"), "criterion_semantic_fingerprint": semantic_fingerprint(_text_blob(criterion)), "verifier_plan": criterion.get("verifier_plan", {})})
    text = _text_blob(description, name, linked)
    mandatory = bool(requirement.get("mandatory", True)) and requirement.get("priority", "high") in ("critical", "high")
    dimensions = _required_dimensions(text, settings, mandatory)
    dimensions["specification"] = "passed"
    return {
        "feature_id": _feature_id(fingerprint),
        "feature_name": name,
        "semantic_fingerprint": fingerprint,
        "description": description,
        "actors": _actors(text),
        "mandatory": mandatory,
        "quality_profile": settings.get("quality_profile", "strict_mvp"),
        "required_targets": settings.get("required_targets", []),
        "backend_required": dimensions["backend"] != "not_required",
        "customer_ui_required": dimensions["customer_ui"] != "not_required",
        "management_ui_required": dimensions["manager_ui"] != "not_required",
        "admin_ui_required": dimensions["admin_ui"] != "not_required",
        "persistence_required": dimensions["persistence"] != "not_required",
        "authorization_required": dimensions["authorization"] != "not_required",
        "tests_required": dimensions["automated_tests"] != "not_required",
        "e2e_required": dimensions["e2e"] != "not_required",
        "native_runtime_required": dimensions["native_runtime"] != "not_required",
        "implementation_status": "specified",
        "evidence_status": "not_verified",
        "blocking_reasons": [],
        "dimensions": dimensions,
        "criteria": linked,
        "evidence": [],
        "claims": [],
        "contract": {
            "actor": _actors(text)[0],
            "precondition": "Required project setup and actor permissions exist.",
            "action": description,
            "expected_outcome": "Observable behavior matches the requested feature.",
            "persistence_requirement": dimensions["persistence"] != "not_required",
            "authorization_requirement": dimensions["authorization"] != "not_required",
            "negative_error_behavior": "Invalid or unauthorized use is rejected without corrupting state.",
            "target_interface_platform": settings.get("required_targets", []),
        },
    }


def _feature_lookup(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup = {}
    for feature in matrix.get("features", []):
        if isinstance(feature, dict):
            lookup[str(feature.get("feature_id") or "")] = feature
    return lookup


def _conflict_sensitive(text: str) -> bool:
    return bool(re.search(r"\b(booking|slot|inventory|stock|reservation|unique|capacity|limited|seat|appointment)\b|status\s+transition", text))


def _matrix_item(item_id: str, feature: dict[str, Any], kind: str, expected: str, required: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "feature_id": feature.get("feature_id", ""),
        "feature_name": feature.get("feature_name", ""),
        "kind": kind,
        "required": required,
        "expected_coverage": expected,
        "achieved_coverage": "missing" if required else "not_required",
        "status": "missing" if required else "not_required",
        "evidence_references": [],
        **extra,
    }


def derive_quality_matrices(project: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    text = _text_blob(project.get("title", ""), project.get("description", ""), project.get("original_request", ""), project.get("project_spec", {}))
    matrices = {kind: [] for kind in MATRIX_KINDS}
    for feature in matrix.get("features", []):
        if not isinstance(feature, dict) or not feature.get("mandatory"):
            continue
        feature_text = _text_blob(feature.get("feature_name"), feature.get("description"), feature.get("contract", {}))
        dims = feature.get("dimensions", {}) if isinstance(feature.get("dimensions"), dict) else {}
        auth_required = dims.get("authorization") != "not_required" or feature.get("authorization_required")
        persistence_required = dims.get("persistence") != "not_required" or feature.get("persistence_required")
        conflict_required = _conflict_sensitive(feature_text)
        for scenario in SCENARIO_TYPES:
            required = True
            if scenario == "authorization_failure" and not auth_required:
                required = False
            if scenario in {"persistence_behavior", "recovery_restart_behavior"} and not persistence_required:
                required = False
            if scenario == "conflicting_concurrent_behavior" and not conflict_required:
                required = False
            matrices["critical_scenarios"].append(_matrix_item(f"SCENARIO-{feature.get('feature_id')}-{scenario}", feature, scenario, scenario.replace("_", " "), required))
        if auth_required:
            for kind, expected in (
                ("allowed_actor", "allowed actor succeeds"),
                ("denied_actor", "denied actor receives the correct denial"),
                ("cross_user_isolation", "one user cannot access another user's private resources"),
            ):
                matrices["rbac"].append(_matrix_item(f"RBAC-{feature.get('feature_id')}-{kind}", feature, kind, expected, True, allowed_roles=feature.get("actors", []), denied_roles=[]))
        if persistence_required:
            matrices["persistence"].append(_matrix_item(f"PERSIST-{feature.get('feature_id')}-restart", feature, "restart_roundtrip", "create/update state, stop runtime or database where applicable, restart, retrieve exact state", True, requires_real_restart=True))
        if conflict_required:
            matrices["concurrency"].append(_matrix_item(f"CONCURRENCY-{feature.get('feature_id')}-conflict", feature, "conflict_race", "simultaneous or near-simultaneous conflicting attempts are handled safely", True))

    has_auth = any(matrices["rbac"]) or any(token in text for token in ("auth", "login", "password", "role", "rbac", "manager", "admin"))
    has_backend = any(token in text for token in ("api", "backend", "server", "database", "upload", "login"))
    has_upload = "upload" in text or "file" in text
    has_password = "password" in text or "login" in text or "auth" in text
    for check in SECURITY_CHECKS:
        required = has_backend
        if check in {"password_hashing", "authentication_failure_behavior", "session_token_safety", "demo_credential_isolation"}:
            required = has_auth or has_password
        elif check in {"rbac_idor"}:
            required = has_auth or any(matrices["rbac"])
        elif check == "upload_validation":
            required = has_upload
        elif check in {"rate_limiting", "dependency_vulnerability_visibility"}:
            required = has_backend and project.get("quality_profile") in {"production_candidate", "production"}
        matrices["security"].append(_matrix_item(f"SECURITY-{check}", {"feature_id": "GLOBAL", "feature_name": "Security baseline"}, check, check.replace("_", " "), required))
    return matrices


def _merge_matrix_statuses(current: dict[str, list[dict[str, Any]]], previous: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    previous = previous or {}
    for kind, rows in current.items():
        old_rows = {str(item.get("id")): item for item in previous.get(kind, []) if isinstance(item, dict)}
        for row in rows:
            old = old_rows.get(str(row.get("id")))
            if not old:
                continue
            for key in ("achieved_coverage", "status", "evidence_references", "tests_executed", "tests_skipped", "tooling_unavailable", "failures", "findings", "remediation_guidance"):
                if key in old:
                    row[key] = old[key]
    return current


def recalculate_quality_matrices(matrix: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    matrices = _merge_matrix_statuses(matrix.get("quality_matrices", {}) if isinstance(matrix.get("quality_matrices"), dict) else {}, previous)
    blocking: list[str] = []
    covered = uncovered = 0
    for kind, rows in matrices.items():
        for row in rows:
            if not row.get("required"):
                row["status"] = "not_required"
                continue
            status = str(row.get("status") or row.get("achieved_coverage") or "missing").lower()
            if status in {"covered", "passed", "pass"}:
                row["status"] = "passed"
                row["achieved_coverage"] = row.get("achieved_coverage") or "covered"
                covered += 1
            else:
                row["status"] = "failed" if status in {"failed", "fail"} else "missing" if status in {"", "missing"} else "not_verified"
                uncovered += 1
                blocking.append(f"{kind}:{row.get('id')}:{row['status']}")
    matrix["quality_matrices"] = matrices
    matrix["quality_matrix_summary"] = {"covered": covered, "uncovered": uncovered, "blocking_gaps": blocking}
    return matrix


def build_feature_matrix(project: dict[str, Any]) -> dict[str, Any]:
    settings = ensure_quality_settings(project)
    spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
    criteria = project.get("acceptance_criteria", []) if isinstance(project.get("acceptance_criteria"), list) else []
    requirements = spec.get("requirements", []) if isinstance(spec.get("requirements"), list) else []
    if not requirements:
        requirements = [
            {"id": f"REQ-{index:03d}", "title": str(feature)[:72], "description": str(feature), "mandatory": True, "priority": "high", "source": "required_features"}
            for index, feature in enumerate(spec.get("required_features", []) or [], start=1)
        ]
    features = [_feature_from_requirement(req, project, criteria, settings) for req in requirements if isinstance(req, dict)]
    if not features and criteria:
        for criterion in criteria:
            req = {"id": f"CRIT-{criterion.get('id', len(features) + 1)}", "title": criterion.get("title"), "description": _text_blob(criterion), "mandatory": criterion.get("priority") in ("critical", "high"), "priority": criterion.get("priority", "high"), "source": "acceptance_criterion"}
            features.append(_feature_from_requirement(req, project, criteria, settings))
    matrix = {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "project_id": project.get("project_id") or project.get("id", ""),
        "quality_profile": settings.get("quality_profile", "strict_mvp"),
        "agent_responsibilities": {
            "alex": "defines MVP feature scope",
            "maya": "creates feature contracts",
            "codex": "reports implementation mapping as claims only",
            "bugcatcher": "maps automated test evidence",
            "sentinel": "maps security and authorization findings/evidence",
            "lupa": "maps architecture-quality findings/evidence",
            "final_audit": "calculates matrix completion from direct current evidence",
        },
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "dimensions": list(DIMENSIONS),
        "features": features,
        "quality_matrices": {},
        "quality_matrix_summary": {},
        "summary": {},
    }
    matrix["quality_matrices"] = derive_quality_matrices(project, matrix)
    return recalculate_matrix(matrix)


def ensure_feature_matrix(project: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    existing = project.get("feature_matrix") if isinstance(project.get("feature_matrix"), dict) else None
    if root:
        loaded, _ = load_feature_matrix(root)
        existing = loaded or existing
    current = build_feature_matrix(project)
    if existing:
        by_fp = {feature.get("semantic_fingerprint"): feature for feature in existing.get("features", []) if isinstance(feature, dict)}
        for feature in current.get("features", []):
            old = by_fp.get(feature.get("semantic_fingerprint"))
            if old:
                for key in ("dimensions", "implementation_status", "evidence_status", "evidence", "claims"):
                    if key in old:
                        feature[key] = old[key]
        current["quality_matrices"] = _merge_matrix_statuses(current.get("quality_matrices", {}), existing.get("quality_matrices", {}) if isinstance(existing.get("quality_matrices"), dict) else {})
    current = recalculate_matrix(current)
    project["feature_matrix"] = current
    if root:
        persist_feature_matrix(root, current)
    return current


def _dimension_for_evidence(criterion: dict[str, Any], evidence: dict[str, Any]) -> str:
    text = _text_blob(criterion, evidence, evidence.get("verifier_type"), evidence.get("method"))
    verifier = str(evidence.get("verifier_type") or evidence.get("method") or criterion.get("verification_method") or "").lower()
    if any(token in verifier for token in ("persistence", "restart")) or "restart" in text:
        return "persistence"
    if any(token in verifier for token in ("browser", "responsive", "ui")):
        return "interaction" if "click" in text or "action" in text else "customer_ui"
    if any(token in verifier for token in ("pytest", "test", "command")) or "automated test" in text:
        return "automated_tests"
    if any(token in verifier for token in ("runtime", "http", "fastapi", "telegram")):
        return "backend"
    if "auth" in text or "role" in text or "rbac" in text:
        return "authorization"
    if "readme" in text or "documentation" in text:
        return "documentation"
    return "direct_runtime"


def _linked_feature_ids(matrix: dict[str, Any], criterion: dict[str, Any]) -> list[str]:
    semantic = semantic_fingerprint(_text_blob(criterion))
    req_ids = set(str(item) for item in criterion.get("requirement_ids", []) if item) if isinstance(criterion.get("requirement_ids", []), list) else {str(criterion.get("requirement_ids"))}
    linked = []
    for feature in matrix.get("features", []):
        criteria = feature.get("criteria", []) if isinstance(feature.get("criteria"), list) else []
        if any(item.get("criterion_semantic_fingerprint") == semantic for item in criteria if isinstance(item, dict)):
            linked.append(feature["feature_id"])
            continue
        if req_ids and any(str(item.get("criterion_id", "")) in req_ids for item in criteria if isinstance(item, dict)):
            linked.append(feature["feature_id"])
    return linked


def apply_implementation_claim(project: dict[str, Any], feature_text: str, claim: str, agent: str = "codex", root: str | None = None) -> dict[str, Any]:
    matrix = ensure_feature_matrix(project, root)
    fp = semantic_fingerprint(feature_text)
    for feature in matrix.get("features", []):
        if feature.get("semantic_fingerprint") == fp or str(feature_text).lower() in _text_blob(feature.get("feature_name"), feature.get("description")):
            feature["implementation_status"] = "claimed"
            feature.setdefault("claims", []).append({"agent": agent, "claim": claim, "created_at": utc_now()})
            for dimension, status in list(feature.get("dimensions", {}).items()):
                if status == "missing":
                    feature["dimensions"][dimension] = "implemented_unverified"
            break
    recalculate_matrix(matrix)
    project["feature_matrix"] = matrix
    if root:
        persist_feature_matrix(root, matrix)
    return matrix


def update_matrix_from_evidence(project: dict[str, Any], criterion: dict[str, Any], evidence: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    matrix = ensure_feature_matrix(project, root)
    dimension = evidence.get("feature_dimension") or _dimension_for_evidence(criterion, evidence)
    if dimension not in DIMENSIONS:
        dimension = "direct_runtime"
    evidence_status = str(evidence.get("status") or evidence.get("verdict") or "not_verified").lower()
    status = "passed" if evidence_status in ("passed", "pass") else "failed" if evidence_status in ("failed", "fail") else "blocked" if "block" in evidence_status else "not_verified"
    for feature_id in _linked_feature_ids(matrix, criterion):
        feature = next((item for item in matrix.get("features", []) if item.get("feature_id") == feature_id), None)
        if not feature:
            continue
        if feature.get("dimensions", {}).get(dimension) != "not_required":
            feature.setdefault("dimensions", {})[dimension] = status
        feature.setdefault("evidence", []).append({
            "criterion_id": criterion.get("id"),
            "criterion_semantic_fingerprint": semantic_fingerprint(_text_blob(criterion)),
            "dimension": dimension,
            "status": status,
            "verifier_type": evidence.get("verifier_type", evidence.get("method", "")),
            "created_at": utc_now(),
        })
    recalculate_matrix(matrix)
    project["feature_matrix"] = matrix
    if root:
        persist_feature_matrix(root, matrix)
    return matrix


def recalculate_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    complete = incomplete = failed = not_verified = mandatory_total = 0
    for feature in matrix.get("features", []):
        dimensions = feature.get("dimensions", {}) if isinstance(feature.get("dimensions"), dict) else {}
        required = {name: status for name, status in dimensions.items() if status != "not_required"}
        blockers = [f"{name}:{status}" for name, status in required.items() if status in BLOCKING_STATUSES]
        feature["blocking_reasons"] = blockers
        if feature.get("mandatory"):
            mandatory_total += 1
        if any(str(status) == "failed" for status in required.values()):
            feature["evidence_status"] = "failed"
            failed += 1
        elif not blockers and required:
            feature["evidence_status"] = "passed"
            complete += 1
        elif any(str(status) == "not_verified" for status in required.values()):
            feature["evidence_status"] = "not_verified"
            not_verified += 1
        else:
            feature["evidence_status"] = "incomplete"
            incomplete += 1
    matrix["summary"] = {
        "total_features": len(matrix.get("features", [])),
        "total_mandatory": mandatory_total,
        "fully_complete": complete,
        "incomplete": incomplete,
        "failed": failed,
        "not_verified": not_verified,
    }
    recalculate_quality_matrices(matrix, matrix.get("quality_matrices", {}))
    matrix["updated_at"] = utc_now()
    return matrix


def matrix_blocks_completion(matrix: dict[str, Any], quality_profile: str) -> tuple[bool, list[str]]:
    blockers = []
    features = [feature for feature in matrix.get("features", []) if isinstance(feature, dict)]
    if quality_profile != "prototype" and not features:
        blockers.append("feature_matrix:missing_feature_dimensions")
    for feature in matrix.get("features", []):
        if not isinstance(feature.get("dimensions"), dict) or not feature.get("dimensions"):
            if quality_profile != "prototype" and feature.get("mandatory"):
                blockers.append(f"{feature.get('feature_id')}: {feature.get('feature_name')} (missing dimensions)")
            continue
        if quality_profile == "prototype":
            hard_blockers = [reason for reason in feature.get("blocking_reasons", []) if reason.endswith(":failed") or reason.endswith(":blocked")]
            if feature.get("mandatory") and hard_blockers:
                blockers.append(f"{feature.get('feature_id')}: {feature.get('feature_name')} ({', '.join(hard_blockers)})")
            continue
        if not feature.get("mandatory"):
            continue
        if feature.get("evidence_status") != "passed":
            blockers.append(f"{feature.get('feature_id')}: {feature.get('feature_name')} ({feature.get('evidence_status')})")
    if quality_profile != "prototype":
        for blocker in matrix.get("quality_matrix_summary", {}).get("blocking_gaps", []):
            if ":failed" in blocker or ":missing" in blocker or ":not_verified" in blocker:
                blockers.append(f"quality_matrix:{blocker}")
    return bool(blockers), blockers


def feature_matrix_report(matrix: dict[str, Any]) -> dict[str, Any]:
    columns = ["Feature", "Mandatory", "Backend", "Customer UI", "Manager UI", "Authorization", "Persistence", "Tests", "E2E", "Platform", "Final status"]
    rows = []
    for feature in matrix.get("features", []):
        dims = feature.get("dimensions", {})
        rows.append({
            "Feature": feature.get("feature_name"),
            "Mandatory": bool(feature.get("mandatory")),
            "Backend": dims.get("backend", "not_required"),
            "Customer UI": dims.get("customer_ui", "not_required"),
            "Manager UI": dims.get("manager_ui", "not_required"),
            "Authorization": dims.get("authorization", "not_required"),
            "Persistence": dims.get("persistence", "not_required"),
            "Tests": dims.get("automated_tests", "not_required"),
            "E2E": dims.get("e2e", "not_required"),
            "Platform": dims.get("native_runtime", "not_required"),
            "Final status": feature.get("evidence_status"),
            "feature_id": feature.get("feature_id"),
            "details": feature,
        })
    return {"columns": columns, "rows": rows, "summary": matrix.get("summary", {}), "quality_matrices": matrix.get("quality_matrices", {}), "quality_matrix_summary": matrix.get("quality_matrix_summary", {})}


def _coverage_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _match_feature(row: dict[str, Any], item: dict[str, Any]) -> bool:
    item_feature = str(item.get("feature_id") or item.get("feature") or item.get("feature_name") or "").lower()
    if not item_feature:
        return True
    return item_feature in str(row.get("feature_id", "")).lower() or item_feature in str(row.get("feature_name", "")).lower()


def _mark_rows(rows: list[dict[str, Any]], items: list[dict[str, Any]], kind_aliases: tuple[str, ...], *, require_restart: bool = False) -> None:
    for item in items:
        item_kind = str(item.get("kind") or item.get("scenario") or item.get("case") or item.get("check") or "").lower().replace(" ", "_")
        item_status = str(item.get("status") or item.get("result") or item.get("coverage") or item.get("verdict") or "").lower()
        if require_restart and not (item.get("real_restart") or item.get("restart_performed") or item.get("process_restarted") or item.get("verifier_type") == "persistence_restart"):
            item_status = "not_verified"
        for row in rows:
            if not row.get("required") or not _match_feature(row, item):
                continue
            row_kind = str(row.get("kind") or "").lower()
            if kind_aliases and item_kind and item_kind != row_kind:
                continue
            if kind_aliases and not item_kind and row_kind not in kind_aliases:
                continue
            row["status"] = "passed" if item_status in {"passed", "pass", "covered", "success"} else "failed" if item_status in {"failed", "fail"} else "not_verified"
            row["achieved_coverage"] = item.get("achieved_coverage") or item.get("test") or item.get("evidence") or row["status"]
            row.setdefault("evidence_references", []).extend(item.get("evidence_references", []) if isinstance(item.get("evidence_references"), list) else [])


def apply_bugcatcher_artifact(project: dict[str, Any], output: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    matrix = ensure_feature_matrix(project, root)
    qm = matrix.setdefault("quality_matrices", derive_quality_matrices(project, matrix))
    _mark_rows(qm.get("critical_scenarios", []), _coverage_items(output.get("critical_scenario_matrix")), tuple(SCENARIO_TYPES))
    _mark_rows(qm.get("rbac", []), _coverage_items(output.get("rbac_test_matrix")), ("allowed_actor", "denied_actor", "cross_user_isolation"))
    _mark_rows(qm.get("persistence", []), _coverage_items(output.get("persistence_restart_tests")), ("restart_roundtrip",), require_restart=True)
    _mark_rows(qm.get("concurrency", []), _coverage_items(output.get("concurrency_race_tests")), ("conflict_race",))
    matrix["bugcatcher_report"] = {
        "covered_mandatory_scenarios": output.get("covered_mandatory_scenarios", []),
        "uncovered_mandatory_scenarios": output.get("uncovered_mandatory_scenarios", output.get("untested_gap_report", [])),
        "tests_executed": output.get("tests_executed", output.get("positive_negative_tests", [])),
        "tests_skipped": output.get("tests_skipped", []),
        "tooling_unavailable": output.get("tooling_unavailable", []),
        "failures": output.get("failures", []),
        "evidence_references": output.get("evidence_references", []),
    }
    recalculate_matrix(matrix)
    project["feature_matrix"] = matrix
    if root:
        persist_feature_matrix(root, matrix)
    return matrix


def apply_sentinel_artifact(project: dict[str, Any], output: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    matrix = ensure_feature_matrix(project, root)
    qm = matrix.setdefault("quality_matrices", derive_quality_matrices(project, matrix))
    performed = {str(item).lower().replace(" ", "_") for item in output.get("checks_performed", []) if item}
    not_performed = {str(item).lower().replace(" ", "_") for item in output.get("checks_not_performed", []) if item}
    findings = output.get("findings", []) if isinstance(output.get("findings"), list) else []
    critical = [finding for finding in findings if isinstance(finding, dict) and str(finding.get("severity", "")).lower() in {"critical", "blocker"}]
    for row in qm.get("security", []):
        if not row.get("required"):
            continue
        kind = str(row.get("kind") or "").lower()
        if kind in performed:
            row["status"] = "passed"
            row["achieved_coverage"] = "check performed"
        if kind in not_performed:
            row["status"] = "not_verified"
            row["achieved_coverage"] = "check not performed"
        if any(kind in _text_blob(finding.get("title", ""), finding.get("check", ""), finding.get("category", "")) for finding in critical):
            row["status"] = "failed"
            row.setdefault("findings", []).extend(critical)
    matrix["sentinel_report"] = {
        "checks_performed": output.get("checks_performed", []),
        "checks_not_performed": output.get("checks_not_performed", []),
        "findings": findings,
        "blocking_policy": output.get("blocking_policy", "critical findings block strict completion"),
        "remediation_guidance": output.get("remediation_guidance", []),
    }
    recalculate_matrix(matrix)
    project["feature_matrix"] = matrix
    if root:
        persist_feature_matrix(root, matrix)
    return matrix
