from pathlib import Path


def test_manual_project_button_is_persistent_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Manual Project" in source
    assert "onClick={onNewProject}" in source
    assert "Create a manual project from your own requirements" in source
