import json
from pathlib import Path

from fastapi.testclient import TestClient

import config_storage
import main
from project_spec import ensure_project_spec_bundle


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _client(monkeypatch, tmp_path, config=None):
    config_path = tmp_path / "studio_config.json"
    state_path = tmp_path / "projects_state.json"
    _write(config_path, config or {})
    _write(state_path, {"projects": {}, "tasks": {}})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(main, "PROJECTS_STATE_FILE", str(state_path))
    main.active_projects.clear()
    main.PROJECT_TASKS.clear()
    main.SYSTEM_SETTINGS.clear()
    main.SYSTEM_SETTINGS.update(main._get_saved_system_settings(config or {}))
    main.agent_configs = main.load_agent_configs()
    return TestClient(main.app), config_path, state_path


def _opencode_connection():
    return {
        "connection_id": "opencode-test",
        "name": "OpenCode Browser",
        "display_name": "OpenCode Browser",
        "connection_type": "opencode_bridge",
        "provider": "opencode_bridge",
        "configured_model": "openai/gpt-5.5",
        "enabled": True,
        "capabilities": {"text_input": {"status": "supported"}, "image_input": {"status": "supported"}},
        "available_models": [
            {"id": "openai/gpt-5.5", "provider": "openai", "capabilities": {"text_input": True, "image_input": True}},
            {"id": "anthropic/claude", "provider": "anthropic", "capabilities": {"text_input": True, "image_input": False}},
        ],
        "capability_metadata": {"openai/gpt-5.5": {"text_input": True, "image_input": True}, "anthropic/claude": {"text_input": True, "image_input": False}},
        "stores_authentication": False,
    }


def _openai_connection():
    return {
        "connection_id": "provider-openai",
        "display_name": "Direct OpenAI API",
        "provider": "openai",
        "connection_type": "api_provider",
        "credential_reference": "openai_key",
        "configured_status": "tested",
        "tested_status": "passed",
        "available_models": [
            {"id": "gpt-4o", "capabilities": {"text_input": True, "image_input": True}},
            {"id": "gpt-4.1", "capabilities": {"text_input": True, "image_input": True}},
        ],
        "capability_metadata": {"gpt-4o": {"text_input": True, "image_input": True}, "gpt-4.1": {"text_input": True, "image_input": True}},
        "stores_authentication": False,
    }


def test_project_modes_persist_across_restart(monkeypatch, tmp_path):
    client, _cfg, state_path = _client(monkeypatch, tmp_path)
    ids = []
    expected = {"prototype": "prototype", "manual": "manual", "mvp": "strict_mvp"}
    for mode in expected:
        data = client.post("/api/projects/manual", json={"title": f"{mode} app", "initial_description": "Build a todo app", "project_mode": mode}).json()
        ids.append((data["project_id"], mode))

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"]

    for project_id, mode in ids:
        assert persisted[project_id]["project_mode"] == expected[mode]
        assert persisted[project_id]["quality_profile"] == ("prototype" if mode == "prototype" else "strict_mvp")


def test_manual_project_quality_targets_and_toggles_persist(monkeypatch, tmp_path):
    client, _cfg, state_path = _client(monkeypatch, tmp_path)
    data = client.post("/api/projects/manual", json={
        "title": "Quality UI",
        "initial_description": "Build booking manager web and mobile app",
        "quality_profile": "strict_mvp",
        "required_targets": ["backend", "manager_web", "android"],
        "optional_targets": ["ios"],
        "strict_completion_toggles": {"require_real_e2e": True, "require_rbac_matrix": True, "require_security_baseline": True, "block_on_mandatory_not_verified": True},
    }).json()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][data["project_id"]]

    settings = persisted["quality_settings"]
    assert settings["required_targets"] == ["backend", "manager_web", "android"]
    assert settings["optional_targets"] == ["ios"]
    assert settings["target_requirements"]["manager_web"] == "required"
    assert settings["target_requirements"]["ios"] == "optional"
    assert settings["require_real_e2e"] is True
    assert settings["require_rbac_matrix"] is True


def test_generic_credentials_do_not_block_prototype_or_mvp_coding():
    text = "Build an app with database, payments, and notifications."
    for mode in ("prototype", "mvp"):
        project = {"title": "Fallback", "description": text, "project_mode": mode, "chat_history": [], "logs": []}
        spec = ensure_project_spec_bundle(project)["project_spec"]
        assert not [gap for gap in spec["requirement_gaps"] if gap["category"] == "missing_credential"]
        assert all(c["blocks_coding"] is False for c in spec["required_credentials"])


def test_generic_email_does_not_force_smtp_credentials():
    project = {"title": "Email", "description": "Send email notifications when bookings change.", "project_mode": "mvp", "chat_history": [], "logs": []}
    spec = ensure_project_spec_bundle(project)["project_spec"]
    assert not any(c["credential_name"] == "SMTP_PASSWORD" for c in spec["required_credentials"])


def test_generic_payments_do_not_force_live_stripe_but_real_stripe_blocks_dependent_feature():
    generic = {"title": "Pay", "description": "Support payments later.", "project_mode": "mvp", "chat_history": [], "logs": []}
    live = {"title": "Pay", "description": "Customers must make real Stripe payment before booking.", "project_mode": "mvp", "chat_history": [], "logs": []}
    generic_spec = ensure_project_spec_bundle(generic)["project_spec"]
    live_spec = ensure_project_spec_bundle(live)["project_spec"]
    assert all(c["live_integration_required"] is False for c in generic_spec["required_credentials"] if c["credential_name"] == "STRIPE_API_KEY")
    stripe = next(c for c in live_spec["required_credentials"] if c["credential_name"] == "STRIPE_API_KEY")
    assert stripe["live_integration_required"] is True
    assert stripe["blocks_feature"] is True
    assert stripe["blocks_coding"] is True


def test_manual_mode_missing_optional_credentials_do_not_globally_block():
    project = {"title": "Manual", "description": "Use real Stripe payment after manual setup.", "project_mode": "manual", "chat_history": [], "logs": []}
    spec = ensure_project_spec_bundle(project)["project_spec"]
    stripe = next(c for c in spec["required_credentials"] if c["credential_name"] == "STRIPE_API_KEY")
    assert stripe["blocks_feature"] is True
    assert stripe["blocks_coding"] is False
    assert not [gap for gap in spec["requirement_gaps"] if gap["category"] == "missing_credential"]


def test_opencode_bridge_and_direct_openai_api_are_separate_and_sanitized(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret-never-return", "_provider_connections": [_opencode_connection(), _openai_connection()]}
    client, _cfg, _state = _client(monkeypatch, tmp_path, cfg)
    data = client.get("/api/provider-connections").json()
    by_id = {c["connection_id"]: c for c in data["connections"]}
    assert by_id["opencode-test"]["connection_type"] == "opencode_oauth_bridge"
    assert by_id["provider-openai"]["connection_type"] == "openai_api"
    assert "sk-secret" not in json.dumps(data)
    assert by_id["opencode-test"].get("stores_authentication") is False


def test_agents_can_use_different_models_and_model_must_belong_to_connection(monkeypatch, tmp_path):
    cfg = {
        "openai_key": "sk-secret",
        "_provider_connections": [_opencode_connection(), _openai_connection()],
        "_global_ai": {"connection_id": "opencode-test", "connection_type": "opencode_oauth_bridge", "provider": "opencode_bridge", "model": "openai/gpt-5.5"},
        "_agent_configs": {
            "alex": {"use_global_connection": True, "use_global_model": True},
            "elena": {"use_global_connection": False, "connection_id": "provider-openai", "provider": "openai", "use_global_model": False, "model": "gpt-4o"},
            "maya": {"use_global_connection": False, "connection_id": "provider-openai", "provider": "openai", "use_global_model": False, "model": "openai/gpt-5.5"},
        },
    }
    client, _cfg, _state = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["alex"]["connection_type"] == "opencode_oauth_bridge"
    assert effective["alex"]["model"] == "openai/gpt-5.5"
    assert effective["elena"]["connection_type"] == "openai_api"
    assert effective["elena"]["model"] == "gpt-4o"
    assert effective["maya"]["capability_status"] == "incompatible"
    assert effective["maya"]["capability_validation"]["model_in_connection"] is False


def test_product_judge_only_accepts_proven_image_capable_models(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_openai_connection()]}
    client, _cfg, _state = _client(monkeypatch, tmp_path, cfg)
    ok = client.post("/api/product-judge/test", json={"connection_id": "provider-openai", "model": "gpt-4o"}).json()
    bad = client.post("/api/product-judge/test", json={"connection_id": "provider-openai", "model": "missing-model"}).json()
    assert ok["available"] is True
    assert bad["available"] is False


def test_agent_invocation_metadata_contains_connection_without_secrets(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_openai_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _cfg, _state = _client(monkeypatch, tmp_path, cfg)
    project = client.post("/api/projects/manual", json={"title": "Audit", "initial_description": "Build x", "project_mode": "mvp"}).json()
    monkeypatch.setattr(main, "ask_studio_ai_with_history", lambda **_kwargs: "ok")
    data = client.post(f"/api/agents/alex/chat", json={"message": "hi", "project_id": project["project_id"], "use_project_context": True}).json()
    assert data["invocation"]["effective_connection_id"] == "provider-openai"
    assert data["invocation"]["connection_type"] == "openai_api"
    assert "sk-secret" not in json.dumps(data)
