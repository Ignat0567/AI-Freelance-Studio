from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.collaboration import install_collaboration_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from collaboration.service import CollaborationService

pytestmark = pytest.mark.unit
TOKEN = "collaboration-api-focused-token-32-bytes"
ORIGIN = "http://127.0.0.1:8080"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(token=TOKEN, bind_host="127.0.0.1", port=8080, launch_id="collaboration-api-test-launch", allow_test_client=True),
    )
    install_collaboration_api(app, service=CollaborationService())
    app.include_router(router)
    return app


def _client() -> TestClient:
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    return TestClient(_app(), base_url=ORIGIN, headers=headers)


def test_list_channels_returns_the_default_channels():
    response = _client().get("/api/collaboration/channels")

    assert response.status_code == 200
    ids = {channel["id"] for channel in response.json()["channels"]}
    assert "general" in ids
    assert "qa" in ids
    assert len(ids) == 9


def test_post_and_list_messages_round_trips():
    client = _client()

    posted = client.post("/api/collaboration/channels/general/messages", json={"sender": "user", "message": "Hello team"})
    assert posted.status_code == 200
    assert posted.json()["message"]["message"] == "Hello team"
    assert posted.json()["message"]["sender_kind"] == "user"

    listed = client.get("/api/collaboration/channels/general/messages")
    assert listed.status_code == 200
    assert len(listed.json()["messages"]) == 1
    assert listed.json()["messages"][0]["message"] == "Hello team"


def test_post_message_to_unknown_channel_returns_404():
    response = _client().post("/api/collaboration/channels/does-not-exist/messages", json={"sender": "user", "message": "hi"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_channel"


def test_list_messages_for_unknown_channel_returns_404():
    response = _client().get("/api/collaboration/channels/does-not-exist/messages")

    assert response.status_code == 404


def test_events_endpoint_reflects_posted_messages():
    client = _client()
    client.post("/api/collaboration/channels/backend/messages", json={"sender": "maya", "message": "shipped the fix"})

    response = client.get("/api/collaboration/events", params={"channel_id": "backend"})

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["kind"] == "message_posted"
    assert events[0]["message"] == "shipped the fix"


def test_events_endpoint_without_channel_returns_all_channels():
    client = _client()
    client.post("/api/collaboration/channels/backend/messages", json={"sender": "maya", "message": "a"})
    client.post("/api/collaboration/channels/frontend/messages", json={"sender": "elena", "message": "b"})

    response = client.get("/api/collaboration/events")

    assert response.status_code == 200
    assert len(response.json()["events"]) == 2


def test_post_message_requires_the_security_token():
    unauthenticated = TestClient(_app(), base_url=ORIGIN)

    response = unauthenticated.post("/api/collaboration/channels/general/messages", json={"sender": "user", "message": "hi"})

    assert response.status_code in (401, 403)


def test_post_message_rejects_empty_message():
    response = _client().post("/api/collaboration/channels/general/messages", json={"sender": "user", "message": ""})

    assert response.status_code == 422
