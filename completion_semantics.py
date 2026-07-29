from __future__ import annotations

from typing import Any


def compute_completion_status(evidence: dict[str, Any]) -> dict[str, Any]:
    blockers = list(evidence.get("blockers") or [])
    missing = []
    if not evidence.get("execution_completed"):
        missing.append("execution_completed")
    if not evidence.get("changes_confirmed"):
        missing.append("changes_confirmed")
    if not evidence.get("required_tests_passed"):
        missing.append("required_tests_passed")
    if evidence.get("browser_required") and not evidence.get("browser_passed"):
        missing.append("browser_passed")
    if evidence.get("product_judge_required") and evidence.get("product_judge_verdict") not in {"approved", "approved_with_nonblocking_notes", "pass"}:
        missing.append("product_judge_pass")
    if not evidence.get("evidence_available"):
        missing.append("evidence_available")
    if evidence.get("cancelled"):
        return {"status": "CANCELLED", "missing_evidence": missing, "blockers": blockers}
    if blockers:
        return {"status": "BLOCKED", "missing_evidence": missing, "blockers": blockers}
    if not missing:
        return {"status": "COMPLETED", "missing_evidence": [], "blockers": []}
    if evidence.get("execution_completed") and evidence.get("changes_confirmed"):
        return {"status": "PARTIALLY_COMPLETED", "missing_evidence": missing, "blockers": []}
    return {"status": "FAILED", "missing_evidence": missing, "blockers": []}
