from __future__ import annotations

import pytest

from collaboration.models import CollaborationEventKind, DEFAULT_CHANNEL_IDS, PresenceStatus
from collaboration.service import CollaborationError, CollaborationService

pytestmark = pytest.mark.unit


def test_channels_returns_the_nine_default_channels():
    service = CollaborationService()

    channels = service.channels()

    assert {channel.id for channel in channels} == DEFAULT_CHANNEL_IDS
    assert len(channels) == 9


def test_post_message_stores_it_and_auto_publishes_a_message_posted_event():
    service = CollaborationService()

    message = service.post_message(channel_id="backend", sender="maya", sender_kind="agent", message="Refactored the auth module.")

    assert message.channel_id == "backend"
    assert message.sender == "maya"
    assert service.list_messages("backend") == (message,)

    events = service.list_events("backend")
    assert len(events) == 1
    assert events[0].kind == CollaborationEventKind.MESSAGE_POSTED
    assert events[0].agent == "maya"
    assert events[0].message == "Refactored the auth module."


def test_user_messages_do_not_carry_an_agent_on_the_event():
    service = CollaborationService()

    service.post_message(channel_id="general", sender="user", sender_kind="user", message="Hello team")

    events = service.list_events("general")
    assert events[0].agent is None


def test_post_message_rejects_unknown_channel():
    service = CollaborationService()

    with pytest.raises(CollaborationError) as excinfo:
        service.post_message(channel_id="does-not-exist", sender="user", sender_kind="user", message="hi")

    assert excinfo.value.code == "unknown_channel"


def test_list_messages_rejects_unknown_channel():
    service = CollaborationService()

    with pytest.raises(CollaborationError):
        service.list_messages("does-not-exist")


def test_publish_event_truncates_overlong_message_to_the_short_text_cap():
    service = CollaborationService()
    long_message = "x" * 500

    event = service.publish_event(channel_id="qa", kind=CollaborationEventKind.TEST_FAILED, message=long_message, agent="bugcatcher")

    assert len(event.message) <= 240
    assert event.message.endswith("...")


def test_list_events_without_channel_returns_every_channel_in_chronological_order():
    service = CollaborationService()
    service.post_message(channel_id="backend", sender="maya", sender_kind="agent", message="first")
    service.post_message(channel_id="qa", sender="bugcatcher", sender_kind="agent", message="second")

    events = service.list_events()

    assert len(events) == 2
    assert [event.channel_id for event in events] == ["backend", "qa"]
    assert events[0].created_at <= events[1].created_at


def test_messages_are_isolated_per_channel():
    service = CollaborationService()
    service.post_message(channel_id="backend", sender="maya", sender_kind="agent", message="backend note")
    service.post_message(channel_id="frontend", sender="elena", sender_kind="agent", message="frontend note")

    assert len(service.list_messages("backend")) == 1
    assert len(service.list_messages("frontend")) == 1
    assert service.list_messages("backend")[0].message == "backend note"


def test_set_presence_records_status_and_snippeted_last_message():
    service = CollaborationService()

    presence = service.set_presence(agent="Codex", status=PresenceStatus.CODING, last_message="x" * 500)

    assert presence.agent == "Codex"
    assert presence.status == PresenceStatus.CODING
    assert len(presence.last_message) <= 240


def test_list_presence_returns_the_latest_status_per_agent_sorted_by_name():
    service = CollaborationService()
    service.set_presence(agent="Codex", status=PresenceStatus.CODING, last_message="writing code")
    service.set_presence(agent="Alex", status=PresenceStatus.THINKING, last_message="reviewing requirements")
    service.set_presence(agent="Codex", status=PresenceStatus.TESTING, last_message="now testing")

    presence = service.list_presence()

    assert [item.agent for item in presence] == ["Alex", "Codex"]
    codex = next(item for item in presence if item.agent == "Codex")
    assert codex.status == PresenceStatus.TESTING
    assert codex.last_message == "now testing"
