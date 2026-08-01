from __future__ import annotations

from pathlib import Path
from threading import Event
import xml.etree.ElementTree as ET

import pytest

from sandbox_test_lab.interactive_session import (
    MAX_SESSION_SECONDS,
    InteractiveSessionRequest,
    interactive_session_run_root,
    write_interactive_session_wsb,
)
from sandbox_test_lab.interactive_session_runner import InteractiveSessionRunner
from sandbox_test_lab.models import RunStatus, SandboxCapability
from sandbox_test_lab.workspace import WorkspaceError


def _capability(**overrides) -> SandboxCapability:
    base = dict(
        supported_os=True, windows_edition="Professional", windows_build=22631,
        virtualization_available=True, sandbox_feature_state="enabled", executable_found=True,
        available=True, executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )
    base.update(overrides)
    return SandboxCapability(**base)


class _FakeProcess:
    def __init__(self, pid: int = 4321):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode or 0


class _FakeSession:
    def __init__(
        self, *, closes_on_request: bool = True, closes_on_terminate: bool = True,
        model: str = "legacy_client", server_pid: int | None = None,
        server_closes_on_terminate: bool = True,
    ):
        self._closes_on_request = closes_on_request
        self._closes_on_terminate = closes_on_terminate
        self._server_closes_on_terminate = server_closes_on_terminate
        self._alive = True
        self._server_alive = server_pid is not None
        self.model = model
        self.server_pid = server_pid
        self.request_close_calls = 0
        self.terminate_calls = 0
        self.terminate_server_calls = 0
        self.capture_calls = []
        self.capture_result = b"fake-frame-bytes"

    def capture_window_png(self, **kwargs):
        self.capture_calls.append(kwargs)
        return self.capture_result

    def is_running(self) -> bool:
        return self._alive or self._server_alive

    def request_close(self) -> None:
        self.request_close_calls += 1
        if self._closes_on_request:
            self._alive = False

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self._closes_on_terminate:
            self._alive = False

    def terminate_server(self) -> None:
        self.terminate_server_calls += 1
        if self._server_closes_on_terminate:
            self._server_alive = False


class _Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleeper(self, seconds: float) -> None:
        self.value += seconds


def test_request_rejects_non_default_timeout():
    with pytest.raises(ValueError, match="timeout is fixed"):
        InteractiveSessionRequest(timeout_seconds=10.0)


def test_request_default_run_id_is_a_uuid():
    request = InteractiveSessionRequest()
    assert len(request.run_id) == 36


def test_run_root_is_isolated_from_other_profiles(tmp_path):
    run_id = "12345678-1234-1234-1234-123456789012"
    root = interactive_session_run_root(run_id, tmp_path)
    assert root == tmp_path / "interactive-runs" / run_id


def test_wsb_has_no_logon_command_and_no_shared_folders(tmp_path):
    config_file = write_interactive_session_wsb(tmp_path)
    tree = ET.parse(config_file)
    root = tree.getroot()
    assert root.tag == "Configuration"
    assert root.find("LogonCommand") is None
    assert root.find("MappedFolders") is None
    assert root.find("Networking").text == "Disable"
    assert root.find("VGpu").text == "Disable"


def test_wsb_rejects_out_of_range_memory(tmp_path):
    with pytest.raises(ValueError, match="memory_mb"):
        write_interactive_session_wsb(tmp_path, memory_mb=1024)


def test_runner_reports_unavailable_when_capability_missing(tmp_path):
    result = InteractiveSessionRunner(
        capability_detector=lambda: _capability(available=False, blockers=("sandbox_feature_disabled",)),
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())
    assert result.status == RunStatus.UNAVAILABLE
    assert result.exit_reason == "sandbox_unavailable"


def test_runner_reports_cancelled_before_launch(tmp_path):
    cancellation = Event()
    cancellation.set()
    result = InteractiveSessionRunner(
        capability_detector=_capability, runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)
    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled_before_launch"


def test_runner_closes_session_when_host_cancels_mid_run(tmp_path):
    clock = _Clock()
    session = _FakeSession()
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 2:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled_by_host"
    assert session.request_close_calls == 1
    assert session.terminate_calls == 0
    run_root = interactive_session_run_root(result.run_id, tmp_path)
    assert (run_root / "host-result.json").is_file()


def test_runner_ends_cleanly_when_guest_closes_its_own_window(tmp_path):
    clock = _Clock()
    session = _FakeSession()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        session._alive = False  # user closed the sandbox window themselves

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())

    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "guest_closed_session"
    assert session.request_close_calls == 0


def test_runner_enforces_max_duration_and_closes_session(tmp_path):
    clock = _Clock()
    session = _FakeSession()
    request = InteractiveSessionRequest()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)

    runner = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    )
    clock.value = MAX_SESSION_SECONDS  # simulate the deadline already elapsed

    result = runner.run(request)
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "interactive_session_max_duration_exceeded"
    assert session.request_close_calls == 1


def test_runner_escalates_to_terminate_when_graceful_close_fails(tmp_path):
    clock = _Clock()
    session = _FakeSession(closes_on_request=False, closes_on_terminate=True)
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 2:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert result.status == RunStatus.CANCELLED
    assert session.request_close_calls == 1
    assert session.terminate_calls == 1
    assert "owned_sandbox_session_survived_terminate" not in result.errors


def test_runner_records_error_when_session_survives_terminate(tmp_path):
    clock = _Clock()
    session = _FakeSession(closes_on_request=False, closes_on_terminate=False)
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 2:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert result.status == RunStatus.CANCELLED
    assert session.terminate_calls == 1
    assert "owned_sandbox_session_survived_terminate" in result.errors


def test_runner_kills_surviving_remote_session_server_as_last_resort(tmp_path):
    """Regression test for a real 2026-07-31 finding: closing/killing the remote-session
    client does not guarantee its server process exits too."""
    clock = _Clock()
    session = _FakeSession(
        closes_on_request=False, closes_on_terminate=True,
        model="remote_session", server_pid=9999, server_closes_on_terminate=True,
    )
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 2:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert result.status == RunStatus.CANCELLED
    assert session.terminate_calls == 1
    assert session.terminate_server_calls == 1
    assert "owned_sandbox_session_survived_terminate" not in result.errors


def test_runner_records_error_when_remote_session_server_also_survives(tmp_path):
    clock = _Clock()
    session = _FakeSession(
        closes_on_request=False, closes_on_terminate=True,
        model="remote_session", server_pid=9999, server_closes_on_terminate=False,
    )
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 2:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert result.status == RunStatus.CANCELLED
    assert session.terminate_server_calls == 1
    assert "owned_sandbox_session_survived_terminate" in result.errors


def test_runner_reports_infrastructure_error_on_workspace_failure(tmp_path, monkeypatch):
    import sandbox_test_lab.interactive_session_runner as runner_module

    def failing_write(*args, **kwargs):
        raise WorkspaceError("simulated_workspace_failure")

    monkeypatch.setattr(runner_module, "write_interactive_session_wsb", failing_write)

    result = InteractiveSessionRunner(
        capability_detector=_capability, runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "infrastructure_error"
    assert any("simulated_workspace_failure" in error for error in result.errors)


def test_runner_retries_discovery_timeout_and_eventually_succeeds(tmp_path):
    """Regression coverage for a real, repeatedly-observed 2026-07-31 finding: Windows
    Sandbox on this dev machine intermittently fails session discovery. The runner should
    retry that specific, known-transient failure rather than giving up on the first try."""
    clock = _Clock()
    session = _FakeSession()
    processes: list[_FakeProcess] = []

    def launcher(argv):
        process = _FakeProcess(pid=1000 + len(processes))
        processes.append(process)
        return process

    attempts = {"n": 0}

    def session_factory(pid, started_at):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise WorkspaceError("owned_sandbox_client_discovery_failed")
        return session

    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if clock.value >= 5:
            cancellation.set()

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=launcher,
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=session_factory,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest(), cancellation)

    assert attempts["n"] == 3
    assert len(processes) == 3
    assert processes[0].returncode == -15
    assert processes[1].returncode == -15
    assert result.status == RunStatus.CANCELLED
    assert any("launch_attempt_1_failed" in error for error in result.errors)
    assert any("launch_attempt_2_failed" in error for error in result.errors)


def test_runner_gives_up_after_max_launch_attempts(tmp_path):
    clock = _Clock()
    processes: list[_FakeProcess] = []

    def launcher(argv):
        process = _FakeProcess(pid=2000 + len(processes))
        processes.append(process)
        return process

    def session_factory(pid, started_at):
        raise WorkspaceError("owned_sandbox_client_discovery_failed")

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=launcher,
        monotonic=clock.monotonic, sleeper=clock.sleeper, poll_interval=1,
        session_factory=session_factory,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())

    from sandbox_test_lab.interactive_session_runner import MAX_LAUNCH_ATTEMPTS
    assert len(processes) == MAX_LAUNCH_ATTEMPTS
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert any("owned_sandbox_client_discovery_failed" in error for error in result.errors)


def test_runner_does_not_retry_non_discovery_workspace_errors(tmp_path):
    clock = _Clock()
    processes: list[_FakeProcess] = []

    def launcher(argv):
        process = _FakeProcess(pid=3000 + len(processes))
        processes.append(process)
        return process

    def session_factory(pid, started_at):
        raise WorkspaceError("owned_sandbox_client_identity_rejected")

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=launcher,
        monotonic=clock.monotonic, sleeper=clock.sleeper, poll_interval=1,
        session_factory=session_factory,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())

    assert len(processes) == 1
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert any("owned_sandbox_client_identity_rejected" in error for error in result.errors)


def test_runner_stops_retrying_when_session_guard_detects_a_conflict(tmp_path):
    """If a failed attempt leaves an orphaned Sandbox process behind, the next attempt's
    session_guard() check must fail loud, not silently retry into a broken state."""
    clock = _Clock()
    processes: list[_FakeProcess] = []

    def launcher(argv):
        process = _FakeProcess(pid=4000 + len(processes))
        processes.append(process)
        return process

    def session_factory(pid, started_at):
        raise WorkspaceError("owned_sandbox_client_discovery_failed")

    guard_calls = {"n": 0}

    def session_guard():
        guard_calls["n"] += 1
        if guard_calls["n"] >= 2:
            raise WorkspaceError("active_windows_sandbox_session")

    result = InteractiveSessionRunner(
        capability_detector=_capability, launcher=launcher,
        monotonic=clock.monotonic, sleeper=clock.sleeper, poll_interval=1,
        session_factory=session_factory, session_guard=session_guard,
        runtime_root=tmp_path,
    ).run(InteractiveSessionRequest())

    assert len(processes) == 1
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert any("active_windows_sandbox_session" in error for error in result.errors)


def test_capture_frame_returns_none_when_nothing_is_running(tmp_path):
    runner = InteractiveSessionRunner(capability_detector=_capability, runtime_root=tmp_path)
    assert runner.capture_frame("11111111-1111-1111-1111-111111111111") is None


def test_capture_frame_delegates_to_the_live_session_while_running(tmp_path):
    clock = _Clock()
    session = _FakeSession()
    request = InteractiveSessionRequest()
    cancellation = Event()
    captured: dict = {}

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if "frame" not in captured:
            captured["during_run"] = runner.capture_frame(request.run_id)
            captured["wrong_run_id"] = runner.capture_frame("22222222-2222-2222-2222-222222222222")
            captured["frame"] = True
        cancellation.set()

    runner = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    )
    result = runner.run(request, cancellation)

    assert captured["during_run"] == session.capture_result
    assert captured["wrong_run_id"] is None
    assert session.capture_calls == [{}]
    assert result.status == RunStatus.CANCELLED
    # Once the run has finished, the runner no longer has a live session to capture from.
    assert runner.capture_frame(request.run_id) is None
