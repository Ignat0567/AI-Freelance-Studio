import json
from pathlib import Path

from fastapi.testclient import TestClient

import config_storage
import main


def _write_config(path: Path, data: dict):
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
    _write_config(config_path, config)
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setitem(main.SYSTEM_SETTINGS, "global_provider", config.get("_system", {}).get("global_provider", "openai"))
    monkeypatch.setitem(main.SYSTEM_SETTINGS, "global_model", config.get("_system", {}).get("global_model", "gpt-4o"))
    main.agent_configs = main.load_agent_configs()
    return TestClient(main.app), config_path


def test_no_saved_connection_has_useful_empty_state(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, {"_agent_configs": {}, "_system": {"global_provider": "openai", "global_model": "gpt-4o"}})
    data = client.get("/api/product-judge/config").json()
    assert data["connections"][0]["connection_id"] == "provider-openai"
    assert data["connections"][0]["configured_status"] == "credential_missing"
    assert data["status"]["status"] == "not_configured"


def test_tested_vision_connection_appears_and_non_vision_model_is_excluded(monkeypatch, tmp_path):
    config = {"openai_key": "sk-test-secret", "_agent_configs": {}, "_provider_connections": [_vision_connection()]}
    client, _ = _client(monkeypatch, tmp_path, config)
    data = client.get("/api/product-judge/config").json()
    assert [item["connection_id"] for item in data["connections"]] == ["provider-openai"]
    models = client.get("/api/provider-connections/provider-openai/models?image_input=true").json()["models"]
    assert [item["id"] for item in models] == ["gpt-4o"]


def test_model_list_updates_after_connection_selection(monkeypatch, tmp_path):
    anthropic = _vision_connection("anthropic", "claude-sonnet-4-20250514")
    config = {"openai_key": "sk-test-secret", "anthropic_key": "anthropic-secret", "_agent_configs": {}, "_provider_connections": [_vision_connection(), anthropic]}
    client, _ = _client(monkeypatch, tmp_path, config)
    openai_models = client.get("/api/provider-connections/provider-openai/models?image_input=true").json()["models"]
    anthropic_models = client.get("/api/provider-connections/provider-anthropic/models?image_input=true").json()["models"]
    assert openai_models[0]["id"] == "gpt-4o"
    assert anthropic_models[0]["id"] == "claude-sonnet-4-20250514"


def test_saved_product_judge_config_survives_restart(monkeypatch, tmp_path):
    config = {"openai_key": "sk-test-secret", "_agent_configs": {}, "_provider_connections": [_vision_connection()]}
    client, config_path = _client(monkeypatch, tmp_path, config)
    payload = {"enabled": True, "connection_id": "provider-openai", "model": "gpt-4o", "use_global": False, "temperature": 0.3, "top_p": 0.9, "top_k": None}
    assert client.post("/api/product-judge/config", json=payload).json()["status"] == "success"
    reloaded = json.loads(config_path.read_text(encoding="utf-8"))["_agent_configs"]["product_judge"]
    main.agent_configs = main.load_agent_configs()
    assert reloaded["connection_id"] == "provider-openai"
    assert main.agent_configs["product_judge"]["model"] == "gpt-4o"


def test_missing_credential_produces_specific_unavailable_reason(monkeypatch, tmp_path):
    connection = _vision_connection()
    connection["configured_status"] = "credential_missing"
    config = {"_agent_configs": {"product_judge": {"connection_id": "provider-openai", "model": "gpt-4o", "enabled": True}}, "_provider_connections": [connection]}
    client, _ = _client(monkeypatch, tmp_path, config)
    status = client.get("/api/product-judge/config").json()["status"]
    assert status["status"] == "credential_missing"
    assert status["reason"] == "credential is missing"


def test_failed_provider_test_does_not_mark_connection_as_proven(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, {"openai_key": "sk-test-secret", "_system": {"global_provider": "openai", "global_model": "gpt-4o"}})
    monkeypatch.setattr(main, "_test_provider_key", lambda _provider, _key: (False, "Provider rejected key or request: HTTP 401"))
    assert client.post("/api/config/ai/test", json={"provider": "openai"}).json()["status"] == "error"
    assert client.get("/api/provider-connections/vision").json()["connections"] == []


def test_successful_provider_test_refreshes_product_judge_options(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, {"openai_key": "sk-test-secret", "_system": {"global_provider": "openai", "global_model": "gpt-5.5"}})
    monkeypatch.setattr(main, "_test_provider_key", lambda _provider, _key: (True, "Provider key accepted"))
    response = client.post("/api/config/ai/test", json={"provider": "openai"}).json()
    assert response["status"] == "ok"
    options = client.get("/api/product-judge/config").json()["connections"]
    assert options[0]["connection_id"] == "provider-openai"
    assert any(model["id"] == "gpt-4o" for model in options[0]["available_models"])


def test_configured_untested_connection_is_selectable_but_not_ready(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, {"openai_key": "sk-test-secret", "_system": {"global_provider": "openai", "global_model": "gpt-4o"}})
    client.post("/api/config/ai", json={"provider": "openai", "model": "gpt-4o", "api_key": ""})
    data = client.get("/api/product-judge/config").json()
    assert data["connections"][0]["connection_id"] == "provider-openai"
    assert data["connections"][0]["tested_status"] == "not_tested"
    assert any(model["id"] == "gpt-4o" and model["capabilities"]["image_input"] is True for model in data["connections"][0]["available_models"])
    readiness = client.post("/api/product-judge/test", json={"enabled": True, "connection_id": "provider-openai", "model": "gpt-4o", "use_global": False}).json()
    assert readiness["status"] == "provider_not_tested"


def test_same_model_fallback_records_same_model_separate_role(monkeypatch, tmp_path):
    config = {"openai_key": "sk-test-secret", "_agent_configs": {"elena": {"use_global": False, "provider": "openai", "model": "gpt-4o"}}, "_provider_connections": [_vision_connection()]}
    client, _ = _client(monkeypatch, tmp_path, config)
    payload = {"enabled": True, "connection_id": "provider-openai", "model": "gpt-4o", "use_global": False}
    readiness = client.post("/api/product-judge/config", json=payload).json()["readiness"]
    assert readiness["independence_level"] == "same_model_separate_role"


def test_different_provider_selection_records_different_provider(monkeypatch, tmp_path):
    anthropic = _vision_connection("anthropic", "claude-sonnet-4-20250514")
    config = {"anthropic_key": "secret", "_agent_configs": {"elena": {"use_global": False, "provider": "openai", "model": "gpt-4o"}}, "_provider_connections": [anthropic]}
    client, _ = _client(monkeypatch, tmp_path, config)
    payload = {"enabled": True, "connection_id": "provider-anthropic", "model": "claude-sonnet-4-20250514", "use_global": False}
    readiness = client.post("/api/product-judge/config", json=payload).json()["readiness"]
    assert readiness["independence_level"] == "different_provider"


def test_raw_api_key_is_not_returned_to_frontend(monkeypatch, tmp_path):
    secret = "sk-test-secret-value-never-return"
    client, _ = _client(monkeypatch, tmp_path, {"openai_key": secret, "_provider_connections": [_vision_connection()]})
    assert secret not in client.get("/api/provider-connections").text
    assert secret not in client.get("/api/product-judge/config").text


def test_save_product_judge_updates_backend_state_and_readiness(monkeypatch, tmp_path):
    config = {"openai_key": "sk-test-secret", "_agent_configs": {}, "_provider_connections": [_vision_connection()]}
    client, _ = _client(monkeypatch, tmp_path, config)
    payload = {"enabled": True, "connection_id": "provider-openai", "model": "gpt-4o", "use_global": False, "temperature": 0.4, "top_p": 0.8, "top_k": None}
    saved = client.post("/api/product-judge/config", json=payload).json()
    assert saved["readiness"]["status"] == "available"
    loaded = client.get("/api/product-judge/config").json()
    assert loaded["config"]["model"] == "gpt-4o"
    assert loaded["status"]["available"] is True


def test_product_judge_readiness_test_reports_correctly(monkeypatch, tmp_path):
    config = {"openai_key": "sk-test-secret", "_agent_configs": {"product_judge": {"connection_id": "provider-openai", "model": "gpt-4o", "enabled": True}}, "_provider_connections": [_vision_connection()]}
    client, _ = _client(monkeypatch, tmp_path, config)
    result = client.post("/api/product-judge/test", json={"enabled": True, "connection_id": "provider-openai", "model": "gpt-4o", "use_global": False}).json()
    assert result["test_result"] == "ready"
    assert result["available"] is True


def test_settings_modal_remains_scrollable_and_closable():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    assert "settings-modal-body" in source
    assert "overflow-y: auto" in css
    assert "onCloseRef.current()" in source
    assert "Save Product Judge" in source
    assert "Test Product Judge" in source
    assert "disabled={!connections.length" not in source
    assert "disabled={!models.length" not in source
