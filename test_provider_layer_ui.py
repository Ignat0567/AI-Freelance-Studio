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
