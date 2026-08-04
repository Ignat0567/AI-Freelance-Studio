import json
from pathlib import Path

from fastapi.testclient import TestClient

import config_storage
import main
from test_security_support import authorized_test_client


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


def _text_connection(provider="anthropic", model="claude-sonnet-4-20250514"):
    return {
        "connection_id": f"provider-{provider}",
        "display_name": f"{provider.upper()} API key",
        "name": f"{provider.upper()} API key",
        "provider": provider,
        "connection_type": "api_provider",
        "credential_reference": f"{provider}_key",
        "configured_status": "tested",
        "tested_status": "passed",
        "available_models": [
            {"id": model, "capabilities": {"text_input": True, "image_input": False, "structured_output": True, "streaming": True, "tool_use": True}},
            {"id": "model-b", "capabilities": {"text_input": True, "image_input": False, "structured_output": True, "streaming": True, "tool_use": True}},
        ],
        "capability_metadata": {
            model: {"text_input": True, "image_input": False, "structured_output": True, "streaming": True, "tool_use": True},
            "model-b": {"text_input": True, "image_input": False, "structured_output": True, "streaming": True, "tool_use": True},
        },
        "configured_model": model,
        "updated_at": "2026-07-13T00:00:00Z",
        "stores_authentication": False,
    }


def _client(monkeypatch, tmp_path, config):
    config_path = tmp_path / "studio_config.json"
    _write(config_path, config)
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    main.SYSTEM_SETTINGS.clear()
    main.SYSTEM_SETTINGS.update(main._get_saved_system_settings(config))
    main.agent_configs = main.load_agent_configs()
    return authorized_test_client(main.app), config_path


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
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2}, "_agent_configs": {"codex": {"use_global_connection": True, "use_global_model": False, "use_global_generation_parameters": False, "model": "gpt-5.5", "temperature": 0.15}, "elena": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.7}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["codex"]["model"] == "gpt-5.5"
    assert effective["codex"]["temperature"] == 0.15
    assert effective["elena"]["model"] == "gpt-4o"
    assert effective["elena"]["temperature"] == 0.7


def test_agent_model_changes_preserve_custom_prompt(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"maya": {"custom_prompt": "Stay in Maya persona.", "use_global_model": True}}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)
    response = client.post("/api/agents/maya/config", json={"use_global_connection": True, "use_global_model": False, "model": "gpt-5.5"})
    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["maya"]
    assert saved["custom_prompt"] == "Stay in Maya persona."
    assert saved["model"] == "gpt-5.5"


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
    assert saved["use_global_generation_parameters"] is True
    assert saved["temperature"] == 0.15
    assert "provider" not in saved
    assert "model" not in saved


def test_product_judge_rejects_incompatible_global_model(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-5.5"}, "_agent_configs": {"product_judge": {"use_global_connection": True, "use_global_model": True}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/product_judge/effective-ai").json()
    assert effective["capability_validation"]["valid"] is False
    assert effective["capability_validation"]["reason"] == "Selected model is not eligible for Product Judge."


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
    client.post("/api/agents/elena/config", json={"use_global_model": True, "use_global_generation_parameters": False, "temperature": 0.7})
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


def test_legacy_agent_config_defaults_to_global_inheritance(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2}, "_agent_configs": {"alex": {}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    effective = client.get("/api/agents/alex/effective-ai").json()
    assert effective["use_global_connection"] is True
    assert effective["use_global_model"] is True
    assert effective["use_global_generation_parameters"] is True
    assert effective["temperature"] == 0.2


def test_per_agent_connection_model_parameters_are_independent(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "anthropic_key": "sk-secret", "_provider_connections": [_vision_connection(), _text_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2, "top_p": 0.8}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)
    alex = client.post("/api/agents/alex/config", json={"use_global_connection": False, "connection_id": "provider-anthropic", "use_global_model": False, "model": "claude-sonnet-4-20250514", "use_global_generation_parameters": False, "temperature": "0,25", "top_p": "0,85", "top_k": "40", "max_tokens": "1000"})
    assert alex.status_code == 200
    maya = client.post("/api/agents/maya/config", json={"use_global_connection": True, "use_global_model": False, "model": "gpt-5.5", "use_global_generation_parameters": True})
    assert maya.status_code == 200
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["alex"]["connection_id"] == "provider-anthropic"
    assert effective["alex"]["model"] == "claude-sonnet-4-20250514"
    assert effective["alex"]["temperature"] == 0.25
    assert effective["alex"]["top_p"] == 0.85
    assert effective["alex"]["top_k"] == 40
    assert effective["alex"]["max_tokens"] == 1000
    assert effective["maya"]["connection_id"] == "provider-openai"
    assert effective["maya"]["model"] == "gpt-5.5"
    assert effective["maya"]["temperature"] == 0.2
    saved = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]
    assert saved["alex"]["temperature"] == 0.25
    assert saved["maya"]["use_global_connection"] is True


def test_agent_config_validation_and_reset_are_scoped(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "anthropic_key": "sk-secret", "_provider_connections": [_vision_connection(), _text_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"maya": {"use_global_model": False, "model": "gpt-5.5"}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    assert client.post("/api/agents/alex/config", json={"use_global_connection": False, "connection_id": "missing"}).json()["detail"]["code"] == "agent_connection_not_found"
    assert client.post("/api/agents/alex/config", json={"use_global_connection": True, "use_global_model": False, "model": "missing-model"}).json()["detail"]["code"] == "agent_model_unavailable"
    assert client.post("/api/agents/alex/config", json={"use_global_generation_parameters": False, "temperature": 9}).json()["detail"]["code"] == "agent_parameters_invalid"
    assert client.post("/api/agents/alex/config", json={"use_global_generation_parameters": False, "top_p": "1,5"}).json()["detail"]["code"] == "agent_parameters_invalid"
    client.post("/api/agents/alex/config", json={"use_global_connection": False, "connection_id": "provider-anthropic", "use_global_model": False, "model": "model-b", "use_global_generation_parameters": False, "temperature": 0.4})
    client.post("/api/agents/alex/config", json={"reset_to_defaults": True})
    effective = client.get("/api/agents/effective-ai").json()
    assert effective["alex"]["use_global_connection"] is True
    assert effective["alex"]["use_global_model"] is True
    assert effective["alex"]["use_global_generation_parameters"] is True
    assert effective["maya"]["model"] == "gpt-5.5"


def test_global_updates_only_affect_inheriting_agents(monkeypatch, tmp_path):
    cfg = {
        "openai_key": "sk-secret",
        "anthropic_key": "sk-secret",
        "_provider_connections": [_vision_connection(), _text_connection()],
        "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o", "temperature": 0.2, "top_p": 0.8},
        "_agent_configs": {
            "alex": {"use_global_connection": False, "connection_id": "provider-anthropic", "provider": "anthropic", "use_global_model": False, "model": "claude-sonnet-4-20250514", "use_global_generation_parameters": False, "temperature": 0.25, "top_p": 0.85, "top_k": 40, "max_tokens": 1000},
            "maya": {"use_global_connection": True, "use_global_model": True, "use_global_generation_parameters": True},
        },
    }
    client, config_path = _client(monkeypatch, tmp_path, cfg)
    client.post("/api/config/ai/global", json={"connection_id": "provider-openai", "provider": "openai", "model": "gpt-5.5", "temperature": 0.55, "top_p": 0.9, "top_k": None, "max_tokens": 2000, "enabled": True})

    effective = client.get("/api/agents/effective-ai").json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]

    assert effective["alex"]["connection_id"] == "provider-anthropic"
    assert effective["alex"]["model"] == "claude-sonnet-4-20250514"
    assert effective["alex"]["temperature"] == 0.25
    assert effective["maya"]["connection_id"] == "provider-openai"
    assert effective["maya"]["model"] == "gpt-5.5"
    assert effective["maya"]["temperature"] == 0.55
    assert effective["maya"]["max_tokens"] == 2000
    assert saved["alex"]["top_p"] == 0.85
    assert saved["alex"]["max_tokens"] == 1000


def test_agent_config_rejects_invalid_integer_parameters_and_unknown_agent(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)

    top_k = client.post("/api/agents/alex/config", json={"use_global_generation_parameters": False, "top_k": "0"})
    max_tokens = client.post("/api/agents/alex/config", json={"use_global_generation_parameters": False, "max_tokens": "1.5"})
    unknown = client.post("/api/agents/unknown/config", json={"use_global_model": True})

    assert top_k.json()["detail"]["code"] == "agent_parameters_invalid"
    assert max_tokens.json()["detail"]["code"] == "agent_parameters_invalid"
    assert unknown.status_code == 404


def test_agent_api_responses_do_not_expose_api_keys(monkeypatch, tmp_path):
    secret = "sk-secret-never-return"
    cfg = {"openai_key": secret, "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)

    assert secret not in client.get("/api/agents").text
    assert secret not in client.get("/api/agents/alex/config").text


def test_product_judge_compatibility_uses_effective_override(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "anthropic_key": "sk-secret", "_provider_connections": [_vision_connection("openai", "gpt-4o"), _text_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"product_judge": {"use_global_connection": False, "connection_id": "provider-anthropic", "provider": "anthropic", "use_global_model": False, "model": "claude-sonnet-4-20250514"}}}
    client, _ = _client(monkeypatch, tmp_path, cfg)

    effective = client.get("/api/agents/product_judge/effective-ai").json()

    assert effective["connection_id"] == "provider-anthropic"
    assert effective["model"] == "claude-sonnet-4-20250514"
    assert effective["capability_validation"]["valid"] is False
    assert effective["capability_validation"]["reason"] == "Selected model is not eligible for Product Judge."


def test_opencode_model_override_uses_agent_effective_model(monkeypatch, tmp_path):
    import opencode_bridge
    cfg = {"openai_key": "sk-secret", "_provider_connections": [_vision_connection()], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-4o"}, "_agent_configs": {"codex": {"use_global_connection": True, "use_global_model": False, "model": "gpt-5.5"}}}
    _client(monkeypatch, tmp_path, cfg)
    bridge = opencode_bridge.OpencodeBridge()
    bridge._binary = "opencode"
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode")
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("blocked test process")))
    result = bridge._run_cli_task(str(tmp_path), "system", "user", model_override=main.agent_opencode_model_override("codex"))
    command = result["command_shape"]
    assert command[command.index("--model") + 1] == "openai/gpt-5.5"


def test_legacy_opencode_global_model_reference_is_migrated(monkeypatch, tmp_path):
    connection = _text_connection("opencode_bridge", "nvidia/deepseek-ai/deepseek-v4-pro")
    connection["connection_id"] = "opencode_bridge"
    connection["connection_type"] = "opencode_oauth_bridge"
    connection["provider"] = "opencode_bridge"
    cfg = {"_provider_connections": [connection], "_global_ai": {"connection_id": "opencode_bridge", "provider": "opencode_bridge", "connection_type": "opencode_oauth_bridge", "model": "opencode_bridge/nvidia/deepseek-ai/deepseek-v4-pro"}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)

    effective = client.get("/api/agents/codex/effective-ai").json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert effective["model"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert saved["_global_ai"]["model"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert saved["_system"]["global_model"] == "nvidia/deepseek-ai/deepseek-v4-pro"


def test_legacy_opencode_agent_model_reference_is_migrated_idempotently(monkeypatch, tmp_path):
    connection = _text_connection("opencode_bridge", "nvidia/deepseek-ai/deepseek-v4-pro")
    connection["connection_id"] = "opencode_bridge"
    connection["connection_type"] = "opencode_oauth_bridge"
    connection["provider"] = "opencode_bridge"
    cfg = {"_provider_connections": [connection], "_global_ai": {"connection_id": "opencode_bridge", "provider": "opencode_bridge", "connection_type": "opencode_oauth_bridge", "model": "nvidia/deepseek-ai/deepseek-v4-pro"}, "_agent_configs": {"alex": {"use_global_connection": False, "connection_id": "opencode_bridge", "use_global_model": False, "model": "opencode_bridge/nvidia/deepseek-ai/deepseek-v4-pro"}}}
    client, config_path = _client(monkeypatch, tmp_path, cfg)

    first = client.get("/api/agents/alex/effective-ai").json()
    second = client.get("/api/agents/alex/effective-ai").json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["alex"]

    assert first["model"] == second["model"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert saved["model"] == "nvidia/deepseek-ai/deepseek-v4-pro"


def test_opencode_command_never_uses_internal_connection_prefix(tmp_path, monkeypatch):
    import opencode_bridge
    bridge = opencode_bridge.OpencodeBridge()
    bridge._binary = "opencode"
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("blocked test process")))
    result = bridge._run_cli_task(str(tmp_path), "system", "user", model_override="opencode_bridge/nvidia/deepseek-ai/deepseek-v4-pro")
    command = result["command_shape"]
    assert command[command.index("--model") + 1] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert "opencode_bridge/nvidia" not in " ".join(command)
    assert "--auto" not in command
    assert "--pure" not in command


def test_global_selector_lists_text_capable_non_vision_connection(monkeypatch, tmp_path):
    connection = _vision_connection("openai", "gpt-4o")
    connection["available_models"] = [{"id": "gpt-5.5", "capabilities": {"text_input": True, "image_input": None}}]
    connection["capability_metadata"] = {"gpt-5.5": {"text_input": True, "image_input": None}}
    cfg = {"openai_key": "sk-secret", "_provider_connections": [connection], "_global_ai": {"connection_id": "provider-openai", "provider": "openai", "model": "gpt-5.5"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    data = client.get("/api/config/ai/global").json()
    assert data["connections"][0]["connection_id"] == "provider-openai"
    models = client.get("/api/provider-connections/provider-openai/models").json()["models"]
    assert models[0]["id"] == "gpt-5.5"
    assert models[0]["capabilities"]["image_input"] is None


def test_legacy_global_provider_model_is_displayed_safely(monkeypatch, tmp_path):
    cfg = {"openai_key": "sk-secret", "_system": {"global_provider": "openai", "global_model": "gpt-5.5"}}
    client, _ = _client(monkeypatch, tmp_path, cfg)
    data = client.get("/api/config/ai/global").json()
    assert data["global_ai"]["provider"] == "openai"
    assert data["global_ai"]["model"] == "gpt-5.5"
    assert any(c["connection_id"] == "provider-openai" for c in data["connections"])


def test_settings_global_ai_ui_uses_styled_cards_and_no_raw_checkboxes():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    global_section = source.split("function GlobalAIInheritanceSettings", 1)[1].split("function AgentAIOverridesSettings", 1)[0]
    assert 'input type="checkbox"' not in global_section
    assert "Provider connection" in global_section
    assert "Default model" in global_section
    assert "No tested text-capable models are available for this connection" in global_section
    assert "Applied successfully:" in global_section
    assert "Save agent settings" not in global_section
    assert "Use global connection" not in global_section
    assert "setModel(next?.available_models" not in global_section
    assert "setModel(''); loadModels(e.target.value)" in global_section
    assert "m.unavailable ? ' (unavailable for this connection)'" in global_section


def test_settings_layout_expands_and_animation_speed_ui_removed():
    settings_source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    dashboard_source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    assert "settings-window-wide" in settings_source
    assert "max-w-2xl" not in settings_source
    assert "animation_speed" not in settings_source
    assert "workspaceWide" in dashboard_source
    assert "workspace-wide" in dashboard_source
    assert ".fs-shell.workspace-wide .fs-body" in css


def test_ai_connections_exposes_subscription_provider_templates():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    section = source.split("function UniversalProviderConnectionsSettings", 1)[1].split("function GlobalAIInheritanceSettings", 1)[0]
    assert "Add provider" in section
    assert "ChatGPT / OpenAI via OpenCode subscription" in section
    assert "ChatGPT Subscription (Codex CLI)" in section
    assert "codex_chatgpt_subscription" in section
    assert "opencode_provider" in section
    assert "OpenAI API Key" in section


def test_settings_agent_override_fields_are_labeled_and_model_temp_separated():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    global_section = source.split("function GlobalAIInheritanceSettings", 1)[1].split("function AgentAIOverridesSettings", 1)[0]
    agent_section = source.split("function AgentModelManagerSettings", 1)[1].split("function AgentRoleContractsSettings", 1)[0]
    for label in ("temperature", "top_p", "top_k", "max tokens"):
        assert f"<label>Default {label}" in global_section
    for label in ("Temperature", "Top_p", "Top_k", "Max tokens"):
        assert f"<label>{label}" in agent_section
    assert "Use Default" in agent_section
    assert "Use global parameters" in agent_section
    assert "disabled={disabled || d.use_global_generation_parameters}" in agent_section
    assert "Authenticate Provider" in agent_section
    assert "subscription/delegated" in agent_section


def test_settings_agent_override_ui_has_connection_and_model_selectors():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    section = source.split("function AgentModelManagerSettings", 1)[1].split("function AgentRoleContractsSettings", 1)[0]
    assert "Agent Model Manager" in section
    assert "Search providers" in section
    assert "Search models" in section
    assert "selectedAgentId" in section
    assert "entries.map(([agentId, item])" in section
    assert "Use Default" in section
    assert "Provider" in section
    assert "Model" in section
    assert "Authenticate Provider" in section
    assert "Test Connection" in section
    assert "Save AI Choice" in section
    assert "Current provider" in section
    assert "Current model" in section
    assert "Agent Prompt" in section
    assert "Reset to Default Prompt" in section


def test_settings_agent_override_state_is_scoped_and_preserves_native_models():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    section = source.split("function AgentModelManagerSettings", 1)[1].split("function AgentRoleContractsSettings", 1)[0]
    assert "fetch(`http://localhost:${activePort}/api/agents/${id}/config`" in section
    assert "setSelectedAgentId(current => current && agentData?.[current]" in section
    assert "model: models[0]?.id" not in section
    assert "setDraft(id, { use_global_connection: false" in section
    assert "modelUnavailable" in section
    assert "model: `${" not in section
    assert "model: connectionId" not in section
    assert "custom_prompt" not in section.split("const saveAgent", 1)[1].split("const savePrompt", 1)[0]


def test_settings_toggle_markup_is_accessible_button_switch():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    toggle_section = source.split("function Toggle", 1)[1].split("function StoragePathsSettings", 1)[0]
    assert '<button' in toggle_section
    assert 'role="switch"' in toggle_section
    assert "aria-label={label}" in toggle_section
    assert "aria-checked={checked}" in toggle_section
    assert "disabled={disabled}" in toggle_section
    assert "onKeyDown" in toggle_section


def test_product_judge_chat_uses_product_judge_identity():
    normal_prompt = main.get_agent_prompt("product_judge")
    fast_prompt = main.get_fast_agent_prompt("product_judge")
    assert "You are Product Judge" in normal_prompt
    assert "You are Product Judge" in fast_prompt
    assert "professional AI assistant" not in normal_prompt
    assert "professional AI assistant" not in fast_prompt


def test_alex_prompt_intake_gates_codex_assignment():
    normal_prompt = main.get_agent_prompt("alex")
    fast_prompt = main.get_fast_agent_prompt("alex")
    for prompt in (normal_prompt, fast_prompt):
        assert "Before assigning Codex, run prompt intake" in prompt
        assert "desktop packaging" in prompt
        assert "AI/RAG requirements" in prompt
        assert "voice input/output" in prompt
        assert "ready-to-send Codex brief" in prompt
        assert "Codex may receive implementation only after" in prompt
