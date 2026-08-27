"""What a coding-CLI call leaves running, and what may be killed for it.

Three `python -m http.server 8099` processes outlived acceptance run 8 on 2026-08-27, started
by repair calls inside the generated project. They bound every interface, so a client's
project was on the local network by accident, and they sat on the port the demo backend uses,
so the next run refused to start -- an hour after the fact, with nothing in the record saying
why.

The last test here starts a real server in a real directory and checks it is gone, because a
cleanup verified only against fake processes is a cleanup nobody has seen work.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from order_workflow.workspace_processes import stop_processes_left_in_workspace


pytestmark = pytest.mark.unit


class _FakeProcess:
    def __init__(self, pid: int, cwd: str, cmdline: list[str], *, refuses: bool = False) -> None:
        self.pid = pid
        self._cwd = cwd
        self._cmdline = cmdline
        self._refuses = refuses
        self.terminated = False
        self.killed = False

    def cwd(self) -> str:
        if self._refuses:
            raise PermissionError("access denied")
        return self._cwd

    def cmdline(self) -> list[str]:
        return self._cmdline

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_a_server_left_running_in_the_workspace_is_stopped(tmp_path):
    inside = _FakeProcess(4242, str(tmp_path / "src"), [sys.executable, "-m", "http.server", "8099"])
    (tmp_path / "src").mkdir()

    stopped = stop_processes_left_in_workspace(tmp_path, processes=lambda: [inside], self_pid=1)

    assert inside.terminated
    assert len(stopped) == 1
    assert "http.server" in stopped[0] and "4242" in stopped[0]


def test_a_process_running_somewhere_else_is_left_alone(tmp_path):
    elsewhere = _FakeProcess(11, str(tmp_path.parent), ["node", "server.js"])

    assert stop_processes_left_in_workspace(tmp_path, processes=lambda: [elsewhere], self_pid=1) == ()
    assert not elsewhere.terminated


def test_the_process_doing_the_cleanup_is_never_a_target(tmp_path):
    """The backend can have the workspace as its own cwd in a test, and killing the thing
    doing the killing is a poor way to clean up."""
    myself = _FakeProcess(99, str(tmp_path), ["python", "backend.py"])

    assert stop_processes_left_in_workspace(tmp_path, processes=lambda: [myself], self_pid=99) == ()
    assert not myself.terminated


def test_a_process_that_will_not_answer_is_skipped_rather_than_failing_the_run(tmp_path):
    # psutil raises for a process that exited mid-scan or belongs to another user. Cleanup
    # must never be able to fail the run it is cleaning up after.
    secretive = _FakeProcess(7, str(tmp_path), ["something"], refuses=True)
    ordinary = _FakeProcess(8, str(tmp_path), ["python", "-m", "http.server"])

    stopped = stop_processes_left_in_workspace(tmp_path, processes=lambda: [secretive, ordinary], self_pid=1)

    assert not secretive.terminated
    assert ordinary.terminated
    assert len(stopped) == 1


def test_a_workspace_that_no_longer_exists_is_not_an_error(tmp_path):
    assert stop_processes_left_in_workspace(tmp_path / "gone", processes=lambda: [], self_pid=1) == ()


def test_a_real_server_started_in_the_workspace_is_really_stopped(tmp_path):
    """The one that matters. Everything above tests the rule; this tests the killing."""
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "0", "--bind", "127.0.0.1"],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 10
        while server.poll() is not None and time.time() < deadline:  # pragma: no cover - startup race
            time.sleep(0.1)
        assert server.poll() is None, "the fixture server exited before the test could run"

        stopped = stop_processes_left_in_workspace(tmp_path)

        assert any(str(server.pid) in item for item in stopped), f"did not stop pid {server.pid}: {stopped}"
        assert server.wait(timeout=10) is not None
    finally:
        if server.poll() is None:  # pragma: no cover - only on failure
            server.kill()
            server.wait(timeout=5)


def test_the_invocation_path_cleans_up_after_itself(tmp_path):
    """The wiring, not the rule. A call that leaves a server behind must have it stopped by
    the time its outcome is returned -- which is what did not happen on 2026-08-27, three
    times in one run."""
    from order_workflow.claude_code_client import ConfiguredClaudeCodeExecutionClient
    from order_workflow.executors import CancellationToken

    outcome = ConfiguredClaudeCodeExecutionClient._invoke(
        [sys.executable, "-c", "import subprocess, sys; subprocess.Popen([sys.executable, '-m', 'http.server', '0', '--bind', '127.0.0.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"],
        "",
        tmp_path,
        CancellationToken(),
        timeout=60,
    )

    assert outcome.stopped_processes, "the call left a server running and nothing stopped it"
    assert "http.server" in outcome.stopped_processes[0]
