from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.orders import install_order_workflow_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from order_workflow import FakeExecutorConfig, FakeProjectExecutionAdapter, ProjectExecutionService
from order_workflow.service import OrderWorkflowError, OrderWorkflowService


pytestmark = pytest.mark.unit
TOKEN = "order-api-focused-token-32-bytes-ok"
ORIGIN = "http://127.0.0.1:8080"
NOW = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)
PDF_DESCRIPTION = (
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
            return f"api-{self.index:04d}"


class ExplodingService(OrderWorkflowService):
    def snapshot(self, order_id: str):
        raise RuntimeError("raw stack trace token=secret-value")


def _service(fake_adapter=None, mode="fake") -> OrderWorkflowService:
    ids = SequenceIds()
    execution = ProjectExecutionService(
        id_factory=ids,
        clock=lambda: NOW,
        fake_adapter=fake_adapter,
        mode=mode,
    )
    return OrderWorkflowService(id_factory=ids, clock=lambda: NOW, execution_service=execution)


def _app(service=None):
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(
            token=TOKEN,
            bind_host="127.0.0.1",
            port=8080,
            launch_id="order-api-test-launch",
            allow_test_client=True,
        ),
    )
    install_order_workflow_api(app, service=service or _service())
    app.include_router(router)
    return app


def _client(app, *, token=TOKEN, origin=ORIGIN, content_type="application/json"):
    headers = {"X-FreelancerStudio-Token": token, "Origin": origin}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return TestClient(app, base_url=ORIGIN, headers=headers)


def _order_payload(**changes):
    payload = {
        "title": "PDF Voice Assistant",
        "description": PDF_DESCRIPTION,
        "product_type": "web_app",
        "preferred_language": "ru",
        "constraints": [],
    }
    payload.update(changes)
    return payload


def _create(client):
    response = client.post("/api/orders", json=_order_payload())
    assert response.status_code == 200, response.text
    return response.json()


def _complete_defaults(client):
    state = _create(client)
    order_id = state["order"]["id"]
    response = client.post(f"/api/orders/{order_id}/defaults", json={"use_recommended_defaults": True})
    assert response.status_code == 200, response.text
    return order_id, response.json()


def _approve(client):
    order_id, _ = _complete_defaults(client)
    brief_state = client.post(f"/api/orders/{order_id}/brief", json={})
    assert brief_state.status_code == 200, brief_state.text
    brief = brief_state.json()["brief"]
    approve = client.post(
        f"/api/orders/{order_id}/brief/approve",
        json={"revision": brief["revision"]},
    )
    assert approve.status_code == 200, approve.text
    return order_id, approve.json()


def test_orders_api_requires_existing_local_security_boundary():
    app = _app()
    no_auth = TestClient(app, base_url=ORIGIN).post("/api/orders", json=_order_payload())
    bad_auth = _client(app, token="incorrect-token-with-enough-length").post("/api/orders", json=_order_payload())
    good = _client(app).post("/api/orders", json=_order_payload())

    assert no_auth.status_code == 401
    assert bad_auth.status_code == 401
    assert good.status_code == 200
    assert TOKEN not in good.text


def test_mutations_require_post_strict_json_and_reject_extra_fields():
    client = _client(_app())

    assert client.get("/api/orders").status_code == 405
    wrong_content = _client(_app(), content_type="text/plain").post("/api/orders", content='{"title":"x"}')
    assert wrong_content.status_code in {400, 415}
    extra = client.post("/api/orders", json={**_order_payload(), "command": "rm -rf ."})
    assert extra.status_code == 422
    token_body = client.post("/api/orders", json={**_order_payload(), "backend_token": TOKEN})
    assert token_body.status_code == 422
    assert TOKEN not in token_body.text
    malformed = client.post("/api/orders", content="{not valid json")
    assert malformed.status_code == 422


def test_create_pdf_order_returns_relevant_questions_without_execution():
    state = _create(_client(_app()))
    ids = [item["id"] for item in state["questions"]]

    assert state["order"]["status"] == "clarification_required"
    assert state["execution"] is None
    assert state["next_action"]["code"] == "answer_clarification"
    assert ids == [
        "target-users",
        "document-ocr",
        "speech-languages",
        "document-persistence",
        "document-processing",
        "elena-design",
    ]
    assert "core-features" not in ids


def test_unsupported_product_type_and_empty_description_are_rejected():
    client = _client(_app())
    unsupported = client.post("/api/orders", json={**_order_payload(), "product_type": "desktop_app"})
    empty = client.post("/api/orders", json={**_order_payload(), "description": ""})

    assert unsupported.status_code == 400
    assert unsupported.json()["detail"]["code"] == "unsupported_product_type"
    assert empty.status_code == 422


def test_answers_validate_question_ids_and_options():
    client = _client(_app())
    state = _create(client)
    order_id = state["order"]["id"]

    invalid = client.post(f"/api/orders/{order_id}/answers", json={"answers": [{"question_id": "target-users", "value": "Robots"}]})
    unknown = client.post(f"/api/orders/{order_id}/answers", json={"answers": [{"question_id": "unknown", "value": "Only me"}]})
    valid = client.post(f"/api/orders/{order_id}/answers", json={"answers": [{"question_id": "target-users", "value": "Only me"}]})

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_answer"
    assert unknown.status_code == 404
    assert valid.status_code == 200
    assert "target-users" in [item["question_id"] for item in valid.json()["answers"]]


def test_defaults_generate_pdf_brief_with_expected_sections():
    client = _client(_app())
    order_id, defaulted = _complete_defaults(client)
    brief_response = client.post(f"/api/orders/{order_id}/brief", json={})
    brief = brief_response.json()["brief"]
    joined_features = " ".join(brief["core_features"]).casefold()
    joined_non_goals = " ".join(brief["non_goals"]).casefold()

    assert defaulted["order"]["status"] == "brief_ready"
    assert brief_response.status_code == 200
    assert "pdf upload" in joined_features
    assert "push-to-talk" in joined_features
    assert "page citations" in joined_features
    assert "ocr" in joined_non_goals
    assert brief["elena_design_choice"] == "show_elena_concept"


def test_brief_unavailable_before_clarification_and_revision_updates_version():
    client = _client(_app())
    created = _create(client)
    order_id = created["order"]["id"]
    assert client.get(f"/api/orders/{order_id}/brief").status_code == 409

    order_id, _ = _complete_defaults(client)
    first = client.post(f"/api/orders/{order_id}/brief", json={}).json()["brief"]
    revised = client.post(
        f"/api/orders/{order_id}/brief/revise",
        json={"operations": [{"kind": "add_requirement", "value": "Keyboard shortcut for push-to-talk"}]},
    )
    assert revised.status_code == 200, revised.text
    second = revised.json()["brief"]
    assert second["revision"] == first["revision"] + 1
    assert "Keyboard shortcut for push-to-talk" in second["core_features"]


def test_stale_and_current_approval_and_handoff_flow():
    client = _client(_app())
    order_id, _ = _complete_defaults(client)
    brief = client.post(f"/api/orders/{order_id}/brief", json={}).json()["brief"]

    assert client.get(f"/api/orders/{order_id}/handoff").status_code == 409
    stale = client.post(f"/api/orders/{order_id}/brief/approve", json={"revision": brief["revision"] + 1})
    approved = client.post(f"/api/orders/{order_id}/brief/approve", json={"revision": brief["revision"]})
    handoff = client.get(f"/api/orders/{order_id}/handoff")

    assert stale.status_code == 409
    assert approved.status_code == 200
    assert approved.json()["approval"]["approved"] is True
    assert handoff.status_code == 200
    serialized = handoff.text.casefold()
    assert "chat_history" not in serialized
    assert "api_key" not in serialized
    assert handoff.json()["handoff"]["source_agent"] == "alex"


def test_execution_requires_approval_then_fake_execution_exposes_events_artifacts_result():
    client = _client(_app())
    order_id, _ = _complete_defaults(client)
    blocked = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    assert blocked.status_code == 409

    order_id, _ = _approve(client)
    started = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    assert started.status_code == 200
    execution_id = started.json()["execution"]["id"]
    for _ in range(30):
        status = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
        if status["status"] == "succeeded":
            break
        sleep(0.01)

    events = client.get(f"/api/orders/{order_id}/events").json()["events"]
    artifacts = client.get(f"/api/orders/{order_id}/artifacts").json()["artifacts"]
    result = client.get(f"/api/orders/{order_id}/result").json()["result"]

    assert status["id"] == execution_id
    assert status["status"] == "succeeded"
    assert events
    assert len(artifacts) == 4
    assert all(item["simulated"] for item in artifacts)
    assert result["test_summary"]["passed"] == 4
    assert TOKEN not in str(result)


def test_duplicate_active_returns_existing_and_terminal_duplicate_conflicts():
    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05)))
    client = _client(_app(service))
    order_id, _ = _approve(client)

    first = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"}).json()["execution"]
    second = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"}).json()["execution"]
    assert second["id"] == first["id"]
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)
    terminal = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    assert terminal.status_code == 409
    assert terminal.json()["detail"]["code"] == "execution_already_completed"


def test_cancellation_and_production_mode_blocker():
    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05)))
    client = _client(_app(service))
    order_id, _ = _approve(client)
    started = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    cancelled = client.post(f"/api/orders/{order_id}/execution/cancel")
    assert started.status_code == 200
    assert cancelled.status_code == 200
    assert cancelled.json()["execution"]["status"] in {"running", "cancelled"}

    production_client = _client(_app())
    prod_order_id, _ = _approve(production_client)
    production = production_client.post(f"/api/orders/{prod_order_id}/execution", json={"mode": "production"})
    assert production.status_code == 200
    body = production.json()
    assert body["execution"]["mode"] == "production"
    assert body["execution"]["status"] == "awaiting_user"
    assert body["blockers"][0]["code"] == "execution_provider_not_configured"


def test_unknown_order_and_internal_errors_are_sanitized():
    client = _client(_app())
    missing = client.get("/api/orders/order_missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "order_not_found"

    app = _app(ExplodingService())
    app.state.order_workflow_service = ExplodingService()
    raw = _client(app).get("/api/orders/order_any")
    assert raw.status_code == 500
    assert raw.json()["detail"]["code"] == "internal_error"
    assert "secret-value" not in raw.text


def test_order_routes_are_registered_through_system_router_seam():
    from api.system import router as system_router

    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(token=TOKEN, bind_host="127.0.0.1", port=8080, launch_id="system-seam", allow_test_client=True),
    )
    install_order_workflow_api(app, service=_service())
    app.include_router(system_router)
    response = _client(app).post("/api/orders", json=_order_payload())
    assert response.status_code == 200
