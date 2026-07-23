import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

import config_storage
import main
from test_security_support import authorized_test_client
from agent_contracts import ROLE_CONTRACTS, apply_agent_artifact, recommended_defaults_for, validate_agent_output


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _vision_connection(provider="openai", model="gpt-4o"):
    return {
        "connection_id": f"provider-{provider}",
        "display_name": f"{provider.upper()} API key",
        "name": f"{provider.upper()} API key",
        "provider": provider,
        "connection_type": "api_provider",
        "configured_status": "tested",
        "tested_status": "passed",
        "available_models": [{"id": model, "capabilities": {"text_input": True, "image_input": True, "structured_output": True}}],
        "capability_metadata": {model: {"text_input": True, "image_input": True, "structured_output": True}},
        "configured_model": model,
    }


def _client(monkeypatch, tmp_path, config):
    config_path = tmp_path / "studio_config.json"
    _write(config_path, config)
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    main.SYSTEM_SETTINGS.clear()
    main.SYSTEM_SETTINGS.update(main._get_saved_system_settings(config))
    main.agent_configs = main.load_agent_configs()
    return authorized_test_client(main.app), config_path


def _alex_output():
    return {
        "project_goal": "Launch booking MVP",
        "quality_profile_recommendation": "strict_mvp",
        "target_platforms": ["web"],
        "mandatory_mvp_features": ["booking", "manager dashboard"],
        "optional_future_features": ["loyalty"],
        "exclusions": ["marketplace"],
        "milestones": ["spec", "build", "qa"],
        "definition_of_done": ["mandatory workflows verified"],
        "risks": ["credentials unavailable"],
        "dependency_credential_requirements": ["email provider optional"],
    }


def _maya_output():
    return {
        "feature_contracts": [{
            "feature": "Manager confirms an appointment",
            "actor": "manager",
            "preconditions": ["manager is authenticated", "appointment exists"],
            "action": "change appointment status to confirmed",
            "expected_outcome": "appointment status is confirmed and visible after refresh",
            "persistence_behavior": "status survives restart",
            "authorization_behavior": "only manager role may confirm",
            "error_negative_cases": ["anonymous user receives 401", "client receives 403"],
            "target_platform_interface": "manager_web",
            "required_evidence_level": "LEVEL_4_INTERACTION",
        }]
    }


def test_every_builtin_agent_has_role_contract():
    for agent_id in main.DEFAULT_AGENTS:
        assert agent_id in ROLE_CONTRACTS
        assert ROLE_CONTRACTS[agent_id]["summary"]
        assert ROLE_CONTRACTS[agent_id]["required_fields"]


def test_agents_cannot_set_project_completed_directly():
    project = {"status": "product_judge", "logs": [], "cancel_requested": False, "agent_artifacts": {"alex": [{"artifact": {"status": "completed"}}]}}

    assert not main._set_project_status(project, "completed")
    assert project["status"] == "product_judge"


def test_agent_output_cannot_directly_mark_accepted():
    output = _alex_output()
    output["completion_policy"] = {"accepted": True}

    result = validate_agent_output("alex", output)

    assert result["status"] == "invalid_agent_output"
    assert "agent_may_not_set_final_status:completion_policy.accepted" in result["errors"]


def test_alex_contract_defines_definition_of_done():
    project = {}
    result = apply_agent_artifact(project, "alex", _alex_output())

    assert result["status"] == "valid"
    assert project["project_spec"]["definition_of_done"] == ["mandatory workflows verified"]


def test_maya_produces_testable_acceptance_criteria_and_rejects_vague():
    project = {"acceptance_criteria": []}
    result = apply_agent_artifact(project, "maya", _maya_output())

    vague = validate_agent_output("maya", {"feature_contracts": [{"feature": "Favorites work"}]})

    assert result["status"] == "valid"
    assert project["acceptance_criteria"][0]["required_evidence_level"] == "LEVEL_4_INTERACTION"
    assert project["acceptance_criteria"][0]["status"] == "not_verified"
    assert vague["status"] == "invalid_agent_output"


def test_codex_implementation_claim_is_not_acceptance_evidence():
    project = {"acceptance_evidence": {}, "acceptance_criteria": [{"id": "AC-1", "status": "not_verified"}]}
    output = {"architecture_plan": [], "module_map": [], "implementation_to_feature_mapping": [{"feature": "booking", "file": "main.py"}], "changed_files": ["main.py"], "known_limitations": [], "unsupported_features": [], "migrations": [], "tests_added": ["test_api.py"], "direct_run_commands": ["python -m pytest"]}

    result = apply_agent_artifact(project, "codex", output)

    assert result["status"] == "valid"
    assert project["implementation_claims"]
    assert project["acceptance_evidence"] == {}
    assert project["acceptance_criteria"][0]["status"] == "not_verified"


def test_bugcatcher_emits_coverage_gaps():
    project = {}
    output = {"critical_scenario_matrix": [{"scenario": "happy_path", "status": "passed"}], "positive_negative_tests": ["happy path", "invalid status"], "rbac_test_matrix": [{"kind": "allowed_actor", "status": "passed"}], "persistence_restart_tests": [{"kind": "restart_roundtrip", "status": "passed", "real_restart": True}], "concurrency_race_tests": [{"kind": "conflict_race", "status": "passed"}], "platform_runtime_test_plan": ["web runtime"], "feature_to_test_mapping": {"booking": ["test_booking"]}, "untested_gap_report": ["iOS not verified"], "covered_mandatory_scenarios": ["booking happy path"], "uncovered_mandatory_scenarios": ["iOS not verified"], "tests_executed": ["test_booking"], "tests_skipped": [], "tooling_unavailable": [], "failures": [], "evidence_references": ["pytest"]}

    result = apply_agent_artifact(project, "bugcatcher", output)

    assert result["status"] == "valid"
    assert project["qa_coverage_plan"]["untested_gap_report"] == ["iOS not verified"]


def test_sentinel_critical_finding_blocks_with_issue():
    project = {"issues": []}
    output = {"authentication": {}, "authorization_rbac": {}, "idor": {}, "secret_handling": {}, "password_hashing": {}, "session_token_storage": {}, "rate_limiting": {}, "input_validation": {}, "cors": {}, "upload_safety": {}, "debug_default_credentials": {}, "dependency_risk": {}, "audit_logging": {}, "checks_performed": [], "checks_not_performed": ["rbac_idor"], "findings": [{"severity": "critical", "title": "IDOR exposes appointments"}], "blocking_policy": "critical findings block", "remediation_guidance": ["add ownership checks"]}

    result = apply_agent_artifact(project, "sentinel", output)

    assert result["status"] == "valid"
    assert project["issues"][0]["source"] == "sentinel"
    assert project["issues"][0]["severity"] == "critical"
    assert project["issues"][0]["status"] == "open"


def test_lupa_architecture_finding_maps_correctly():
    project = {"issues": []}
    output = {"file_module_size": {}, "separation_of_concerns": {}, "duplication": {}, "domain_logic_placement": {}, "error_handling": {}, "typed_schemas": {}, "migrations": {}, "todo_placeholders": {}, "maintainability": {}, "testability": {}, "findings": [{"type": "architecture", "severity": "medium", "title": "Domain logic lives in UI component"}]}

    result = apply_agent_artifact(project, "lupa", output)

    assert result["status"] == "valid"
    assert project["issues"][0]["source"] == "lupa"
    assert project["issues"][0]["verification_method"] == "code_review"


def test_product_judge_remains_read_only():
    project = {"project_spec": {"project_goal": "original"}}
    before = copy.deepcopy(project)
    output = {"verdict": "approved", "findings": [], "blocking_findings": [], "evidence_references": ["screenshot.png"]}

    result = apply_agent_artifact(project, "product_judge", output)

    assert result["read_only"] is True
    assert project["project_spec"] == before["project_spec"]


def test_schemas_reject_malformed_output():
    result = validate_agent_output("alex", {"project_goal": "missing fields"})

    assert result["status"] == "invalid_agent_output"
    assert any(error.startswith("missing_required_field:") for error in result["errors"])


def test_user_parameter_overrides_persist(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"alex": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.44, "top_p": 0.66}}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)

    effective = client.get("/api/agents/alex/effective-ai").json()
    main.agent_configs = main.load_agent_configs()

    assert effective["temperature"] == 0.44
    assert effective["top_p"] == 0.66
    assert json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["alex"]["temperature"] == 0.44


def test_recommended_defaults_apply_correctly(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)

    agents = client.get("/api/agents").json()

    assert agents["alex"]["recommended_defaults"] == recommended_defaults_for("alex")
    assert agents["maya"]["effective_ai"]["temperature"] == 0.20
    assert agents["product_judge"]["effective_ai"]["recommended_defaults"]["top_p"] == 0.85


def test_settings_ui_reflects_agent_contracts_without_api_keys():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    assert "Agent Role Contracts" in source
    assert "Effective model:" in source
    assert "Inheritance source:" in source
    assert "temperature/top_p/top_k" in source
    assert "Role contract summary:" in source
    assert "Reset to recommended defaults:" in source
    assert "API keys are not shown here" in source
