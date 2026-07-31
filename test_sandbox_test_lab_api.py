from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.sandbox_test_lab import (
    get_test_lab_job_service,
    install_sandbox_test_lab_api,
    router,
)
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from sandbox_test_lab.adapter import (
    SandboxAvailability,
    SandboxProfile,
    SandboxProgress,
    SandboxReadiness,
    SandboxStatus,
    SandboxTestLabAdapter,
    SandboxTestLabResult,
)
from sandbox_test_lab.job_service import (
    SandboxJobError,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxTestLabJobService,
)
from sandbox_test_lab.models import SandboxCapability


pytestmark = pytest.mark.unit
TOKEN = "sandbox-api-focused-token-32-bytes-long"
ORIGIN = "http://127.0.0.1:8080"
BASE_PATH = "/api/sandbox-test-lab"


def _snapshot(
    status=SandboxJobStatus.QUEUED,
    *,
    run_id=None,
    profile=SandboxProfile.PRODUCTION_SELF_TEST,
    progress=None,
    manual_close_required=False,
):
    if progress is None:
        progress = {
            SandboxJobStatus.QUEUED: SandboxProgress.NOT_STARTED,
            SandboxJobStatus.PREPARING: SandboxProgress.PREPARING,
            SandboxJobStatus.RUNNING: SandboxProgress.RUNNING_CHECKS,
            SandboxJobStatus.CANCELLING: SandboxProgress.CANCELLING,
            SandboxJobStatus.PASSED: SandboxProgress.COMPLETE,
            SandboxJobStatus.FAILED: SandboxProgress.COMPLETE,
            SandboxJobStatus.TIMED_OUT: SandboxProgress.COMPLETE,
            SandboxJobStatus.CANCELLED: SandboxProgress.COMPLETE,
            SandboxJobStatus.INTERRUPTED: SandboxProgress.COMPLETE,
        }[status]
    return SandboxJobSnapshot(
        run_id or str(uuid4()),
        profile,
        status,
        progress,
        manual_close_required,
    )


class FakeJobService:
    def __init__(self, initial=()):
        self.records = {item.run_id: item for item in initial}
        self.start_calls = []
        self.snapshot_calls = []
        self.cancel_calls = []
        self.shutdown_calls = []
        self.start_error = None
        self.snapshot_error = None
        self.cancel_error = None

    def start(self, profile):
        self.start_calls.append(profile)
        if self.start_error:
            raise self.start_error
        item = _snapshot(profile=profile)
        self.records[item.run_id] = item
        return item

    def snapshot(self, run_id):
        self.snapshot_calls.append(run_id)
        if self.snapshot_error:
            raise self.snapshot_error
        item = self.records.get(run_id)
        if item is None:
            raise SandboxJobError("sandbox_job_not_found")
        return SandboxJobSnapshot(
            item.run_id,
            item.profile,
            item.status,
            item.progress,
            item.manual_close_required,
        )

    def cancel(self, run_id):
        self.cancel_calls.append(run_id)
        if self.cancel_error:
            raise self.cancel_error
        current = self.snapshot(run_id)
        item = _snapshot(
            SandboxJobStatus.CANCELLING,
            run_id=current.run_id,
            profile=current.profile,
        )
        self.records[run_id] = item
        return item

    def shutdown(self, timeout=2.0):
        self.shutdown_calls.append(timeout)


class CompletionRaceService(FakeJobService):
    def cancel(self, run_id):
        self.cancel_calls.append(run_id)
        current = self.records[run_id]
        self.records[run_id] = _snapshot(
            SandboxJobStatus.PASSED,
            run_id=run_id,
            profile=current.profile,
        )
        raise SandboxJobError("sandbox_job_terminal")


def _ready():
    return SandboxAvailability(True, SandboxReadiness.READY)


def _unavailable():
    return SandboxAvailability(False, SandboxReadiness.UNAVAILABLE)


def _app(
    service=None,
    *,
    availability_provider=_ready,
    bind_host="127.0.0.1",
    allow_test_client=True,
):
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(
            token=TOKEN,
            bind_host=bind_host,
            port=8080,
            launch_id="sandbox-api-focused-launch",
            allow_test_client=allow_test_client,
        ),
    )
    active_service = service or FakeJobService()
    install_sandbox_test_lab_api(
        app,
        service=active_service,
        availability_provider=availability_provider,
        shutdown_timeout=0.01,
    )
    app.include_router(router)
    return app, active_service


def _client(app, *, token=TOKEN, origin=ORIGIN, base_url=ORIGIN):
    return TestClient(
        app,
        base_url=base_url,
        headers={
            "X-FreelancerStudio-Token": token,
            "Origin": origin,
            "Content-Type": "application/json",
        },
    )


def _launch(client, *, key=None, payload=None):
    return client.post(
        f"{BASE_PATH}/runs",
        headers={"Idempotency-Key": key or str(uuid4())},
        json=payload
        or {
            "operation": "production_self_test",
            "parameters": {},
        },
    )


@pytest.mark.parametrize("token", [None, "incorrect-token-with-enough-length"])
def test_all_routes_reject_missing_or_incorrect_token(token):
    app, _ = _app()
    headers = {"Origin": ORIGIN}
    if token is not None:
        headers["X-FreelancerStudio-Token"] = token
    client = TestClient(app, base_url=ORIGIN, headers=headers)

    for path in (
        f"{BASE_PATH}/capabilities",
        f"{BASE_PATH}/runs/{uuid4()}",
    ):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "local_authorization_required"}}


def test_correct_trusted_request_is_accepted_without_returning_token():
    app, _ = _app()
    response = _client(app).get(f"{BASE_PATH}/capabilities")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert TOKEN not in response.text


def test_network_bind_is_rejected_by_common_local_only_dependency():
    app, _ = _app(bind_host="192.168.1.10")
    response = _client(app, base_url="http://192.168.1.10:8080").get(
        f"{BASE_PATH}/capabilities"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "network_bind_disallowed"}


def test_non_loopback_client_and_invalid_host_are_rejected():
    non_loopback_app, _ = _app(allow_test_client=False)
    non_loopback = _client(non_loopback_app).get(f"{BASE_PATH}/capabilities")
    host_app, _ = _app()
    invalid_host = _client(host_app, base_url="http://127.0.0.1:8081").get(
        f"{BASE_PATH}/capabilities"
    )

    assert non_loopback.status_code == 403
    assert non_loopback.json() == {"error": {"code": "loopback_client_required"}}
    assert invalid_host.status_code == 403
    assert invalid_host.json() == {"error": {"code": "invalid_host"}}


@pytest.mark.parametrize("origin", ["https://evil.example", "null", None])
def test_launch_rejects_invalid_missing_and_null_origin(origin):
    app, service = _app()
    headers = {
        "X-FreelancerStudio-Token": TOKEN,
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid4()),
    }
    if origin is not None:
        headers["Origin"] = origin
    response = TestClient(app, base_url=ORIGIN).post(
        f"{BASE_PATH}/runs",
        headers=headers,
        content='{"operation":"production_self_test","parameters":{}}',
    )

    assert response.status_code == 403
    assert response.json() == {"error": {"code": "invalid_origin"}}
    assert service.start_calls == []


def test_query_or_body_token_never_authorizes_and_body_token_is_extra():
    app, service = _app()
    missing = TestClient(app, base_url=ORIGIN).get(
        f"{BASE_PATH}/capabilities?token={TOKEN}"
    )
    extra = _launch(
        _client(app),
        payload={
            "operation": "production_self_test",
            "parameters": {},
            "token": TOKEN,
        },
    )

    assert missing.status_code == 401
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "invalid_launch_request"
    assert service.start_calls == []


def test_capability_unavailable_is_stable_and_does_not_create_a_job():
    app, service = _app(availability_provider=_unavailable)
    response = _client(app).get(f"{BASE_PATH}/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "reasons": [
            {
                "code": "sandbox_capability_unavailable",
                "message": "Windows Sandbox capability is unavailable.",
            }
        ],
        "backend_mode": "loopback",
        "operations": {
            "launch": False,
            "cancel": False,
            "status": False,
            "evidence": False,
        },
    }
    assert service.start_calls == []
    assert "path" not in response.text.lower()
    assert TOKEN not in response.text


def test_valid_launch_returns_202_safe_public_id_and_calls_service_once():
    app, service = _app()
    response = _launch(_client(app))

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["run_id"] in service.records
    assert service.start_calls == [SandboxProfile.PRODUCTION_SELF_TEST]
    assert set(response.json()) == {"run_id", "status"}


def test_interactive_session_operation_is_accepted_and_routed_to_its_profile():
    app, service = _app()
    response = _launch(
        _client(app),
        payload={"operation": "interactive_session", "parameters": {}},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert service.start_calls == [SandboxProfile.INTERACTIVE_SESSION]


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "payload_load_probe", "parameters": {}},
        {"operation": "production_self_test", "parameters": {}, "extra": True},
        {"operation": "production_self_test", "parameters": {"command": "whoami"}},
        {"command": "whoami", "parameters": {}},
        {"operation": "production_self_test"},
    ],
)
def test_launch_allow_list_and_strict_schema_reject_unsafe_input(payload):
    app, service = _app()
    response = _launch(_client(app), payload=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_launch_request"
    assert service.start_calls == []


def test_launch_rejects_wrong_content_type_malformed_and_missing_body():
    app, service = _app()
    client = _client(app)
    key = str(uuid4())
    wrong_type = client.post(
        f"{BASE_PATH}/runs",
        headers={"Content-Type": "text/plain", "Idempotency-Key": key},
        content='{"operation":"production_self_test","parameters":{}}',
    )
    malformed = client.post(
        f"{BASE_PATH}/runs",
        headers={"Idempotency-Key": key},
        content="{",
    )
    missing = client.post(
        f"{BASE_PATH}/runs",
        headers={"Idempotency-Key": key},
        content="",
    )

    assert wrong_type.status_code == 415
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_launch_request"
    assert missing.status_code == 422
    assert service.start_calls == []


def test_launch_and_cancel_reject_duplicate_json_fields():
    item = _snapshot(SandboxJobStatus.RUNNING)
    app, service = _app(FakeJobService((item,)))
    client = _client(app)
    launch = client.post(
        f"{BASE_PATH}/runs",
        headers={"Idempotency-Key": str(uuid4())},
        content='{"operation":"production_self_test","operation":"production_screenshot","parameters":{}}',
    )
    cancel = client.post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        content='{"reason":"user_requested","reason":"user_requested"}',
    )

    assert launch.status_code == 422
    assert launch.json()["error"]["code"] == "invalid_launch_request"
    assert cancel.status_code == 422
    assert cancel.json()["error"]["code"] == "invalid_cancel_request"
    assert service.start_calls == []
    assert service.cancel_calls == []


def test_launch_requires_one_canonical_idempotency_key():
    app, service = _app()
    client = _client(app)
    payload = {"operation": "production_self_test", "parameters": {}}

    missing = client.post(f"{BASE_PATH}/runs", json=payload)
    invalid = client.post(
        f"{BASE_PATH}/runs",
        headers={"Idempotency-Key": "not-a-uuid"},
        json=payload,
    )

    assert missing.status_code == 400
    assert invalid.status_code == 400
    assert service.start_calls == []


def test_exact_launch_retry_returns_original_and_conflict_does_not_launch():
    app, service = _app()
    client = _client(app)
    key = str(uuid4())
    first = _launch(client, key=key)
    retry = _launch(client, key=key)
    conflict = _launch(
        client,
        key=key,
        payload={"operation": "production_screenshot", "parameters": {}},
    )

    assert first.status_code == retry.status_code == 202
    assert first.json() == retry.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert service.start_calls == [SandboxProfile.PRODUCTION_SELF_TEST]


def test_concurrent_exact_retries_create_one_job():
    app, service = _app()
    client = _client(app)
    key = str(uuid4())

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: _launch(client, key=key), range(4)))

    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["run_id"] for response in responses}) == 1
    assert service.start_calls == [SandboxProfile.PRODUCTION_SELF_TEST]


def test_launch_unavailable_and_service_exception_are_safely_mapped():
    unavailable_app, unavailable_service = _app(availability_provider=_unavailable)
    unavailable = _launch(_client(unavailable_app))
    failing_service = FakeJobService()
    failing_service.start_error = RuntimeError(r"private C:\secret\runner.exe --token hidden")
    failing_app, _ = _app(failing_service)
    failed = _launch(_client(failing_app))

    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "test_lab_unavailable"
    assert unavailable_service.start_calls == []
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert "secret" not in failed.text.lower()
    assert "runner.exe" not in failed.text.lower()


@pytest.mark.parametrize(
    ("snapshot", "public_status", "terminal"),
    [
        (_snapshot(), "queued", False),
        (_snapshot(SandboxJobStatus.RUNNING), "running", False),
        (
            _snapshot(
                SandboxJobStatus.RUNNING,
                progress=SandboxProgress.LAUNCHING,
            ),
            "launching",
            False,
        ),
        (_snapshot(SandboxJobStatus.PASSED), "succeeded", True),
        (_snapshot(SandboxJobStatus.FAILED), "failed", True),
        (_snapshot(SandboxJobStatus.INTERRUPTED), "infrastructure_error", True),
    ],
)
def test_status_explicitly_maps_copied_snapshots(snapshot, public_status, terminal):
    app, service = _app(FakeJobService((snapshot,)))
    response = _client(app).get(f"{BASE_PATH}/runs/{snapshot.run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == public_status
    assert response.json()["terminal"] is terminal
    response.json()["progress"]["phase"] = "mutated"
    assert service.records[snapshot.run_id].progress is snapshot.progress


def test_status_unknown_and_internal_errors_are_sanitized():
    app, _ = _app()
    unknown = _client(app).get(f"{BASE_PATH}/runs/{uuid4()}")
    failing = FakeJobService()
    failing.snapshot_error = RuntimeError("private host path C:\\secret")
    failing_app, _ = _app(failing)
    internal = _client(failing_app).get(f"{BASE_PATH}/runs/{uuid4()}")

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "run_not_found"
    assert internal.status_code == 500
    assert internal.json()["error"]["code"] == "internal_error"
    assert "secret" not in internal.text.lower()


@pytest.mark.parametrize("status", [SandboxJobStatus.QUEUED, SandboxJobStatus.RUNNING])
def test_cancel_queued_and_active_run_is_accepted(status):
    item = _snapshot(status)
    app, service = _app(FakeJobService((item,)))
    response = _client(app).post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        json={"reason": "user_requested"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": item.run_id,
        "status": "cancelling",
        "accepted": True,
    }
    assert service.cancel_calls == [item.run_id]


def test_cancel_is_idempotent_for_cancelling_and_cancelled_runs():
    cancelling = _snapshot(SandboxJobStatus.CANCELLING)
    cancelled = _snapshot(SandboxJobStatus.CANCELLED)
    app, service = _app(FakeJobService((cancelling, cancelled)))
    client = _client(app)

    first = client.post(
        f"{BASE_PATH}/runs/{cancelling.run_id}/cancel",
        json={"reason": "user_requested"},
    )
    second = client.post(
        f"{BASE_PATH}/runs/{cancelled.run_id}/cancel",
        json={"reason": "user_requested"},
    )

    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is False
    assert service.cancel_calls == []


@pytest.mark.parametrize(
    "status",
    [SandboxJobStatus.PASSED, SandboxJobStatus.FAILED, SandboxJobStatus.TIMED_OUT],
)
def test_cancel_does_not_change_terminal_completion(status):
    item = _snapshot(status)
    app, service = _app(FakeJobService((item,)))
    response = _client(app).post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        json={"reason": "user_requested"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_already_terminal"
    assert service.records[item.run_id].status is status
    assert service.cancel_calls == []


def test_cancel_unknown_invalid_body_and_service_failure_are_stable():
    app, service = _app()
    client = _client(app)
    unknown = client.post(
        f"{BASE_PATH}/runs/{uuid4()}/cancel",
        json={"reason": "user_requested"},
    )
    item = _snapshot(SandboxJobStatus.RUNNING)
    service.records[item.run_id] = item
    invalid = client.post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        json={"reason": "user_requested", "token": TOKEN},
    )
    service.cancel_error = RuntimeError("private cancellation detail")
    failed = client.post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        json={"reason": "user_requested"},
    )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_cancel_request"
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert "private" not in failed.text.lower()


def test_completion_wins_cancel_race():
    item = _snapshot(SandboxJobStatus.RUNNING)
    service = CompletionRaceService((item,))
    app, _ = _app(service)
    response = _client(app).post(
        f"{BASE_PATH}/runs/{item.run_id}/cancel",
        json={"reason": "user_requested"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_already_terminal"
    assert service.records[item.run_id].status is SandboxJobStatus.PASSED


def test_dependency_override_replaces_job_service():
    app, original = _app()
    replacement = FakeJobService()
    app.dependency_overrides[get_test_lab_job_service] = lambda: replacement
    response = _launch(_client(app))

    assert response.status_code == 202
    assert original.start_calls == []
    assert replacement.start_calls == [SandboxProfile.PRODUCTION_SELF_TEST]


def test_application_owns_one_service_and_calls_shutdown():
    service = FakeJobService()
    app, installed = _app(service)

    assert installed is service
    assert app.state.sandbox_test_lab_job_service is service
    with _client(app) as client:
        assert client.get(f"{BASE_PATH}/capabilities").status_code == 200
        assert client.get(f"{BASE_PATH}/capabilities").status_code == 200

    assert service.shutdown_calls == [0.01]
    assert service.start_calls == []


def test_listing_and_evidence_content_routes_are_not_exposed():
    item = _snapshot(SandboxJobStatus.PASSED)
    app, _ = _app(FakeJobService((item,)))
    client = _client(app)

    assert client.get(f"{BASE_PATH}/runs").status_code == 405
    assert client.get(f"{BASE_PATH}/runs/{item.run_id}/evidence").status_code == 404


class BlockingRunner:
    def __init__(self):
        self.backend_run_id = str(uuid4())
        self.entered_status = Event()
        self.release_status = Event()
        self.cancel_called = Event()

    def _result(self, status, progress):
        return SandboxTestLabResult(
            self.backend_run_id,
            SandboxProfile.PRODUCTION_SELF_TEST,
            status,
            SandboxReadiness.READY,
            progress,
        )

    def prepare(self, profile):
        self.backend_run_id = str(uuid4())
        return self._result(SandboxStatus.PREPARED, SandboxProgress.PREPARING)

    def launch(self, run_id):
        return self._result(SandboxStatus.LAUNCHING, SandboxProgress.LAUNCHING)

    def status(self, run_id):
        self.entered_status.set()
        self.release_status.wait(1)
        return self._result(SandboxStatus.RUNNING, SandboxProgress.RUNNING_CHECKS)

    def evidence(self, run_id):
        return self._result(SandboxStatus.RUNNING, SandboxProgress.COLLECTING_EVIDENCE)

    def cancel(self, run_id):
        self.cancel_called.set()
        return self._result(SandboxStatus.CANCELLED, SandboxProgress.COMPLETE)


def test_job_service_shutdown_is_bounded_and_stops_new_jobs():
    runner = BlockingRunner()
    adapter = SandboxTestLabAdapter(
        enabled=True,
        runner=runner,
        capability_detector=lambda: SandboxCapability(
            supported_os=True,
            windows_edition="Professional",
            windows_build=22631,
            virtualization_available=True,
            sandbox_feature_state="enabled",
            executable_found=True,
            available=True,
        ),
    )
    service = SandboxTestLabJobService(
        enabled=True,
        adapter_factory=lambda: adapter,
        poll_interval=0.001,
    )
    started = service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    assert runner.entered_status.wait(1)

    began = time.monotonic()
    service.shutdown(0.01)
    elapsed = time.monotonic() - began

    assert elapsed < 0.5
    assert service.snapshot(started.run_id).status is SandboxJobStatus.CANCELLING
    with pytest.raises(SandboxJobError, match="sandbox_job_service_stopping"):
        service.start(SandboxProfile.PRODUCTION_SCREENSHOT)

    runner.release_status.set()
    assert runner.cancel_called.wait(1)
    assert service.wait(started.run_id, 1).status is SandboxJobStatus.CANCELLED


class FailingStateStore:
    def __init__(self):
        self.fail = True

    def load(self):
        return ()

    def save(self, snapshot):
        if self.fail:
            self.fail = False
            raise SandboxJobError("sandbox_job_state_write_failed")


def test_job_service_launch_setup_failures_do_not_wedge_active_state():
    runner = BlockingRunner()
    adapter = SandboxTestLabAdapter(
        enabled=True,
        runner=runner,
        capability_detector=lambda: SandboxCapability(
            supported_os=True,
            windows_edition="Professional",
            windows_build=22631,
            virtualization_available=True,
            sandbox_feature_state="enabled",
            executable_found=True,
            available=True,
        ),
    )
    failing_store_service = SandboxTestLabJobService(
        enabled=True,
        adapter_factory=lambda: adapter,
        state_store=FailingStateStore(),
    )
    with pytest.raises(SandboxJobError, match="sandbox_job_state_write_failed"):
        failing_store_service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    recovered = failing_store_service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    failing_store_service.shutdown(0.01)
    runner.release_status.set()
    assert failing_store_service.wait(recovered.run_id, 1).status is SandboxJobStatus.CANCELLED

    calls = 0

    def thread_factory(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("thread construction failed")
        from threading import Thread

        return Thread(**kwargs)

    construction_service = SandboxTestLabJobService(
        enabled=True,
        adapter_factory=lambda: adapter,
        thread_factory=thread_factory,
    )
    with pytest.raises(SandboxJobError, match="sandbox_job_worker_start_failed"):
        construction_service.start(SandboxProfile.PRODUCTION_SELF_TEST)

    runner.release_status.clear()
    started = construction_service.start(SandboxProfile.PRODUCTION_SELF_TEST)
    construction_service.shutdown(0.01)
    runner.release_status.set()
    assert construction_service.wait(started.run_id, 1).status is SandboxJobStatus.CANCELLED
