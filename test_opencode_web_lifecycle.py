import io
from pathlib import Path

import opencode_bridge


class FakeProcess:
    def __init__(self, exit_code=None):
        self._exit_code = exit_code
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.pid = 1234

    def poll(self):
        return self._exit_code


def _reset_web_state(monkeypatch):
    monkeypatch.setattr(opencode_bridge, "_opencode_web_process", None)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_url", "")
    opencode_bridge._opencode_web_output.clear()


def _disable_output_threads(monkeypatch):
    thread = type("Thread", (), {"start": lambda self: None})
    monkeypatch.setattr(opencode_bridge.threading, "Thread", lambda *args, **kwargs: thread())


def test_opencode_web_uses_dynamic_local_port_and_safe_command(monkeypatch, tmp_path):
    _reset_web_state(monkeypatch)
    captured = {}
    process = FakeProcess()
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge, "_find_free_local_port", lambda: 45123)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda url: url.endswith(":45123"))
    _disable_output_threads(monkeypatch)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or process)

    result = opencode_bridge.start_opencode_web(str(tmp_path), readiness_timeout=0.1)

    assert result == {"status": "ready", "error_code": "", "url": "http://127.0.0.1:45123", "port": 45123, "reused": False, "message": "OpenCode Web is ready. Opening the local interface."}
    assert captured["command"] == ["opencode.cmd", "web", "--hostname", "127.0.0.1", "--port", "45123"]
    assert captured["kwargs"]["shell"] is False
    assert not any("key" in argument.lower() or "token" in argument.lower() or "secret" in argument.lower() for argument in captured["command"])


def test_opencode_web_reuses_ready_instance_without_duplicate_process(monkeypatch):
    _reset_web_state(monkeypatch)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_url", "http://127.0.0.1:45124")
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: True)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate process")))

    result = opencode_bridge.start_opencode_web(readiness_timeout=0.1)

    assert result["status"] == "ready"
    assert result["reused"] is True
    assert result["port"] == 45124


def test_opencode_web_reports_missing_executable(monkeypatch):
    _reset_web_state(monkeypatch)
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: None)

    result = opencode_bridge.start_opencode_web(readiness_timeout=0.1)

    assert result["error_code"] == "executable_missing"


def test_opencode_web_reports_early_exit(monkeypatch):
    _reset_web_state(monkeypatch)
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge, "_find_free_local_port", lambda: 45125)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: False)
    _disable_output_threads(monkeypatch)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess(exit_code=1))

    result = opencode_bridge.start_opencode_web(readiness_timeout=0.1)

    assert result["error_code"] == "early_exit"


def test_opencode_web_classifies_port_failure(monkeypatch):
    _reset_web_state(monkeypatch)
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge, "_find_free_local_port", lambda: 45126)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: False)
    _disable_output_threads(monkeypatch)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: opencode_bridge._opencode_web_output.append("EADDRINUSE") or FakeProcess(exit_code=1))

    result = opencode_bridge.start_opencode_web(readiness_timeout=0.1)

    assert result["error_code"] == "port_unavailable"


def test_opencode_web_timeout_terminates_owned_process(monkeypatch):
    _reset_web_state(monkeypatch)
    process = FakeProcess()
    terminated = []
    monkeypatch.setattr(opencode_bridge, "_discover_opencode", lambda: "opencode.cmd")
    monkeypatch.setattr(opencode_bridge, "_find_free_local_port", lambda: 45127)
    monkeypatch.setattr(opencode_bridge, "_opencode_web_ready", lambda _url: False)
    _disable_output_threads(monkeypatch)
    monkeypatch.setattr(opencode_bridge.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(opencode_bridge, "_terminate_process_tree", lambda proc: terminated.append(proc))

    result = opencode_bridge.start_opencode_web(readiness_timeout=0)

    assert result["error_code"] == "readiness_timeout"
    assert terminated == [process]


def test_browser_open_failure_has_manual_url_fallback():
    settings = Path("frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
    app = Path("frontend/src/App.jsx").read_text(encoding="utf-8")

    assert "if (!browserWindow)" in settings
    assert "browser could not be opened" in settings
    assert "browser could not be opened" in app
