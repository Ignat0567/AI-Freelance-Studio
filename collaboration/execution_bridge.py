from __future__ import annotations

from order_workflow.models import EventKind, EventLevel, ExecutionEvent, ExecutionStage

from .models import CollaborationEventKind, PresenceStatus
from .service import CollaborationService

_STAGE_CHANNELS: dict[ExecutionStage, str] = {
    ExecutionStage.REQUIREMENTS: "general",
    ExecutionStage.DESIGN: "design",
    ExecutionStage.PLANNING: "architecture",
    ExecutionStage.UI_SHELL: "frontend",
    ExecutionStage.CORE_FEATURE: "backend",
    ExecutionStage.BACKEND_DECISION: "architecture",
    ExecutionStage.IMPLEMENTATION: "backend",
    ExecutionStage.VERIFICATION: "qa",
    ExecutionStage.REPAIR: "backend",
    ExecutionStage.PACKAGING: "deployment",
    ExecutionStage.COMPLETED: "general",
}

_STAGE_PRESENCE: dict[ExecutionStage, PresenceStatus] = {
    ExecutionStage.REQUIREMENTS: PresenceStatus.THINKING,
    ExecutionStage.DESIGN: PresenceStatus.REVIEWING,
    ExecutionStage.PLANNING: PresenceStatus.THINKING,
    ExecutionStage.UI_SHELL: PresenceStatus.CODING,
    ExecutionStage.CORE_FEATURE: PresenceStatus.CODING,
    ExecutionStage.BACKEND_DECISION: PresenceStatus.THINKING,
    ExecutionStage.IMPLEMENTATION: PresenceStatus.CODING,
    ExecutionStage.VERIFICATION: PresenceStatus.TESTING,
    ExecutionStage.REPAIR: PresenceStatus.CODING,
    ExecutionStage.PACKAGING: PresenceStatus.REVIEWING,
    ExecutionStage.COMPLETED: PresenceStatus.IDLE,
}


def _channel_for(event: ExecutionEvent) -> str:
    if event.stage is None:
        return "general"
    return _STAGE_CHANNELS.get(event.stage, "general")


def _kind_for(event: ExecutionEvent) -> CollaborationEventKind:
    if event.kind is EventKind.BLOCKER:
        return CollaborationEventKind.ERROR_DETECTED
    if event.kind is EventKind.ARTIFACT:
        return CollaborationEventKind.FILE_MODIFIED
    if event.kind is EventKind.RESULT:
        return CollaborationEventKind.TASK_COMPLETED if event.level is EventLevel.INFO else CollaborationEventKind.ERROR_DETECTED
    if event.kind is EventKind.MILESTONE:
        return CollaborationEventKind.MILESTONE_REACHED
    return CollaborationEventKind.AGENT_ACTIVITY


def _presence_for(event: ExecutionEvent) -> PresenceStatus:
    if event.kind is EventKind.BLOCKER:
        return PresenceStatus.WAITING
    if event.kind is EventKind.RESULT:
        return PresenceStatus.IDLE
    if event.stage is None:
        return PresenceStatus.IDLE
    return _STAGE_PRESENCE.get(event.stage, PresenceStatus.IDLE)


def publish_execution_event(collaboration_service: CollaborationService, event: ExecutionEvent) -> None:
    agent = event.agent if event.agent and event.agent != "Studio" else None
    collaboration_service.publish_event(
        channel_id=_channel_for(event),
        kind=_kind_for(event),
        message=event.message,
        agent=agent,
        task_id=event.execution_id,
    )
    if agent is not None:
        collaboration_service.set_presence(agent=agent, status=_presence_for(event), last_message=event.message)
