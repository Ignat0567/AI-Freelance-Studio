from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest

from sandbox_test_lab.adapter import (
    SandboxDiagnosticCode,
    SandboxProfile,
    SandboxReadiness,
    SandboxStatus,
    SandboxTestLabAdapter,
)
from sandbox_test_lab.interactive_session import SandboxInputAction
from sandbox_test_lab.job_service import SandboxJobDisabledError
from sandbox_test_lab.models import RunStatus, SandboxCapability, SandboxRunResult
from sandbox_test_lab.production_bridge import (
    PRODUCTION_UI_EXTERNAL_OPT_IN,
    ProductionSandboxTestLabRunner,
    create_production_sandbox_runtime,
)


RUN_ID = "93bdb128-a642-4f33-b05e-b2ec9cdad1e3"


def _result(status: RunStatus, *, evidence: bool = False, errors: tuple[str, ...] = ()) -> SandboxRunResult:
    return SandboxRunResult(
        RUN_ID,
        status,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:01Z",
        1.0,
        status.value,
        "validated.json" if evidence else None,
        errors=errors,
    )


class _BlockingRunner:
    def __init__(self, terminal_status: RunStatus, release: threading.Event | None = None):
        self.terminal_status = terminal_status
        self.release = release
        self.started = threading.Event()

    def run(self, request, cancellation):
        assert request.run_id == RUN_ID
        self.started.set()
        if self.release is not None:
            self.release.wait(2)
        elif self.terminal_status is RunStatus.CANCELLED:
            assert cancellation.wait(2)
        return _result(self.terminal_status, evidence=self.terminal_status is RunStatus.PASSED)


def _runner(blocking: _BlockingRunner, enabled=lambda: True) -> ProductionSandboxTestLabRunner:
    return ProductionSandboxTestLabRunner(
        opt_in_enabled=enabled,
        self_test_runner_factory=lambda: blocking,
        self_test_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )


def _adapter(blocking: _BlockingRunner) -> SandboxTestLabAdapter:
    capability = SandboxCapability(
        supported_os=True,
        windows_edition="Professional",
        windows_build=22631,
        virtualization_available=True,
        sandbox_feature_state="enabled",
        executable_found=True,
        available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe",
        powershell_found=True,
    )
    return SandboxTestLabAdapter(
        enabled=True,
        runner=_runner(blocking),
        capability_detector=lambda: capability,
    )


def _wait_for_status(adapter, status):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = adapter.status(RUN_ID)
        if snapshot.status is status:
            return snapshot
        time.sleep(0.01)
    pytest.fail(f"runner did not reach {status.value}")


def test_bridge_runs_blocking_production_runner_asynchronously_and_maps_evidence():
    release = threading.Event()
    blocking = _BlockingRunner(RunStatus.PASSED, release)
    adapter = _adapter(blocking)

    prepared = adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    launched = adapter.launch(prepared.run_id)
    assert launched.status is SandboxStatus.LAUNCHING
    assert blocking.started.wait(1)
    assert adapter.status(RUN_ID).status is SandboxStatus.RUNNING

    release.set()
    terminal = _wait_for_status(adapter, SandboxStatus.PASSED)
    assert terminal.evidence_validated is True
    assert terminal.evidence_references == (RUN_ID,)
    assert terminal.validated_checks[0].passed is True
    assert terminal.manual_close_required is False


def test_bridge_cancellation_is_non_blocking_and_reaches_cancelled():
    blocking = _BlockingRunner(RunStatus.CANCELLED)
    adapter = _adapter(blocking)
    adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.launch(RUN_ID)
    assert blocking.started.wait(1)

    cancelling = adapter.cancel(RUN_ID)
    assert cancelling.status is SandboxStatus.CANCELLING
    assert _wait_for_status(adapter, SandboxStatus.CANCELLED).manual_close_required is False


def test_bridge_cancels_prepared_run_without_starting_worker():
    blocking = _BlockingRunner(RunStatus.CANCELLED)
    adapter = _adapter(blocking)
    adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)

    cancelled = adapter.cancel(RUN_ID)

    assert cancelled.status is SandboxStatus.CANCELLED
    assert blocking.started.is_set() is False


def test_bridge_worker_does_not_regress_cancelling_state_to_running():
    blocking = _BlockingRunner(RunStatus.CANCELLED)
    delayed = []

    class DelayedThread:
        def __init__(self, *, target, args, **kwargs):
            self.target = target
            self.args = args
            delayed.append(self)

        def start(self):
            return None

    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        self_test_runner_factory=lambda: blocking,
        self_test_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
        thread_factory=DelayedThread,
    )
    bridge.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    bridge.launch(RUN_ID)
    assert bridge.cancel(RUN_ID).status is SandboxStatus.CANCELLING

    delayed[0].target(*delayed[0].args)

    assert bridge.status(RUN_ID).status is SandboxStatus.CANCELLED
    assert blocking.started.is_set() is False


def test_bridge_late_cancellation_wins_over_racing_passed_result():
    release = threading.Event()
    blocking = _BlockingRunner(RunStatus.PASSED, release)
    adapter = _adapter(blocking)
    adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.launch(RUN_ID)
    assert blocking.started.wait(1)
    assert adapter.cancel(RUN_ID).status is SandboxStatus.CANCELLING

    release.set()

    terminal = _wait_for_status(adapter, SandboxStatus.CANCELLED)
    assert terminal.evidence_validated is True
    assert terminal.validated_checks[0].passed is True


def test_bridge_cancellation_after_internal_pass_returns_cancelled_to_adapter():
    blocking = _BlockingRunner(RunStatus.PASSED)
    bridge = _runner(blocking)
    capability = SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631,
        virtualization_available=True, sandbox_feature_state="enabled",
        executable_found=True, available=True,
    )
    adapter = SandboxTestLabAdapter(
        enabled=True, runner=bridge, capability_detector=lambda: capability,
    )
    adapter.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    adapter.launch(RUN_ID)
    assert blocking.started.wait(1)
    deadline = time.monotonic() + 1
    while bridge.status(RUN_ID).status is not SandboxStatus.PASSED:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    cancelled = adapter.cancel(RUN_ID)

    assert cancelled.status is SandboxStatus.CANCELLED
    assert adapter.status(RUN_ID).status is SandboxStatus.CANCELLED


def test_bridge_routes_screenshot_profile_to_screenshot_runner():
    screenshot = _BlockingRunner(RunStatus.CANCELLED)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        self_test_runner_factory=lambda: pytest.fail("self-test runner was selected"),
        screenshot_runner_factory=lambda: screenshot,
        screenshot_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )

    prepared = bridge.prepare(SandboxProfile.PRODUCTION_SCREENSHOT)
    cancelled = bridge.cancel(prepared.run_id)

    assert prepared.profile is SandboxProfile.PRODUCTION_SCREENSHOT
    assert cancelled.status is SandboxStatus.CANCELLED


def test_bridge_routes_interactive_session_profile_to_its_own_runner():
    interactive = _BlockingRunner(RunStatus.CANCELLED)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        self_test_runner_factory=lambda: pytest.fail("self-test runner was selected"),
        screenshot_runner_factory=lambda: pytest.fail("screenshot runner was selected"),
        interactive_session_runner_factory=lambda: interactive,
        interactive_session_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )

    prepared = bridge.prepare(SandboxProfile.INTERACTIVE_SESSION)
    cancelled = bridge.cancel(prepared.run_id)

    assert prepared.profile is SandboxProfile.INTERACTIVE_SESSION
    assert cancelled.status is SandboxStatus.CANCELLED


class _CaptureCapableRunner(_BlockingRunner):
    def __init__(self, *args, frame=b"frame-bytes", input_result=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.frame = frame
        self.capture_calls = []
        self.input_result = input_result
        self.input_calls = []

    def capture_frame(self, run_id):
        self.capture_calls.append(run_id)
        return self.frame

    def send_input(self, run_id, action):
        self.input_calls.append((run_id, action))
        return self.input_result


def test_current_frame_delegates_for_interactive_session_run():
    release = threading.Event()
    interactive = _CaptureCapableRunner(RunStatus.CANCELLED, release)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        interactive_session_runner_factory=lambda: interactive,
        interactive_session_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )
    bridge.prepare(SandboxProfile.INTERACTIVE_SESSION)
    bridge.launch(RUN_ID)
    assert interactive.started.wait(1)

    assert bridge.current_frame(RUN_ID) == b"frame-bytes"
    assert interactive.capture_calls == [RUN_ID]
    assert bridge.current_frame("99999999-9999-9999-9999-999999999999") is None
    release.set()


def test_current_frame_is_none_for_non_interactive_profile():
    release = threading.Event()
    blocking = _BlockingRunner(RunStatus.CANCELLED, release)
    bridge = _runner(blocking)
    bridge.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    bridge.launch(RUN_ID)
    assert blocking.started.wait(1)

    assert bridge.current_frame(RUN_ID) is None
    release.set()


def test_current_frame_is_none_when_runner_lacks_capture_support():
    interactive = _BlockingRunner(RunStatus.CANCELLED)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        interactive_session_runner_factory=lambda: interactive,
        interactive_session_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )
    bridge.prepare(SandboxProfile.INTERACTIVE_SESSION)
    assert bridge.current_frame(RUN_ID) is None


def test_current_frame_is_none_without_a_prepared_run():
    bridge = ProductionSandboxTestLabRunner(opt_in_enabled=lambda: True)
    assert bridge.current_frame(RUN_ID) is None


def test_send_input_delegates_for_interactive_session_run():
    release = threading.Event()
    interactive = _CaptureCapableRunner(RunStatus.CANCELLED, release)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        interactive_session_runner_factory=lambda: interactive,
        interactive_session_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )
    bridge.prepare(SandboxProfile.INTERACTIVE_SESSION)
    bridge.launch(RUN_ID)
    assert interactive.started.wait(1)

    action = SandboxInputAction(kind="key", key="enter")
    assert bridge.send_input(RUN_ID, action) is True
    assert interactive.input_calls == [(RUN_ID, action)]
    assert bridge.send_input("99999999-9999-9999-9999-999999999999", action) is False
    release.set()


def test_send_input_is_false_for_non_interactive_profile():
    release = threading.Event()
    blocking = _BlockingRunner(RunStatus.CANCELLED, release)
    bridge = _runner(blocking)
    bridge.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    bridge.launch(RUN_ID)
    assert blocking.started.wait(1)

    assert bridge.send_input(RUN_ID, SandboxInputAction(kind="key", key="enter")) is False
    release.set()


def test_send_input_is_false_when_runner_lacks_input_support():
    interactive = _BlockingRunner(RunStatus.CANCELLED)
    bridge = ProductionSandboxTestLabRunner(
        opt_in_enabled=lambda: True,
        interactive_session_runner_factory=lambda: interactive,
        interactive_session_request_factory=lambda: SimpleNamespace(run_id=RUN_ID),
    )
    bridge.prepare(SandboxProfile.INTERACTIVE_SESSION)
    assert bridge.send_input(RUN_ID, SandboxInputAction(kind="key", key="enter")) is False


def test_send_input_is_false_without_a_prepared_run():
    bridge = ProductionSandboxTestLabRunner(opt_in_enabled=lambda: True)
    assert bridge.send_input(RUN_ID, SandboxInputAction(kind="key", key="enter")) is False


def test_bridge_rechecks_exact_opt_in_before_launch():
    environment = {PRODUCTION_UI_EXTERNAL_OPT_IN: "1"}
    bridge = _runner(_BlockingRunner(RunStatus.PASSED), lambda: environment.get(PRODUCTION_UI_EXTERNAL_OPT_IN) == "1")
    bridge.prepare(SandboxProfile.PRODUCTION_SELF_TEST)
    environment.pop(PRODUCTION_UI_EXTERNAL_OPT_IN)

    with pytest.raises(RuntimeError, match="opt_in_required"):
        bridge.launch(RUN_ID)


def test_final_external_launch_guard_rejects_an_active_unrelated_session(monkeypatch):
    bridge = _runner(_BlockingRunner(RunStatus.PASSED))
    monkeypatch.setattr(
        "sandbox_test_lab.production_bridge.ensure_no_active_windows_sandbox_session",
        lambda: (_ for _ in ()).throw(RuntimeError("active_windows_sandbox_session")),
    )

    with pytest.raises(RuntimeError, match="active_windows_sandbox_session"):
        bridge._guard_external_launch()


def test_bridge_marks_only_exact_session_cleanup_failures_for_manual_close():
    translated = ProductionSandboxTestLabRunner._translate(
        SandboxProfile.PRODUCTION_SELF_TEST,
        _result(
            RunStatus.INFRASTRUCTURE_ERROR,
            errors=("owned_sandbox_session_cleanup_failed_WorkspaceError",),
        ),
    )
    assert translated.manual_close_required is True


def test_bridge_surfaces_ownership_failed_when_test_failed_and_cleanup_failed():
    translated = ProductionSandboxTestLabRunner._translate(
        SandboxProfile.PRODUCTION_SELF_TEST,
        _result(
            RunStatus.FAILED,
            evidence=False,
            errors=("owned_sandbox_session_cleanup_failed_WorkspaceError",),
        ),
    )
    assert translated.manual_close_required is True
    assert translated.errors == (SandboxDiagnosticCode.SANDBOX_OWNERSHIP_FAILED,)


def test_bridge_surfaces_guest_succeeded_ownership_failed_when_evidence_validated():
    translated = ProductionSandboxTestLabRunner._translate(
        SandboxProfile.PRODUCTION_SELF_TEST,
        _result(
            RunStatus.PASSED,
            evidence=True,
            errors=("owned_sandbox_session_cleanup_failed_WorkspaceError",),
        ),
    )
    assert translated.manual_close_required is True
    assert translated.errors == (SandboxDiagnosticCode.SANDBOX_GUEST_SUCCEEDED_OWNERSHIP_FAILED,)


def test_runtime_is_default_off_without_validating_or_launching(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "sandbox_test_lab.production_bridge.validate_trusted_production_artifact",
        lambda: called.append(True),
    )
    runtime = create_production_sandbox_runtime(tmp_path, environ={})

    assert runtime.availability_provider().readiness is SandboxReadiness.DISABLED
    with pytest.raises(SandboxJobDisabledError):
        runtime.service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    assert called == []


def test_runtime_fails_closed_when_opted_in_artifact_validation_fails(tmp_path, monkeypatch):
    def fail_validation():
        raise ValueError("invalid artifact")

    monkeypatch.setattr(
        "sandbox_test_lab.production_bridge.validate_trusted_production_artifact",
        fail_validation,
    )
    runtime = create_production_sandbox_runtime(
        tmp_path,
        environ={PRODUCTION_UI_EXTERNAL_OPT_IN: "1"},
    )

    assert runtime.availability_provider().readiness is SandboxReadiness.UNAVAILABLE
    with pytest.raises(SandboxJobDisabledError):
        runtime.service.start(SandboxProfile.PRODUCTION_SELF_TEST)
