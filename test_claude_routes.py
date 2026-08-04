import claude_bridge
import main
from test_security_support import authorized_test_client


client = authorized_test_client(main.app)


def test_claude_status_route_reports_not_installed(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: None)

    response = client.get("/api/claude/status")

    assert response.status_code == 200
    assert response.json()["installed"] is False


def test_claude_authenticate_route_starts_terminal(monkeypatch):
    monkeypatch.setattr(
        claude_bridge,
        "start_claude_auth_terminal",
        lambda workdir=None: {"status": "started", "error_code": "", "manual_command": "claude login", "message": "ok"},
    )

    response = client.post("/api/claude/authenticate")

    assert response.status_code == 200
    assert response.json()["status"] == "started"


def test_claude_authenticate_route_surfaces_error_as_500(monkeypatch):
    monkeypatch.setattr(
        claude_bridge,
        "start_claude_auth_terminal",
        lambda workdir=None: {"status": "error", "error_code": "executable_missing", "message": "Claude Code executable was not found."},
    )

    response = client.post("/api/claude/authenticate")

    assert response.status_code == 500
