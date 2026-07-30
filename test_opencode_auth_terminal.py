from pathlib import Path

import opencode_bridge


class FakeProcess:
    pid = 9876


def test_windows_authentication_opens_visible_interactive_terminal(monkeypatch, tmp_path):
    captured = {}
    executable = r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd"
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge.shutil, "which", lambda _binary: executable)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(
        opencode_bridge.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or FakeProcess(),
    )

    result = opencode_bridge.start_opencode_auth_terminal(str(tmp_path))

    assert result["status"] == "started"
    assert result["manual_command"] == "opencode auth login"
    assert captured["command"][:3] == [r"C:\Windows\System32\cmd.exe", "/d", "/k"]
    assert executable in captured["command"][3]
    assert "auth login" in captured["command"][3]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["creationflags"] == getattr(opencode_bridge.subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    assert "stdin" not in captured["kwargs"]
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]
    assert not any(secret in " ".join(captured["command"]).lower() for secret in ("api_key", "token=", "password="))


def test_terminal_launch_failure_returns_copyable_manual_command(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge.shutil, "which", lambda binary: binary)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")))

    result = opencode_bridge.start_opencode_auth_terminal()

    assert result == {
        "status": "manual_required",
        "error_code": "terminal_launch_failed",
        "message": "The OpenCode terminal could not be opened. Run the command shown below in a terminal.",
        "manual_command": "opencode auth login",
    }


def test_authentication_flow_never_reads_opencode_auth_storage():
    bridge_source = Path("opencode_bridge.py").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    routes_source = Path("api/opencode_routes.py").read_text(encoding="utf-8")

    assert "auth.json" not in bridge_source
    assert "auth.json" not in main_source
    assert "auth.json" not in routes_source


def test_frontend_exposes_separate_authenticate_provider_action():
    source = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")

    assert ">Authenticate Provider</button>" in source
    assert "/api/opencode/authenticate" in source
    assert "Complete the authentication steps in the OpenCode terminal. When finished, return here and select Test Connection." in source
    assert "Run manually:" in source
    assert "method: 'oauth'" not in source


def test_backend_authentication_route_uses_interactive_terminal_flow():
    main_source = Path("main.py").read_text(encoding="utf-8")
    routes_source = Path("api/opencode_routes.py").read_text(encoding="utf-8")

    assert '@router.post("/api/opencode/authenticate")' in routes_source
    assert "start_opencode_auth_terminal(workdir=base_dir)" in routes_source
    assert "/api/opencode/login" not in main_source
    assert "/api/opencode/login" not in routes_source
