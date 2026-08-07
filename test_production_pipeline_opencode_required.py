from pathlib import Path


AGENT_CONFIG_SOURCE = Path("provider_agent_config.py").read_text(encoding="utf-8")


def test_alternative_coding_backend_restricted_to_real_file_editing_connections():
    assert "_CODING_CAPABLE_CONNECTION_TYPES = {" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.CLAUDE_SUBSCRIPTION.value" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.OPENCODE_PROVIDER.value" in AGENT_CONFIG_SOURCE
    # A plain API-key/chat connection type is deliberately not in the coding-capable
    # set, since APIAdapter cannot edit files or run commands -- routing coding
    # through one would silently reintroduce the banned text-only fallback.
    assert "OPENAI_API_KEY" not in AGENT_CONFIG_SOURCE.split("_CODING_CAPABLE_CONNECTION_TYPES = {")[1].split("}")[0]
