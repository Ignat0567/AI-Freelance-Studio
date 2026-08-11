from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from time import sleep

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    ElenaDesignChoice,
    EventKind,
    EventLevel,
    ExecutionMode,
    ExecutionServiceError,
    ExecutionStage,
    ExecutionStatus,
    FakeExecutorConfig,
    FakeProjectExecutionAdapter,
    InMemoryExecutionStateStore,
    ProjectBriefService,
    ProjectExecutionService,
    ReadinessResult,
    ExecutionResult,
    TestSummary as WorkflowTestSummary,
    UserOrder,
    readiness_blocker,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
REQUEST = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions about their contents by voice or text, receive answers grounded in the "
    "documents with page references, and hear the answers spoken aloud."
)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"exec-{self.index:04d}"


def _clock():
    return NOW + timedelta(seconds=1)


def _approved_contract(order_id="order_pdf"):
    ids = SequenceIds()
    order = UserOrder(
        id=order_id,
        title="PDF Voice Assistant",
        description=REQUEST,
        product_type="web_app",
        preferred_language="ru",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=ids, clock=_clock)
    brief = briefs.generate(result.order, result.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    design_service = DesignPreviewService(id_factory=ids, clock=_clock)
    preview = design_service.approve(design_service.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=_clock).create_implementation_handoff(brief, preview)
    return brief, handoff


def _service(**kwargs) -> ProjectExecutionService:
    return ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, **kwargs)


class DeferredWorker:
    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def join(self, timeout=None) -> None:
        return None


class MaliciousAdapter:
    def check_readiness(self, brief):
        return ReadinessResult.ready_result()

    def execute(self, request, event_sink, cancellation):
        artifact = event_sink.artifact(
            kind="project_summary",
            name="summary token=hidden",
            summary="summary password=hunter2",
            reference="token:supersecret-reference",
        )
        return ExecutionResult(
            success=False,
            outcome="failed token=super-secret-token",
            summary="Raw stack trace Authorization: Bearer abcdefghijklmnop",
            test_summary=WorkflowTestSummary(failed=1),
            warnings=("password=hunter2",),
            errors=("api_key=sk-abcdefghijklmnop",),
            artifact_ids=(artifact.id,),
            completed_at=NOW,
        )


class NonCooperativeAdapter(FakeProjectExecutionAdapter):
    def execute(self, request, event_sink, cancellation):
        sleep(0.2)
        return super().execute(request, event_sink, cancellation)


def test_approved_brief_starts_fake_execution_and_reaches_success():
    brief, handoff = _approved_contract()
    service = _service()

    started = service.start(brief, handoff)
    finished = service.wait(started.id, 2)

    assert started.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.SUCCEEDED}
    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.stage is ExecutionStage.COMPLETED
    assert finished.active_agent == "Product Judge"
    assert finished.result is not None
    assert finished.result.success is True
    assert finished.result.test_summary.passed == 4
    assert finished.result.final_stage is ExecutionStage.COMPLETED
    assert len(finished.artifacts) == 4
    assert all(item.simulated for item in finished.artifacts)
    assert {item.name for item in finished.artifacts} == {
        "project_summary.md",
        "handoff.json",
        "verification_report.json",
        "delivery_report.md",
    }
    assert {event.stage for event in finished.events if event.kind is EventKind.ACTIVITY} >= {
        ExecutionStage.REQUIREMENTS,
        ExecutionStage.PLANNING,
        ExecutionStage.IMPLEMENTATION,
        ExecutionStage.VERIFICATION,
        ExecutionStage.COMPLETED,
    }


def test_start_rejects_unapproved_stale_unresolved_undecided_and_unsupported_briefs():
    brief, handoff = _approved_contract()
    service = _service()

    with pytest.raises(ExecutionServiceError) as unapproved:
        service.start(brief.model_copy(update={"approved_at": None, "approved_revision": None, "approval_fingerprint": None}), handoff)
    assert unapproved.value.code == "brief_not_approved"

    with pytest.raises(ExecutionServiceError) as stale:
        service.start(brief.model_copy(update={"approval_fingerprint": "0" * 64}), handoff)
    assert stale.value.code == "brief_not_approved"

    with pytest.raises(ExecutionServiceError) as unresolved:
        service.start(brief.model_copy(update={"open_questions": ("Choose deployment",)}), handoff)
    assert unresolved.value.code == "unresolved_questions"

    undecided = brief.model_copy(update={"approved_at": None, "approved_revision": None, "approval_fingerprint": None, "elena_design_choice": ElenaDesignChoice.UNDECIDED, "elena_design_concept": None})
    with pytest.raises(ExecutionServiceError):
        service.start(undecided, handoff)

    unsupported = brief.model_copy(update={"recommended_stack": {"frontend": "Desktop", "backend": "FastAPI", "storage": "SQLite"}})
    with pytest.raises(ExecutionServiceError) as unsupported_error:
        service.start(unsupported, handoff)
    assert unsupported_error.value.code in {"brief_not_approved", "unsupported_product_type"}


def test_duplicate_active_returns_existing_and_terminal_duplicate_conflicts():
    brief, handoff = _approved_contract()
    adapter = FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05))
    service = _service(fake_adapter=adapter)

    first = service.start(brief, handoff)
    second = service.start(brief, handoff)
    assert second.id == first.id

    service.wait(first.id, 2)
    with pytest.raises(ExecutionServiceError) as duplicate:
        service.start(brief, handoff)
    assert duplicate.value.code == "execution_already_completed"


def test_restored_terminal_execution_preserves_duplicate_prevention():
    brief, handoff = _approved_contract()
    store = InMemoryExecutionStateStore()
    first_service = _service(state_store=store)
    first_service.wait(first_service.start(brief, handoff).id, 2)

    restored_service = _service(state_store=store)
    with pytest.raises(ExecutionServiceError) as duplicate:
        restored_service.start(brief, handoff)
    assert duplicate.value.code == "execution_already_completed"


def test_different_orders_can_run_independently():
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.01)))

    first = service.start(first_brief, first_handoff)
    second = service.start(second_brief, second_handoff)

    assert first.id != second.id
    assert service.wait(first.id, 2).status is ExecutionStatus.SUCCEEDED
    assert service.wait(second.id, 2).status is ExecutionStatus.SUCCEEDED


def test_changed_brief_version_requires_new_approval():
    brief, handoff = _approved_contract()
    changed = brief.model_copy(
        update={
            "revision": brief.revision + 1,
            "approved_at": None,
            "approved_revision": None,
            "approval_fingerprint": None,
        }
    )
    with pytest.raises(ExecutionServiceError) as error:
        _service().start(changed, handoff)
    assert error.value.code == "brief_not_approved"


def test_running_execution_can_be_cancelled_and_executor_observes_token():
    brief, handoff = _approved_contract()
    adapter = FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05))
    service = _service(fake_adapter=adapter)

    started = service.start(brief, handoff)
    for _ in range(20):
        if service.snapshot(started.id).status is ExecutionStatus.RUNNING:
            break
        sleep(0.005)
    cancelled = service.cancel(started.id)
    finished = service.wait(started.id, 2)

    assert cancelled.status in {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED}
    assert finished.status is ExecutionStatus.CANCELLED
    assert finished.result is not None
    assert finished.result.outcome == "cancelled"
    assert adapter.observed_cancellation or any("Cancellation requested" in event.message for event in finished.events)
    assert service.cancel(started.id).status is ExecutionStatus.CANCELLED


def test_queued_execution_can_be_cancelled_before_worker_runs():
    brief, handoff = _approved_contract()
    service = _service(thread_factory=DeferredWorker)

    started = service.start(brief, handoff)
    cancelled = service.cancel(started.id)

    assert started.status is ExecutionStatus.QUEUED
    assert cancelled.status is ExecutionStatus.CANCELLED
    assert cancelled.result is not None
    assert cancelled.result.outcome == "cancelled"


def test_cancel_after_success_does_not_change_result():
    brief, handoff = _approved_contract()
    service = _service()
    finished = service.wait(service.start(brief, handoff).id, 2)
    after = service.cancel(finished.id)

    assert after.status is ExecutionStatus.SUCCEEDED
    assert after.result == finished.result


def test_fake_failure_and_internal_exception_are_sanitized():
    brief, handoff = _approved_contract()
    failing_service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(fail=True)))
    failed = failing_service.wait(failing_service.start(brief, handoff).id, 2)
    assert failed.status is ExecutionStatus.FAILED
    assert failed.result is not None
    assert failed.result.errors == ("simulated_verification_failure",)

    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(raise_unexpected=True)))
    errored = service.wait(service.start(brief, handoff).id, 2)
    payload = errored.to_json().casefold()
    assert errored.status is ExecutionStatus.FAILED
    assert "execution_internal_error" in payload
    assert "secret-value" not in payload
    assert "stack trace" not in payload

    malicious = _service(fake_adapter=MaliciousAdapter())
    sanitized = malicious.wait(malicious.start(brief, handoff).id, 2)
    serialized = sanitized.to_json().casefold()
    assert "super-secret-token" not in serialized
    assert "hunter2" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert "authorization: bearer" not in serialized
    assert "supersecret-reference" not in serialized
    assert sanitized.artifacts[0].reference == "redacted"


def test_fake_blocker_and_production_mode_readiness_are_actionable_without_fallback():
    brief, handoff = _approved_contract()
    blocked_service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(readiness_blocker=True)))
    blocked = blocked_service.start(brief, handoff)

    assert blocked.status is ExecutionStatus.AWAITING_USER
    assert blocked.blockers[0].code == "execution_provider_not_configured"
    assert blocked.blockers[0].action == "Open Settings"

    production = _service(mode=ExecutionMode.PRODUCTION)
    readiness = production.check_readiness(brief, handoff)
    assert readiness.ready is False
    assert readiness.blockers[0].code == "execution_provider_not_configured"
    blocked_production = production.start(brief, handoff)
    assert blocked_production.mode is ExecutionMode.PRODUCTION
    assert blocked_production.status is ExecutionStatus.AWAITING_USER


def test_injected_production_adapter_is_required_for_production_success():
    brief, handoff = _approved_contract()
    adapter = FakeProjectExecutionAdapter()
    service = _service(mode=ExecutionMode.PRODUCTION, production_adapter=adapter)

    finished = service.wait(service.start(brief, handoff).id, 2)

    assert finished.mode is ExecutionMode.PRODUCTION
    assert finished.status is ExecutionStatus.SUCCEEDED


def test_events_snapshots_retention_and_late_events_are_safe():
    brief, handoff = _approved_contract()
    service = _service(event_limit=3)
    finished = service.wait(service.start(brief, handoff).id, 2)

    assert len(finished.events) <= 3
    assert tuple(event.created_at for event in finished.events) == tuple(sorted(event.created_at for event in finished.events))
    with pytest.raises(Exception):
        finished.current_activity = "mutated"
    service._emit(finished.id, kind=EventKind.ACTIVITY, stage=ExecutionStage.PLANNING, agent="Codex", progress=1, message="Late regression")
    after = service.snapshot(finished.id)
    assert after.status is ExecutionStatus.SUCCEEDED
    assert "Late regression" not in after.to_json()
    assert "chat_history" not in after.to_json()


def test_custom_readiness_blocker_is_structured():
    blocker = readiness_blocker("workspace_unavailable", "Workspace is not writable.", "Choose Folder")
    result = ReadinessResult.blocked(blocker)

    assert result.ready is False
    assert result.blockers[0].code == "workspace_unavailable"
    assert result.blockers[0].action_required is True


class FlakyAdapter(FakeProjectExecutionAdapter):
    """Fails the first call (like a transient provider rate limit), succeeds on retry."""

    def __init__(self, config: FakeExecutorConfig | None = None) -> None:
        super().__init__(config or FakeExecutorConfig())
        self.calls = 0

    def execute(self, request, event_sink, cancellation):
        self.calls += 1
        if self.calls == 1:
            return ExecutionResult(
                success=False,
                outcome="failed",
                summary="Simulated transient provider failure.",
                test_summary=WorkflowTestSummary(failed=1),
                errors=("provider_rate_limited",),
                completed_at=NOW,
            )
        return super().execute(request, event_sink, cancellation)


def test_retry_reruns_a_failed_execution_reusing_the_same_id():
    brief, handoff = _approved_contract()
    adapter = FlakyAdapter()
    service = _service(fake_adapter=adapter)

    first_attempt = service.wait(service.start(brief, handoff).id, 2)
    assert first_attempt.status is ExecutionStatus.FAILED

    retried = service.retry(first_attempt.id, brief, handoff)
    assert retried.id == first_attempt.id
    finished = service.wait(retried.id, 2)

    assert finished.id == first_attempt.id
    assert finished.status is ExecutionStatus.SUCCEEDED
    assert adapter.calls == 2
    # the first attempt's event history survives the retry -- it's appended to, not wiped
    assert len(finished.events) > len(first_attempt.events)
    assert any("Retrying execution" in event.message for event in finished.events)


def test_retry_rejects_execution_that_is_not_failed():
    brief, handoff = _approved_contract()
    service = _service()
    succeeded = service.wait(service.start(brief, handoff).id, 2)

    with pytest.raises(ExecutionServiceError) as error:
        service.retry(succeeded.id, brief, handoff)
    assert error.value.code == "execution_not_retryable"


def test_retry_rejects_unknown_execution_id():
    brief, handoff = _approved_contract()
    service = _service()

    with pytest.raises(ExecutionServiceError) as error:
        service.retry("execution_does_not_exist", brief, handoff)
    assert error.value.code == "execution_not_found"


def test_retry_rejects_mismatched_brief():
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    service = _service(fake_adapter=FlakyAdapter())

    failed = service.wait(service.start(first_brief, first_handoff).id, 2)
    assert failed.status is ExecutionStatus.FAILED

    with pytest.raises(ExecutionServiceError) as error:
        service.retry(failed.id, second_brief, second_handoff)
    assert error.value.code == "execution_not_found"


def test_shutdown_stops_accepting_new_executions_and_is_bounded():
    brief, handoff = _approved_contract()
    service = _service(fake_adapter=NonCooperativeAdapter(FakeExecutorConfig(step_delay_seconds=0.05)))
    started = service.start(brief, handoff)
    service.shutdown(timeout=0.2)

    assert service.snapshot(started.id).status is ExecutionStatus.CANCELLED

    with pytest.raises(ExecutionServiceError) as error:
        service.start(*_approved_contract("order_after_shutdown"))
    assert error.value.code == "execution_service_stopping"
