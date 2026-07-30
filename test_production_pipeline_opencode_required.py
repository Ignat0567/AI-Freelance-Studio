from pathlib import Path


MAIN_SOURCE = Path("main.py").read_text(encoding="utf-8")
AGENT_CONFIG_SOURCE = Path("provider_agent_config.py").read_text(encoding="utf-8")


def test_production_coding_stage_requires_opencode_without_ai_developer_fallback():
    assert "_pipeline_codex_generate" not in MAIN_SOURCE
    assert "ai_developer" not in MAIN_SOURCE
    assert "run_ai_development_cycle" not in MAIN_SOURCE

    # OpenCode is still the default coding backend and is required unless the
    # agent has an explicitly configured coding-capable provider connection
    # (real file-editing/command-execution CLI backend, never a plain
    # text-only chat/API fallback -- see _CODING_CAPABLE_CONNECTION_TYPES).
    assert "OpenCode is the default mandatory coding backend" in MAIN_SOURCE
    assert "codex_connection = resolve_agent_provider_connection(\"codex\")" in MAIN_SOURCE
    assert "if codex_connection is None and not _HAS_OPENCODE:" in MAIN_SOURCE
    assert "_set_project_status(project, \"blocked\", reason=\"OpenCode bridge unavailable\")" in MAIN_SOURCE
    assert "Code generation was not started." in MAIN_SOURCE


def test_alternative_coding_backend_restricted_to_real_file_editing_connections():
    assert "_CODING_CAPABLE_CONNECTION_TYPES = {" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.CODEX_CHATGPT_SUBSCRIPTION.value" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.CLAUDE_SUBSCRIPTION.value" in AGENT_CONFIG_SOURCE
    assert "ConnectionType.OPENCODE_PROVIDER.value" in AGENT_CONFIG_SOURCE
    # A plain API-key/chat connection type is deliberately not in the coding-capable
    # set, since APIAdapter cannot edit files or run commands -- routing coding
    # through one would silently reintroduce the banned text-only fallback.
    assert "OPENAI_API_KEY" not in AGENT_CONFIG_SOURCE.split("_CODING_CAPABLE_CONNECTION_TYPES = {")[1].split("}")[0]


def test_production_coding_stage_uses_opencode_task_execution():
    assert "oc_bridge = _get_oc_bridge()" in MAIN_SOURCE
    assert "_opencode_preflight_for_agent(\"codex\", target_path)" in MAIN_SOURCE
    assert "oc_bridge.execute_coding_task(" in MAIN_SOURCE
    assert "OpenCode generated no files" in MAIN_SOURCE
