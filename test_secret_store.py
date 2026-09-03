import json

import pytest
from fastapi.testclient import TestClient

import config_storage
import main
from test_security_support import authorized_test_client
import secret_store


pytestmark = pytest.mark.unit


def _client(monkeypatch, tmp_path, config=None):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps(config or {}, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    return authorized_test_client(main.app), config_path


def test_env_secret_store_reads_provider_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-secret")

    assert secret_store.EnvSecretStore().get("openai_key") == "sk-env-secret"
    assert secret_store.get_secret("openai_key", {"openai_key": "sk-legacy-secret"}) == "sk-env-secret"


def test_env_secret_store_reads_xai_api_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-env-secret")

    assert secret_store.EnvSecretStore().get("xai_key") == "xai-env-secret"
    assert secret_store.env_name_for_secret("xai_key") == "XAI_API_KEY"


def test_env_secret_store_reads_openrouter_api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env-secret")

    assert secret_store.EnvSecretStore().get("openrouter_key") == "sk-or-env-secret"
    assert secret_store.env_name_for_secret("openrouter_key") == "OPENROUTER_API_KEY"


def test_legacy_secret_warning_is_safe(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = secret_store.secret_status("openai_key", {"openai_key": "sk-legacy-secret"})

    assert status["saved"] is True
    assert status["source"] == "legacy_config"
    assert status["masked"] == "sk-l********cret"
    assert "sk-legacy-secret" not in json.dumps(status)
    assert status["legacy_warning"]["env"] == "OPENAI_API_KEY"


def test_save_ai_settings_does_not_write_new_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, config_path = _client(monkeypatch, tmp_path, {})

    response = client.post("/api/config/ai", json={"provider": "openai", "model": "gpt-5.5", "api_key": "sk-new-secret"}).json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert response["status"] == "saved"
    assert "secret_store_warning" in response
    assert "sk-new-secret" not in response["secret_store_warning"]
    assert "openai_key" not in saved
    assert "sk-new-secret" not in config_path.read_text(encoding="utf-8")


def test_provider_test_uses_transient_key_without_persisting(monkeypatch, tmp_path):
    captured = {}
    client, config_path = _client(monkeypatch, tmp_path, {"_system": {"global_provider": "openai", "global_model": "gpt-5.5"}})
    monkeypatch.setattr(main, "_test_provider_key", lambda provider, key: captured.update(provider=provider, key=key) or (True, "Provider key accepted"))

    response = client.post("/api/config/ai/test", json={"provider": "openai", "api_key": "sk-transient-secret"}).json()

    assert response["status"] == "ok"
    assert captured == {"provider": "openai", "key": "sk-transient-secret"}
    assert "secret_store_warning" in response
    assert "sk-transient-secret" not in json.dumps(response)
    assert "sk-transient-secret" not in config_path.read_text(encoding="utf-8")


def test_legacy_secret_is_reported_without_value(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, _ = _client(monkeypatch, tmp_path, {"openai_key": "sk-legacy-secret"})

    response = client.get("/api/config/ai").json()

    assert response["saved_keys"]["openai"]["source"] == "legacy_config"
    assert response["legacy_secret_warnings"][0]["env"] == "OPENAI_API_KEY"
    assert "sk-legacy-secret" not in json.dumps(response)


def test_generic_key_endpoint_skips_secret_writes(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path, {})

    response = client.post("/api/config/keys", json={"keys": {"openai_key": "sk-new-secret", "display_name": "Studio"}}).json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert response["status"] == "saved"
    assert response["skipped_secrets"] == ["openai_key"]
    assert saved == {"display_name": "Studio"}
    assert "sk-new-secret" not in config_path.read_text(encoding="utf-8")


def test_github_token_is_not_written_and_env_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-env-secret")
    client, config_path = _client(monkeypatch, tmp_path, {})

    response = client.post("/api/config/github", json={"token": "ghp-new-secret", "username": "octo", "repo": "repo"}).json()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    loaded = client.get("/api/config/github").json()

    assert response["connected"] is True
    assert response["secret_store_warning"]
    assert saved["_github"] == {"token": "", "username": "octo", "repo": "repo"}
    assert loaded["token"] is True
    assert "ghp-env-secret" not in json.dumps(response)
    assert "ghp-new-secret" not in config_path.read_text(encoding="utf-8")


def test_legacy_secret_cleanup_preserves_settings_and_is_idempotent(monkeypatch, tmp_path):
    config = {
        "display_name": "Studio",
        "openai_key": "redacted",
        "freelancer_client_id": "client",
        "freelancer_client_secret": "redacted",
        "_github": {"token": "redacted", "username": "octo"},
        "_accounts": [{"id": "account-1", "credentials": {"token": "redacted"}, "label": "Work"}],
        "_provider_connections": [{"connection_id": "provider-1", "nested": [{"access-token": "redacted", "model": "kept"}]}],
    }
    client, config_path = _client(monkeypatch, tmp_path, config)

    response = client.post("/api/config/legacy-secrets/cleanup")
    cleaned = json.loads(config_path.read_text(encoding="utf-8"))

    assert response.json() == {
        "removed_fields": 5,
        "categories": ["account_credentials", "github_token", "provider_connection_credentials", "provider_keys"],
    }
    assert cleaned == {
        "display_name": "Studio",
        "freelancer_client_id": "client",
        "_github": {"username": "octo"},
        "_accounts": [{"id": "account-1", "label": "Work"}],
        "_provider_connections": [{"connection_id": "provider-1", "nested": [{"model": "kept"}]}],
    }
    assert len(list(tmp_path.glob("studio_config.legacy-secrets-*.json.bak"))) == 1
    assert client.post("/api/config/legacy-secrets/cleanup").json() == {"removed_fields": 0, "categories": []}
