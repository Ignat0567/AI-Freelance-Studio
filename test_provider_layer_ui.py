from pathlib import Path


def test_settings_modal_exposes_universal_provider_connection_layer():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    assert "AI Connections" in source
    assert "/api/provider-layer" in source
    assert "ExecutionBrief to ProviderAdapter to AgentEvent" in source
    assert "Studio never reads CLI OAuth tokens" in source
    assert "API fallback is disabled by default" in source
    assert "Без отдельной оплаты API. Используются ресурсы вашего компьютера." in source
    assert "/api/provider-connections/${connectionId}/credential" in source
    assert "/api/provider-connections/${connectionId}/models" in source
    assert "/api/provider-connections/${connectionId}/login" in source
    assert "/api/provider-connections/${connectionId}/logout" in source
    assert "Save/replace key" in source
    assert "Remove key" in source
    assert "Save model" in source
    assert "Save assignment" in source
    assert "localStorage" not in source[source.find("function UniversalProviderConnectionsSettings"):source.find("function GlobalAIInheritanceSettings")]


def test_settings_modal_offers_every_registered_connection_type_as_a_template():
    """Backend adapters are useless if the user cannot create the connection: five local
    runtimes shipped working adapters but had no template here, so they were unreachable."""
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    for template_key in (
        "'openrouter-api-key'",
        "'lm-studio-local'",
        "'llama-cpp-server'",
        "'localai-local'",
        "'vllm-local'",
        "'openai-compatible-local'",
    ):
        assert template_key in source

    for connection_type in (
        "openrouter_api_key",
        "lm_studio_local",
        "llama_cpp_server",
        "localai_local",
        "vllm_local",
        "openai_compatible_local",
    ):
        assert connection_type in source

    for endpoint in ("http://127.0.0.1:1234", "http://127.0.0.1:8080", "http://127.0.0.1:8000"):
        assert endpoint in source


def test_settings_modal_identifies_local_connections_by_exact_type():
    """llama_cpp_server contains neither 'local' nor 'ollama', so substring matching would
    wrongly warn that a local runtime is blocked by the LOCAL_ONLY locality policy."""
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    assert "LOCAL_CONNECTION_TYPES" in source
    assert "isLocalConnection" in source
    assert "!isLocalConnection(selected)" in source
    assert "!(selected.connection_type || '').includes('local')" not in source


def test_settings_modal_allows_editing_a_local_endpoint():
    """Two shipped local runtimes both default to port 8080, so a read-only endpoint left
    users unable to run both."""
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    assert "Save endpoint" in source
    assert "endpointDrafts" in source
    assert "{ endpoint: endpointDrafts[connection.connection_id]" in source
