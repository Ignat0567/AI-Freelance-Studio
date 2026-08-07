from pathlib import Path

import project_state
from agent_contracts import apply_agent_artifact
from feature_matrix import apply_bugcatcher_artifact, build_feature_matrix, ensure_feature_matrix, load_feature_matrix, matrix_blocks_completion, recalculate_matrix
from quality_profiles import LEVEL_3_RUNTIME, evaluate_quality_completion


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, description: str | None = None, profile: str = "strict_mvp") -> dict:
    requirement_text = description or "Authenticated customers book appointment slots, managers approve status transitions, data persists after restart."
    project = {
        "project_id": "matrix-policy-test",
        "title": "Matrix Policy Test",
        "description": requirement_text,
        "quality_profile": profile,
        "project_mode": profile,
        "target_path": str(tmp_path),
        "project_spec": {
            "quality_profile": profile,
            "requirements": [{"id": "REQ-1", "title": requirement_text[:72], "description": requirement_text, "mandatory": True, "priority": "high"}],
            "project_profiles": ["fastapi"],
        },
        "project_profiles": ["fastapi"],
        "acceptance_criteria": [
            {"id": "AC-PERSIST", "title": "Data persists after restart", "priority": "high", "status": "passed"},
            {"id": "AC-WORKFLOW", "title": "Real E2E workflow passes", "priority": "high", "status": "passed"},
            {"id": "AC-RBAC", "title": "Authentication and RBAC roles pass", "priority": "high", "status": "passed"},
        ],
        "issues": [],
        "logs": [],
    }
    ensure_feature_matrix(project, str(tmp_path))
    return project


def _bugcatcher_output(**updates):
    output = {
        "critical_scenario_matrix": [],
        "positive_negative_tests": [],
        "rbac_test_matrix": [],
        "persistence_restart_tests": [],
        "concurrency_race_tests": [],
        "platform_runtime_test_plan": [],
        "feature_to_test_mapping": {},
        "untested_gap_report": [],
        "covered_mandatory_scenarios": [],
        "uncovered_mandatory_scenarios": [],
        "tests_executed": [],
        "tests_skipped": [],
        "tooling_unavailable": [],
        "failures": [],
        "evidence_references": [],
    }
    output.update(updates)
    return output


def test_four_generic_tests_cannot_imply_all_features_covered(tmp_path):
    project = _project(tmp_path)
    apply_bugcatcher_artifact(project, _bugcatcher_output(tests_executed=["test_1", "test_2", "test_3", "test_4"]), str(tmp_path))

    blocks, blockers = matrix_blocks_completion(project["feature_matrix"], "strict_mvp")

    assert blocks is True
    assert any("quality_matrix:" in blocker for blocker in blockers)


def test_missing_mandatory_scenario_blocks_strict_mvp(tmp_path):
    project = _project(tmp_path)
    feature_id = project["feature_matrix"]["features"][0]["feature_id"]
    apply_bugcatcher_artifact(project, _bugcatcher_output(critical_scenario_matrix=[{"feature_id": feature_id, "scenario": "happy_path", "status": "passed"}]), str(tmp_path))

    gaps = project["feature_matrix"]["quality_matrix_summary"]["blocking_gaps"]

    assert any("validation_failure" in gap for gap in gaps)


def test_rbac_positive_only_test_is_insufficient(tmp_path):
    project = _project(tmp_path)
    feature_id = project["feature_matrix"]["features"][0]["feature_id"]
    apply_bugcatcher_artifact(project, _bugcatcher_output(rbac_test_matrix=[{"feature_id": feature_id, "kind": "allowed_actor", "status": "passed"}]), str(tmp_path))

    rbac = project["feature_matrix"]["quality_matrices"]["rbac"]

    assert any(row["kind"] == "allowed_actor" and row["status"] == "passed" for row in rbac)
    assert any(row["kind"] == "denied_actor" and row["status"] != "passed" for row in rbac)


def test_cross_user_isolation_is_required_where_applicable(tmp_path):
    project = _project(tmp_path, "Authenticated users manage private booking records and cannot access another user's records.")

    assert any(row["kind"] == "cross_user_isolation" and row["required"] for row in project["feature_matrix"]["quality_matrices"]["rbac"])


def test_restart_persistence_requires_real_restart(tmp_path):
    project = _project(tmp_path)
    feature_id = project["feature_matrix"]["features"][0]["feature_id"]
    apply_bugcatcher_artifact(project, _bugcatcher_output(persistence_restart_tests=[{"feature_id": feature_id, "kind": "restart_roundtrip", "status": "passed", "real_restart": False}]), str(tmp_path))

    persistence = project["feature_matrix"]["quality_matrices"]["persistence"]
    assert persistence[0]["status"] == "not_verified"


def test_concurrency_required_only_for_conflict_sensitive_feature(tmp_path):
    booking = _project(tmp_path / "booking", "Customers reserve a unique booking slot.")
    static = _project(tmp_path / "static", "A static marketing site displays business hours.")

    assert booking["feature_matrix"]["quality_matrices"]["concurrency"]
    assert static["feature_matrix"]["quality_matrices"].get("concurrency") == []


def test_security_critical_issue_blocks(tmp_path):
    project = _project(tmp_path)
    output = {"authentication": {}, "authorization_rbac": {}, "idor": {}, "secret_handling": {}, "password_hashing": {}, "session_token_storage": {}, "rate_limiting": {}, "input_validation": {}, "cors": {}, "upload_safety": {}, "debug_default_credentials": {}, "dependency_risk": {}, "audit_logging": {}, "checks_performed": [], "checks_not_performed": ["rbac_idor"], "findings": [{"severity": "critical", "title": "rbac_idor exposes appointments"}], "blocking_policy": "critical findings block", "remediation_guidance": ["enforce ownership checks"]}

    result = apply_agent_artifact(project, "sentinel", output)
    blocks, blockers = matrix_blocks_completion(project["feature_matrix"], "strict_mvp")

    assert result["status"] == "valid"
    assert blocks is True
    assert project["issues"][0]["severity"] == "critical"


def test_optional_integration_credential_absence_does_not_block_core_mvp():
    project = {"quality_profile": "strict_mvp", "project_spec": {"quality_profile": "strict_mvp", "requested_target_platforms": []}, "acceptance_criteria": [{"title": "Data persists", "status": "passed"}, {"title": "Real E2E workflow", "status": "passed"}], "issues": []}
    checks = [
        {"name": "required_files", "status": "passed", "evidence": {}},
        {"name": "readme_instructions", "status": "passed", "evidence": {}},
        {"name": "runtime_smoke", "status": "passed", "evidence": {}},
        {"name": "latest_full_qa", "status": "passed", "evidence": {}},
        {"name": "acceptance_criteria", "status": "passed", "evidence": {}},
        {"name": "secret_scan", "status": "passed", "evidence": {}},
        {"name": "open_blocking_issues", "status": "passed", "evidence": {}},
        {"name": "feature_completeness_matrix", "status": "passed", "evidence": {}},
        {"name": "mandatory_test_matrices", "status": "passed", "evidence": {}},
        {"name": "architecture_review", "status": "passed", "evidence": {}},
    ]

    result = evaluate_quality_completion(project, checks, blocked_by_credentials=False)

    assert "required_credentials_missing" not in result["blockers"]


def test_coverage_matrix_survives_restart(tmp_path):
    _write(tmp_path / "README.md", "# Demo")
    project = _project(tmp_path)
    project_state.persist_project_state(project, str(tmp_path))
    matrix, error = load_feature_matrix(str(tmp_path))

    assert error == ""
    assert matrix["quality_matrices"]["critical_scenarios"]
    assert matrix["quality_matrix_summary"]["blocking_gaps"]
