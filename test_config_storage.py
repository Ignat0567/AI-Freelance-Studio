import json

import pytest

import config_storage
import main


def test_config_storage_config_file_is_used_at_each_call(monkeypatch, tmp_path):
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps({"value": "first"}), encoding="utf-8")
    second_path.write_text(json.dumps({"value": "second"}), encoding="utf-8")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(first_path))
    assert config_storage.load_studio_keys() == {"value": "first"}

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(second_path))
    assert config_storage.load_studio_keys() == {"value": "second"}


def test_main_load_studio_keys_uses_config_storage_config_file(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps({"_system": {"theme": "light"}}), encoding="utf-8")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    assert main.load_studio_keys() == {"_system": {"theme": "light"}}


def test_main_save_studio_keys_uses_config_storage_config_file(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    main.save_studio_keys({"name": "Studio"})

    assert json.loads(config_path.read_text(encoding="utf-8")) == {"name": "Studio"}


def test_load_studio_keys_returns_empty_for_missing_and_invalid_json(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing.json"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{invalid", encoding="utf-8")

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(missing_path))
    assert config_storage.load_studio_keys() == {}

    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(invalid_path))
    assert config_storage.load_studio_keys() == {}


def test_save_studio_keys_preserves_unicode_without_ascii_escaping(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    config_storage.save_studio_keys({"language": "Русский"})

    raw = config_path.read_text(encoding="utf-8")

    assert "Русский" in raw
    assert "\\u0420" not in raw
    assert json.loads(raw) == {"language": "Русский"}


def test_save_studio_keys_propagates_write_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(tmp_path))

    with pytest.raises(OSError):
        config_storage.save_studio_keys({"value": "x"})


def test_reset_all_credentials_still_clears_entire_config(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps({"display_name": "Studio", "_system": {"theme": "light"}}), encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    response = main.reset_all_credentials()

    assert response == {"status": "reset"}
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_storage_supports_portable_home_source_contract():
    source = open("config_storage.py", "r", encoding="utf-8").read()

    assert 'os.environ.get("FREELANCERSTUDIO_HOME")' in source
