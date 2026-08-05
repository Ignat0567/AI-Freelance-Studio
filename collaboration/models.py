from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import StringConstraints

from order_workflow.models import LongText, PublicId, ShortText, StrictDomainModel

ChannelId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class CollaborationEventKind(str, Enum):
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    MESSAGE_POSTED = "message_posted"
    ERROR_DETECTED = "error_detected"
    FIX_APPLIED = "fix_applied"
    FILE_MODIFIED = "file_modified"
    BUILD_STARTED = "build_started"
    BUILD_FINISHED = "build_finished"
    TEST_PASSED = "test_passed"
    TEST_FAILED = "test_failed"


class Channel(StrictDomainModel):
    id: ChannelId
    label: ShortText


DEFAULT_CHANNELS: tuple[Channel, ...] = (
    Channel(id="general", label="General"),
    Channel(id="architecture", label="Architecture"),
    Channel(id="backend", label="Backend"),
    Channel(id="frontend", label="Frontend"),
    Channel(id="qa", label="QA"),
    Channel(id="security", label="Security"),
    Channel(id="deployment", label="Deployment"),
    Channel(id="marketing", label="Marketing"),
    Channel(id="design", label="Design"),
)

DEFAULT_CHANNEL_IDS: frozenset[str] = frozenset(channel.id for channel in DEFAULT_CHANNELS)


class ChatMessage(StrictDomainModel):
    id: PublicId
    channel_id: ChannelId
    sender: ShortText
    sender_kind: Literal["user", "agent"]
    message: LongText
    attachments: tuple[ShortText, ...] = ()
    referenced_files: tuple[ShortText, ...] = ()
    task_id: PublicId | None = None
    created_at: datetime


class CollaborationEvent(StrictDomainModel):
    id: PublicId
    channel_id: ChannelId
    kind: CollaborationEventKind
    agent: ShortText | None = None
    message: ShortText
    task_id: PublicId | None = None
    created_at: datetime


class CollaborationError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message or code
