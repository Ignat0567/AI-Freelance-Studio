from __future__ import annotations

import pytest

from order_workflow.execution_config import ExecutionConfigurationProvider


pytestmark = pytest.mark.unit


def _provider(config, *, secrets=(), opencode=(True, "1.17.11", "opencode.cmd"), active_backend="", workspace_root=None, environ=None):
    secret_values = set(secrets)

    def lookup(name, _config=None):
        return "secret-value" if name in secret_values else ""

    return ExecutionConfigurationProvider(
        config_loader=lambda: config,
        secret_lookup=lookup,
        opencode_version_probe=lambda: opencode,
        active_backend_probe=lambda: active_backend,
        workspace_root=workspace_root,
        environ=environ or {},
    )


def test_provider_missing_secret_present_and_local_no_key_modes(tmp_path):
    missing = _provider({}, workspace_root=tmp_path).snapshot()
    assert missing.provider.code == "provider_not_configured"

    no_secret = _provider({"_system": {"global_provider": "openai", "global_model": "gpt-5.5"}}, workspace_root=tmp_path).snapshot()
    assert no_secret.provider.code == "provider_secret_missing"
    assert no_secret.provider.secret_present is False

    with_secret = _provider({"_system": {"global_provider": "openai", "global_model": "gpt-5.5"}}, secrets=("openai_key",), workspace_root=tmp_path).snapshot()
    assert with_secret.provider.code == "provider_configured"
    assert with_secret.provider.secret_present is True

    local = _provider({"_system": {"global_provider": "ollama", "global_model": "codellama"}}, workspace_root=tmp_path).snapshot()
    assert local.provider.code == "provider_configured"
    assert local.provider.secret_required is False


def test_global_ai_settings_are_visible_to_execution_readiness(tmp_path):
    snapshot = _provider(
        {"_global_ai": {"provider": "openai", "model": "gpt-5.5"}},
        secrets=("openai_key",),
        workspace_root=tmp_path,
    ).snapshot()

    assert snapshot.provider.code == "provider_configured"
    assert snapshot.provider.provider == "openai"
    assert snapshot.provider.secret_required is True
    assert snapshot.provider.secret_present is True
    assert snapshot.model.code == "model_selected"
    assert snapshot.model.model == "gpt-5.5"


def test_ready_opencode_bridge_connection_satisfies_provider_and_model(tmp_path):
    snapshot = _provider(
        {
            "_provider_connections": [
                {
                    "connection_type": "opencode_oauth_bridge",
                    "configured_provider": "nvidia",
                    "configured_model": "nvidia/deepseek-ai/deepseek-v4-pro",
                    "readiness_status": "ready",
                    "auth_status": "authenticated",
                    "enabled": True,
                }
            ]
        },
        workspace_root=tmp_path,
    ).snapshot()

    assert snapshot.provider.code == "provider_configured"
    assert snapshot.provider.provider == "opencode_bridge"
    assert snapshot.provider.secret_required is False
    assert snapshot.provider.secret_present is True
    assert snapshot.model.code == "model_selected"
    assert snapshot.model.model == "nvidia/deepseek-ai/deepseek-v4-pro"


def test_opencode_bridge_requires_ready_connection_and_model(tmp_path):
    not_ready = _provider(
        {"_provider_connections": [{"connection_type": "opencode_oauth_bridge", "configured_model": "openai/gpt-5.5"}]},
        workspace_root=tmp_path,
    ).snapshot()
    assert not_ready.provider.code == "provider_not_configured"
    assert not_ready.model.code == "model_not_selected"

    missing_model = _provider(
        {"_provider_connections": [{"connection_type": "opencode_oauth_bridge", "readiness_status": "ready"}]},
        workspace_root=tmp_path,
    ).snapshot()
    assert missing_model.provider.code == "provider_not_configured"
    assert missing_model.model.code == "model_not_selected"


def test_model_missing_selected_and_unsupported(tmp_path):
    missing = _provider({"_system": {"global_provider": "ollama"}}, workspace_root=tmp_path).snapshot()
    assert missing.model.code == "model_not_selected"

    selected = _provider({"_system": {"global_provider": "ollama", "global_model": "codellama"}}, workspace_root=tmp_path).snapshot()
    assert selected.model.code == "model_selected"
    assert selected.model.model == "codellama"

    unsupported = _provider({"_system": {"global_provider": "ollama", "global_model": "../bad"}}, workspace_root=tmp_path).snapshot()
    assert unsupported.model.code == "model_unsupported_for_execution"


def test_active_claude_code_cli_satisfies_provider_and_model_with_no_stored_config(tmp_path):
    snapshot = _provider({}, active_backend="claude_code", workspace_root=tmp_path).snapshot()

    assert snapshot.provider.code == "provider_configured"
    assert snapshot.provider.provider == "claude_code"
    assert snapshot.provider.secret_required is False
    assert snapshot.model.code == "model_selected"
    assert snapshot.model.model == "claude/default"

    absent = _provider({}, active_backend="", workspace_root=tmp_path).snapshot()
    assert absent.provider.code == "provider_not_configured"
    assert absent.model.code == "model_not_selected"


def test_opencode_workspace_and_live_opt_in_statuses(tmp_path):
    unavailable = _provider({}, opencode=(False, "", ""), workspace_root=tmp_path).snapshot()
    assert unavailable.opencode.code == "opencode_unavailable"

    ready = _provider({}, opencode=(True, "1.17.11", "C:/bin/opencode.cmd"), workspace_root=tmp_path, environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"}).snapshot()
    assert ready.opencode.code == "opencode_available"
    assert ready.opencode.version == "1.17.11"
    assert ready.live_opt_in.code == "live_execution_opt_in_enabled"

    locked = _provider({}, workspace_root=tmp_path, environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "true"}).snapshot()
    assert locked.live_opt_in.code == "live_execution_opt_in_required"

    missing_root = _provider({}, workspace_root=tmp_path / "missing").snapshot()
    assert missing_root.workspace.code == "workspace_root_unavailable"


def test_readiness_snapshot_does_not_expose_secrets(tmp_path):
    snapshot = _provider({"_system": {"global_provider": "openai", "global_model": "gpt-5.5"}}, secrets=("openai_key",), workspace_root=tmp_path).snapshot()
    serialized = snapshot.to_json().casefold()
    assert "secret-value" not in serialized
    assert "api_key" not in serialized
    assert "token" not in serialized
