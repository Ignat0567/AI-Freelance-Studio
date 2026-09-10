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
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.returncode = returncode

    def poll(self):
        # Already finished -- skips the real 0.2s-sleep wait loop entirely.
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


class _FakePopen:
    """Captures the argv Popen was called with and hands back a scripted fake process."""

    def __init__(self, result_payload: dict) -> None:
        self.result_payload = result_payload
        self.last_cmd: list[str] | None = None

    def __call__(self, cmd, **kwargs):
        self.last_cmd = cmd
        return _FakeProcess(stdout=json.dumps(self.result_payload), returncode=0)


class _SequencedFakePopen:
    """Hands back a different scripted process on each successive call, in order --
    for testing the retry-once-on-silent-crash behavior."""

    def __init__(self, processes: list[_FakeProcess]) -> None:
        self._processes = list(processes)
        self.call_count = 0
        self.last_cmd: list[str] | None = None

    def __call__(self, cmd, **kwargs):
        self.last_cmd = cmd
        self.call_count += 1
        return self._processes.pop(0)


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


def test_silent_nonzero_exit_is_retried_once_and_recovers(monkeypatch, tmp_path):
    # First attempt: exit 1, nothing on stdout or stderr (the observed live wrapper-crash
    # symptom -- the actual coding work had already completed, but the process itself then
    # exited nonzero with no captured output). Second attempt: succeeds normally.
    fake_popen = _SequencedFakePopen([
        _FakeProcess(stdout="", stderr="", returncode=1),
        _FakeProcess(stdout=json.dumps({"is_error": False, "result": "done"}), returncode=0),
    ])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)
    warnings = []

    class _Sink:
        def emit(self, **kwargs):
            warnings.append(kwargs)

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is True
    assert fake_popen.call_count == 2
    assert any("retrying once" in str(w.get("message", "")) for w in warnings)


def test_silent_nonzero_exit_does_not_retry_a_second_time(monkeypatch, tmp_path):
    fake_popen = _SequencedFakePopen([
        _FakeProcess(stdout="", stderr="", returncode=1),
        _FakeProcess(stdout="", stderr="", returncode=1),
    ])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is False
    assert fake_popen.call_count == 2


def test_nonzero_exit_with_real_error_output_is_not_retried(monkeypatch, tmp_path):
    # A genuine failure produces stderr text -- must fail immediately, not be mistaken for
    # the silent-crash case and retried (which would just waste a second, costly CLI call).
    fake_popen = _SequencedFakePopen([
        _FakeProcess(stdout="", stderr="a real error message", returncode=1),
    ])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is False
    assert fake_popen.call_count == 1
    assert "a real error message" in result.summary


def test_successful_call_captures_token_usage(monkeypatch, tmp_path):
    payload = {
        "is_error": False,
        "result": "done",
        "total_cost_usd": 0.19616135,
        "usage": {
            "input_tokens": 14,
            "output_tokens": 2066,
            "cache_read_input_tokens": 256717,
            "cache_creation_input_tokens": 16218,
        },
    }
    fake_popen = _SequencedFakePopen([_FakeProcess(stdout=json.dumps(payload), returncode=0)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is True
    assert result.usage is not None
    assert result.usage.total_cost_usd == pytest.approx(0.19616135)
    assert result.usage.input_tokens == 14
    assert result.usage.output_tokens == 2066
    assert result.usage.cache_read_input_tokens == 256717
    assert result.usage.cache_creation_input_tokens == 16218
    assert result.rate_limit_message is None


def test_rate_limited_failure_captures_usage_and_reset_message(monkeypatch, tmp_path):
    # This is the exact shape Claude Code CLI returns when a session/weekly limit is
    # hit: nonzero exit, but a full JSON payload on stdout with is_error, a human
    # readable reset message, and usage/cost figures for the call that hit the limit.
    payload = {
        "is_error": True,
        "api_error_status": 429,
        "result": "You've hit your session limit · resets 1:10am (Europe/Berlin)",
        "total_cost_usd": 0.33191555,
        "usage": {
            "input_tokens": 24,
            "output_tokens": 5066,
            "cache_read_input_tokens": 476901,
            "cache_creation_input_tokens": 21484,
        },
    }
    fake_popen = _SequencedFakePopen([_FakeProcess(stdout=json.dumps(payload), returncode=1)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is False
    assert result.rate_limit_message == "You've hit your session limit · resets 1:10am (Europe/Berlin)"
    assert result.usage is not None
    assert result.usage.total_cost_usd == pytest.approx(0.33191555)
    assert result.usage.output_tokens == 5066


def test_non_rate_limit_failure_has_no_rate_limit_message(monkeypatch, tmp_path):
    payload = {"is_error": True, "result": "Something else went wrong.", "usage": {"input_tokens": 1, "output_tokens": 1}}
    fake_popen = _SequencedFakePopen([_FakeProcess(stdout=json.dumps(payload), returncode=1)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("a prompt", tmp_path, _Sink(), CancellationToken())

    assert result.success is False
    assert result.rate_limit_message is None
    assert result.usage is not None


def test_an_expired_cli_login_is_named_rather_than_reported_as_a_coding_failure(monkeypatch, tmp_path):
    # An expired OAuth token arrives as an ordinary nonzero exit carrying a full JSON
    # payload. Left unnamed it reads as "the model declined", which sends the next person
    # looking at prompts instead of at their own session. No retry or repair can fix it.
    payload = json.dumps({
        "api_error_status": 401,
        "result": "Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue.",
        "type": "result",
    })
    fake_popen = _SequencedFakePopen([_FakeProcess(stdout=payload, returncode=1)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    class _Sink:
        def emit(self, **_kwargs):
            pass

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("build it", tmp_path, _Sink(), CancellationToken())

    assert result.success is False
    assert result.errors == ("claude_code_auth_expired",)
    assert "re-authenticate" in result.summary.casefold()
    # Output was present, so the silent-crash retry must not have fired.
    assert fake_popen.call_count == 1


class _RecordingSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, **kwargs):
        self.messages.append(kwargs.get("message", ""))


def test_every_call_reports_its_wall_clock_against_its_budget(monkeypatch, tmp_path):
    """A timeout and a silent crash write the same log signature, so the only way to tell
    them apart afterwards was to compare timestamps by hand. And a duration distribution
    whose p90 sits just under the ceiling means the ceiling is producing failures -- which
    stays invisible while only the calls that die of the limit are timed."""
    fake_popen = _FakePopen({"is_error": False, "result": "done"})
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)
    sink = _RecordingSink()

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("build it", tmp_path, sink, CancellationToken(), timeout=900)

    assert result.timeout_seconds == 900
    assert result.elapsed_seconds is not None and result.elapsed_seconds >= 0
    budget_lines = [message for message in sink.messages if "budget" in message]
    assert len(budget_lines) == 1
    # Both numbers and the ratio: 400s is comfortable against 1500 and a near-miss at 450.
    assert "900s budget" in budget_lines[0]
    assert "%" in budget_lines[0]


def test_a_failed_call_is_timed_too(monkeypatch, tmp_path):
    payload = json.dumps({"api_error_status": 401, "result": "OAuth access token has expired", "type": "result"})
    fake_popen = _SequencedFakePopen([_FakeProcess(stdout=payload, returncode=1)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("build it", tmp_path, _RecordingSink(), CancellationToken(), timeout=450)

    assert result.errors == ("claude_code_auth_expired",)
    assert result.timeout_seconds == 450
    assert result.elapsed_seconds is not None


def test_a_timed_out_call_still_reports_its_budget(monkeypatch, tmp_path):
    """The calls the budget question is about were the only ones missing from the record."""
    class _NeverFinishes(_FakeProcess):
        # poll() returning None is what "still running" means to _invoke's wait loop, so the
        # deadline is what ends this call -- the real shape of a ceiling strike. A one-second
        # budget keeps the test at one second instead of the production 450.
        def poll(self):
            return None

        # wait() returns normally: the deadline branch calls terminate() then wait(), and
        # catching only subprocess.TimeoutExpired there means any other exception escapes as
        # a failed launch instead of a timeout.
        def wait(self, timeout=None):
            return 0

    fake_popen = _SequencedFakePopen([_NeverFinishes(stdout="", returncode=0)])
    monkeypatch.setattr("order_workflow.claude_code_client.subprocess.Popen", fake_popen)
    monkeypatch.setattr("order_workflow.claude_code_client._ensure_isolated_git_repo", lambda _path: None)
    sink = _RecordingSink()

    client = ConfiguredClaudeCodeExecutionClient()
    result = client.execute_project_prompt("build it", tmp_path, sink, CancellationToken(), timeout=1)

    assert result.timed_out is True
    budget_lines = [m for m in sink.messages if "budget" in m]
    assert len(budget_lines) == 1, "a killed call has to enter the record like any other"
    assert "1s budget" in budget_lines[0]
    assert "stopped at the ceiling" in budget_lines[0]


def test_no_settings_source_is_loaded_from_the_generated_workspace(monkeypatch, tmp_path):
    """The working directory of this call is the generated project -- a directory the call
    itself writes into. A `.claude/settings.json` landing there can define hooks (shell
    commands) and skills (bundled scripts), and this call runs with permissions bypassed,
    so loading it would let one order execute code inside the next one. Verified against
    the real CLI on 2026-09-10: a SessionStart hook in the working directory ran without
    this flag and did not run with it."""
    _, cmd = _run(monkeypatch, tmp_path)

    assert "--setting-sources" in cmd, "settings sources must be pinned, not left at the CLI default"
    assert cmd[cmd.index("--setting-sources") + 1] == "", "empty means: load neither the workspace's nor the operator's settings"
