from pathlib import Path

import claude_bridge


class FakeProcess:
    pid = 4321


def test_discover_claude_returns_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_run_capture", lambda cmd, timeout: (None, "", "not found"))
    monkeypatch.setattr(claude_bridge.os.path, "isfile", lambda _path: False)

    assert claude_bridge._discover_claude() is None


def test_open_workspace_reports_missing_executable(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: None)

    result = claude_bridge.start_claude_workspace_terminal("C:\\projects\\demo")

    assert result["status"] == "error"
    assert result["error_code"] == "executable_missing"
    assert result["manual_command"] == "claude"


def test_open_workspace_opens_terminal_in_project_directory(monkeypatch, tmp_path):
    captured = {}
    executable = r"C:\Users\tester\AppData\Roaming\npm\claude.cmd"
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: "claude.cmd")
    monkeypatch.setattr(claude_bridge.shutil, "which", lambda _binary: executable)
    monkeypatch.setattr(claude_bridge.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(
        claude_bridge.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or FakeProcess(),
    )

    result = claude_bridge.start_claude_workspace_terminal(str(tmp_path))

    assert result["status"] == "started"
    assert result["manual_command"] == "claude"
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert executable in captured["command"][3]
    assert "login" not in captured["command"][3]


def test_claude_bridge_never_stores_credentials():
    source = Path("claude_bridge.py").read_text(encoding="utf-8")

    assert "api_key" not in source
    assert "session_key" not in source
    assert ".credentials" not in source
