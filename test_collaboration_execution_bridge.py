from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.collaboration import router as collaboration_router
from api.orders import router as orders_router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from collaboration.execution_bridge import publish_execution_event
from collaboration.models import CollaborationEventKind, PresenceStatus
from collaboration.service import CollaborationService
from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    EventKind,
    EventLevel,
    ExecutionStage,
    ExecutionStatus,
    FakeExecutorConfig,
    FakeProjectExecutionAdapter,
    ProjectBriefService,
    ProjectExecutionService,
    UserOrder,
)
from order_workflow.models import ExecutionEvent

pytestmark = pytest.mark.unit
TOKEN = "collaboration-bridge-focused-token-32bytes"
ORIGIN = "http://127.0.0.1:8080"
PDF_DESCRIPTION = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions about their contents by voice or text, receive answers grounded in the "
    "documents with page references, and hear the answers spoken aloud."
)
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


def _event(**overrides) -> ExecutionEvent:
    defaults = dict(
        id="event_abc123",
        execution_id="execution_xyz789",
        kind=EventKind.ACTIVITY,
        level=EventLevel.INFO,
        message="Doing agent things",
        stage=ExecutionStage.IMPLEMENTATION,
        agent="Codex",
        created_at=NOW,
    )
    defaults.update(overrides)
    return ExecutionEvent(**defaults)


# --- mapping unit tests -----------------------------------------------------


@pytest.mark.parametrize(
    "stage,expected_channel",
    [
        (ExecutionStage.REQUIREMENTS, "general"),
        (ExecutionStage.DESIGN, "design"),
        (ExecutionStage.PLANNING, "architecture"),
        (ExecutionStage.IMPLEMENTATION, "backend"),
        (ExecutionStage.VERIFICATION, "qa"),
        (ExecutionStage.REPAIR, "backend"),
        (ExecutionStage.PACKAGING, "deployment"),
        (ExecutionStage.COMPLETED, "general"),
    ],
)
def test_stage_routes_to_the_expected_channel(stage, expected_channel):
    service = CollaborationService()

    publish_execution_event(service, _event(stage=stage))

    assert service.list_events()[-1].channel_id == expected_channel


def test_activity_event_maps_to_agent_activity_kind():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.ACTIVITY))

    assert service.list_events()[-1].kind == CollaborationEventKind.AGENT_ACTIVITY


def test_blocker_event_maps_to_error_detected():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.BLOCKER, level=EventLevel.WARNING))

    assert service.list_events()[-1].kind == CollaborationEventKind.ERROR_DETECTED


def test_artifact_event_maps_to_file_modified():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.ARTIFACT))

    assert service.list_events()[-1].kind == CollaborationEventKind.FILE_MODIFIED


def test_successful_result_event_maps_to_task_completed():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.RESULT, level=EventLevel.INFO, agent="Product Judge"))

    event = service.list_events()[-1]
    assert event.kind == CollaborationEventKind.TASK_COMPLETED
    assert event.agent == "Product Judge"


def test_failed_result_event_maps_to_error_detected():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.RESULT, level=EventLevel.WARNING))

    assert service.list_events()[-1].kind == CollaborationEventKind.ERROR_DETECTED


def test_studio_agent_name_is_not_attributed_to_a_specific_agent():
    service = CollaborationService()

    publish_execution_event(service, _event(agent="Studio"))

    assert service.list_events()[-1].agent is None


def test_execution_id_becomes_the_event_task_id():
    service = CollaborationService()

    publish_execution_event(service, _event(execution_id="execution_real123"))

    assert service.list_events()[-1].task_id == "execution_real123"


@pytest.mark.parametrize(
    "stage,expected_status",
    [
        (ExecutionStage.REQUIREMENTS, PresenceStatus.THINKING),
        (ExecutionStage.DESIGN, PresenceStatus.REVIEWING),
        (ExecutionStage.PLANNING, PresenceStatus.THINKING),
        (ExecutionStage.IMPLEMENTATION, PresenceStatus.CODING),
        (ExecutionStage.VERIFICATION, PresenceStatus.TESTING),
        (ExecutionStage.REPAIR, PresenceStatus.CODING),
        (ExecutionStage.PACKAGING, PresenceStatus.REVIEWING),
        (ExecutionStage.COMPLETED, PresenceStatus.IDLE),
    ],
)
def test_activity_event_sets_presence_from_stage(stage, expected_status):
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.ACTIVITY, stage=stage, agent="Codex"))

    presence = service.list_presence()
    assert len(presence) == 1
    assert presence[0].status == expected_status


def test_blocker_event_sets_presence_to_waiting_regardless_of_stage():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.BLOCKER, stage=ExecutionStage.IMPLEMENTATION, agent="BugCatcher", level=EventLevel.WARNING))

    presence = service.list_presence()
    assert presence[0].status == PresenceStatus.WAITING


def test_result_event_sets_presence_to_idle():
    service = CollaborationService()

    publish_execution_event(service, _event(kind=EventKind.RESULT, stage=ExecutionStage.IMPLEMENTATION, agent="Product Judge", level=EventLevel.INFO))

    presence = service.list_presence()
    assert presence[0].status == PresenceStatus.IDLE


def test_studio_agent_never_gets_a_presence_entry():
    service = CollaborationService()

    publish_execution_event(service, _event(agent="Studio"))

    assert service.list_presence() == ()


# --- real ProjectExecutionService integration -------------------------------


def test_real_fake_execution_mirrors_every_stage_into_the_collaboration_timeline():
    collaboration = CollaborationService()
    brief, handoff = _approved_contract()
    service = ProjectExecutionService(
        id_factory=SequenceIds(),
        clock=_clock,
        collaboration_sink=lambda event: publish_execution_event(collaboration, event),
    )

    started = service.start(brief, handoff)
    finished = service.wait(started.id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    events = collaboration.list_events()
    assert len(events) >= 4
    channels = {event.channel_id for event in events}
    assert "backend" in channels
    assert "qa" in channels
    assert any(event.kind == CollaborationEventKind.TASK_COMPLETED for event in events)


def test_collaboration_sink_failure_never_breaks_real_execution():
    brief, handoff = _approved_contract()

    def _raising_sink(event) -> None:
        raise RuntimeError("collaboration backend is down")

    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, collaboration_sink=_raising_sink)

    started = service.start(brief, handoff)
    finished = service.wait(started.id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED


def test_no_collaboration_sink_is_a_no_op():
    brief, handoff = _approved_contract()
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock)

    finished = service.wait(service.start(brief, handoff).id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED


def test_blocked_execution_publishes_a_blocker_event():
    collaboration = CollaborationService()
    brief, handoff = _approved_contract()
    blocked_adapter = FakeProjectExecutionAdapter(FakeExecutorConfig(readiness_blocker=True))
    service = ProjectExecutionService(
        id_factory=SequenceIds(),
        clock=_clock,
        fake_adapter=blocked_adapter,
        collaboration_sink=lambda event: publish_execution_event(collaboration, event),
    )

    service.start(brief, handoff)

    events = collaboration.list_events()
    assert any(event.kind == CollaborationEventKind.ERROR_DETECTED for event in events)


def test_cancel_of_a_queued_execution_publishes_a_completion_event():
    collaboration = CollaborationService()
    brief, handoff = _approved_contract()
    adapter = FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.2))
    service = ProjectExecutionService(
        id_factory=SequenceIds(),
        clock=_clock,
        fake_adapter=adapter,
        collaboration_sink=lambda event: publish_execution_event(collaboration, event),
    )
    started = service.start(brief, handoff)

    service.cancel(started.id)
    service.wait(started.id, 2)

    events = collaboration.list_events()
    assert len(events) >= 1


# --- real HTTP integration: the default lazy wiring in api/orders.py -------


def _app_without_any_override() -> FastAPI:
    """No install_order_workflow_api / install_collaboration_api call here on purpose:
    both services must come from their real lazy get_or_create getters, so this
    exercises api/orders.py's actual default collaboration_sink wiring, not a test double."""
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(token=TOKEN, bind_host="127.0.0.1", port=8080, launch_id="bridge-api-test-launch", allow_test_client=True),
    )
    app.include_router(orders_router)
    app.include_router(collaboration_router)
    return app


def _client(app: FastAPI) -> TestClient:
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    return TestClient(app, base_url=ORIGIN, headers=headers)


def _order_payload():
    return {
        "title": "PDF Voice Assistant",
        "description": PDF_DESCRIPTION,
        "product_type": "web_app",
        "preferred_language": "ru",
        "constraints": [],
    }


def test_a_real_order_executed_through_the_http_api_reaches_the_collaboration_timeline():
    client = _client(_app_without_any_override())

    created = client.post("/api/orders", json=_order_payload())
    order_id = created.json()["order"]["id"]
    client.post(f"/api/orders/{order_id}/defaults", json={"use_recommended_defaults": True})
    brief = client.post(f"/api/orders/{order_id}/brief", json={}).json()["brief"]
    approved = client.post(f"/api/orders/{order_id}/brief/approve", json={"revision": brief["revision"]})
    preview = approved.json()["design_preview"]
    client.post(
        f"/api/orders/{order_id}/design-preview/approve",
        json={"preview_id": preview["preview_id"], "brief_version": preview["brief_version"]},
    )
    started = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    assert started.status_code == 200, started.text

    for _ in range(50):
        status = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
        if status["status"] == "succeeded":
            break
        sleep(0.05)
    else:
        pytest.fail("fake execution did not reach succeeded status in time")

    timeline = client.get("/api/collaboration/events")
    assert timeline.status_code == 200
    events = timeline.json()["events"]
    assert len(events) >= 4
    assert any(event["kind"] == "task_completed" for event in events)
    assert {"backend", "qa"}.issubset({event["channel_id"] for event in events})

    presence = client.get("/api/collaboration/presence")
    assert presence.status_code == 200
    presence_by_agent = {item["agent"]: item["status"] for item in presence.json()["presence"]}
    assert presence_by_agent["Codex"] == "coding"
    assert presence_by_agent["BugCatcher"] == "testing"
    assert presence_by_agent["Product Judge"] == "idle"
