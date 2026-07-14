import json
from pathlib import Path

from fastapi.testclient import TestClient

import main


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _vision_connection(provider="openai", model="gpt-4o"):
    return {
        "connection_id": f"provider-{provider}",
        "display_name": f"{provider.upper()} API key",
        "name": f"{provider.upper()} API key",
        "provider": provider,
        "connection_type": "api_provider",
        "credential_reference": f"{provider}_key",
        "configured_status": "tested",
        "tested_status": "passed",
        "last_test_result": {"status": "ok", "message": "Provider key accepted"},
        "available_models": [
            {"id": model, "capabilities": {"text_input": True, "image_input": True, "structured_output": True, "streaming": True, "tool_use": True}},
            {"id": "gpt-5.5", "capabilities": {"text_input": True, "image_input": None, "structured_output": True, "streaming": True, "tool_use": True}},
        ],
        "capability_metadata": {
            model: {"text_input": True, "image_input": True, "structured_output": True, "streaming": True, "tool_use": True},
            "gpt-5.5": {"text_input": True, "image_input": None, "structured_output": True, "streaming": True, "tool_use": True},
        },
        "configured_model": model,
        "updated_at": "2026-07-13T00:00:00Z",
        "stores_authentication": False,
    }


def _client(monkeypatch, tmp_path, config):
    config_path = tmp_path / "studio_config.json"
    _write(config_path, config)
    monkeypatch.setattr(main, "CONFIG_FILE", str(config_path))
    main.SYSTEM_SETTINGS.clear()
    main.SYSTEM_SETTINGS.update(main._get_saved_system_settings(config))
    main.agent_configs = main.load_agent_configs()
    return TestClient(main.app), config_path


def test_all_agents_can_inherit_one_global_connection_model(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2, "enabled": True}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    data = client.post("/api/agents/apply-global-inheritance").json()
    assert "alex" in data["updated"]
    assert "product_judge" in data["updated"]
    effective = client.get("/api/agents/effective-ai").json()
    for agent_id in main.DEFAULT_AGENTS:
        assert effective[agent_id]["provider"] == "openai"
        assert effective[agent_id]["model"] == "gpt-4o"
        assert effective[agent_id]["use_global_connection"] is True
        assert effective[agent_id]["use_global_model"] is True


def test_same_global_model_allows_different_agent_temperatures(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2}, "_agent_configs": {"alex": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.4}, "maya": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.3}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["alex"]["model"] == effective["maya"]["model"] == "gpt-4o"
    assert effective["alex"]["temperature"] == 0.4
    assert effective["maya"]["temperature"] == 0.3


def test_agent_specific_model_override_and_temperature_override(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2}, "_agent_configs": {"codex": {"use_global_connection": True, "use_global_model": False, "model": "gpt-4.1", "temperature": 0.15}, "elena": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.7}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["codex"]["model"] == "gpt-4.1"
    assert effective["codex"]["temperature"] == 0.15
    assert effective["elena"]["model"] == "gpt-4o"
    assert effective["elena"]["temperature"] == 0.7


def test_changing_global_model_updates_inherited_but_not_overridden_agents(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"alex": {"use_global_model": True}, "codex": {"use_global_model": False, "model": "gpt-4o"}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    client.post("/api/config/ai/global", json={"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4.1", "temperature": 0.2})
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["alex"]["model"] == "gpt-4.1"
    assert effective["codex"]["model"] == "gpt-4o"


def test_apply_to_all_sets_inheritance_not_copied_values(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"codex": {"temperature": 0.15}}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)
    client.post("/api/agents/apply-global-inheritance")
    saved = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["codex"]
    assert saved["use_global_connection"] is True
    assert saved["use_global_model"] is True
    assert saved["temperature"] == 0.15
    assert "provider" not in saved
    assert "model" not in saved


def test_product_judge_rejects_incompatible_global_model(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-5.5"}, "_agent_configs": {"product_judge": {"use_global_connection": True, "use_global_model": True}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/product_judge/effective-ai").json()
    assert effective["capability_validation"]["valid"] is False
    assert effective["capability_validation"]["reason"] == "Global model is not eligible for Product Judge."


def test_product_judge_accepts_proven_vision_capable_global_model(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"product_judge": {"use_global_connection": True, "use_global_model": True}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/product_judge/effective-ai").json()
    assert effective["capability_validation"]["valid"] is True


def test_opencode_config_not_changed_by_agent_parameter_changes(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    called = {"sync": False}
    monkeypatch.setattr(main, "sync_opencode_config", lambda: called.update(sync=True) or True)
    client.post("/api/agents/codex/config", json={"temperature": 0.15, "use_global_model": True})
    assert called["sync"] is False


def test_unsupported_top_k_is_not_sendable(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "top_k": 40}, "_agent_configs": {"alex": {"use_global_generation_parameters": True}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/alex/effective-ai").json()
    assert "top_k" not in effective["sendable_generation_parameters"]
    assert effective["unsupported_generation_parameters"]["top_k"] == "Not supported by this model/provider"


def test_preview_matches_runtime_resolver(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    preview = client.get("/api/agents/alex/effective-ai").json()
    direct = main.resolve_effective_agent_ai_config("alex")
    assert preview["provider"] == direct["provider"]
    assert preview["model"] == direct["model"]


def test_configuration_survives_restart_and_raw_credentials_are_not_exposed(monkeypatch, tmp_path):
    secret = "sk-secret-never-return"
    cfg = {"openai_key": secret, "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)
    client.post("/api/agents/elena/config", json={"use_global_model": True, "temperature": 0.7})
    main.agent_configs = main.load_agent_configs()
    assert main.resolve_effective_agent_ai_config("elena")["temperature"] == 0.7
    assert secret not in client.get("/api/config/ai/global").text
    assert secret not in client.get("/api/agents/effective-ai").text
    assert json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["elena"]["temperature"] == 0.7


def test_reset_to_defaults_restores_inheritance_defaults(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"alex": {"use_global_model": False, "model": "gpt-4.1"}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    client.post("/api/agents/alex/config", json={"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "model": ""})
    effective = client.get("/api/agents/alex/effective-ai").json()
    assert effective["use_global_model"] is True
    assert effective["model"] == "gpt-4o"
