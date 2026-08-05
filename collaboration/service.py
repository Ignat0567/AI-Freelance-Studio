from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Literal

from order_workflow.models import new_public_id

from .models import (
    DEFAULT_CHANNEL_IDS,
    DEFAULT_CHANNELS,
    AgentPresence,
    Channel,
    ChatMessage,
    CollaborationError,
    CollaborationEvent,
    CollaborationEventKind,
    PresenceStatus,
)
from .store import CollaborationStore, InMemoryCollaborationStore

_EVENT_MESSAGE_MAX_LENGTH = 240


def _event_snippet(text: str) -> str:
    trimmed = text.strip()
    if len(trimmed) <= _EVENT_MESSAGE_MAX_LENGTH:
        return trimmed
    return trimmed[: _EVENT_MESSAGE_MAX_LENGTH - 3].rstrip() + "..."


class CollaborationService:
    def __init__(self, *, store: CollaborationStore | None = None) -> None:
        self._store = store or InMemoryCollaborationStore()
        self._presence: dict[str, AgentPresence] = {}
        self._lock = RLock()

    def channels(self) -> tuple[Channel, ...]:
        return DEFAULT_CHANNELS

    def _require_known_channel(self, channel_id: str) -> None:
        if channel_id not in DEFAULT_CHANNEL_IDS:
            raise CollaborationError("unknown_channel", f"Unknown channel: {channel_id}")

    def post_message(
        self,
        *,
        channel_id: str,
        sender: str,
        sender_kind: Literal["user", "agent"],
        message: str,
        attachments: tuple[str, ...] = (),
        referenced_files: tuple[str, ...] = (),
        task_id: str | None = None,
    ) -> ChatMessage:
        self._require_known_channel(channel_id)
        with self._lock:
            chat_message = ChatMessage(
                id=new_public_id("msg"),
                channel_id=channel_id,
                sender=sender,
                sender_kind=sender_kind,
                message=message,
                attachments=attachments,
                referenced_files=referenced_files,
                task_id=task_id,
                created_at=datetime.now(timezone.utc),
            )
            self._store.save_message(chat_message)
            self._publish(
                channel_id=channel_id,
                kind=CollaborationEventKind.MESSAGE_POSTED,
                message=_event_snippet(message),
                agent=sender if sender_kind == "agent" else None,
                task_id=task_id,
            )
            return chat_message

    def list_messages(self, channel_id: str) -> tuple[ChatMessage, ...]:
        self._require_known_channel(channel_id)
        return self._store.list_messages(channel_id)

    def publish_event(
        self,
        *,
        channel_id: str,
        kind: CollaborationEventKind,
        message: str,
        agent: str | None = None,
        task_id: str | None = None,
    ) -> CollaborationEvent:
        self._require_known_channel(channel_id)
        with self._lock:
            return self._publish(channel_id=channel_id, kind=kind, message=message, agent=agent, task_id=task_id)

    def _publish(
        self,
        *,
        channel_id: str,
        kind: CollaborationEventKind,
        message: str,
        agent: str | None,
        task_id: str | None,
    ) -> CollaborationEvent:
        event = CollaborationEvent(
            id=new_public_id("evt"),
            channel_id=channel_id,
            kind=kind,
            agent=agent,
            message=_event_snippet(message),
            task_id=task_id,
            created_at=datetime.now(timezone.utc),
        )
        self._store.save_event(event)
        return event

    def list_events(self, channel_id: str | None = None) -> tuple[CollaborationEvent, ...]:
        if channel_id is not None:
            self._require_known_channel(channel_id)
        events = self._store.list_events(channel_id)
        return tuple(sorted(events, key=lambda item: item.created_at))

    def set_presence(self, *, agent: str, status: PresenceStatus, last_message: str | None = None) -> AgentPresence:
        with self._lock:
            presence = AgentPresence(
                agent=agent,
                status=status,
                last_message=_event_snippet(last_message) if last_message else None,
                updated_at=datetime.now(timezone.utc),
            )
            self._presence[agent] = presence
            return presence

    def list_presence(self) -> tuple[AgentPresence, ...]:
        with self._lock:
            return tuple(sorted(self._presence.values(), key=lambda item: item.agent))


def get_or_create_collaboration_service(app) -> CollaborationService:
    service = getattr(app.state, "collaboration_service", None)
    if service is None:
        service = CollaborationService()
        app.state.collaboration_service = service
    return service
