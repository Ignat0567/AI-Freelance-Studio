import json
from pathlib import Path

import config_storage
import main
EXPECTED_FLOW = "Open Settings -> AI Provider -> Detect Again -> Repair automatically or Test Connection -> Save Connection -> Retry Generation."


def test_recovery_instructions_cover_each_real_failure_state():
    expected_reasons = {
        "nodejs_missing": "Node.js is required",
        "opencode_not_installed": "Download OpenCode",
        "executable_not_detected": "executable was not detected",
        "server_not_running": "local service is required",
        "opencode_model_reference_invalid": "model reference is invalid",
        "opencode_model_not_found": "selected model is not available",
        "opencode_authentication_failure": "provider is not authenticated",
        "opencode_workspace_invalid": "workspace path is invalid",
        "provider_rate_limited": "rate limit",
        "provider_quota_exceeded": "quota",
        "provider_not_authenticated": "No authorized provider",
        "models_unavailable": "no models are available",
        "connection_test_failed": "readiness was not confirmed",
        "connection_not_saved": "was not saved",
    }

    for code, reason in expected_reasons.items():
        instruction = main._opencode_recovery_instruction(code)
        assert reason in instruction
        assert EXPECTED_FLOW in instruction


def test_recovery_classifier_distinguishes_actionable_failures():
    assert main._opencode_recovery_code("npm is required") == "nodejs_missing"
    assert main._opencode_recovery_code("OpenCode is not installed") == "opencode_not_installed"
    assert main._opencode_recovery_code("executable ENOENT") == "executable_not_detected"
    assert main._opencode_recovery_code("server required but serve failed") == "server_not_running"
    assert main._opencode_recovery_code("Model not found: nvidia/missing") == "opencode_model_not_found"
    assert main._opencode_recovery_code("rate limit reached") == "provider_rate_limited"
    assert main._opencode_recovery_code("quota exceeded") == "provider_quota_exceeded"
    assert main._opencode_recovery_code("unauthorized provider") == "provider_not_authenticated"
    assert main._opencode_recovery_code("no models available") == "models_unavailable"
    assert main._opencode_recovery_code("connection not saved") == "connection_not_saved"
    assert main._opencode_recovery_code("unknown failure") == "connection_test_failed"


def test_production_sources_do_not_reference_removed_opencode_login_action():
    paths = [
        Path("main.py"),
        Path("qa_engine.py"),
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
    dashboard = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    frontend = settings + connection + dashboard

    for action in ("Download Node.js", "Download OpenCode", "Detect Again", "Authenticate Provider", "Repair automatically", "Test Connection", "Save Connection", "Retry Generation"):
        assert action in frontend


def test_frontend_does_not_require_start_web_for_cli_generation():
    source = Path("frontend/src/components/OpenCodeConnectionSetup.jsx").read_text(encoding="utf-8")
    assert "Start OpenCode Web before testing" not in source
    assert "Local server" in source
    assert "not_required" in source


def test_retry_generation_resumes_blocked_project_from_saved_phase(monkeypatch):
    scheduled = []

    class BackgroundTasks:
        def add_task(self, function, *args):
            scheduled.append((function, args))

    project = {
        "project_id": "blocked-project",
        "title": "Blocked project",
        "status": "blocked",
        "_phase": "coding",
        "logs": [],
        "cancel_requested": False,
    }
    monkeypatch.setattr(main, "active_projects", {project["project_id"]: project})
    monkeypatch.setattr(main, "_save_projects_state", lambda: None)

    result = main.retry_project_qa(project["project_id"], BackgroundTasks())

    assert result == {"status": "retrying_generation", "message": "Generation restarted from 'coding'."}
    assert project["status"] == "coding"
    assert scheduled == [(main.async_studio_production_pipeline, (project["project_id"],))]


def test_blocked_dashboard_retry_uses_generation_retry_endpoint():
    source = Path("frontend/src/App.jsx").read_text(encoding="utf-8")

    assert "activeProject.status === 'blocked' ? handleQARetry(activePort, activeProject) : handleRestart()" in source


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
