from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.orders import install_order_workflow_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from order_workflow import (
    ExecutionResult,
    FakeExecutorConfig,
    FakeProjectExecutionAdapter,
    ProjectExecutionService,
    TestSummary as WorkflowTestSummary,
    TokenUsage,
)
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


def _service(fake_adapter=None, mode="fake", revision_adapter=None) -> OrderWorkflowService:
    ids = SequenceIds()
    execution = ProjectExecutionService(
        id_factory=ids,
        clock=lambda: NOW,
        fake_adapter=fake_adapter,
        mode=mode,
        revision_adapter=revision_adapter,
    )
    return OrderWorkflowService(id_factory=ids, clock=lambda: NOW, execution_service=execution)


def _app(service=None, ai_ask=None):
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
    install_order_workflow_api(app, service=service or _service(), ai_ask=ai_ask)
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
    preview = approve.json()["design_preview"]
    preview_approval = client.post(
        f"/api/orders/{order_id}/design-preview/approve",
        json={"preview_id": preview["preview_id"], "brief_version": preview["brief_version"]},
    )
    assert preview_approval.status_code == 200, preview_approval.text
    return order_id, preview_approval.json()


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

    assert client.get("/api/orders").status_code == 200  # listing orders is a real, read-only GET endpoint
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


def test_create_bot_order_is_accepted_and_never_asks_elena_design():
    client = _client(_app())
    response = client.post("/api/orders", json={
        **_order_payload(),
        "title": "Habit Bot",
        "product_type": "bot",
        "description": "Build a Telegram bot for tracking daily habits, with a command to add a habit and mark it done.",
    })

    assert response.status_code == 200
    state = response.json()
    assert state["order"]["product_type"] == "bot"
    question_ids = [item["id"] for item in state["questions"]]
    assert "elena-design" not in question_ids


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
    preview = approved.json()["design_preview"]
    handoff_before_preview = client.get(f"/api/orders/{order_id}/handoff")
    preview_approval = client.post(f"/api/orders/{order_id}/design-preview/approve", json={"preview_id": preview["preview_id"], "brief_version": preview["brief_version"]})
    handoff = client.get(f"/api/orders/{order_id}/handoff")

    assert stale.status_code == 409
    assert approved.status_code == 200
    assert approved.json()["approval"]["approved"] is True
    assert approved.json()["design_preview"]["layout_type"] == "three_panel_workspace"
    assert handoff_before_preview.status_code == 409
    assert preview_approval.status_code == 200
    assert handoff.status_code == 200
    serialized = handoff.text.casefold()
    assert "chat_history" not in serialized
    assert "api_key" not in serialized
    assert handoff.json()["handoff"]["source_agent"] == "alex"
    assert handoff.json()["handoff"]["design_preview_id"] == preview["preview_id"]


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


def test_readiness_route_is_authorized_stable_and_does_not_start_execution():
    app = _app()
    client = _client(app)
    order_id = _create(client)["order"]["id"]

    no_auth = TestClient(app, base_url=ORIGIN).get(f"/api/orders/{order_id}/readiness")
    unknown = client.get("/api/orders/order_missing/readiness")
    invalid = client.get(f"/api/orders/{order_id}/readiness?mode=live")
    readiness = client.get(f"/api/orders/{order_id}/readiness")
    execution = client.get(f"/api/orders/{order_id}/execution")

    assert no_auth.status_code == 401
    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["mode"] == "production"
    assert payload["can_run_simulation"] is False
    assert payload["can_run_live"] is False
    assert payload["blockers"][0]["code"] == "brief_not_approved"
    assert execution.status_code == 404 or execution.status_code == 409
    assert TOKEN not in readiness.text


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


class _FlakyAdapter(FakeProjectExecutionAdapter):
    def __init__(self, config: FakeExecutorConfig | None = None) -> None:
        super().__init__(config or FakeExecutorConfig())
        self.calls = 0
        self.received_titles: list[str] = []

    def execute(self, request, event_sink, cancellation):
        self.calls += 1
        self.received_titles.append(request.title)
        if self.calls == 1:
            return ExecutionResult(success=False, outcome="failed", summary="Simulated failure.", test_summary=WorkflowTestSummary(failed=1), errors=("provider_rate_limited",), completed_at=NOW)
        return super().execute(request, event_sink, cancellation)


def test_retry_reruns_a_failed_execution_without_a_new_order():
    adapter = _FlakyAdapter()
    service = _service(fake_adapter=adapter)
    client = _client(_app(service))
    order_id, _ = _approve(client)

    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "failed":
            break
        sleep(0.01)
    failed = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
    assert failed["status"] == "failed"

    retried = client.post(f"/api/orders/{order_id}/execution/retry")
    assert retried.status_code == 200
    assert retried.json()["execution"]["id"] == failed["id"]

    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)
    final = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
    assert final["id"] == failed["id"]
    assert final["status"] == "succeeded"
    assert adapter.calls == 2
    # Regression: found live -- retry_execution() used to omit title, and title is a
    # slug PREFIX in reserve_owned_project_workspace(), not cosmetic: a retry with a
    # different (empty) title than the original start_execution() call resolves to a
    # DIFFERENT directory, silently abandoning the workspace it was supposed to resume.
    assert adapter.received_titles == [_order_payload()["title"]] * 2


def test_retry_uses_the_same_title_as_the_original_start_so_the_workspace_matches():
    adapter = _FlakyAdapter()
    service = _service(fake_adapter=adapter)
    client = _client(_app(service))
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "failed":
            break
        sleep(0.01)

    client.post(f"/api/orders/{order_id}/execution/retry")

    assert all(title == _order_payload()["title"] for title in adapter.received_titles)
    assert all(title for title in adapter.received_titles)  # never empty


def test_retry_rejects_execution_that_never_failed():
    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.01)))
    client = _client(_app(service))
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)

    retried = client.post(f"/api/orders/{order_id}/execution/retry")
    assert retried.status_code == 409
    assert retried.json()["detail"]["code"] == "execution_not_retryable"


class _UsageReportingAdapter(FakeProjectExecutionAdapter):
    def __init__(self, *, usage: TokenUsage | None, rate_limit_message: str | None = None) -> None:
        super().__init__(FakeExecutorConfig())
        self._usage = usage
        self._rate_limit_message = rate_limit_message

    def execute(self, request, event_sink, cancellation):
        base = super().execute(request, event_sink, cancellation)
        return base.model_copy(update={"usage": self._usage, "rate_limit_message": self._rate_limit_message})


def test_usage_summary_aggregates_across_executions_and_reports_latest_rate_limit():
    usage = TokenUsage(total_cost_usd=0.2, input_tokens=10, output_tokens=20, cache_read_input_tokens=30, cache_creation_input_tokens=40)
    service = _service(fake_adapter=_UsageReportingAdapter(usage=usage, rate_limit_message="You've hit your session limit · resets 1:10am (Europe/Berlin)"))
    client = _client(_app(service))
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)

    summary = client.get("/api/orders/usage-summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["executions_with_usage"] == 1
    assert body["total_cost_usd"] == pytest.approx(0.2)
    assert body["total_input_tokens"] == 10
    assert body["total_output_tokens"] == 20
    assert body["last_rate_limit"]["message"] == "You've hit your session limit · resets 1:10am (Europe/Berlin)"
    assert body["last_rate_limit"]["order_id"] == order_id


def test_usage_summary_with_no_executions_is_all_zero():
    client = _client(_app())

    summary = client.get("/api/orders/usage-summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["executions_with_usage"] == 0
    assert body["total_cost_usd"] == 0
    assert body["last_rate_limit"] is None


class _FakeRevisionAdapter:
    def __init__(self) -> None:
        self.received_notes: list[str] = []
        self.received_titles: list[str] = []

    def check_readiness(self, brief):
        from order_workflow import ReadinessResult

        return ReadinessResult.ready_result()

    def execute(self, request, event_sink, cancellation):
        self.received_notes.append(request.revision_note)
        self.received_titles.append(request.title)
        return ExecutionResult(success=True, outcome="generated", summary="Revision applied.", test_summary=WorkflowTestSummary(skipped=1), completed_at=NOW)


def test_revise_execution_creates_a_new_execution_after_success():
    revision_adapter = _FakeRevisionAdapter()
    service = _service(revision_adapter=revision_adapter)
    client = _client(_app(service))
    order_id, _ = _approve(client)
    original = client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"}).json()["execution"]
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)

    revised = client.post(f"/api/orders/{order_id}/execution/revise", json={"revision_note": "Add a dark mode toggle"})
    assert revised.status_code == 200
    revised_execution = revised.json()["execution"]
    assert revised_execution["id"] != original["id"]
    assert revised_execution["revised_from"] == original["id"]

    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)
    final = client.get(f"/api/orders/{order_id}/execution").json()["execution"]
    assert final["id"] == revised_execution["id"]
    assert final["status"] == "succeeded"
    assert revision_adapter.received_notes == ["Add a dark mode toggle"]
    # Same regression as retry: revise_execution() must pass the order's real title
    # through, or it resolves a different workspace directory than the original delivery.
    assert revision_adapter.received_titles == [_order_payload()["title"]]


def test_revise_execution_rejects_an_empty_revision_note():
    service = _service(revision_adapter=_FakeRevisionAdapter())
    client = _client(_app(service))
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})
    for _ in range(30):
        if client.get(f"/api/orders/{order_id}/execution").json()["execution"]["status"] == "succeeded":
            break
        sleep(0.01)

    response = client.post(f"/api/orders/{order_id}/execution/revise", json={"revision_note": ""})
    assert response.status_code == 422


def test_revise_execution_rejects_when_not_yet_succeeded():
    service = _service(fake_adapter=FakeProjectExecutionAdapter(FakeExecutorConfig(step_delay_seconds=0.05)), revision_adapter=_FakeRevisionAdapter())
    client = _client(_app(service))
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})

    response = client.post(f"/api/orders/{order_id}/execution/revise", json={"revision_note": "Add a dark mode toggle"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_not_revisable"


def test_unknown_order_and_internal_errors_are_sanitized(caplog):
    client = _client(_app())
    missing = client.get("/api/orders/order_missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "order_not_found"

    app = _app(ExplodingService())
    app.state.order_workflow_service = ExplodingService()
    with caplog.at_level(logging.ERROR, logger="api.orders"):
        raw = _client(app).get("/api/orders/order_any")
    assert raw.status_code == 500
    assert raw.json()["detail"]["code"] == "internal_error"
    assert "secret-value" not in raw.text
    # the real exception used to vanish entirely (not even in the server's own log),
    # which made any unexpected order-workflow failure nearly impossible to diagnose.
    assert "Unhandled error in order workflow request" in caplog.text
    assert "raw stack trace token=secret-value" in caplog.text


def test_autopilot_reaches_handoff_ready_using_the_injected_ai_ask():
    crm_payload = _order_payload(title="CRM", description="Create a CRM for tracking clients, leads, pipeline and deals with task activity tracking.")

    def fake_ai_ask(_prompt: str) -> str:
        return "Track clients, leads, pipeline stages, and deal activity."

    app = _app(ai_ask=fake_ai_ask)
    client = _client(app)
    created = client.post("/api/orders", json=crm_payload)
    assert created.status_code == 200, created.text
    order_id = created.json()["order"]["id"]

    response = client.post(f"/api/orders/{order_id}/autopilot", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handoff_ready"] is True
    assert body["brief"] is not None
    assert body["approval"] is not None and body["approval"]["approved"] is True


def test_autopilot_uses_the_real_default_ai_ask_when_none_is_installed():
    app = _app()  # no ai_ask override -- falls back to _default_ai_ask
    client = _client(app)
    created = client.post("/api/orders", json=_order_payload())
    assert created.status_code == 200, created.text
    order_id = created.json()["order"]["id"]

    response = client.post(f"/api/orders/{order_id}/autopilot", json={})

    # PDF-signal description resolves without any free-text clarification answer,
    # so the real default ai_ask is never actually invoked here -- this only proves
    # the fallback wiring doesn't error out when no test override is installed.
    assert response.status_code == 200, response.text
    assert response.json()["handoff_ready"] is True


def test_autopilot_propagates_order_workflow_errors_through_the_status_table():
    client = _client(_app())

    response = client.post("/api/orders/order_missing/autopilot", json={})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "order_not_found"


def test_proposal_returns_real_markdown_and_estimate_for_a_ready_brief():
    def fake_ai_ask(_prompt: str) -> str:
        return "A concrete, specific value proposition sentence."

    app = _app(ai_ask=fake_ai_ask)
    client = _client(app)
    order_id, _ = _complete_defaults(client)
    brief_state = client.post(f"/api/orders/{order_id}/brief", json={})
    assert brief_state.status_code == 200, brief_state.text

    response = client.post(f"/api/orders/{order_id}/proposal", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "# Project Proposal" in body["proposal_markdown"]
    assert "A concrete, specific value proposition sentence." in body["proposal_markdown"]
    assert body["estimate"]["min_hours"] > 0
    assert body["estimate"]["min_cost"] > 0
    assert body["estimate"]["currency"] == "USD"


def test_proposal_requires_a_ready_brief():
    client = _client(_app())
    created = client.post("/api/orders", json=_order_payload())
    order_id = created.json()["order"]["id"]

    response = client.post(f"/api/orders/{order_id}/proposal", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "brief_not_ready"


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


def test_answering_execution_questions_rejects_an_execution_that_is_not_paused():
    # The endpoint must not silently accept answers for a run that never asked anything --
    # that would look like it worked while changing nothing.
    client = _client(_app())
    order_id, _ = _approve(client)
    client.post(f"/api/orders/{order_id}/execution", json={"mode": "fake"})

    response = client.post(
        f"/api/orders/{order_id}/execution/answers",
        json={"answers": [{"question_id": "midbuild-assumption-1", "value": "Keep it as assumed"}]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {"execution_not_awaiting_answers", "execution_already_completed"}


def test_answering_execution_questions_requires_a_started_execution():
    client = _client(_app())
    order_id, _ = _approve(client)

    response = client.post(
        f"/api/orders/{order_id}/execution/answers",
        json={"answers": [{"question_id": "midbuild-assumption-1", "value": "Keep it as assumed"}]},
    )

    assert response.status_code in {404, 409}, response.text


def test_the_prepared_build_instruction_can_be_previewed_before_anything_is_spent():
    # The point of the screen this serves: read the exact instruction the coding CLI will
    # get while it is still free to change it.
    client = _client(_app())
    order_id, _ = _approve(client)

    response = client.get(f"/api/orders/{order_id}/execution/prompt")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "ui_shell"
    assert "Implement ONLY the UI shell" in body["prompt"]
    assert body["additions_are_append_only"] is True


def test_client_additions_land_above_the_strict_rules_they_must_not_override():
    client = _client(_app())
    order_id, _ = _approve(client)

    response = client.get(f"/api/orders/{order_id}/execution/prompt?additions=Put+the+filters+in+a+sidebar")

    prompt = response.json()["prompt"]
    assert "Put the filters in a sidebar" in prompt
    # Every strict rule carries a downstream gate (the preview script the browser checks
    # connect to, the no-backend rule the decision gate assumes), so client text must not
    # be able to land after them and countermand them.
    assert prompt.index("Put the filters in a sidebar") < prompt.index("Strict rules for this phase")
    assert "take precedence over the client's additional notes" in prompt


def test_previewing_the_instruction_creates_no_execution():
    client = _client(_app())
    order_id, _ = _approve(client)

    client.get(f"/api/orders/{order_id}/execution/prompt")

    assert client.get(f"/api/orders/{order_id}").json()["execution"] is None
