from __future__ import annotations

from pathlib import Path
from threading import Event
import xml.etree.ElementTree as ET

import pytest

from sandbox_test_lab.interactive_session import (
    MAX_INPUT_TEXT_LENGTH,
    MAX_SESSION_SECONDS,
    PROJECT_DESTINATION,
    InteractiveSessionRequest,
    SandboxInputAction,
    interactive_session_run_root,
    resolve_project_source,
    stage_project_files,
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
        self.input_calls = []
        self.input_result = True

    def capture_window_png(self, **kwargs):
        self.capture_calls.append(kwargs)
        return self.capture_result

    def send_input(self, action):
        self.input_calls.append(action)
        return self.input_result

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


class TestSandboxInputAction:
    """This is the entire security-relevant validation surface for Phase 5c -- no action
    reaches PowerShell without passing through here (and, redundantly by design, the
    matching Pydantic model at the API layer) first."""

    def test_click_accepts_boundary_coordinates(self):
        for x, y in ((0.0, 0.0), (1.0, 1.0), (0.5, 0.5)):
            SandboxInputAction(kind="click", x=x, y=y)

    @pytest.mark.parametrize("x,y", [(-0.01, 0.5), (1.01, 0.5), (0.5, -0.01), (0.5, 1.01)])
    def test_click_rejects_out_of_range_coordinates(self, x, y):
        with pytest.raises(ValueError, match="0.0, 1.0"):
            SandboxInputAction(kind="click", x=x, y=y)

    def test_click_rejects_bool_as_coordinate(self):
        with pytest.raises(ValueError, match="0.0, 1.0"):
            SandboxInputAction(kind="click", x=True, y=0.5)

    def test_click_requires_both_coordinates(self):
        with pytest.raises(ValueError, match="requires x and y"):
            SandboxInputAction(kind="click", x=0.5, y=None)

    def test_click_rejects_unknown_button(self):
        with pytest.raises(ValueError, match="button is invalid"):
            SandboxInputAction(kind="click", x=0.5, y=0.5, button="middle")

    def test_click_rejects_extra_fields_from_other_kinds(self):
        with pytest.raises(ValueError, match="only x, y, and button"):
            SandboxInputAction(kind="click", x=0.5, y=0.5, text="sneaky")

    def test_click_defaults_to_left_button(self):
        assert SandboxInputAction(kind="click", x=0.5, y=0.5).button == "left"

    def test_type_accepts_bounded_text(self):
        SandboxInputAction(kind="type", text="a")
        SandboxInputAction(kind="type", text="x" * MAX_INPUT_TEXT_LENGTH)

    def test_type_rejects_empty_text(self):
        with pytest.raises(ValueError, match="1-"):
            SandboxInputAction(kind="type", text="")

    def test_type_rejects_oversized_text(self):
        with pytest.raises(ValueError, match="1-"):
            SandboxInputAction(kind="type", text="x" * (MAX_INPUT_TEXT_LENGTH + 1))

    def test_type_rejects_non_string_text(self):
        with pytest.raises(ValueError, match="1-"):
            SandboxInputAction(kind="type", text=12345)

    @pytest.mark.parametrize("text", ["hi\n", "hi\r", "hi\ttab", "bad\x00null"])
    def test_type_rejects_control_characters(self, text):
        with pytest.raises(ValueError, match="control characters"):
            SandboxInputAction(kind="type", text=text)

    def test_type_rejects_extra_fields_from_other_kinds(self):
        with pytest.raises(ValueError, match="only text"):
            SandboxInputAction(kind="type", text="hi", key="enter")

    @pytest.mark.parametrize("key", ["enter", "escape", "tab", "backspace"])
    def test_key_accepts_allow_listed_names(self, key):
        SandboxInputAction(kind="key", key=key)

    def test_key_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="key name is invalid"):
            SandboxInputAction(kind="key", key="F5")

    def test_key_rejects_extra_fields_from_other_kinds(self):
        with pytest.raises(ValueError, match="only key"):
            SandboxInputAction(kind="key", key="enter", x=0.5)

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="kind is invalid"):
            SandboxInputAction(kind="drag")


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


def test_wsb_maps_project_source_read_write_and_disables_redirection(tmp_path):
    project = tmp_path / "staged"
    project.mkdir()
    config_file = write_interactive_session_wsb(tmp_path / "run", project_source=project)
    tree = ET.parse(config_file)
    root = tree.getroot()
    assert root.find("LogonCommand") is None
    assert root.find("Networking").text == "Disable"
    assert root.find("ClipboardRedirection").text == "Disable"
    assert root.find("PrinterRedirection").text == "Disable"
    mapped = root.find("MappedFolders/MappedFolder")
    assert mapped.findtext("HostFolder") == str(project)
    assert mapped.findtext("SandboxFolder") == PROJECT_DESTINATION
    assert mapped.findtext("ReadOnly") == "false"


class TestInteractiveSessionRequestProjectSource:
    def test_accepts_a_real_existing_absolute_directory(self, tmp_path):
        request = InteractiveSessionRequest(project_source=tmp_path)
        assert request.project_source == tmp_path

    def test_rejects_a_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="absolute existing directory"):
            InteractiveSessionRequest(project_source=Path("relative"))

    def test_rejects_a_nonexistent_directory(self, tmp_path):
        with pytest.raises(ValueError, match="absolute existing directory"):
            InteractiveSessionRequest(project_source=tmp_path / "missing")


class TestResolveProjectSource:
    def _project(self, projects_root, name="my-project"):
        project = projects_root / name
        project.mkdir(parents=True)
        (project / "app.exe").write_bytes(b"fake")
        return project

    def test_valid_name_and_existing_directory_resolves(self, tmp_path):
        project = self._project(tmp_path)
        resolved = resolve_project_source("my-project", projects_root=tmp_path)
        assert resolved == project

    @pytest.mark.parametrize("name", ["", "a" * 101, "../escape", "sub/dir", "back\\slash", "has space", "unïcode"])
    def test_invalid_name_formats_are_rejected(self, tmp_path, name):
        with pytest.raises(ValueError, match="project name is invalid"):
            resolve_project_source(name, projects_root=tmp_path)

    def test_missing_directory_is_rejected(self, tmp_path):
        with pytest.raises(WorkspaceError):
            resolve_project_source("does-not-exist", projects_root=tmp_path)

    def test_file_instead_of_directory_is_rejected(self, tmp_path):
        (tmp_path / "not-a-dir").write_bytes(b"x")
        with pytest.raises(WorkspaceError, match="regular non-reparse directory"):
            resolve_project_source("not-a-dir", projects_root=tmp_path)

    def test_empty_directory_is_rejected(self, tmp_path):
        (tmp_path / "empty-project").mkdir()
        with pytest.raises(WorkspaceError, match="empty"):
            resolve_project_source("empty-project", projects_root=tmp_path)

    def test_reparse_point_directory_is_rejected(self, tmp_path, monkeypatch):
        project = self._project(tmp_path, "reparse-project")
        import sandbox_test_lab.interactive_session as interactive_session_module
        monkeypatch.setattr(
            interactive_session_module, "_is_reparse_point",
            lambda path: path == project,
        )
        with pytest.raises(WorkspaceError, match="regular non-reparse directory"):
            resolve_project_source("reparse-project", projects_root=tmp_path)

    def test_symlinked_directory_is_rejected(self, tmp_path):
        target = tmp_path / "real-project"
        target.mkdir()
        (target / "app.exe").write_bytes(b"fake")
        link = tmp_path / "linked-project"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink creation is unavailable")
        with pytest.raises(WorkspaceError):
            resolve_project_source("linked-project", projects_root=tmp_path)


class TestStageProjectFiles:
    def test_copies_nested_content(self, tmp_path):
        source = tmp_path / "source"
        (source / "sub").mkdir(parents=True)
        (source / "app.exe").write_bytes(b"fake")
        (source / "sub" / "data.txt").write_text("hello")
        run_root = tmp_path / "run"

        staged = stage_project_files(source, run_root)

        assert staged == run_root / "project"
        assert (staged / "app.exe").read_bytes() == b"fake"
        assert (staged / "sub" / "data.txt").read_text() == "hello"

    @pytest.mark.parametrize("excluded_name", [".env", ".env.local", ".git", ".freelancerstudio"])
    def test_excludes_secret_shaped_entries(self, tmp_path, excluded_name):
        source = tmp_path / "source"
        source.mkdir()
        (source / "app.exe").write_bytes(b"fake")
        excluded = source / excluded_name
        if excluded_name in {".git", ".freelancerstudio"}:
            excluded.mkdir()
            (excluded / "secret").write_text("token")
        else:
            excluded.write_text("SECRET=token")
        run_root = tmp_path / "run"

        staged = stage_project_files(source, run_root)

        assert (staged / "app.exe").is_file()
        assert not (staged / excluded_name).exists()

    def test_excludes_nested_secret_shaped_entries(self, tmp_path):
        source = tmp_path / "source"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / ".env").write_text("SECRET=token")
        run_root = tmp_path / "run"

        staged = stage_project_files(source, run_root)

        assert not (staged / "sub" / ".env").exists()

    def test_skips_symlinks_rather_than_following_them(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "outside-secret.txt"
        target.write_text("SECRET")
        link = source / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        run_root = tmp_path / "run"

        staged = stage_project_files(source, run_root)

        assert not (staged / "linked.txt").exists()


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


def test_runner_resolves_run_root_to_its_physical_location_before_use(tmp_path, monkeypatch):
    """Regression test mirroring video_evidence.py's Phase 5d fix: on a machine where
    Python itself runs via a virtualized install, Path.resolve() can return a real physical
    location that differs from the logical path used to build run_root -- but
    WindowsSandbox.exe (an external, unpackaged process reading the .wsb file's
    HostFolder) can only ever see the physical path. Simulated here (no privilege to create
    real symlinks in this environment) by monkeypatching Path.resolve to map one directory
    tree onto another."""
    import sandbox_test_lab.interactive_session_runner as runner_module

    logical_root = tmp_path / "logical"
    physical_root = tmp_path / "physical"
    physical_root.mkdir()

    real_resolve = Path.resolve

    def fake_resolve(path, *args, **kwargs):
        try:
            relative = path.relative_to(logical_root)
        except ValueError:
            return real_resolve(path, *args, **kwargs)
        return real_resolve(physical_root / relative, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    captured = {}
    real_write = runner_module.write_interactive_session_wsb

    def spy_write(run_root, **kwargs):
        captured["run_root"] = run_root
        return real_write(run_root, **kwargs)

    monkeypatch.setattr(runner_module, "write_interactive_session_wsb", spy_write)

    clock = _Clock()
    session = _FakeSession()
    cancellation = Event()

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        cancellation.set()

    runner = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=logical_root,
    )
    runner.run(InteractiveSessionRequest(), cancellation)

    assert str(logical_root) not in str(captured["run_root"])
    assert str(physical_root) in str(captured["run_root"])


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
        frame_capture_interval=float("inf"),  # isolate on-demand capture from the periodic recorder (tested separately)
    )
    result = runner.run(request, cancellation)

    assert captured["during_run"] == session.capture_result
    assert captured["wrong_run_id"] is None
    assert session.capture_calls == [{}]
    assert result.status == RunStatus.CANCELLED
    # Once the run has finished, the runner no longer has a live session to capture from.
    assert runner.capture_frame(request.run_id) is None


def test_send_input_returns_false_when_nothing_is_running(tmp_path):
    runner = InteractiveSessionRunner(capability_detector=_capability, runtime_root=tmp_path)
    action = SandboxInputAction(kind="key", key="enter")
    assert runner.send_input("11111111-1111-1111-1111-111111111111", action) is False


def test_send_input_delegates_to_the_live_session_while_running(tmp_path):
    clock = _Clock()
    session = _FakeSession()
    request = InteractiveSessionRequest()
    cancellation = Event()
    sent: dict = {}
    action = SandboxInputAction(kind="click", x=0.5, y=0.5)

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if "during_run" not in sent:
            sent["during_run"] = runner.send_input(request.run_id, action)
            sent["wrong_run_id"] = runner.send_input("22222222-2222-2222-2222-222222222222", action)
        cancellation.set()

    runner = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    )
    result = runner.run(request, cancellation)

    assert sent["during_run"] is True
    assert sent["wrong_run_id"] is False
    assert session.input_calls == [action]
    assert result.status == RunStatus.CANCELLED
    # Once the run has finished, the runner no longer has a live session to send input to.
    assert runner.send_input(request.run_id, action) is False


def test_send_input_reports_when_the_session_declines_the_action(tmp_path):
    """The session itself can decline (e.g. the focus-verification gate failed inside the
    sandbox) -- the runner must pass that False through, not paper over it."""
    clock = _Clock()
    session = _FakeSession()
    session.input_result = False
    request = InteractiveSessionRequest()
    cancellation = Event()
    sent: dict = {}
    action = SandboxInputAction(kind="key", key="escape")

    def sleeper(seconds: float) -> None:
        clock.sleeper(seconds)
        if "during_run" not in sent:
            sent["during_run"] = runner.send_input(request.run_id, action)
        cancellation.set()

    runner = InteractiveSessionRunner(
        capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
        monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
        session_factory=lambda pid, started_at: session,
        runtime_root=tmp_path,
    )
    runner.run(request, cancellation)

    assert sent["during_run"] is False


class TestVideoEvidenceRecording:
    """Phase 5d: periodic frame capture + video encoding wiring inside run()'s own loop --
    no new thread, driven by the same monotonic/sleeper injection as every other timing
    test in this file."""

    def _runner(self, tmp_path, session, *, cancellation, cancel_at, **overrides):
        clock = _Clock()

        def sleeper(seconds: float) -> None:
            clock.sleeper(seconds)
            if clock.value >= cancel_at:
                cancellation.set()

        defaults = dict(
            capability_detector=_capability, launcher=lambda argv: _FakeProcess(),
            monotonic=clock.monotonic, sleeper=sleeper, poll_interval=1,
            session_factory=lambda pid, started_at: session,
            runtime_root=tmp_path, frame_capture_interval=1.0,
        )
        defaults.update(overrides)
        return InteractiveSessionRunner(**defaults)

    def test_captures_frames_at_the_configured_interval_and_hands_them_to_the_encoder(self, tmp_path):
        session = _FakeSession()
        cancellation = Event()
        encoded_calls = []

        def fake_encoder(frame_paths, destination, *, run_root, ffmpeg_path):
            encoded_calls.append(list(frame_paths))
            return False

        runner = self._runner(tmp_path, session, cancellation=cancellation, cancel_at=5, video_encoder=fake_encoder)
        request = InteractiveSessionRequest()

        result = runner.run(request, cancellation)

        assert result.status == RunStatus.CANCELLED
        assert session.capture_calls == [{}] * 4
        assert len(encoded_calls) == 1
        assert len(encoded_calls[0]) == 4
        assert [path.name for path in encoded_calls[0]] == [
            "frame-000000.png", "frame-000001.png", "frame-000002.png", "frame-000003.png",
        ]

    def test_ring_buffers_frames_beyond_max_retained_and_deletes_the_oldest_from_disk(self, tmp_path):
        session = _FakeSession()
        cancellation = Event()
        disk_state: dict = {}

        def fake_encoder(frame_paths, destination, *, run_root, ffmpeg_path):
            disk_state["remaining_on_disk"] = sorted(p.name for p in (run_root / "frames").glob("*.png"))
            disk_state["frame_paths"] = list(frame_paths)
            return False

        runner = self._runner(
            tmp_path, session, cancellation=cancellation, cancel_at=6,
            max_retained_frames=2, video_encoder=fake_encoder,
        )
        runner.run(InteractiveSessionRequest(), cancellation)

        assert [path.name for path in disk_state["frame_paths"]] == ["frame-000003.png", "frame-000004.png"]
        assert sorted(disk_state["remaining_on_disk"]) == ["frame-000003.png", "frame-000004.png"]

    def test_skips_periodic_capture_when_the_session_returns_no_frame(self, tmp_path):
        session = _FakeSession()
        session.capture_result = None
        cancellation = Event()
        encoded_calls = []

        def fake_encoder(frame_paths, destination, *, run_root, ffmpeg_path):
            encoded_calls.append(list(frame_paths))
            return False

        runner = self._runner(tmp_path, session, cancellation=cancellation, cancel_at=2, video_encoder=fake_encoder)
        result = runner.run(InteractiveSessionRequest(), cancellation)

        assert result.status == RunStatus.CANCELLED
        assert encoded_calls == []  # never invoked -- nothing was ever successfully captured

    def test_run_status_is_unaffected_when_encoding_raises(self, tmp_path):
        session = _FakeSession()
        cancellation = Event()

        def raising_encoder(frame_paths, destination, *, run_root, ffmpeg_path):
            raise RuntimeError("boom")

        runner = self._runner(tmp_path, session, cancellation=cancellation, cancel_at=2, video_encoder=raising_encoder)
        result = runner.run(InteractiveSessionRequest(), cancellation)

        assert result.status == RunStatus.CANCELLED
        assert result.exit_reason == "cancelled_by_host"

    def test_run_status_is_unaffected_when_archiving_raises(self, tmp_path):
        session = _FakeSession()
        cancellation = Event()

        def raising_archiver(diagnostics_root, run_id, video_path):
            raise RuntimeError("boom")

        runner = self._runner(
            tmp_path, session, cancellation=cancellation, cancel_at=2,
            video_encoder=lambda *args, **kwargs: True, video_archiver=raising_archiver,
        )
        result = runner.run(InteractiveSessionRequest(), cancellation)

        assert result.status == RunStatus.CANCELLED
        assert result.exit_reason == "cancelled_by_host"

    def test_archives_the_encoded_video_when_encoding_succeeds(self, tmp_path):
        session = _FakeSession()
        cancellation = Event()
        archive_calls = []
        diagnostics_root = tmp_path / "diagnostics"

        runner = self._runner(
            tmp_path, session, cancellation=cancellation, cancel_at=2,
            diagnostics_root=diagnostics_root,
            video_encoder=lambda *args, **kwargs: True,
            video_archiver=lambda *args: archive_calls.append(args),
        )
        request = InteractiveSessionRequest()

        runner.run(request, cancellation)

        assert len(archive_calls) == 1
        archived_diagnostics_root, archived_run_id, archived_video_path = archive_calls[0]
        assert archived_diagnostics_root == diagnostics_root
        assert archived_run_id == request.run_id
        assert archived_video_path.name == "session.webm"

    def test_frames_directory_is_removed_after_the_run_finishes(self, tmp_path):
        """Uses the real default encoder (no pinned ffmpeg binary in this test environment,
        so it fails and returns False) -- cleanup must happen regardless of encode success."""
        session = _FakeSession()
        cancellation = Event()

        runner = self._runner(tmp_path, session, cancellation=cancellation, cancel_at=2)
        request = InteractiveSessionRequest()

        result = runner.run(request, cancellation)

        run_root = interactive_session_run_root(result.run_id, tmp_path)
        assert not (run_root / "frames").exists()
