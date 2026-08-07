import json
from pathlib import Path

import config_storage
import main


def test_production_sources_do_not_reference_removed_opencode_login_action():
    paths = [
        Path("main.py"),
        Path("frontend/src/components/OpenCodeConnectionSetup.jsx"),
        Path("frontend/src/components/SettingsModal.jsx"),
        Path("frontend/src/components/StudioDashboard.jsx"),
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "OpenCode Login" not in source
        assert "Complete OpenCode login" not in source


def test_recovery_flow_names_existing_frontend_actions():
    settings = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    connection = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")
    frontend = settings + connection

    for action in ("Download Node.js", "Download OpenCode", "Detect Again", "Authenticate Provider", "Repair automatically", "Test Connection", "Save Connection"):
        assert action in frontend


def test_frontend_does_not_require_start_web_for_cli_generation():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")
    assert "Start OpenCode Web before testing" not in source
    assert "Local server" in source
    assert "not_required" in source


def _write_config(monkeypatch, tmp_path, config):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    main.SYSTEM_SETTINGS.clear()
    main.SYSTEM_SETTINGS.update(main._get_saved_system_settings(config))
    main.agent_configs = main.load_agent_configs()
    monkeypatch.setattr(main, "_HAS_OPENCODE", True)
    return config_path


def _opencode_provider_connection(model="nvidia/gpt-4o", models=None):
    return {
        "connection_id": "provider-nvidia",
        "connection_type": "other_provider_api",
        "transport": "opencode",
        "provider": "nvidia",
        "name": "NVIDIA via OpenCode",
        "executable_path": "opencode.cmd",
        "configured_model": model,
        "available_models": models or [{"id": model, "capabilities": {"text_input": True, "image_input": None}}],
        "capability_metadata": {model: {"text_input": True, "image_input": None}},
        "enabled": True,
    }


def _readiness(error_code, models):
    return {
        "ready": False,
        "error_code": error_code,
        "message": "The selected OpenCode model is not available.",
        "available_models": models,
        "checks": {"server": {"status": "not_required"}},
    }


def test_preflight_detects_opencode_transport_without_literal_connection_id(monkeypatch, tmp_path):
    config_path = _write_config(monkeypatch, tmp_path, {
        "_provider_connections": [_opencode_provider_connection("nvidia/llama-3.3-70b-instruct")],
        "_global_ai": {"connection_id": "provider-nvidia", "provider": "nvidia", "connection_type": "other_provider_api", "model": "nvidia/llama-3.3-70b-instruct"},
    })
    calls = []

    def readiness(_exe, model, _endpoint):
        calls.append(model)
        if model == "nvidia/llama-3.3-70b-instruct":
            return _readiness("selected_model_unavailable", ["nvidia/meta/llama-3.3-70b-instruct"])
        return {"ready": True, "checks": {"server": {"status": "not_required"}}, "available_models": ["nvidia/meta/llama-3.3-70b-instruct"]}

    monkeypatch.setattr(main, "test_opencode_readiness", readiness)

    result = main._opencode_preflight_for_agent("codex")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ready"] is True
    assert result["automatic_repair"]["reason"] == "exact_legacy_alias"
    assert calls == ["nvidia/llama-3.3-70b-instruct", "nvidia/meta/llama-3.3-70b-instruct"]
    assert saved["_global_ai"]["model"] == "nvidia/meta/llama-3.3-70b-instruct"


def test_unavailable_model_without_unambiguous_fallback_returns_selector(monkeypatch, tmp_path):
    config_path = _write_config(monkeypatch, tmp_path, {
        "_provider_connections": [_opencode_provider_connection("nvidia/gpt-4o")],
        "_global_ai": {"connection_id": "provider-nvidia", "provider": "nvidia", "connection_type": "other_provider_api", "model": "nvidia/gpt-4o"},
    })
    monkeypatch.setattr(main, "test_opencode_readiness", lambda *_args: _readiness("selected_model_unavailable", ["nvidia/model-a", "nvidia/model-b"]))

    result = main._opencode_preflight_for_agent("codex")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ready"] is False
    assert result["blocking_reason"] == "selected_model_unavailable"
    assert result["selector_required"] is True
    assert [item["native_model_id"] for item in result["available_models"]] == ["nvidia/model-a", "nvidia/model-b"]
    assert saved["_global_ai"]["model"] == "nvidia/gpt-4o"


def test_agent_model_override_is_not_blocked_by_global_unavailable_model(monkeypatch, tmp_path):
    config_path = _write_config(monkeypatch, tmp_path, {
        "_provider_connections": [_opencode_provider_connection("nvidia/gpt-4o")],
        "_global_ai": {"connection_id": "provider-nvidia", "provider": "nvidia", "connection_type": "other_provider_api", "model": "nvidia/gpt-4o"},
        "_agent_configs": {"codex": {"use_global_connection": True, "use_global_model": False, "model": "nvidia/model-a"}},
    })
    monkeypatch.setattr(main, "test_opencode_readiness", lambda _exe, model, _endpoint: {"ready": True, "checks": {"server": {"status": "not_required"}}, "available_models": [model]})

    result = main._opencode_preflight_for_agent("codex")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ready"] is True
    assert result["model_id"] == "nvidia/model-a"
    assert saved["_global_ai"]["model"] == "nvidia/gpt-4o"


def test_agent_specific_automatic_repair_does_not_overwrite_global_model(monkeypatch, tmp_path):
    config_path = _write_config(monkeypatch, tmp_path, {
        "_provider_connections": [_opencode_provider_connection("nvidia/gpt-4o")],
        "_global_ai": {"connection_id": "provider-nvidia", "provider": "nvidia", "connection_type": "other_provider_api", "model": "nvidia/gpt-4o"},
        "_agent_configs": {"codex": {"use_global_connection": True, "use_global_model": False, "model": "nvidia/llama-3.3-70b-instruct"}},
    })

    def readiness(_exe, model, _endpoint):
        if model == "nvidia/llama-3.3-70b-instruct":
            return _readiness("selected_model_unavailable", ["nvidia/meta/llama-3.3-70b-instruct"])
        return {"ready": True, "checks": {"server": {"status": "not_required"}}, "available_models": ["nvidia/meta/llama-3.3-70b-instruct"]}

    monkeypatch.setattr(main, "test_opencode_readiness", readiness)

    result = main._opencode_preflight_for_agent("codex")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["ready"] is True
    assert result["model_id"] == "nvidia/meta/llama-3.3-70b-instruct"
    assert saved["_agent_configs"]["codex"]["model"] == "nvidia/meta/llama-3.3-70b-instruct"
    assert saved["_global_ai"]["model"] == "nvidia/gpt-4o"
