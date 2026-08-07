import json

import config_storage
import main
from test_security_support import authorized_test_client


def _client(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    return authorized_test_client(main.app), config_path


def _ready_result(model="claude/sonnet", executable_path=r"C:\Tools\claude.cmd"):
    return {
        "ready": True,
        "error_code": "",
        "message": "Claude Code CLI is installed and logged in.",
        "executable_path": executable_path,
        "account": "user@example.com",
    }


def _blocked_result(error_code="claude_code_not_logged_in", message="Run `claude auth login`."):
    return {"ready": False, "error_code": error_code, "message": message, "executable_path": r"C:\Tools\claude.cmd"}


def test_detect_reports_dependencies_without_touching_auth(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        main,
        "get_claude_code_onboarding_dependencies",
        lambda: {
            "components": {
                "node": {"installed": True, "version": "v24.0.0", "path": "node.exe", "path_refresh_recommended": False},
                "npm": {"installed": True, "version": "11.0.0", "path": "npm.cmd", "path_refresh_recommended": False},
                "claude": {"installed": True, "version": "2.1.223", "path": r"C:\Tools\claude.cmd", "path_refresh_recommended": False},
            },
            "restart_recommended": False,
            "restart_message": "",
        },
    )

    response = client.post("/api/provider-connections/claude/detect", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "detected"
    assert body["executable_path"] == r"C:\Tools\claude.cmd"
    assert {m["id"] for m in body["available_models"]} >= {"claude/default", "claude/sonnet", "claude/opus"}
    assert "OAuth" not in body["authentication"] or "owned by the official Claude Code CLI" in body["authentication"]


def test_transient_test_reports_readiness_without_saving(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_claude_code_readiness", lambda executable: _blocked_result())

    response = client.post("/api/provider-connections/claude/test", json={"configured_model": "claude/sonnet"})

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["error_code"] == "claude_code_not_logged_in"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_save_is_blocked_when_readiness_check_fails(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_claude_code_readiness", lambda executable: _blocked_result("claude_code_unavailable", "Claude Code CLI was not found."))
    writes = []
    monkeypatch.setattr(main, "save_studio_keys", lambda data: writes.append(data))

    response = client.post("/api/provider-connections/claude", json={"configured_model": "claude/sonnet"})

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "claude_code_unavailable"
    assert writes == []
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_successful_save_persists_verified_metadata_and_sets_global_provider(monkeypatch, tmp_path):
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_claude_code_readiness", lambda executable: _ready_result())

    response = client.post("/api/provider-connections/claude", json={"name": "My Claude Code", "configured_model": "claude/sonnet"})

    assert response.status_code == 200
    body = response.json()
    connection = body["connection"]
    assert connection["connection_id"] == "claude-subscription"
    assert connection["connection_type"] == "claude_subscription"
    assert connection["configured_model"] == "claude/sonnet"
    assert connection["priority"] == 30
    assert connection["readiness_status"] == "ready"
    # No OAuth token or credential value was ever part of the readiness result or the saved record.
    assert "token" not in json.dumps(connection).lower()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["_system"]["global_provider"] == "anthropic"
    assert saved["_system"]["global_model"] == "claude/sonnet"
    assert saved["_global_ai"]["connection_type"] == "claude_subscription"
    stored = next(item for item in saved["_provider_connections"] if item["connection_id"] == "claude-subscription")
    assert stored["connection_type"] == "claude_subscription"
    assert stored["priority"] == 30


def test_saved_claude_connection_outranks_a_legacy_api_key_style_entry(monkeypatch, tmp_path):
    """Regression guard for the OAuth-first sort fix in provider_config.py: once a Claude
    Code bridge connection is saved, it must still surface ahead of the synthetic
    "<PROVIDER> API key" entry Global AI auto-creates."""
    client, config_path = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_claude_code_readiness", lambda executable: _ready_result())
    client.post("/api/provider-connections/claude", json={"configured_model": "claude/sonnet"})

    response = client.get("/api/provider-connections")

    assert response.status_code == 200
    connections = response.json()["connections"]
    ids = [item["connection_id"] for item in connections]
    assert "claude-subscription" in ids
    assert ids.index("claude-subscription") < ids.index("provider-anthropic")


def test_specific_bridge_routes_are_not_shadowed_by_the_generic_connection_id_test_route(monkeypatch, tmp_path):
    """Regression guard: /api/provider-connections/{connection_id}/test is registered
    generically and previously shadowed /opencode/test and /claude/test because it was
    declared earlier, making both literal routes 404 with "Provider connection not found"
    no matter what. The literal routes must be registered ahead of the parametrized one."""
    client, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "test_claude_code_readiness", lambda executable: _blocked_result())

    response = client.post("/api/provider-connections/claude/test", json={})

    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json().get("error_code") is not None  # a real readiness payload, not a 404 "not found" shape
