import json
from threading import Event
import time
from uuid import uuid4

import pytest

from sandbox_test_lab.adapter import (
    SandboxProfile,
    SandboxProgress,
    SandboxReadiness,
    SandboxStatus,
    SandboxTestLabResult,
)
from sandbox_test_lab.interactive_session import SandboxInputAction
from sandbox_test_lab.job_service import (
    InMemorySandboxJobStateStore,
    JsonSandboxJobStateStore,
    SandboxJobDisabledError,
    SandboxJobError,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxTestLabJobService,
)


class ScriptedAdapter:
    def __init__(
        self,
        *,
        preparation_error=False,
        launch_status=SandboxStatus.LAUNCHING,
        statuses=(SandboxStatus.RUNNING,),
        evidence_statuses=(SandboxStatus.PASSED,),
        block_running=None,
        sensitive_values=False,
        frame_result=None,
        input_result=False,
    ):
        self.backend_run_id = str(uuid4())
        self.profile = SandboxProfile.PRODUCTION_SELF_TEST
        self.preparation_error = preparation_error
        self.launch_status = launch_status
        self.statuses = list(statuses)
        self.evidence_statuses = list(evidence_statuses)
        self.block_running = block_running
        self.sensitive_values = sensitive_values
        self.frame_result = frame_result
        self.frame_calls = []
        self.input_result = input_result
        self.input_calls = []
        self.calls = []
        if sensitive_values:
            self.absolute_path = r"C:\private\artifact.exe"
            self.commands = ("installer.exe --secret",)
            self.environment = {"TOKEN": "private"}
            self.raw_evidence = {"secret": "private"}

    def _result(self, status, *, progress=None, manual_close_required=False):
        if progress is None:
            progress = {
                SandboxStatus.PREPARED: SandboxProgress.PREPARING,
                SandboxStatus.LAUNCHING: SandboxProgress.LAUNCHING,
                SandboxStatus.RUNNING: SandboxProgress.RUNNING_CHECKS,
                SandboxStatus.CANCELLING: SandboxProgress.CANCELLING,
                SandboxStatus.PASSED: SandboxProgress.COMPLETE,
                SandboxStatus.FAILED: SandboxProgress.COMPLETE,
                SandboxStatus.TIMED_OUT: SandboxProgress.COMPLETE,
                SandboxStatus.CANCELLED: SandboxProgress.COMPLETE,
            }[status]
        result = SandboxTestLabResult(
            run_id=self.backend_run_id,
            profile=self.profile,
            status=status,
            readiness=SandboxReadiness.READY,
            progress=progress,
            manual_close_required=manual_close_required,
        )
        return result

    def prepare(self, profile):
        self.calls.append(("prepare", profile))
        if self.preparation_error:
            raise RuntimeError("private preparation failure")
        self.profile = profile
        return self._result(SandboxStatus.PREPARED)

    def launch(self, run_id):
        self.calls.append(("launch", run_id))
        return self._result(self.launch_status)

    def status(self, run_id):
        self.calls.append(("status", run_id))
        if self.block_running is not None:
            self.block_running.set()
        status = self.statuses.pop(0) if self.statuses else SandboxStatus.RUNNING
        return self._result(status)

    def evidence(self, run_id):
        self.calls.append(("evidence", run_id))
        status = self.evidence_statuses.pop(0) if self.evidence_statuses else SandboxStatus.RUNNING
        progress = (
            SandboxProgress.COLLECTING_EVIDENCE
            if status is SandboxStatus.RUNNING
            else None
        )
        return self._result(
            status,
            progress=progress,
            manual_close_required=status in {
                SandboxStatus.PASSED,
                SandboxStatus.FAILED,
                SandboxStatus.TIMED_OUT,
            },
        )

    def cancel(self, run_id):
        self.calls.append(("cancel", run_id))
        return self._result(SandboxStatus.CANCELLED, manual_close_required=True)

    def frame(self, run_id):
        self.frame_calls.append(run_id)
        return self.frame_result

    def send_input(self, run_id, action):
        self.input_calls.append((run_id, action))
        return self.input_result


def _service(adapter, **kwargs):
    poll_interval = kwargs.pop("poll_interval", 0.001)
    return SandboxTestLabJobService(
        enabled=True,
        adapter_factory=lambda: adapter,
        poll_interval=poll_interval,
        **kwargs,
    )


def _wait_for(service, run_id, status, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.snapshot(run_id)
        if snapshot.status is status:
            return snapshot
        time.sleep(0.001)
    raise AssertionError(f"job did not reach {status.value}")


def test_successful_job_uses_unique_public_id_and_reports_manual_close():
    adapter = ScriptedAdapter(sensitive_values=True)
    service = _service(adapter)

    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    finished = service.wait(started.run_id, 1)

    assert finished.status is SandboxJobStatus.PASSED
    assert finished.progress is SandboxProgress.COMPLETE
    assert finished.manual_close_required is True
    assert started.run_id != adapter.backend_run_id
    assert adapter.calls[:2] == [
        ("prepare", SandboxProfile.PRODUCTION_SELF_TEST),
        ("launch", adapter.backend_run_id),
    ]


def test_preparation_failure_is_normalized_without_details():
    service = _service(ScriptedAdapter(preparation_error=True))

    started = service.start("production_self_test")
    finished = service.wait(started.run_id, 1)

    assert finished.status is SandboxJobStatus.FAILED
    assert set(finished.to_dict()) == {
        "run_id",
        "profile",
        "status",
        "progress",
        "manual_close_required",
    }


@pytest.mark.parametrize(
    ("backend_status", "job_status"),
    [
        (SandboxStatus.FAILED, SandboxJobStatus.FAILED),
        (SandboxStatus.INFRASTRUCTURE_ERROR, SandboxJobStatus.FAILED),
        (SandboxStatus.TIMED_OUT, SandboxJobStatus.TIMED_OUT),
    ],
)
def test_runner_failure_and_timeout_are_normalized(backend_status, job_status):
    adapter = ScriptedAdapter(statuses=(backend_status,), evidence_statuses=())
    service = _service(adapter)

    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)

    assert service.wait(started.run_id, 1).status is job_status


def test_duplicate_concurrent_launch_is_rejected_until_terminal():
    running = Event()
    adapter = ScriptedAdapter(
        statuses=(SandboxStatus.RUNNING,) * 100,
        evidence_statuses=(SandboxStatus.RUNNING,) * 100,
        block_running=running,
    )
    service = _service(adapter, poll_interval=0.01)
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    assert running.wait(1)

    with pytest.raises(SandboxJobError, match="sandbox_job_already_active"):
        service.start(SandboxProfile.PRODUCTION_SCREENSHOT)

    service.cancel(started.run_id)
    assert service.wait(started.run_id, 1).status is SandboxJobStatus.CANCELLED


def test_job_service_frame_delegates_to_the_adapter_once_prepared():
    running = Event()
    adapter = ScriptedAdapter(
        statuses=(SandboxStatus.RUNNING,) * 10,
        evidence_statuses=(SandboxStatus.RUNNING,) * 10,
        block_running=running,
        frame_result=b"frame-bytes",
    )
    service = _service(adapter)
    started = service.start(SandboxProfile.INTERACTIVE_SESSION)
    assert running.wait(1)

    assert service.frame(started.run_id) == b"frame-bytes"
    assert adapter.frame_calls == [adapter.backend_run_id]

    service.cancel(started.run_id)
    assert service.wait(started.run_id, 1).status is SandboxJobStatus.CANCELLED


def test_job_service_frame_is_none_for_an_unknown_run():
    service = _service(ScriptedAdapter())
    assert service.frame(str(uuid4())) is None


def test_job_service_send_input_delegates_to_the_adapter_once_prepared():
    running = Event()
    adapter = ScriptedAdapter(
        statuses=(SandboxStatus.RUNNING,) * 10,
        evidence_statuses=(SandboxStatus.RUNNING,) * 10,
        block_running=running,
        input_result=True,
    )
    service = _service(adapter)
    started = service.start(SandboxProfile.INTERACTIVE_SESSION)
    assert running.wait(1)

    action = SandboxInputAction(kind="key", key="escape")
    assert service.send_input(started.run_id, action) is True
    assert adapter.input_calls == [(adapter.backend_run_id, action)]

    service.cancel(started.run_id)
    assert service.wait(started.run_id, 1).status is SandboxJobStatus.CANCELLED


def test_job_service_send_input_is_false_for_an_unknown_run():
    service = _service(ScriptedAdapter())
    action = SandboxInputAction(kind="key", key="escape")
    assert service.send_input(str(uuid4()), action) is False


def test_cooperative_cancellation_uses_exact_owned_backend_run():
    running = Event()
    adapter = ScriptedAdapter(
        statuses=(SandboxStatus.RUNNING,) * 10,
        evidence_statuses=(SandboxStatus.RUNNING,) * 10,
        block_running=running,
    )
    service = _service(adapter)
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    assert running.wait(1)

    cancelling = service.cancel(started.run_id)
    finished = service.wait(started.run_id, 1)

    assert cancelling.status in {SandboxJobStatus.CANCELLING, SandboxJobStatus.CANCELLED}
    assert finished.status is SandboxJobStatus.CANCELLED
    assert finished.manual_close_required is True
    assert adapter.calls.count(("cancel", adapter.backend_run_id)) == 1


def test_wrong_profile_and_run_ids_are_rejected_without_backend_calls():
    adapter = ScriptedAdapter()
    service = _service(adapter)

    with pytest.raises(ValueError, match="profile is invalid"):
        service.start("arbitrary_installer --argument")
    with pytest.raises(ValueError, match="canonical UUID"):
        service.snapshot("wrong")
    with pytest.raises(SandboxJobError, match="sandbox_job_not_found"):
        service.cancel(str(uuid4()))

    assert adapter.calls == []


def test_terminal_state_is_absorbing_and_never_calls_backend_again():
    adapter = ScriptedAdapter()
    service = _service(adapter)
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    terminal = service.wait(started.run_id, 1)
    calls = list(adapter.calls)

    with pytest.raises(SandboxJobError, match="sandbox_job_terminal"):
        service.cancel(started.run_id)

    assert service.snapshot(started.run_id) == terminal
    assert adapter.calls == calls


def test_progress_is_normalized_instead_of_copying_backend_values():
    adapter = ScriptedAdapter(
        statuses=(SandboxStatus.RUNNING,),
        evidence_statuses=(SandboxStatus.RUNNING, SandboxStatus.PASSED),
    )
    service = _service(adapter, poll_interval=0.02)
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    collecting = _wait_for(service, started.run_id, SandboxJobStatus.RUNNING)

    assert collecting.progress in {
        SandboxProgress.LAUNCHING,
        SandboxProgress.RUNNING_CHECKS,
        SandboxProgress.COLLECTING_EVIDENCE,
    }
    assert service.wait(started.run_id, 1).progress is SandboxProgress.COMPLETE


def test_nonterminal_persisted_job_becomes_interrupted_after_restart(tmp_path):
    state_path = tmp_path / "job-state.json"
    store = JsonSandboxJobStateStore(state_path)
    persisted = SandboxJobSnapshot(
        str(uuid4()),
        SandboxProfile.PRODUCTION_SELF_TEST,
        SandboxJobStatus.RUNNING,
        SandboxProgress.RUNNING_CHECKS,
        True,
    )
    store.save(persisted)

    restarted = SandboxTestLabJobService(enabled=False, state_store=store)
    snapshot = restarted.snapshot(persisted.run_id)

    assert snapshot.status is SandboxJobStatus.INTERRUPTED
    assert snapshot.progress is SandboxProgress.COMPLETE
    assert snapshot.manual_close_required is True
    assert JsonSandboxJobStateStore(state_path).load() == (snapshot,)


def test_default_off_creates_no_thread_or_backend():
    created = {"backend": 0, "thread": 0}

    def backend_factory():
        created["backend"] += 1
        return ScriptedAdapter()

    def thread_factory(**kwargs):
        created["thread"] += 1
        raise AssertionError("thread must not be created")

    service = SandboxTestLabJobService(
        adapter_factory=backend_factory,
        thread_factory=thread_factory,
    )

    with pytest.raises(SandboxJobDisabledError):
        service.start(SandboxProfile.PRODUCTION_SELF_TEST)

    assert created == {"backend": 0, "thread": 0}


def test_snapshots_and_persisted_state_exclude_sensitive_backend_data(tmp_path):
    state_path = tmp_path / "job-state.json"
    service = _service(
        ScriptedAdapter(sensitive_values=True),
        state_store=JsonSandboxJobStateStore(state_path),
    )
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    snapshot = service.wait(started.run_id, 1)
    serialized = json.dumps(snapshot.to_dict()).lower()
    persisted = state_path.read_text(encoding="utf-8").lower()

    for forbidden in (
        "c:\\\\",
        "installer.exe",
        "--secret",
        "token",
        "private",
        "environment",
        "command",
        "evidence",
    ):
        assert forbidden not in serialized
        assert forbidden not in persisted
