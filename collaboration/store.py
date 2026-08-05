from __future__ import annotations

from threading import RLock
from typing import Protocol

from .models import ChatMessage, CollaborationEvent


class CollaborationStore(Protocol):
    def save_message(self, message: ChatMessage) -> None: ...

    def list_messages(self, channel_id: str) -> tuple[ChatMessage, ...]: ...

    def save_event(self, event: CollaborationEvent) -> None: ...

    def list_events(self, channel_id: str | None = None) -> tuple[CollaborationEvent, ...]: ...


class InMemoryCollaborationStore:
    """RAM-only message/event store for the MVP collaboration layer, mirroring
    order_workflow.persistence.InMemoryExecutionStateStore's shape."""

    def __init__(self) -> None:
        self._messages: dict[str, list[ChatMessage]] = {}
        self._events: list[CollaborationEvent] = []
        self._lock = RLock()

    def save_message(self, message: ChatMessage) -> None:
        with self._lock:
            self._messages.setdefault(message.channel_id, []).append(message)

    def list_messages(self, channel_id: str) -> tuple[ChatMessage, ...]:
        with self._lock:
            return tuple(self._messages.get(channel_id, ()))

    def save_event(self, event: CollaborationEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_events(self, channel_id: str | None = None) -> tuple[CollaborationEvent, ...]:
        with self._lock:
            if channel_id is None:
                return tuple(self._events)
            return tuple(item for item in self._events if item.channel_id == channel_id)
