from pathlib import Path

import claude_bridge


class FakeProcess:
    pid = 4321


def test_discover_claude_returns_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_run_capture", lambda cmd, timeout: (None, "", "not found"))
    monkeypatch.setattr(claude_bridge.os.path, "isfile", lambda _path: False)

    assert claude_bridge._discover_claude() is None


def test_get_claude_status_reports_not_installed_without_binary(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: None)

    status = claude_bridge.get_claude_status()

    assert status == {"installed": False, "binary": "", "version": ""}


def test_get_claude_status_reports_version_when_installed(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: "claude.cmd")
    monkeypatch.setattr(claude_bridge, "_run_capture", lambda cmd, timeout: (0, "1.2.3", ""))

    status = claude_bridge.get_claude_status()

    assert status["installed"] is True
    assert status["binary"] == "claude.cmd"
    assert status["version"] == "1.2.3"


def test_authenticate_reports_missing_executable(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: None)

    result = claude_bridge.start_claude_auth_terminal()

    assert result["status"] == "error"
    assert result["error_code"] == "executable_missing"
    assert result["manual_command"] == "claude login"


def test_authenticate_opens_visible_interactive_terminal(monkeypatch, tmp_path):
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

    result = claude_bridge.start_claude_auth_terminal(str(tmp_path))

    assert result["status"] == "started"
    assert result["manual_command"] == "claude login"
    assert captured["command"][:3] == [r"C:\Windows\System32\cmd.exe", "/d", "/k"]
    assert executable in captured["command"][3]
    assert "login" in captured["command"][3]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["creationflags"] == getattr(claude_bridge.subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    assert not any(secret in " ".join(captured["command"]).lower() for secret in ("api_key", "token=", "password="))


def test_authenticate_falls_back_to_manual_command_on_launch_failure(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: "claude.cmd")
    monkeypatch.setattr(claude_bridge.shutil, "which", lambda binary: binary)
    monkeypatch.setattr(claude_bridge.os, "name", "nt")
    monkeypatch.setattr(claude_bridge.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")))

    result = claude_bridge.start_claude_auth_terminal()

    assert result == {
        "status": "manual_required",
        "error_code": "terminal_launch_failed",
        "message": "The Claude Code terminal could not be opened. Run the command shown below in a terminal.",
        "manual_command": "claude login",
    }


def test_authenticate_returns_manual_required_on_non_windows(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: "claude")
    monkeypatch.setattr(claude_bridge.os, "name", "posix")

    result = claude_bridge.start_claude_auth_terminal()

    assert result["status"] == "manual_required"
    assert result["error_code"] == "terminal_unavailable"
    assert result["manual_command"] == "claude login"


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
