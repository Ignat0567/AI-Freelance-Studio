from __future__ import annotations

import json
from pathlib import Path

import pytest

import claude_bridge
from order_workflow.claude_code_client import ConfiguredClaudeCodeExecutionClient
from order_workflow.executors import CancellationToken

pytestmark = pytest.mark.unit


class _FakeStream:
    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True) or [""]

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _FakeStdin:
    def __init__(self) -> None:
        self.written = ""
        self.closed = False

    def write(self, data):
        self.written += data

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, result_payload: dict) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream(json.dumps(result_payload))
        self.stderr = _FakeStream("")
        self.returncode = 0

    def poll(self):
        # Already finished -- skips the real 0.2s-sleep wait loop entirely.
        return self.returncode


class _FakePopen:
    """Captures the argv Popen was called with and hands back a scripted fake process."""

    def __init__(self, result_payload: dict) -> None:
        self.result_payload = result_payload
        self.last_cmd: list[str] | None = None

    def __call__(self, cmd, **kwargs):
        self.last_cmd = cmd
        return _FakeProcess(self.result_payload)


@pytest.fixture(autouse=True)
def _fake_claude_cli(monkeypatch):
    monkeypatch.setattr(claude_bridge, "_discover_claude", lambda: "claude.cmd")


def _run(monkeypatch, tmp_path, *, model=None):
    fake_popen = _FakePopen({"is_error": False, "result": "done"})
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken(), model=model)
    return result, fake_popen.last_cmd


def test_no_model_given_omits_the_model_flag(monkeypatch, tmp_path):
    result, cmd = _run(monkeypatch, tmp_path)

    assert result.success is True
    assert "--model" not in cmd


def test_model_is_passed_through_as_a_bare_alias(monkeypatch, tmp_path):
    _, cmd = _run(monkeypatch, tmp_path, model="sonnet")

    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_catalog_prefixed_model_id_is_stripped_to_the_bare_alias(monkeypatch, tmp_path):
    _, cmd = _run(monkeypatch, tmp_path, model="claude/opus")

    assert cmd[cmd.index("--model") + 1] == "opus"


def test_prompt_is_sent_over_stdin_not_argv(monkeypatch, tmp_path):
    fake_popen = _FakePopen({"is_error": False, "result": "done"})
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    client.execute_project_prompt("a very specific prompt token", tmp_path, _Sink(), CancellationToken())

    assert "a very specific prompt token" not in fake_popen.last_cmd
