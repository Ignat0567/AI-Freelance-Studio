import json
from pathlib import Path

from fastapi.testclient import TestClient

import config_storage
import main
from test_security_support import authorized_test_client


def _client(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    return authorized_test_client(main.app), config_path


def _ready_result(model="openai/gpt-5.5"):
    return {
        "status": "ok",
        "ready": True,
        "error_code": "",
        "message": "ready",
        "checks": {
            "executable": {"status": "passed", "version": "1.17.11"},
            "server": {"status": "passed", "url": "http://127.0.0.1:45123"},
            "authentication": {"status": "passed", "providers": ["openai"], "auth_types": ["oauth"]},
            "models": {"status": "passed", "count": 1},
            "selection": {"status": "passed", "provider": "openai", "model": model},
        },
        "executable_path": r"C:\Tools\opencode.exe",
        "server_url": "http://127.0.0.1:45123",
        "authorized_providers": ["openai"],
        "auth_types": ["oauth"],
        "selected_auth_types": ["oauth"],
        "available_models": [model],
    }


def test_direct_post_cannot_bypass_failed_backend_readiness(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    calls = []
    writes = []
    monkeypatch.setattr(main, "test_opencode_readiness", lambda executable, model, endpoint: calls.append((executable, model, endpoint)) or {
        "status": "error", "ready": False, "error_code": "selected_model_unavailable", "message": "Selected model unavailable", "checks": {"selection": {"status": "failed"}},
    })
    monkeypatch.setattr(main, "save_studio_keys", lambda _data: writes.append(_data))

    response = client.post("/api/provider-connections/opencode", json={
        "name": "Bypass attempt",
        "configured_model": "openai/unavailable",
        "executable_path": r"C:\fake\opencode.exe",
        "local_endpoint": "http://127.0.0.1:49999",
        "capabilities": {"text_input": {"status": "supported"}},
        "last_checked_at": "forged",
    })

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "selected_model_unavailable"
    assert calls == [(r"C:\fake\opencode.exe", "openai/unavailable", "http://127.0.0.1:49999")]
    assert writes == []
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_successful_save_persists_only_verified_safe_metadata(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(main, "test_opencode_readiness", lambda executable, model, endpoint: calls.append((executable, model, endpoint)) or _ready_result())

    response = client.post("/api/provider-connections/opencode", json={
        "name": "Verified OpenCode",
        "configured_model": "openai/gpt-5.5",
        "executable_path": "opencode.cmd",
        "local_endpoint": "http://127.0.0.1:45123",
        "capabilities": {"text_input": {"status": "supported", "token": "do-not-store"}},
        "last_checked_at": "forged",
    })

    assert response.status_code == 200
    assert calls == [("opencode.cmd", "openai/gpt-5.5", "http://127.0.0.1:45123")]
    stored = json.loads(config_path.read_text(encoding="utf-8"))["_provider_connections"][0]
    assert set(stored) == {
        "connection_id", "connection_type", "name", "enabled", "executable_path",
        "local_endpoint", "server_port", "configured_provider", "configured_model",
        "auth_type", "auth_status", "readiness_status", "last_checked_at", "created_at", "updated_at",
    }
    assert stored["configured_provider"] == "openai"
    assert stored["configured_model"] == "openai/gpt-5.5"
    assert stored["auth_type"] == "oauth"
    assert stored["auth_status"] == "authenticated"
    assert stored["readiness_status"] == "ready"
    assert stored["last_checked_at"] == stored["created_at"] == stored["updated_at"]
    assert stored["last_checked_at"] != "forged"
    serialized = json.dumps(stored).lower()
    assert "do-not-store" not in serialized
    assert "capabilities" not in stored
    assert "available_models" not in stored


def test_save_rejects_secret_and_unknown_extra_fields(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_opencode_readiness", lambda *args: _ready_result())

    response = client.post("/api/provider-connections/opencode", json={
        "name": "Rejected extras",
        "configured_model": "openai/gpt-5.5",
        "api_key": "do-not-store",
    })

    assert response.status_code == 422
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_frontend_invalidates_readiness_and_sends_no_test_claims():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")

    assert source.count("setTested(null)") >= 2
    assert "disabled={busy || !tested?.ready}" in source
    assert "const payload = { ...draft };" in source
    assert "capabilities: tested.connection.capabilities" not in source
