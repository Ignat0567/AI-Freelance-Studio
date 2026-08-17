"""A live run holds a coding-CLI subprocess and a Docker QA container for ~20 minutes, on
the same machine Studio runs on. Two at once means two of each and finishes neither on time,
so live runs are bounded -- while simulated runs, which hold nothing, are not.

The behaviour under the bound is *queue*, not *reject*: a client who pressed start gets
their build. These tests pin that distinction, because rejecting would be the easier thing
to implement and the wrong thing to ship.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Event
from time import sleep

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    ExecutionMode,
    ExecutionResult,
    ExecutionStatus,
    FakeProjectExecutionAdapter,
    ProjectBriefService,
    ProjectExecutionService,
    ReadinessResult,
    TestSummary as WorkflowTestSummary,
    UserOrder,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
REQUEST = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions about their contents by voice or text, receive answers grounded in the "
    "documents with page references, and hear the answers spoken aloud."
)


class _SequenceIds:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._index = 0

    def __call__(self) -> str:
        self._index += 1
        return f"{self._prefix}-{self._index:04d}"


def _approved_contract(order_id: str):
    ids = _SequenceIds(order_id)
    order = UserOrder(
        id=order_id,
        title="PDF Voice Assistant",
        description=REQUEST,
        product_type="web_app",
        preferred_language="en",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    resolved = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=ids, clock=lambda: NOW)
    brief = briefs.generate(resolved.order, resolved.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    designs = DesignPreviewService(id_factory=ids, clock=lambda: NOW)
    preview = designs.approve(designs.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=lambda: NOW).create_implementation_handoff(brief, preview)
    return brief, handoff


class BlockingAdapter:
    """Stands in for a live adapter: reports when it is inside execute, and stays there
    until released, so overlap is observable rather than inferred from timing."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.concurrent = 0
        self.peak_concurrent = 0
        self.executions: list[str] = []

    def check_readiness(self, brief) -> ReadinessResult:
        return ReadinessResult.ready_result()

    def execute(self, request, event_sink, cancellation) -> ExecutionResult:
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        self.executions.append(request.execution_id)
        self.entered.set()
        try:
            self.release.wait(5)
        finally:
            self.concurrent -= 1
        return ExecutionResult(
            success=True,
            outcome="generated",
            summary="Built.",
            test_summary=WorkflowTestSummary(skipped=1),
            completed_at=NOW,
        )


def _live_service(adapter, **kwargs) -> ProjectExecutionService:
    return ProjectExecutionService(
        id_factory=_SequenceIds("exec"),
        clock=lambda: NOW,
        mode=ExecutionMode.PRODUCTION,
        live_adapter=adapter,
        **kwargs,
    )


def test_a_second_live_order_waits_instead_of_running_alongside_the_first():
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    adapter = BlockingAdapter()
    service = _live_service(adapter)

    first = service.start(first_brief, first_handoff, live=True)
    assert adapter.entered.wait(5)

    second = service.start(second_brief, second_handoff, live=True)
    # Give the second worker every chance to slip past the bound before asserting it did not.
    sleep(0.3)

    assert adapter.executions == [first.id]
    queued = service.snapshot(second.id)
    assert queued.status is ExecutionStatus.QUEUED
    assert any("Waiting for the build already running" in event.message for event in queued.events)

    adapter.release.set()
    assert service.wait(first.id, 5).status is ExecutionStatus.SUCCEEDED
    assert service.wait(second.id, 5).status is ExecutionStatus.SUCCEEDED
    assert adapter.executions == [first.id, second.id]
    assert adapter.peak_concurrent == 1


def test_the_queued_order_is_run_rather_than_rejected():
    """The bound must not turn into a refusal: whoever waited still gets their build."""
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    adapter = BlockingAdapter()
    service = _live_service(adapter)

    first = service.start(first_brief, first_handoff, live=True)
    assert adapter.entered.wait(5)
    second = service.start(second_brief, second_handoff, live=True)
    adapter.release.set()

    assert service.wait(second.id, 5).status is ExecutionStatus.SUCCEEDED
    assert service.snapshot(second.id).result is not None
    assert first.id != second.id


def test_raising_the_bound_lets_live_runs_overlap_again():
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    adapter = BlockingAdapter()
    service = _live_service(adapter, live_slots=2)

    first = service.start(first_brief, first_handoff, live=True)
    assert adapter.entered.wait(5)
    second = service.start(second_brief, second_handoff, live=True)
    sleep(0.3)

    assert sorted(adapter.executions) == sorted([first.id, second.id])
    assert adapter.peak_concurrent == 2

    adapter.release.set()
    assert service.wait(first.id, 5).status is ExecutionStatus.SUCCEEDED
    assert service.wait(second.id, 5).status is ExecutionStatus.SUCCEEDED


def test_simulated_runs_are_not_bounded_by_the_live_slot():
    """A demo or a test must not serialise behind a live build -- a FAKE run holds neither a
    CLI subprocess nor a container."""
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    live_adapter = BlockingAdapter()
    service = ProjectExecutionService(
        id_factory=_SequenceIds("exec"),
        clock=lambda: NOW,
        mode=ExecutionMode.PRODUCTION,
        live_adapter=live_adapter,
        fake_adapter=FakeProjectExecutionAdapter(),
    )

    live = service.start(first_brief, first_handoff, live=True)
    assert live_adapter.entered.wait(5)

    simulated = service.start(second_brief, second_handoff, mode=ExecutionMode.FAKE)
    assert service.wait(simulated.id, 5).status is ExecutionStatus.SUCCEEDED

    live_adapter.release.set()
    assert service.wait(live.id, 5).status is ExecutionStatus.SUCCEEDED


def test_a_slot_is_released_even_when_the_adapter_raises():
    """Otherwise one crash costs every later live run: the slot would never come back."""
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")

    class ExplodingAdapter(BlockingAdapter):
        def execute(self, request, event_sink, cancellation):
            self.executions.append(request.execution_id)
            self.entered.set()
            raise RuntimeError("adapter blew up")

    adapter = ExplodingAdapter()
    service = _live_service(adapter)

    first = service.start(first_brief, first_handoff, live=True)
    assert service.wait(first.id, 5).status is ExecutionStatus.FAILED

    second = service.start(second_brief, second_handoff, live=True)
    finished = service.wait(second.id, 5)

    assert finished.status is ExecutionStatus.FAILED
    assert adapter.executions == [first.id, second.id]


def test_unbounded_is_available_for_a_caller_that_has_the_machine_to_spare():
    first_brief, first_handoff = _approved_contract("order_one")
    second_brief, second_handoff = _approved_contract("order_two")
    adapter = BlockingAdapter()
    service = _live_service(adapter, live_slots=0)

    first = service.start(first_brief, first_handoff, live=True)
    assert adapter.entered.wait(5)
    second = service.start(second_brief, second_handoff, live=True)
    sleep(0.3)

    assert adapter.peak_concurrent == 2

    adapter.release.set()
    service.wait(first.id, 5)
    service.wait(second.id, 5)


def test_a_negative_bound_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ProjectExecutionService(live_slots=-1)
