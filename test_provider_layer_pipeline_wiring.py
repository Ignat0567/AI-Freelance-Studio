import json
from pathlib import Path

import config_storage
import main
from provider_contracts import AgentEvent, ProviderConnection


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _claude_connection(connection_id="claude-main", enabled=True):
    return {
        "connection_id": connection_id,
        "provider_id": "anthropic",
        "connection_type": "claude_subscription",
        "auth_method": "delegated_cli_login",
        "display_name": "Claude Code",
        "model_id": "claude/default",
        "credential_reference": "",
        "endpoint": "",
        "executable_path": "",
        "enabled": enabled,
        "priority": 100,
        "metadata": {},
    }


def _api_key_connection(connection_id="openai-key"):
    return {
        "connection_id": connection_id,
        "provider_id": "openai",
        "connection_type": "openai_api_key",
        "auth_method": "api_key",
        "display_name": "OpenAI API key",
        "model_id": "gpt-4o",
        "credential_reference": "openai_key",
        "endpoint": "",
        "executable_path": "",
        "enabled": True,
        "priority": 100,
        "metadata": {},
    }


def _configure(monkeypatch, tmp_path, connections, agent_configs):
    config_path = tmp_path / "studio_config.json"
    _write(config_path, {"_universal_provider_connections": connections, "_agent_configs": agent_configs})
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    main.agent_configs = main.load_agent_configs()


def test_no_primary_connection_keeps_existing_opencode_only_behavior(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection()], {"codex": {}})

    assert main.resolve_agent_provider_connection("codex") is None


def test_primary_connection_pointing_at_cli_subscription_is_used(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection()], {"codex": {"primary_connection": "claude-main"}})

    connection = main.resolve_agent_provider_connection("codex")

    assert isinstance(connection, ProviderConnection)
    assert connection.connection_id == "claude-main"
    assert connection.connection_type == "claude_subscription"


def test_primary_connection_pointing_at_plain_api_key_is_rejected(monkeypatch, tmp_path):
    """API-key/chat connections cannot edit files or run commands (APIAdapter
    capability has file_editing=False, command_execution=False) -- routing
    coding generation through one would silently reintroduce the banned
    text-only fallback, so it must fall back to the mandatory-OpenCode path."""
    _configure(monkeypatch, tmp_path, [_api_key_connection()], {"codex": {"primary_connection": "openai-key"}})

    assert main.resolve_agent_provider_connection("codex") is None


def test_primary_connection_pointing_at_unknown_id_is_ignored(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection()], {"codex": {"primary_connection": "does-not-exist"}})

    assert main.resolve_agent_provider_connection("codex") is None


def test_disabled_connection_record_is_ignored(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection(enabled=False)], {"codex": {"primary_connection": "claude-main"}})

    assert main.resolve_agent_provider_connection("codex") is None


def test_disabled_agent_config_is_ignored(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection()], {"codex": {"primary_connection": "claude-main", "enabled": False}})

    assert main.resolve_agent_provider_connection("codex") is None


def test_only_codex_lookup_is_used_other_agents_unaffected(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, [_claude_connection()], {"maya": {"primary_connection": "claude-main"}})

    assert main.resolve_agent_provider_connection("codex") is None


class _FakeAdapter:
    def __init__(self, events, cancel_calls):
        self._events = events
        self._cancel_calls = cancel_calls

    async def execute(self, brief):
        for event in self._events:
            yield event

    async def cancel(self, execution_id):
        self._cancel_calls.append(execution_id)


def test_run_provider_adapter_coding_task_maps_success(monkeypatch):
    connection = ProviderConnection(
        connection_id="claude-main", provider_id="anthropic", connection_type="claude_subscription",
        auth_method="delegated_cli_login", display_name="Claude Code",
    )
    events = [
        AgentEvent("started", "Execution started", "exec-1"),
        AgentEvent("text_delta", "Implementing feature", "exec-1"),
        AgentEvent("completed", "Done", "exec-1"),
    ]
    fake = _FakeAdapter(events, [])
    monkeypatch.setattr(main.provider_registry, "create", lambda _connection: fake)
    logs = []

    result = main._run_provider_adapter_coding_task(connection, object(), log_callback=logs.append, cancel_check=lambda: False)

    assert result["success"] is True
    assert result["cancelled"] is False
    assert result["session_id"] == "exec-1"
    assert "Implementing feature" in result["summary"]
    assert any("Execution started" in line for line in logs)


def test_run_provider_adapter_coding_task_maps_error(monkeypatch):
    connection = ProviderConnection(
        connection_id="claude-main", provider_id="anthropic", connection_type="claude_subscription",
        auth_method="delegated_cli_login", display_name="Claude Code",
    )
    events = [
        AgentEvent("started", "Execution started", "exec-2"),
        AgentEvent("error", "CLI executable was not found", "exec-2"),
    ]
    fake = _FakeAdapter(events, [])
    monkeypatch.setattr(main.provider_registry, "create", lambda _connection: fake)

    result = main._run_provider_adapter_coding_task(connection, object(), log_callback=lambda _msg: None, cancel_check=lambda: False)

    assert result["success"] is False
    assert result["cancelled"] is False
    assert result["error"] == "CLI executable was not found"


def test_run_provider_adapter_coding_task_cancels_when_requested(monkeypatch):
    connection = ProviderConnection(
        connection_id="claude-main", provider_id="anthropic", connection_type="claude_subscription",
        auth_method="delegated_cli_login", display_name="Claude Code",
    )
    events = [
        AgentEvent("started", "Execution started", "exec-3"),
        AgentEvent("cancelled", "Execution cancelled", "exec-3"),
    ]
    cancel_calls = []
    fake = _FakeAdapter(events, cancel_calls)
    monkeypatch.setattr(main.provider_registry, "create", lambda _connection: fake)

    result = main._run_provider_adapter_coding_task(connection, object(), log_callback=lambda _msg: None, cancel_check=lambda: True)

    assert result["cancelled"] is True
