import json
from pathlib import Path

import config_storage
import opencode_bridge


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_preferred_provider_model_reads_system_from_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"_system": {"global_provider": "anthropic", "global_model": "claude-sonnet-4-20250514"}})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(opencode_bridge, "_opencode_auth_provider_ids", lambda: set())

    assert opencode_bridge._preferred_provider_model() == ("anthropic", "claude-sonnet-4-20250514")


def test_get_studio_config_reads_provider_connections_from_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    connections = [{"connection_id": "oc-test", "connection_type": "opencode_bridge"}]
    _write_config(config_path, {"_provider_connections": connections})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert opencode_bridge._get_studio_config()["_provider_connections"] == connections


def test_legacy_provider_key_fallback_is_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_path = tmp_path / "studio_config.json"
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir()
    _write_config(config_path, {"_system": {"global_provider": "openai", "global_model": "gpt-5.5"}, "openai_api_key": "sk-legacy"})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(opencode_bridge, "_get_opencode_config_dir", lambda: str(opencode_dir))
    monkeypatch.setattr(opencode_bridge, "_opencode_auth_provider_ids", lambda: set())

    assert opencode_bridge._ensure_opencode_config() is True

    generated = json.loads((opencode_dir / "opencode.json").read_text(encoding="utf-8"))
    assert generated["model"] == "openai/gpt-5.5"
    assert generated["provider"]["openai"]["options"]["apiKey"] == "sk-legacy"


def test_missing_or_invalid_config_keeps_existing_default_behavior(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing.json"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(opencode_bridge, "_opencode_auth_provider_ids", lambda: set())

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(missing_path))
    assert opencode_bridge._preferred_provider_model() == ("nvidia", "meta/llama-3.3-70b-instruct")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(invalid_path))
    assert opencode_bridge._preferred_provider_model() == ("nvidia", "meta/llama-3.3-70b-instruct")


def test_config_storage_monkeypatch_affects_opencode_status(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    _write_config(config_path, {"_system": {"global_provider": "mistral", "global_model": "mistral-large-latest"}})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: None)
    monkeypatch.setattr(opencode_bridge, "_get_opencode_config_dir", lambda: str(tmp_path / "opencode"))
    monkeypatch.setattr(opencode_bridge, "_opencode_auth_provider_ids", lambda: set())

    status = opencode_bridge.get_opencode_status()

    assert status["installed"] is False
    assert status["selected_provider"] == "mistral"
    assert status["selected_model"] == "mistral-large-latest"
    assert status["effective_provider"] == "mistral"


def test_opencode_bridge_provider_uses_native_model_provider(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir()
    _write_config(config_path, {"_system": {"global_provider": "opencode_bridge", "global_model": "nvidia/deepseek-ai/deepseek-v4-pro"}})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(opencode_bridge, "_get_opencode_config_dir", lambda: str(opencode_dir))
    monkeypatch.setattr(opencode_bridge, "_opencode_auth_provider_ids", lambda: set())

    assert opencode_bridge._ensure_opencode_config() is True

    generated = json.loads((opencode_dir / "opencode.json").read_text(encoding="utf-8"))
    assert generated["model"] == "nvidia/deepseek-ai/deepseek-v4-pro"
    assert "opencode_bridge/nvidia" not in json.dumps(generated)


def test_opencode_bridge_has_no_direct_studio_config_file_read():
    source = Path(opencode_bridge.__file__).read_text(encoding="utf-8")

    assert "studio_config.json" not in source
    assert "studio_cfg_path" not in source
    assert "load_studio_keys()" in source


def test_opencode_config_dir_defaults_to_portable_studio_home(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FREELANCERSTUDIO_HOME", str(tmp_path))

    config_dir = opencode_bridge._get_opencode_config_dir()

    assert config_dir == str(tmp_path / ".opencode")


def test_opencode_config_dir_allows_explicit_absolute_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-opencode-config"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(override))

    config_dir = opencode_bridge._get_opencode_config_dir()

    assert config_dir == str(override)
