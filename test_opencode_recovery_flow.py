from pathlib import Path

import main
EXPECTED_FLOW = "Open Settings -> AI Provider -> Download Node.js if Node.js or npm is missing -> Detect Again -> Download OpenCode if it is missing -> Detect Again -> Authenticate Provider -> complete the steps in the OpenCode terminal -> Start OpenCode Web or server -> Test Connection -> Save Connection -> Retry Generation."


def test_recovery_instructions_cover_each_real_failure_state():
    expected_reasons = {
        "nodejs_missing": "Node.js is required",
        "opencode_not_installed": "Download OpenCode",
        "executable_not_detected": "executable was not detected",
        "server_not_running": "server is unavailable",
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
    assert main._opencode_recovery_code("server connection refused") == "server_not_running"
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

    for action in ("Download Node.js", "Download OpenCode", "Detect Again", "Authenticate Provider", "Start OpenCode Web", "Test Connection", "Save Connection", "Retry Generation"):
        assert action in frontend


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
