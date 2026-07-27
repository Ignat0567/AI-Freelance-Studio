from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.orders import install_order_workflow_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from order_workflow import FakeExecutorConfig, FakeProjectExecutionAdapter, ProjectExecutionService
from order_workflow.service import OrderWorkflowService


pytestmark = pytest.mark.unit
TOKEN = "acceptance-order-token-32-bytes-ok"
ORIGIN = "http://127.0.0.1:8080"
NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)
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
            return f"accept-{self.index:04d}"


def _app(*, fake_adapter=None, mode="fake"):
    ids = SequenceIds()
    execution = ProjectExecutionService(
        id_factory=ids,
        clock=lambda: NOW,
        fake_adapter=fake_adapter,
        mode=mode,
    )
    service = OrderWorkflowService(id_factory=ids, clock=lambda: NOW, execution_service=execution)
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(
            token=TOKEN,
            bind_host="127.0.0.1",
            port=8080,
            launch_id="order-acceptance",
            allow_test_client=True,
        ),
    )
    install_order_workflow_api(app, service=service)
    app.include_router(router)
    return app


def _client(app, *, token=TOKEN, content_type="application/json"):
    headers = {"X-FreelancerStudio-Token": token, "Origin": ORIGIN}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return TestClient(app, base_url=ORIGIN, headers=headers)


def _payload():
    return {
        "title": "PDF Voice Assistant",
        "description": PDF_DESCRIPTION,
        "product_type": "web_app",
        "preferred_language": "ru",
        "constraints": [],
    }


def _post_ok(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    return response.json()


def _poll_terminal(client, order_id, timeout=3.0):
    deadline = monotonic() + timeout
    last = None
    while monotonic() < deadline:
        response = client.get(f"/api/orders/{order_id}/execution")
        assert response.status_code == 200, response.text
        last = response.json()["execution"]
        if last["status"] in {"succeeded", "failed", "cancelled"}:
            return last
        sleep(0.02)
    raise AssertionError(f"execution did not reach terminal state: {last}")


def test_pdf_voice_assistant_full_api_acceptance_sequence():
    client = _client(_app(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.01))))

    created = _post_ok(client, "/api/orders", _payload())
    order_id = created["order"]["id"]
    questions = client.get(f"/api/orders/{order_id}/questions").json()["questions"]

    assert created["order"]["title"] == "PDF Voice Assistant"
    assert created["order"]["execution_id"] is None
    assert [item["id"] for item in questions] == [
        "target-users",
        "document-ocr",
        "speech-languages",
        "document-persistence",
        "document-processing",
        "elena-design",
    ]
    assert all("reason" in item and item["reason"] for item in questions)
    assert client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"}).status_code == 409

    defaulted = _post_ok(client, f"/api/orders/{order_id}/defaults", {"use_recommended_defaults": True})
    assert defaulted["order"]["id"] == order_id
    assert defaulted["order"]["status"] == "brief_ready"
    assert any("single-user local" in item for item in defaulted["assumptions"])

    generated = _post_ok(client, f"/api/orders/{order_id}/brief", {})
    fetched = client.get(f"/api/orders/{order_id}/brief")
    assert fetched.status_code == 200
    assert fetched.json()["brief"]["id"] == generated["brief"]["id"]
    brief = generated["brief"]
    features = " | ".join(brief["core_features"])
    criteria = " | ".join(brief["acceptance_criteria"])
    for expected in [
        "PDF upload",
        "Document processing status",
        "Document list",
        "Document removal",
        "Text extraction",
        "Document chunking and local indexing",
        "Document-grounded answers",
        "Document name and page citations",
        "Typed questions",
        "Push-to-talk questions",
        "Visible recognized speech text",
        "Speech output",
        "Stop speech action",
        "Honest no-answer response",
    ]:
        assert expected in features
    for expected in [
        "upload a text-based PDF",
        "processing status",
        "typed question",
        "grounded only in uploaded documents",
        "document name and page reference",
        "do not contain an answer",
        "Push-to-talk voice input",
        "Recognized text is visible",
        "answer spoken aloud",
        "stop speech playback",
        "actionable blockers",
        "Raw exceptions and stack traces",
    ]:
        assert expected in criteria
    assert any("OCR" in item for item in brief["non_goals"])

    stale = client.post(f"/api/orders/{order_id}/brief/approve", json={"revision": brief["revision"] + 1})
    assert stale.status_code == 409
    approved = _post_ok(client, f"/api/orders/{order_id}/brief/approve", {"revision": brief["revision"]})
    assert approved["approval"]["approved"] is True
    assert approved["order"]["id"] == order_id
    preview = approved["design_preview"]
    preview_text = json.dumps(preview, sort_keys=True).casefold()
    assert preview["layout_type"] == "three_panel_workspace"
    for expected in ["left pdf library panel", "center voice/text chat", "right source evidence panel", "citation pinning", "no-answer state", "speech playback"]:
        assert expected in preview_text

    blocked_handoff = client.get(f"/api/orders/{order_id}/handoff")
    assert blocked_handoff.status_code == 409
    preview_approved = _post_ok(client, f"/api/orders/{order_id}/design-preview/approve", {"preview_id": preview["preview_id"], "brief_version": preview["brief_version"]})
    assert preview_approved["design_preview"]["approved"] is True

    handoff = client.get(f"/api/orders/{order_id}/handoff")
    assert handoff.status_code == 200
    handoff_text = handoff.text.casefold()
    assert handoff.json()["handoff"]["source_agent"] == "alex"
    assert handoff.json()["handoff"]["target_agent"] == "codex"
    assert handoff.json()["handoff"]["design_preview_id"] == preview["preview_id"]
    assert "chat_history" not in handoff_text
    assert "api_key" not in handoff_text

    started = _post_ok(client, f"/api/orders/{order_id}/execution", {"mode": "fake"})
    execution_id = started["execution"]["id"]
    immediate = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
    assert immediate["id"] == execution_id
    assert immediate["status"] in {"queued", "running", "succeeded"}
    terminal = _poll_terminal(client, order_id)
    assert terminal["status"] == "succeeded"

    events = client.get(f"/api/orders/{order_id}/events").json()["events"]
    artifacts = client.get(f"/api/orders/{order_id}/artifacts").json()["artifacts"]
    result = client.get(f"/api/orders/{order_id}/result").json()["result"]

    assert [event["created_at"] for event in events] == sorted(event["created_at"] for event in events)
    assert any(event["agent"] == "Codex" for event in events)
    assert any(event["stage"] == "verification" for event in events)
    assert len(artifacts) == 4
    assert all(item["simulated"] for item in artifacts)
    assert result["success"] is True
    assert result["test_summary"]["passed"] == 4
    serialized = json.dumps({"events": events, "artifacts": artifacts, "result": result}, sort_keys=True).casefold()
    assert "traceback" not in serialized
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_duplicate_cancel_recovery_and_production_negative_paths():
    client = _client(_app(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05))))
    order_id = _post_ok(client, "/api/orders", _payload())["order"]["id"]
    _post_ok(client, f"/api/orders/{order_id}/defaults", {"use_recommended_defaults": True})
    brief = _post_ok(client, f"/api/orders/{order_id}/brief", {})["brief"]
    approved = _post_ok(client, f"/api/orders/{order_id}/brief/approve", {"revision": brief["revision"]})
    preview = approved["design_preview"]
    _post_ok(client, f"/api/orders/{order_id}/design-preview/approve", {"preview_id": preview["preview_id"], "brief_version": preview["brief_version"]})

    first = _post_ok(client, f"/api/orders/{order_id}/execution", {"mode": "fake"})["execution"]
    duplicate = _post_ok(client, f"/api/orders/{order_id}/execution", {"mode": "fake"})["execution"]
    assert duplicate["id"] == first["id"]
    cancelled = client.post(f"/api/orders/{order_id}/execution/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["execution"]["status"] in {"running", "cancelled"}
    cancellation_terminal = _poll_terminal(client, order_id)
    assert cancellation_terminal["status"] == "cancelled"
    assert cancellation_terminal["result"]["outcome"] == "cancelled"

    prod_client = _client(_app())
    prod_order = _post_ok(prod_client, "/api/orders", _payload())["order"]["id"]
    _post_ok(prod_client, f"/api/orders/{prod_order}/defaults", {"use_recommended_defaults": True})
    prod_brief = _post_ok(prod_client, f"/api/orders/{prod_order}/brief", {})["brief"]
    prod_approved = _post_ok(prod_client, f"/api/orders/{prod_order}/brief/approve", {"revision": prod_brief["revision"]})
    prod_preview = prod_approved["design_preview"]
    _post_ok(prod_client, f"/api/orders/{prod_order}/design-preview/approve", {"preview_id": prod_preview["preview_id"], "brief_version": prod_preview["brief_version"]})
    production = _post_ok(prod_client, f"/api/orders/{prod_order}/execution", {"mode": "production"})
    assert production["execution"]["mode"] == "production"
    assert production["execution"]["status"] == "awaiting_user"
    assert production["blockers"][0]["code"] == "execution_provider_not_configured"


def test_orders_security_strictness_and_token_safety_regression():
    app = _app()
    assert TestClient(app, base_url=ORIGIN).post("/api/orders", json=_payload()).status_code == 401
    assert _client(app, token="incorrect-token-with-enough-length").post("/api/orders", json=_payload()).status_code == 401
    assert _client(app, content_type="text/plain").post("/api/orders", content=json.dumps(_payload())).status_code in {400, 415}
    command = _client(app).post("/api/orders", json={**_payload(), "command": "powershell.exe"})
    token_body = _client(app).post("/api/orders", json={**_payload(), "backend_token": TOKEN})
    assert command.status_code == 422
    assert token_body.status_code == 422
    assert TOKEN not in token_body.text


def test_ui_acceptance_path_is_wired_without_renderer_token_or_hidden_pdf_execution_logic():
    root = Path("frontend/src/features/order-workflow")
    page = (root / "OrderWorkflowPage.jsx").read_text(encoding="utf-8")
    create = (root / "OrderCreatePanel.jsx").read_text(encoding="utf-8")
    clarification = (root / "ClarificationPanel.jsx").read_text(encoding="utf-8")
    brief = (root / "ProjectBriefPanel.jsx").read_text(encoding="utf-8")
    execution = (root / "ExecutionDashboard.jsx").read_text(encoding="utf-8")
    result = (root / "ExecutionResultPanel.jsx").read_text(encoding="utf-8")
    api = (root / "orderWorkflowApi.js").read_text(encoding="utf-8")
    state = (root / "orderWorkflowState.js").read_text(encoding="utf-8")
    dashboard = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")

    assert "Create Project" in dashboard
    assert "Use PDF Voice Assistant example" in create
    assert "fillExample" in create
    assert "onSubmit={submitOrder}" in page
    assert "orderWorkflowApi.createOrder" in page
    assert "Use recommended defaults" in clarification
    assert "orderWorkflowApi.applyDefaults" in page
    assert "Visible assumptions" in clarification
    assert "orderWorkflowApi.generateBrief" in page
    assert "Approve brief" in brief
    assert "Elena Design Preview" in brief
    assert "orderWorkflowApi.approveDesignPreview" in page
    assert "orderWorkflowApi.approveBrief" in page
    assert "Run simulation" in execution
    assert "Prepare production dry-run" in execution
    assert "orderWorkflowApi.startExecution" in page
    assert "orderWorkflowApi.cancelExecution" in page
    assert "orderWorkflowApi.getOrder" in page
    assert "Simulation mode" in execution
    assert "Simulated artifact" in result
    assert "test_summary" in result
    assert "studio_order_workflow_last_order_id_v1" in page
    assert "The backend restarted and this in-memory order is no longer available" in page
    assert "startInFlight" in page
    assert "clearInterval" in page
    assert "approval?.approved" in page and "handoff_ready" in page
    assert "aria-live" in page + execution
    assert "role=\"alert\"" in page + execution
    assert "prefers-reduced-motion" in (root / "OrderWorkflow.css").read_text(encoding="utf-8")
    assert "X-FreelancerStudio-Token" not in api + page + state
    assert "backend_token" not in api + page + state
    assert "PDF Voice Assistant" not in execution + result


def test_ui_theme_modes_keep_core_content_available_in_source():
    css = Path("frontend/src/features/order-workflow/OrderWorkflow.css").read_text(encoding="utf-8")
    page = Path("frontend/src/features/order-workflow/OrderWorkflowPage.jsx").read_text(encoding="utf-8")
    assert "var(--fs-text)" in css
    assert "var(--fs-muted)" in css
    assert "var(--fs-border)" in css
    assert "@media (max-width: 860px)" in css
    assert "Create Project" in page
    assert "MVP Core Workflow" in page
