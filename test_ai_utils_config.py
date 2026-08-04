import json
from pathlib import Path

import config_storage
import ai_utils


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_provider_key_is_read_from_config_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"openai_key": "sk-from-config"})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert ai_utils.get_api_key("openai") == "sk-from-config"


def test_legacy_api_key_fallback_is_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"openai_api_key": "sk-legacy-config"})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert ai_utils.get_api_key("openai") == "sk-legacy-config"


def test_opencode_provider_connections_are_read_from_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {
        "_provider_connections": [{
            "connection_type": "opencode_bridge",
            "enabled": True,
            "configured_model": "openai/gpt-5.5",
        }],
    })
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    captured = {}

    class FakeOpenCodeBridgeConnection:
        @classmethod
        def from_dict(cls, data):
            captured["connection"] = data
            return cls()

        def execute(self, payload):
            captured["payload"] = payload
            return {"status": "success", "text": "bridge-ok"}

    monkeypatch.setattr("opencode_provider.OpenCodeBridgeConnection", FakeOpenCodeBridgeConnection)

    result = ai_utils.ask_studio_ai_with_history(
        "opencode_bridge",
        "openai/gpt-5.5",
        "system",
        [{"role": "user", "content": "hello"}],
    )

    assert result == "bridge-ok"
    assert captured["connection"]["connection_type"] == "opencode_bridge"
    assert captured["payload"]["user_content"] == "hello"


def test_missing_or_invalid_config_returns_existing_empty_key_behavior(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    missing_path = tmp_path / "missing.json"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{invalid", encoding="utf-8")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(missing_path))
    assert ai_utils.get_api_key("openai") == ""

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(invalid_path))
    assert ai_utils.get_api_key("openai") == ""
    assert ai_utils.ask_studio_ai_with_history("opencode_bridge", "openai/gpt-5.5", "system", []) == "OpenCode OAuth is not configured yet. Open Settings -> AI Provider and select Authenticate Provider."


def test_opencode_direct_oauth_uses_requested_model_when_saved_connection_is_stale(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {
        "_provider_connections": [{
            "connection_id": "opencode-live",
            "connection_type": "opencode_oauth_bridge",
            "enabled": True,
            "configured_model": "nvidia/old-model",
        }],
        "_global_ai": {"provider": "opencode_bridge", "model": "openai/gpt-5.5"},
    })
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    captured = {}

    class FakeOpenCodeBridgeConnection:
        @classmethod
        def from_dict(cls, data):
            captured["connection"] = data
            return cls()

        def execute(self, payload):
            captured["payload"] = payload
            return {"status": "success", "text": "direct-ok"}

    monkeypatch.setattr("opencode_provider.OpenCodeBridgeConnection", FakeOpenCodeBridgeConnection)
    result = ai_utils.ask_studio_ai_with_history("opencode_bridge", "openai/gpt-5.5", "system", [{"role": "user", "content": "hi"}])
    assert result == "direct-ok"
    assert captured["connection"]["configured_model"] == "openai/gpt-5.5"
    assert captured["payload"]["requested_model"] == "openai/gpt-5.5"


def test_ai_utils_uses_config_storage_without_direct_studio_config_reads():
    source = Path(ai_utils.__file__).read_text(encoding="utf-8")

    assert "studio_config.json" not in source
    assert "load_studio_keys()" in source
    assert "with open(" not in source
