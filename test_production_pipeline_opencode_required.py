from pathlib import Path


MAIN_SOURCE = Path("main.py").read_text(encoding="utf-8")


def test_production_coding_stage_requires_opencode_without_ai_developer_fallback():
    assert "_pipeline_codex_generate" not in MAIN_SOURCE
    assert "ai_developer" not in MAIN_SOURCE
    assert "run_ai_development_cycle" not in MAIN_SOURCE

    assert "OpenCode is the mandatory coding backend" in MAIN_SOURCE
    assert "if not _HAS_OPENCODE:" in MAIN_SOURCE
    assert "_set_project_status(project, \"blocked\", reason=\"OpenCode bridge unavailable\")" in MAIN_SOURCE
    assert "[System]: OpenCode bridge is not available. Code generation was not started." in MAIN_SOURCE


def test_production_coding_stage_uses_opencode_task_execution():
    assert "oc_bridge = _get_oc_bridge()" in MAIN_SOURCE
    assert "_opencode_preflight_for_agent(\"codex\", target_path)" in MAIN_SOURCE
    assert "oc_bridge.execute_coding_task(" in MAIN_SOURCE
    assert "OpenCode generated no files" in MAIN_SOURCE
