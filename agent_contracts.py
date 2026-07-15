"""Machine-verifiable contracts for FreelancerStudio role agents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from feature_matrix import apply_bugcatcher_artifact, apply_sentinel_artifact


AGENT_RECOMMENDED_DEFAULTS: dict[str, dict[str, Any]] = {
    "alex": {"temperature": 0.25, "top_p": 0.85},
    "maya": {"temperature": 0.20, "top_p": 0.80},
    "elena": {"temperature": 0.60, "top_p": 0.90},
    "codex": {"temperature": 0.15, "top_p": 0.80},
    "bugcatcher": {"temperature": 0.10, "top_p": 0.75},
    "sentinel": {"temperature": 0.10, "top_p": 0.75},
    "lupa": {"temperature": 0.15, "top_p": 0.80},
    "goldie": {"temperature": 0.25, "top_p": 0.85},
    "product_judge": {"temperature": 0.25, "top_p": 0.85},
}

ROLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "alex": {
        "summary": "Project management artifact: scope, profile, platforms, milestones, DoD, risks, dependencies. Cannot pass implementation.",
        "required_fields": ["project_goal", "quality_profile_recommendation", "target_platforms", "mandatory_mvp_features", "optional_future_features", "exclusions", "milestones", "definition_of_done", "risks", "dependency_credential_requirements"],
        "feeds": ["project_spec", "feature_matrix", "acceptance_planning"],
        "cannot": ["mark_technical_implementation_passed", "mark_project_completed"],
    },
    "maya": {
        "summary": "Business-analysis artifact: each mandatory feature becomes a testable acceptance contract with actor, action, outcomes, auth, persistence, negatives, target, evidence level.",
        "required_fields": ["feature_contracts"],
        "item_schema": {"feature_contracts": ["feature", "actor", "preconditions", "action", "expected_outcome", "persistence_behavior", "authorization_behavior", "error_negative_cases", "target_platform_interface", "required_evidence_level"]},
        "feeds": ["acceptance_criteria", "feature_matrix", "tests"],
        "cannot": ["accept_vague_criteria", "mark_technical_implementation_passed"],
    },
    "elena": {
        "summary": "UI/UX artifact: screens, navigation, states, interactions, responsive/device requirements, accessibility, visual acceptance dimensions. Claims do not pass UI evidence.",
        "required_fields": ["screen_inventory", "navigation_map", "state_inventory", "loading_empty_error_states", "interaction_contracts", "responsive_device_requirements", "accessibility_requirements", "visual_acceptance_dimensions"],
        "feeds": ["project_spec", "ui_contracts", "acceptance_criteria"],
        "cannot": ["pass_product_judge", "pass_ui_evidence"],
    },
    "codex": {
        "summary": "Implementation artifact: architecture plan, module map, feature mapping, changed files, limitations, unsupported features, migrations, tests, run commands. Cannot declare MVP passed.",
        "required_fields": ["architecture_plan", "module_map", "implementation_to_feature_mapping", "changed_files", "known_limitations", "unsupported_features", "migrations", "tests_added", "direct_run_commands"],
        "feeds": ["architecture_review", "feature_matrix", "tests"],
        "cannot": ["declare_mvp_passed", "create_acceptance_evidence_from_claims"],
    },
    "bugcatcher": {
        "summary": "QA artifact: scenario matrix, positive/negative tests, RBAC, restart persistence, concurrency where relevant, platform/runtime plan, feature-test mapping, untested gaps.",
        "required_fields": ["critical_scenario_matrix", "positive_negative_tests", "rbac_test_matrix", "persistence_restart_tests", "concurrency_race_tests", "platform_runtime_test_plan", "feature_to_test_mapping", "untested_gap_report", "covered_mandatory_scenarios", "uncovered_mandatory_scenarios", "tests_executed", "tests_skipped", "tooling_unavailable", "failures", "evidence_references"],
        "feeds": ["tests", "feature_matrix", "final_audit"],
        "cannot": ["imply_broad_coverage_from_small_suite"],
    },
    "sentinel": {
        "summary": "Security artifact: auth, RBAC, IDOR, secrets, password hashing, sessions, rate limits, validation, CORS, uploads, debug defaults, dependencies, audit logging. Critical findings block.",
        "required_fields": ["authentication", "authorization_rbac", "idor", "secret_handling", "password_hashing", "session_token_storage", "rate_limiting", "input_validation", "cors", "upload_safety", "debug_default_credentials", "dependency_risk", "audit_logging", "checks_performed", "checks_not_performed", "findings", "blocking_policy", "remediation_guidance"],
        "feeds": ["security_review", "issues", "final_audit"],
        "cannot": ["silently_ignore_critical_findings"],
    },
    "lupa": {
        "summary": "Code-review artifact: size, separation, duplication, domain logic, errors, schemas, migrations, TODOs, maintainability, testability. Pragmatic, not arbitrary style blocking.",
        "required_fields": ["file_module_size", "separation_of_concerns", "duplication", "domain_logic_placement", "error_handling", "typed_schemas", "migrations", "todo_placeholders", "maintainability", "testability", "findings"],
        "feeds": ["architecture_review", "issues", "final_audit"],
        "cannot": ["block_on_arbitrary_style_preference_only"],
    },
    "goldie": {
        "summary": "Business/financial artifact: monetization, costs, providers, operating risks, market model, revenue assumptions. Does not control technical completion.",
        "required_fields": ["monetization_assumptions", "cost_drivers", "external_provider_costs", "production_operating_risks", "marketplace_vs_single_business_model", "revenue_assumptions"],
        "feeds": ["business_review", "project_spec_risks"],
        "cannot": ["control_technical_completion"],
    },
    "product_judge": {
        "summary": "Independent read-only, evidence-bound, vision-capable product review. Cannot override objective failures.",
        "required_fields": ["verdict", "findings", "blocking_findings", "evidence_references"],
        "feeds": ["product_judge_review", "final_audit"],
        "cannot": ["write_project_files", "override_objective_failure"],
        "read_only": True,
    },
}

VAGUE_CRITERIA = {"favorites work", "works", "buttons work", "ui works", "login works"}
RESERVED_COMPLETION_FIELDS = {"status", "project_status", "final_status", "completion_status", "accepted", "completed", "passed", "completion_policy"}
RESERVED_COMPLETION_VALUES = {"completed", "accepted", "passed", "mvp passed", "strict_mvp_accepted", "production_candidate_accepted", "production_release_accepted"}


def role_contract_for(agent_id: str) -> dict[str, Any]:
    contract = ROLE_CONTRACTS.get(str(agent_id or "").lower(), {})
    return deepcopy(contract)


def recommended_defaults_for(agent_id: str) -> dict[str, Any]:
    return dict(AGENT_RECOMMENDED_DEFAULTS.get(str(agent_id or "").lower(), {"temperature": 0.2, "top_p": None}))


def validate_agent_output(agent_id: str, output: dict[str, Any]) -> dict[str, Any]:
    agent = str(agent_id or "").lower()
    contract = ROLE_CONTRACTS.get(agent)
    errors: list[str] = []
    if not contract:
        errors.append("unknown_agent_contract")
    if not isinstance(output, dict):
        errors.append("output_must_be_object")
        output = {}
    for field in RESERVED_COMPLETION_FIELDS & set(output):
        value = output.get(field)
        if value is True or str(value).strip().lower() in RESERVED_COMPLETION_VALUES:
            errors.append(f"agent_may_not_set_final_status:{field}")
        if field == "completion_policy" and isinstance(value, dict) and value.get("accepted") is True:
            errors.append("agent_may_not_set_final_status:completion_policy.accepted")
    for field in (contract or {}).get("required_fields", []):
        value = output.get(field)
        if field not in output or value is None or value == "":
            errors.append(f"missing_required_field:{field}")
    item_schema = (contract or {}).get("item_schema", {})
    for list_field, required in item_schema.items():
        items = output.get(list_field)
        if not isinstance(items, list) or not items:
            errors.append(f"missing_required_items:{list_field}")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"invalid_item:{list_field}[{index}]")
                continue
            for field in required:
                if item.get(field) in (None, "", [], {}):
                    errors.append(f"missing_required_field:{list_field}[{index}].{field}")
            if agent == "maya":
                text = " ".join(str(item.get(key, "")) for key in ("feature", "action", "expected_outcome")).strip().lower()
                if text in VAGUE_CRITERIA or any(vague == str(item.get("feature", "")).strip().lower() for vague in VAGUE_CRITERIA):
                    errors.append(f"vague_criterion:{list_field}[{index}]")
    return {"status": "valid" if not errors else "invalid_agent_output", "errors": errors, "agent_id": agent, "contract_version": 1}


def _issue_from_finding(agent_id: str, finding: dict[str, Any], index: int) -> dict[str, Any]:
    severity = str(finding.get("severity") or "medium").lower()
    if severity == "blocker":
        severity = "critical"
    title = str(finding.get("title") or finding.get("check") or f"{agent_id.title()} finding")[:120]
    verification = "security_review" if agent_id == "sentinel" else "code_review"
    return {
        "id": f"ISSUE-{agent_id.upper()}-CONTRACT-{index:03d}",
        "source": agent_id,
        "severity": severity,
        "requirement_id": str(finding.get("requirement_id") or ""),
        "criterion_id": str(finding.get("criterion_id") or ""),
        "title": title,
        "evidence": {"structured_finding": finding},
        "reproduction": [str(step) for step in finding.get("reproduction", [])][:10] if isinstance(finding.get("reproduction"), list) else [],
        "owner": "codex",
        "status": "open",
        "attempts": 0,
        "verification_method": verification,
    }


def apply_agent_artifact(project: dict[str, Any], agent_id: str, output: dict[str, Any]) -> dict[str, Any]:
    validation = validate_agent_output(agent_id, output)
    agent = validation["agent_id"]
    record = {"agent_id": agent, "validation": validation, "artifact": deepcopy(output) if isinstance(output, dict) else output}
    project.setdefault("agent_artifacts", {}).setdefault(agent, []).append(record)
    if validation["status"] != "valid":
        project.setdefault("invalid_agent_outputs", []).append(record)
        return validation
    if ROLE_CONTRACTS.get(agent, {}).get("read_only"):
        return {**validation, "read_only": True}
    if agent == "alex":
        spec = project.setdefault("project_spec", {})
        for source, target in (("project_goal", "project_goal"), ("quality_profile_recommendation", "quality_profile_recommendation"), ("target_platforms", "requested_target_platforms"), ("mandatory_mvp_features", "mandatory_mvp_features"), ("optional_future_features", "optional_future_features"), ("exclusions", "exclusions"), ("milestones", "milestones"), ("definition_of_done", "definition_of_done"), ("risks", "risks"), ("dependency_credential_requirements", "dependency_credential_requirements")):
            spec[target] = deepcopy(output.get(source))
    elif agent == "maya":
        criteria = project.setdefault("acceptance_criteria", [])
        for item in output.get("feature_contracts", []):
            criteria.append({
                "id": f"AC-MAYA-{len(criteria) + 1:03d}",
                "title": str(item.get("feature")),
                "priority": "high",
                "status": "not_verified",
                "actor": item.get("actor"),
                "preconditions": item.get("preconditions"),
                "action": item.get("action"),
                "expected_result": item.get("expected_outcome"),
                "persistence_behavior": item.get("persistence_behavior"),
                "authorization_behavior": item.get("authorization_behavior"),
                "negative_cases": item.get("error_negative_cases"),
                "target_platform_interface": item.get("target_platform_interface"),
                "required_evidence_level": item.get("required_evidence_level"),
            })
    elif agent == "elena":
        project["ui_contracts"] = deepcopy(output)
    elif agent == "codex":
        project.setdefault("implementation_claims", []).append(deepcopy(output))
    elif agent == "bugcatcher":
        project["qa_coverage_plan"] = deepcopy(output)
        apply_bugcatcher_artifact(project, output, project.get("target_path"))
    elif agent == "sentinel":
        project["security_review"] = deepcopy(output)
        apply_sentinel_artifact(project, output, project.get("target_path"))
        for index, finding in enumerate(output.get("findings", []), start=1):
            if isinstance(finding, dict) and str(finding.get("severity", "")).lower() in {"critical", "blocker"}:
                project.setdefault("issues", []).append(_issue_from_finding(agent, finding, index))
    elif agent == "lupa":
        project["architecture_review"] = deepcopy(output)
        for index, finding in enumerate(output.get("findings", []), start=1):
            if isinstance(finding, dict) and str(finding.get("type", "")).lower() in {"architecture", "maintainability", "testability"}:
                project.setdefault("issues", []).append(_issue_from_finding(agent, finding, index))
    elif agent == "goldie":
        project["business_financial_review"] = deepcopy(output)
    return validation
