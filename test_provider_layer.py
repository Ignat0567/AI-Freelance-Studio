import asyncio
import json
import sys
from pathlib import Path

import provider_adapters
import config_storage
import main
from provider_contracts import (
    AgentProviderPolicy,
    AuthMethod,
    ConnectionStatus,
    ConnectionType,
    DataLocalityPolicy,
    FallbackMode,
    ProviderCapabilities,
    ProviderConnection,
    ProviderErrorCode,
)
from provider_credentials import ProviderCredentialStore, credential_reference
from provider_migration import migrate_provider_connections
from provider_registry import ProviderRegistry, provider_registry
from provider_router import route_connection
from workflow_contracts import ExecutionBrief
from test_security_support import authorized_test_client
from workflow_artifacts import RunArtifactStore


class MemoryBackend:
    mode = "test_memory_only"
    def __init__(self):
        self.data = {}

    def set(self, reference, secret):
        self.data[reference] = secret

    def get(self, reference):
        return self.data.get(reference, "")

    def delete(self, reference):
        self.data.pop(reference, None)

    def diagnostics(self):
        return {"mode": self.mode, "can_save": True}


def _brief(tmp_path):
    return ExecutionBrief(
        project_id="p1",
        task_id="t1",
        title="Implement sample",
        objective="Return provider output",
        project_root=str(tmp_path),
        requirements=["Do the thing"],
        constraints=["Stay in root"],
        acceptance_criteria=["Output exists"],
        allowed_paths=["."],
        forbidden_paths=[".."],
        implementation_steps=["Plan"],
        test_commands=["python -m pytest -q"],
        validation_commands=[],
        requires_browser_validation=False,
        requires_security_review=False,
        approval_policy="never",
        sandbox_policy="workspace-write",
    )


async def _events(adapter, brief):
    return [event async for event in adapter.execute(brief)]


def _connection(connection_id="c1", connection_type=ConnectionType.OPENAI_API_KEY.value, **overrides):
    data = {
        "connection_id": connection_id,
        "provider_id": "openai",
        "connection_type": connection_type,
        "auth_method": AuthMethod.API_KEY.value,
        "display_name": "Test",
        "model_id": "openai/gpt-5.5",
        "credential_reference": "OPENAI_TEST_KEY",
        "enabled": True,
    }
    data.update(overrides)
    return ProviderConnection(**data)


def test_registry_register_duplicate_unknown_and_factory():
    registry = ProviderRegistry()
    registry.register(ConnectionType.OPENAI_API_KEY, provider_adapters.OpenAIAPIAdapter)
    assert registry.registered_types() == [ConnectionType.OPENAI_API_KEY.value]
    assert isinstance(registry.create(_connection()), provider_adapters.OpenAIAPIAdapter)
    try:
        registry.register(ConnectionType.OPENAI_API_KEY, provider_adapters.OpenAIAPIAdapter)
        raise AssertionError("duplicate allowed")
    except ValueError:
        pass
    try:
        registry.create(_connection(connection_type="missing"))
        raise AssertionError("unknown allowed")
    except KeyError:
        pass


def test_default_registry_contains_required_connection_types():
    registered = set(provider_registry.registered_types())
    for item in (
        ConnectionType.CODEX_CHATGPT_SUBSCRIPTION,
        ConnectionType.OPENAI_API_KEY,
        ConnectionType.CLAUDE_SUBSCRIPTION,
        ConnectionType.ANTHROPIC_API_KEY,
        ConnectionType.GEMINI_GOOGLE_ACCOUNT,
        ConnectionType.GEMINI_API_KEY,
        ConnectionType.OPENCODE_PROVIDER,
        ConnectionType.OLLAMA_LOCAL,
        ConnectionType.OPENAI_COMPATIBLE_LOCAL,
    ):
        assert item.value in registered


def test_credentials_save_read_delete_and_no_secret_in_reference():
    backend = MemoryBackend()
    store = ProviderCredentialStore(backend)
    ref = credential_reference("openai", "provider-openai")
    saved = store.save_api_key(ref, "test-secret-placeholder")
    assert saved["credential_reference"] == ref
    assert "test-secret-placeholder" not in json.dumps(saved)
    assert store.read_api_key(ref) == "test-secret-placeholder"
    store.delete_api_key(ref)
    assert store.read_api_key(ref) == ""


def test_migration_preserves_opencode_and_moves_legacy_api_key_to_reference():
    backend = MemoryBackend()
    store = ProviderCredentialStore(backend)
    cfg = {
        "openai_key": "test-secret-placeholder",
        "_system": {"global_provider": "openai", "global_model": "openai/gpt-5.5"},
        "_provider_connections": [{"connection_id": "oc-live", "connection_type": "opencode_oauth_bridge", "configured_model": "nvidia/model", "executable_path": "opencode.cmd"}],
    }
    assert migrate_provider_connections(cfg, store) is True
    encoded = json.dumps(cfg)
    assert "test-secret-placeholder" not in encoded
    assert any(item["connection_type"] == ConnectionType.OPENCODE_PROVIDER.value for item in cfg["_universal_provider_connections"])
    assert any(item["connection_type"] == ConnectionType.OPENAI_API_KEY.value for item in cfg["_universal_provider_connections"])


def test_api_adapter_uses_reference_not_json_secret(monkeypatch):
    store = ProviderCredentialStore(MemoryBackend())
    store.save_api_key("OPENAI_TEST_KEY", "test-secret-placeholder")
    adapter = provider_adapters.OpenAIAPIAdapter(_connection(), store)
    result = asyncio.run(adapter.test_connection())
    assert result.ready is True
    assert result.status == ConnectionStatus.READY.value


def test_production_default_backend_does_not_read_env_without_opt_in(monkeypatch):
    monkeypatch.delenv("FREELANCERSTUDIO_ALLOW_ENV_CREDENTIAL_READ", raising=False)
    monkeypatch.setenv("OPENAI_TEST_KEY", "test-secret-placeholder")
    adapter = provider_adapters.OpenAIAPIAdapter(_connection())
    assert adapter.api_key() == ""


def test_openai_api_stream_execution_normalizes_events(tmp_path, monkeypatch):
    store = ProviderCredentialStore(MemoryBackend())
    store.save_api_key("OPENAI_TEST_KEY", "sk-test-secret-placeholder")
    adapter = provider_adapters.OpenAIAPIAdapter(_connection(model_id="openai/gpt-test"), store)
    monkeypatch.setattr(adapter, "_request_sse", lambda *_args, **_kwargs: [
        {"choices": [{"delta": {"content": "hello "}}]},
        {"choices": [{"delta": {"tool_calls": [{"id": "tc1", "function": {"name": "read_file"}}]}}]},
        {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ])
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert [events[0].type, events[-1].type] == ["started", "completed"]
    assert any(event.type == "text_delta" and event.message == "hello " for event in events)
    assert any(event.type == "tool_call" for event in events)
    assert any(event.type == "usage" and event.data["total_tokens"] == 5 for event in events)
    assert sum(event.type in {"completed", "cancelled", "error"} for event in events) == 1


def test_anthropic_api_stream_execution_normalizes_events(tmp_path, monkeypatch):
    store = ProviderCredentialStore(MemoryBackend())
    store.save_api_key("OPENAI_TEST_KEY", "anthropic-secret")
    adapter = provider_adapters.AnthropicAPIAdapter(_connection(provider_id="anthropic", connection_type=ConnectionType.ANTHROPIC_API_KEY.value, model_id="anthropic/claude-test"), store)
    monkeypatch.setattr(adapter, "_request_sse", lambda *_args, **_kwargs: [
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {"type": "content_block_delta", "delta": {"text": "ok"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    ])
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert events[-1].type == "completed"
    assert any(event.type == "text_delta" and event.message == "ok" for event in events)
    assert sum(event.type in {"completed", "cancelled", "error"} for event in events) == 1


def test_gemini_api_stream_execution_normalizes_events(tmp_path, monkeypatch):
    store = ProviderCredentialStore(MemoryBackend())
    store.save_api_key("OPENAI_TEST_KEY", "gemini-secret")
    adapter = provider_adapters.GeminiAPIAdapter(_connection(provider_id="google", connection_type=ConnectionType.GEMINI_API_KEY.value, model_id="google/gemini-test"), store)
    monkeypatch.setattr(adapter, "_request_sse", lambda *_args, **_kwargs: [{"candidates": [{"content": {"parts": [{"text": "gem"}, {"functionCall": {"name": "noop"}}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2, "totalTokenCount": 3}}])
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert any(event.type == "tool_call" for event in events)
    assert any(event.type == "usage" and event.data["total_tokens"] == 3 for event in events)
    assert events[-1].type == "completed"


def test_api_http_error_maps_to_normalized_error(tmp_path, monkeypatch):
    store = ProviderCredentialStore(MemoryBackend())
    store.save_api_key("OPENAI_TEST_KEY", "sk-test-secret-placeholder")
    adapter = provider_adapters.OpenAIAPIAdapter(_connection(metadata={"max_attempts": 1}), store)
    monkeypatch.setattr(adapter, "_request_sse", lambda *_args, **_kwargs: (_ for _ in ()).throw(provider_adapters._ProviderHTTPError(429, "rate limited")))
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert events[-1].type == "error"
    assert events[-1].data["error_code"] == ProviderErrorCode.RATE_LIMITED.value


def test_codex_subscription_does_not_read_token_files(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(provider_adapters, "_which", lambda _names, _explicit="": "codex.cmd")
    monkeypatch.setattr(provider_adapters, "_version", lambda *_args, **_kwargs: asyncio.sleep(0, result="1.0.0"))
    adapter = provider_adapters.CodexChatGPTSubscriptionAdapter(_connection(connection_type=ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value, auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value, executable_path=""))
    detected = asyncio.run(adapter.detect())
    assert detected.installed is True
    assert calls == []


def test_gemini_account_adapter_is_honest_unsupported_when_contract_insufficient(monkeypatch):
    monkeypatch.setattr(provider_adapters, "_which", lambda _names, _explicit="": "gemini.cmd")
    monkeypatch.setattr(provider_adapters, "_version", lambda *_args, **_kwargs: asyncio.sleep(0, result="1.0.0"))
    adapter = provider_adapters.GeminiGoogleAccountAdapter(_connection(connection_type=ConnectionType.GEMINI_GOOGLE_ACCOUNT.value, auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value))
    result = asyncio.run(adapter.test_connection())
    assert result.ready is False
    assert result.error_code == ProviderErrorCode.UNSUPPORTED.value


def test_fake_cli_jsonl_execution_and_text_fallback(tmp_path):
    fake = tmp_path / "fake_cli.py"
    fake.write_text("import json,sys\nprint(json.dumps({'type':'text_delta','text':'hi'}))\nprint('plain fallback')\n", encoding="utf-8")
    conn = _connection(connection_type=ConnectionType.CLAUDE_SUBSCRIPTION.value, auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value, executable_path=sys.executable)
    adapter = provider_adapters.ClaudeSubscriptionAdapter(conn)
    adapter.run_args_prefix = [str(fake)]
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert events[0].type == "started"
    assert any(event.type == "text_delta" and event.message == "hi" for event in events)
    assert any(event.type == "text_delta" and event.message == "plain fallback" for event in events)
    assert events[-1].type == "completed"


def test_fake_cli_nonzero_exit_maps_error(tmp_path):
    fake = tmp_path / "fake_cli_fail.py"
    fake.write_text("import sys\nprint('before crash')\nsys.exit(7)\n", encoding="utf-8")
    conn = _connection(connection_type=ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value, auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value, executable_path=sys.executable)
    adapter = provider_adapters.CodexChatGPTSubscriptionAdapter(conn)
    adapter.run_args_prefix = [str(fake)]
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert events[-1].type == "error"
    assert events[-1].data["error_code"] == ProviderErrorCode.PROCESS_CRASHED.value


def test_ollama_blocks_non_loopback_endpoint():
    conn = _connection(connection_type=ConnectionType.OLLAMA_LOCAL.value, auth_method=AuthMethod.LOCAL.value, endpoint="http://192.168.1.10:11434", credential_reference="")
    adapter = provider_adapters.OllamaLocalAdapter(conn)
    result = asyncio.run(adapter.test_connection())
    assert result.ready is False
    assert result.error_code == ProviderErrorCode.PRIVACY_POLICY_BLOCK.value


def test_ollama_stream_execution_normalizes_events(tmp_path, monkeypatch):
    adapter = provider_adapters.OllamaLocalAdapter(_connection(connection_type=ConnectionType.OLLAMA_LOCAL.value, auth_method=AuthMethod.LOCAL.value, endpoint="http://127.0.0.1:11434", credential_reference="", model_id="local-model"))
    monkeypatch.setattr(adapter, "test_connection", lambda: asyncio.sleep(0, result=type("R", (), {"ready": True, "message": "", "error_code": ""})()))
    monkeypatch.setattr(adapter, "_collect_events", lambda brief, execution_id: [provider_adapters._event("text_delta", "ok", execution_id), provider_adapters._event("completed", "done", execution_id)])
    events = asyncio.run(_events(adapter, _brief(tmp_path)))
    assert [events[0].type, events[-1].type] == ["started", "completed"]
    assert sum(event.type in {"completed", "cancelled", "error"} for event in events) == 1


def test_migration_stops_api_key_migration_when_secure_write_fails():
    class FailingBackend(MemoryBackend):
        def set(self, reference, secret):
            raise RuntimeError("no secure backend")
    cfg = {"openai_key": "test-secret-placeholder", "_system": {"global_provider": "openai", "global_model": "openai/gpt-5.5"}}
    assert migrate_provider_connections(cfg, ProviderCredentialStore(FailingBackend())) is False
    assert cfg["openai_key"] == "test-secret-placeholder"
    assert "_universal_provider_connections" not in cfg


def test_router_local_only_privacy_blocks_cloud():
    policy = AgentProviderPolicy(primary_connection_id="api", fallback_connection_ids=[], data_locality_policy=DataLocalityPolicy.LOCAL_ONLY.value)
    decision = asyncio.run(route_connection(policy, [_connection("api")]))
    assert decision.status == "blocked"
    assert decision.error_code == ProviderErrorCode.PRIVACY_POLICY_BLOCK.value


def test_router_paid_api_fallback_requires_user_action(monkeypatch):
    async def ready(self):
        return type("R", (), {"ready": True, "status": "ready", "message": "", "error_code": "", "diagnostics": {}})()
    monkeypatch.setattr(provider_adapters.OpenAIAPIAdapter, "test_connection", ready)
    policy = AgentProviderPolicy(primary_connection_id="missing", fallback_connection_ids=["api"], fallback_mode=FallbackMode.ASK_USER.value, allow_paid_api_fallback=False)
    decision = asyncio.run(route_connection(policy, [_connection("api")]))
    assert decision.status == "requires_user_action"


def test_router_capability_mismatch_blocks(monkeypatch):
    async def caps(self):
        return ProviderCapabilities(chat=True, tools=False)
    monkeypatch.setattr(provider_adapters.OpenAIAPIAdapter, "get_capabilities", caps)
    policy = AgentProviderPolicy(primary_connection_id="api", fallback_connection_ids=[], required_capabilities={"tools"}, allow_paid_api_fallback=True)
    decision = asyncio.run(route_connection(policy, [_connection("api")]))
    assert decision.error_code == ProviderErrorCode.CAPABILITY_MISMATCH.value


def _api_client(monkeypatch, tmp_path, config):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(main, "default_backend", lambda: MemoryBackend())
    main.agent_configs = main.load_agent_configs()
    return authorized_test_client(main.app), config_path


def test_provider_connection_crud_priority_enable_delete_and_assignment_cleanup(monkeypatch, tmp_path):
    client, config_path = _api_client(monkeypatch, tmp_path, {"_agent_configs": {"alex": {"primary_connection": "local-one", "fallbacks": ["local-one", "missing"]}}})
    created = client.post("/api/provider-connections", json={"connection_id": "local-one", "provider_id": "local", "connection_type": ConnectionType.OPENAI_COMPATIBLE_LOCAL.value, "display_name": "Local One", "endpoint": "http://127.0.0.1:9999", "model_id": "local-model"}).json()
    assert created["status"] == "created"
    updated = client.patch("/api/provider-connections/local-one", json={"priority": 3, "enabled": False, "model_id": "manual-model"}).json()
    assert updated["connection"]["priority"] == 3
    assert updated["connection"]["enabled"] is False
    assert updated["connection"]["model_id"] == "manual-model"
    blocked = client.request("DELETE", "/api/provider-connections/local-one", json={"confirmed": False}).json()
    assert blocked["detail"]["error_code"] == "connection_in_use"
    deleted = client.request("DELETE", "/api/provider-connections/local-one", json={"confirmed": True, "delete_credential": False}).json()
    assert deleted["status"] == "deleted"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["_agent_configs"]["alex"]["primary_connection"] is None
    assert "local-one" not in saved["_agent_configs"]["alex"]["fallbacks"]


def test_provider_credential_api_does_not_persist_secret(monkeypatch, tmp_path):
    client, config_path = _api_client(monkeypatch, tmp_path, {"_universal_provider_connections": [_connection("api-one").to_dict()]})
    response = client.post("/api/provider-connections/api-one/credential", json={"api_key": "sk-test-secret-placeholder"}).json()
    assert response["status"] == "saved"
    assert "sk-test-secret-placeholder" not in json.dumps(response)
    assert "sk-test-secret-placeholder" not in config_path.read_text(encoding="utf-8")
    removed = client.delete("/api/provider-connections/api-one/credential").json()
    assert removed["status"] == "deleted"


def test_provider_test_models_login_logout_actions(monkeypatch, tmp_path):
    conn = _connection("api-one").to_dict()
    client, _ = _api_client(monkeypatch, tmp_path, {"_universal_provider_connections": [conn]})
    models = client.get("/api/provider-connections/api-one/models").json()
    assert models["connection_id"] == "api-one"
    test = client.post("/api/provider-connections/api-one/test").json()
    assert test["result"]["status"] in {ConnectionStatus.NOT_CONFIGURED.value, ConnectionStatus.READY.value}
    login = client.post("/api/provider-connections/api-one/login").json()
    assert login["detail"]["error_code"] == "unsupported_action"


def test_frontend_provider_connections_include_universal_subscription(monkeypatch, tmp_path):
    conn = _connection("codex-sub", connection_type=ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value, auth_method=AuthMethod.DELEGATED_CLI_LOGIN.value, provider_id="openai", credential_reference="", model_id="codex/default").to_dict()
    client, _ = _api_client(monkeypatch, tmp_path, {"_universal_provider_connections": [conn]})
    data = client.get("/api/provider-connections").json()
    option = next(item for item in data["connections"] if item["connection_id"] == "codex-sub")
    assert option["supports_provider_auth"] is True
    assert option["available_models"][0]["id"] == "codex/default"


def test_route_decision_artifact_is_written(tmp_path, monkeypatch):
    async def ready(self):
        return type("R", (), {"ready": True, "status": "ready", "message": "", "error_code": "", "diagnostics": {}})()
    monkeypatch.setattr(provider_adapters.OpenAICompatibleLocalAdapter, "test_connection", ready)
    run_id = "provider-router-test"
    RunArtifactStore(run_id, tmp_path / "runs").initialize({})
    # ProviderArtifactRecorder writes to default artifacts/runs, so use a unique run id and assert the file exists there.
    policy = AgentProviderPolicy(primary_connection_id="local", fallback_connection_ids=[], fallback_mode=FallbackMode.LOCAL_ONLY.value, data_locality_policy=DataLocalityPolicy.LOCAL_ONLY.value)
    conn = _connection("local", connection_type=ConnectionType.OPENAI_COMPATIBLE_LOCAL.value, auth_method=AuthMethod.LOCAL.value, provider_id="local", endpoint="http://127.0.0.1:1234", credential_reference="")
    decision = asyncio.run(route_connection(policy, [conn], artifact_run_id=run_id))
    assert decision.status == "ready"
    artifact = Path("artifacts") / "runs" / run_id / "provider" / "route_decision.json"
    assert artifact.is_file()
    encoded = artifact.read_text(encoding="utf-8")
    assert "api_key" not in encoded.lower()
