"""Read-only, evidence-bound Product Judge for subjective UI criteria."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_utils import ask_studio_ai_with_history, provider_capabilities
from opencode_provider import OpenCodeBridgeConnection, bridge_effective_capabilities
from project_state import project_snapshot_fingerprint


PRODUCT_JUDGE_VERDICTS = {
    "approved",
    "approved_with_nonblocking_notes",
    "blocking_objection",
    "insufficient_evidence",
}
FINDING_SEVERITIES = {"info", "minor", "major", "critical"}
SECRET_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s'\"]+|\bsk-[A-Za-z0-9_-]{16,}\b|\b\d{7,}:[A-Za-z0-9_-]{20,}\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def evidence_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def independence_level(
    implementation_provider: str | None,
    implementation_model: str | None,
    judge_provider: str | None,
    judge_model: str | None,
) -> str:
    if not judge_provider or not judge_model:
        return "unavailable"
    if implementation_provider and judge_provider != implementation_provider:
        return "different_provider"
    if implementation_model and judge_model != implementation_model:
        return "same_provider_different_model"
    return "same_model_separate_role"


def is_subjective_product_quality(criterion: dict[str, Any]) -> bool:
    """Classify the criterion from its meaning, never its identifier."""
    text = " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result", "trace")).lower()
    signals = (
        "modern", "tidy", "visual quality", "professional appearance", "product polish",
        "visual design", "look intentional", "unpolished", "readable layout",
    )
    return any(signal in text for signal in signals)


def _viewport_from_path(path: str) -> tuple[str, int | None, int | None]:
    name = Path(path).stem.lower()
    match = re.search(r"(\d+)x(\d+)$", name)
    width = int(match.group(1)) if match else None
    height = int(match.group(2)) if match else None
    categories = (
        "laptop", "full_hd_desktop", "high_resolution_desktop", "ultra_wide_desktop",
        "tablet_portrait", "tablet_landscape",
    )
    return next((item for item in categories if item in name), "unknown"), width, height


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>" if match.group(1) else "<redacted>", value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): "<redacted>" if re.search(r"(?i)(api[_-]?key|token|secret|password)", str(key)) else _redact(item) for key, item in value.items()}
    return value


def build_judge_input(
    criterion: dict[str, Any],
    objective: dict[str, Any],
    screenshots: list[str],
    project: dict[str, Any],
    runtime_profile: dict[str, Any],
    snapshot_fingerprint: str,
) -> tuple[dict[str, Any] | None, str]:
    if not objective.get("passed"):
        return None, "Objective browser evidence did not pass"
    if not screenshots:
        return None, "No screenshot artifacts were supplied"

    artifacts = []
    for path in screenshots:
        if not path or not os.path.isfile(path):
            return None, f"Screenshot artifact is missing: {path}"
        category, width, height = _viewport_from_path(path)
        if not width or not height:
            return None, f"Screenshot viewport metadata is missing: {path}"
        artifacts.append({
            "category": category,
            "width": width,
            "height": height,
            "artifact_path": path,
            "sha256": sha256_file(path),
            "objective_verdict": "passed",
        })
    if len(artifacts) < 4:
        return None, "At least four representative screenshots are required"

    viewport_results = objective.get("viewport_results") if isinstance(objective.get("viewport_results"), list) else []
    bundle = {
        "project_name": str(project.get("title") or project.get("project_name") or ""),
        "project_purpose": " ".join(str(project.get(key, "")) for key in ("title", "description")).strip(),
        "product_kind": runtime_profile.get("product_kind", "unknown"),
        "ui_runtime": runtime_profile.get("ui_runtime", "unknown"),
        "criterion_id": str(criterion.get("id") or ""),
        "criterion_text": " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result")).strip(),
        "required_user_tasks": project.get("required_user_tasks", []),
        "representative_viewports": [{key: item[key] for key in ("category", "width", "height")} for item in artifacts],
        "screenshot_artifacts": artifacts,
        "objective_browser_evidence": {"passed": True, "viewport_results": viewport_results},
        "browser_errors": [
            {"category": item.get("category"), "page_errors": item.get("page_errors", []), "console_errors": item.get("console_errors", []), "network_failures": item.get("network_failures", [])}
            for item in viewport_results
        ],
        "layout_findings": objective.get("layout_findings", []),
        "interaction_results": objective.get("primary_actions", []),
        "known_limitations": project.get("known_limitations", []),
        "project_snapshot_fingerprint": snapshot_fingerprint,
        "objective_evidence_fingerprint": evidence_fingerprint(objective),
    }
    return _redact(bundle), ""


def _prompt(bundle: dict[str, Any]) -> str:
    return (
        "You are the independent, read-only Product Judge for a software delivery audit. "
        "Evaluate only the supplied evidence and screenshots. Do not assume missing facts, praise by default, "
        "rewrite code, invoke tools, edit files, repair the product, call OpenCode, or mark the project complete. "
        "Screenshots alone do not prove acceptance. Separate subjective preferences from blocking usability defects. "
        "Evaluate visual hierarchy, readability, clutter, consistency, task clarity, professional appearance, and cross-viewport quality. "
        "A blocking objection normally requires a major or critical finding grounded in an evidence reference. "
        "Return ONLY a JSON object with this exact shape: "
        '{"verdict":"approved|approved_with_nonblocking_notes|blocking_objection|insufficient_evidence",'
        '"findings":[{"id":"F-001","category":"visual_hierarchy|readability|clutter|consistency|task_clarity|professional_appearance|cross_viewport_quality",'
        '"severity":"info|minor|major|critical","viewport":"category or all","observation":"evidence-grounded text",'
        '"evidence_reference":"screenshot path or objective evidence reference","blocking":false,"recommended_action":"action or empty string"}]}. '
        "Use insufficient_evidence when the supplied evidence cannot support a judgment.\n\n"
        f"PRODUCT JUDGE INPUT:\n{json.dumps(bundle, ensure_ascii=False, indent=2)}"
    )


def _image_message(provider: str, prompt: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if provider == "anthropic":
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for artifact in artifacts:
            media_type = mimetypes.guess_type(artifact["artifact_path"])[0] or "image/png"
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(Path(artifact["artifact_path"]).read_bytes()).decode("ascii")}})
        return [{"role": "user", "content": content}]
    content = [{"type": "text", "text": prompt}]
    for artifact in artifacts:
        media_type = mimetypes.guess_type(artifact["artifact_path"])[0] or "image/png"
        encoded = base64.b64encode(Path(artifact["artifact_path"]).read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}})
    return [{"role": "user", "content": content}]


def validate_judge_response(raw: Any) -> tuple[dict[str, Any] | None, str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "Judge response was not valid JSON"
    if not isinstance(raw, dict):
        return None, "Judge response was not a JSON object"
    verdict = raw.get("verdict")
    findings = raw.get("findings")
    if verdict not in PRODUCT_JUDGE_VERDICTS or not isinstance(findings, list):
        return None, "Judge response did not use the required verdict/findings schema"
    normalized = []
    required = {"id", "category", "severity", "viewport", "observation", "evidence_reference", "blocking", "recommended_action"}
    for finding in findings:
        if not isinstance(finding, dict) or not required <= set(finding):
            return None, "A judge finding is missing required fields"
        if finding["severity"] not in FINDING_SEVERITIES or not isinstance(finding["blocking"], bool):
            return None, "A judge finding has an invalid severity or blocking flag"
        if finding["blocking"] and finding["severity"] not in {"major", "critical"}:
            return None, "Only major or critical findings may block acceptance"
        normalized.append({key: finding[key] for key in required})
    blocking = [finding for finding in normalized if finding["blocking"]]
    if verdict == "blocking_objection" and not blocking:
        return None, "A blocking objection requires a blocking finding"
    if verdict in {"approved", "approved_with_nonblocking_notes"} and blocking:
        return None, "An approval verdict cannot include blocking findings"
    return {"verdict": verdict, "findings": normalized, "blocking_findings": blocking}, ""


def combined_subjective_verdict(objective_passed: bool, judge_verdict: str) -> str:
    if not objective_passed:
        return "failed"
    if judge_verdict in {"approved", "approved_with_nonblocking_notes"}:
        return "passed"
    if judge_verdict == "blocking_objection":
        return "failed"
    return "not_verified"


def judge_result_is_fresh(result: dict[str, Any], criterion_id: str, snapshot_fingerprint: str, objective_fingerprint: str) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("criterion_id") != criterion_id or result.get("project_snapshot_fingerprint") != snapshot_fingerprint:
        return False
    if result.get("objective_evidence_fingerprint") != objective_fingerprint:
        return False
    for artifact in result.get("evidence_references", []):
        path = artifact.get("artifact_path") if isinstance(artifact, dict) else ""
        if not path or not os.path.isfile(path) or artifact.get("sha256") != sha256_file(path):
            return False
    return True


def run_product_judge(
    criterion: dict[str, Any],
    objective: dict[str, Any],
    screenshots: list[str],
    project: dict[str, Any],
    runtime_profile: dict[str, Any],
    snapshot_fingerprint: str,
    config: dict[str, Any] | None,
    implementation_identity: dict[str, str] | None = None,
    invoke: Callable[..., str] = ask_studio_ai_with_history,
) -> dict[str, Any]:
    config = config or {}
    provider = str(config.get("provider") or "").lower()
    model = str(config.get("model") or "")
    implementation_identity = implementation_identity or {}
    level = independence_level(implementation_identity.get("provider"), implementation_identity.get("model"), provider, model)
    bundle, reason = build_judge_input(criterion, objective, screenshots, project, runtime_profile, snapshot_fingerprint)
    result = {
        "review_type": "INDEPENDENT_PRODUCT_JUDGE_REVIEW",
        "criterion_id": str(criterion.get("id") or ""),
        "project_path": project.get("target_path", ""),
        "project_snapshot_fingerprint": snapshot_fingerprint,
        "objective_evidence_fingerprint": evidence_fingerprint(objective),
        "provider": provider,
        "model": model,
        "independence_level": level,
        "timestamp": utc_now(),
        "input_summary": bundle or {},
        "evidence_references": (bundle or {}).get("screenshot_artifacts", []),
        "can_pass_from_judge_alone": False,
    }
    if not config.get("enabled", True):
        return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": "Product Judge is disabled"}
    if not bundle:
        return {**result, "verdict": "insufficient_evidence", "availability": "insufficient_evidence", "findings": [], "blocking_findings": [], "reason": reason}
    if provider == "opencode_bridge":
        connection_data = config.get("connection") if isinstance(config.get("connection"), dict) else {}
        connection = OpenCodeBridgeConnection.from_dict(connection_data)
        if not model or not bridge_effective_capabilities(connection).get("image_input"):
            return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": "This OpenCode connection works for text but cannot currently transport images required by Product Judge"}
        project_path = str(project.get("target_path") or project.get("project_path") or "")
        before = project_snapshot_fingerprint(project_path) if project_path and os.path.isdir(project_path) else snapshot_fingerprint
        response = connection.execute({
            "agent_role": "product_judge", "system_instruction": "",
            "user_content": _prompt(bundle), "image_attachments": [item["artifact_path"] for item in bundle["screenshot_artifacts"]],
            "requested_model": model, "timeout": int(config.get("timeout", 180)), "session_id": "fresh-product-judge-session",
        })
        after = project_snapshot_fingerprint(project_path) if project_path and os.path.isdir(project_path) else snapshot_fingerprint
        if before != after:
            return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": "Product Judge source-integrity check failed"}
        if response.get("status") != "success":
            return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": f"Product Judge request failed: {response.get('error_category', 'unknown')}"}
        raw = response.get("text", "")
    elif not provider_capabilities(provider).get("image_input") or not model:
        return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": "Configured provider/model does not have a supported image-input transport"}
    else:
        try:
            raw = invoke(
                provider=provider,
                model_name=model,
                system_prompt="Independent Product Judge. Read-only. Return only the validated JSON structure.",
                chat_history=_image_message(provider, _prompt(bundle), bundle["screenshot_artifacts"]),
                temperature=float(config.get("temperature", 0.3)),
                top_p=config.get("top_p"),
                top_k=config.get("top_k"),
                max_tokens=3000,
            )
        except Exception as exc:
            return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": f"Product Judge request failed: {str(exc)[:300]}"}
    validated, error = validate_judge_response(raw)
    if not validated:
        return {**result, "verdict": "insufficient_evidence", "availability": "unavailable", "findings": [], "blocking_findings": [], "reason": error}
    return {**result, **validated, "availability": "available", "reason": ""}
