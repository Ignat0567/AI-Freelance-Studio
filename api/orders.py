from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

import config_storage
from ai_utils import ask_studio_ai_with_history
from collaboration.execution_bridge import publish_execution_event
from collaboration.service import get_or_create_collaboration_service
from order_workflow import ExecutionMode
from order_workflow.order_store import JsonExecutionStore, JsonOrderStore
from order_workflow.api_models import (
    AnswersRequest,
    ApproveBriefRequest,
    ApproveDesignPreviewRequest,
    CreateOrderRequest,
    DesignPreviewRequest,
    DefaultsRequest,
    ReviseDesignPreviewRequest,
    ReviseBriefRequest,
    ReviseExecutionRequest,
    StartExecutionRequest,
)
from order_workflow.autopilot import run_order_to_handoff_automatically
from order_workflow.execution_config import ExecutionConfigurationProvider
from order_workflow.proposal import build_proposal, estimate_project_cost, generate_value_proposition
from order_workflow.service import OrderWorkflowError, OrderWorkflowService


router = APIRouter(prefix="/api/orders", tags=["orders"])

_logger = logging.getLogger(__name__)


_STATUS_BY_CODE = {
    "order_not_found": 404,
    "question_not_found": 404,
    "execution_not_found": 404,
    "execution_not_retryable": 409,
    "execution_not_revisable": 409,
    "execution_not_awaiting_answers": 409,
    "revision_note_required": 422,
    "unsupported_product_type": 400,
    "invalid_answer": 422,
    "brief_revision_invalid": 422,
    "brief_not_ready": 409,
    "brief_approval_stale": 409,
    "brief_not_approved": 409,
    "design_preview_not_ready": 409,
    "design_preview_not_required": 409,
    "design_preview_not_approved": 409,
    "design_preview_stale": 409,
    "design_revision_note_required": 422,
    "invalid_execution_mode": 422,
    "live_execution_opt_in_required": 409,
    "handoff_blocked": 409,
    "execution_already_completed": 409,
    "execution_already_active": 409,
    "unresolved_questions": 409,
    "elena_choice_required": 409,
}


def get_order_workflow_service(request: Request) -> OrderWorkflowService:
    service = getattr(request.app.state, "order_workflow_service", None)
    if service is None:
        app = request.app

        def _collaboration_sink(event) -> None:
            publish_execution_event(get_or_create_collaboration_service(app), event)

        service = OrderWorkflowService(
            collaboration_sink=_collaboration_sink,
            store=JsonOrderStore(Path(config_storage.DATA_DIR) / "orders_state.json"),
            execution_state_store=JsonExecutionStore(Path(config_storage.DATA_DIR) / "order_executions_state.json"),
        )
        request.app.state.order_workflow_service = service
    return service


@router.get("")
def list_orders(request: Request):
    service = get_order_workflow_service(request)
    return {"orders": service.list_orders()}


def _default_ai_ask(prompt: str) -> str:
    snapshot = ExecutionConfigurationProvider().snapshot()
    return ask_studio_ai_with_history(snapshot.provider.provider, snapshot.model.model, prompt, [], temperature=0.3)


def get_order_autopilot_ai_ask(request: Request) -> Callable[[str], str]:
    return getattr(request.app.state, "order_workflow_ai_ask", None) or _default_ai_ask


def install_order_workflow_api(app, *, service: OrderWorkflowService | None = None, ai_ask: Callable[[str], str] | None = None) -> OrderWorkflowService:
    active = service or OrderWorkflowService()
    app.state.order_workflow_service = active
    if ai_ask is not None:
        app.state.order_workflow_ai_ask = ai_ask
    return active


def _handle_error(exc: OrderWorkflowError) -> None:
    raise HTTPException(
        status_code=_STATUS_BY_CODE.get(exc.code, 500 if exc.code == "internal_error" else 400),
        detail={"code": exc.code, "message": exc.message},
    ) from None


async def _body(request: Request, model_type):
    try:
        value = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "invalid_json", "message": "Request body must be valid JSON."}) from None
    try:
        return model_type.model_validate(value)
    except ValidationError:
        raise HTTPException(status_code=422, detail={"code": "invalid_order_request", "message": "Request body is invalid."}) from None


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except OrderWorkflowError as exc:
        _handle_error(exc)
    except HTTPException:
        raise
    except Exception:
        # The client only ever sees the generic message below (never a raw traceback, which
        # could leak internal paths/secrets) -- but until now the real exception went nowhere
        # at all, not even the server's own log, making an unexpected order-workflow failure
        # nearly impossible to diagnose from the running app (had to be reproduced by calling
        # the service directly in a separate script to see what actually broke).
        _logger.exception("Unhandled error in order workflow request: %s", getattr(func, "__name__", func))
        raise HTTPException(status_code=500, detail={"code": "internal_error", "message": "Internal order workflow error."}) from None


@router.post("")
async def create_order(request: Request):
    payload = await _body(request, CreateOrderRequest)
    return _call(get_order_workflow_service(request).create_order, payload)


@router.get("/usage-summary")
def get_usage_summary(request: Request):
    # Registered before /{order_id} so this static path is never shadowed by it.
    return _call(get_order_workflow_service(request).usage_summary)


@router.get("/{order_id}")
def get_order(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).snapshot, order_id)


@router.get("/{order_id}/questions")
def get_questions(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).questions, order_id)


@router.post("/{order_id}/answers")
async def answer_questions(order_id: str, request: Request):
    payload = await _body(request, AnswersRequest)
    answers = tuple((item.question_id, item.value) for item in payload.answers)
    return _call(get_order_workflow_service(request).answer, order_id, answers)


@router.post("/{order_id}/defaults")
async def apply_defaults(order_id: str, request: Request):
    await _body(request, DefaultsRequest)
    return _call(get_order_workflow_service(request).defaults, order_id)


@router.post("/{order_id}/autopilot")
def run_autopilot(order_id: str, request: Request):
    return _call(run_order_to_handoff_automatically, get_order_workflow_service(request), order_id, get_order_autopilot_ai_ask(request))


@router.post("/{order_id}/proposal")
def generate_order_proposal(order_id: str, request: Request):
    service = get_order_workflow_service(request)
    ai_ask = get_order_autopilot_ai_ask(request)

    def _build():
        brief = service.get_brief_model(order_id)
        estimate = estimate_project_cost(brief)
        value_proposition = generate_value_proposition(brief, ai_ask)
        proposal_markdown = build_proposal(brief, estimate, value_proposition)
        return {
            "proposal_markdown": proposal_markdown,
            "estimate": {
                "min_hours": estimate.min_hours,
                "max_hours": estimate.max_hours,
                "hourly_rate": estimate.hourly_rate,
                "min_cost": estimate.min_cost,
                "max_cost": estimate.max_cost,
                "currency": estimate.currency,
            },
        }

    return _call(_build)


@router.get("/{order_id}/brief")
def get_brief(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).get_brief, order_id)


@router.post("/{order_id}/brief")
def generate_brief(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).generate_brief, order_id)


@router.post("/{order_id}/brief/revise")
async def revise_brief(order_id: str, request: Request):
    payload = await _body(request, ReviseBriefRequest)
    return _call(get_order_workflow_service(request).revise_brief, order_id, payload.operations)


@router.post("/{order_id}/brief/approve")
async def approve_brief(order_id: str, request: Request):
    payload = await _body(request, ApproveBriefRequest)
    return _call(
        get_order_workflow_service(request).approve_brief,
        order_id,
        revision=payload.revision,
        fingerprint=payload.fingerprint,
    )


@router.get("/{order_id}/design-preview")
def get_design_preview(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).get_design_preview, order_id)


@router.post("/{order_id}/design-preview")
async def generate_design_preview(order_id: str, request: Request):
    payload = await _body(request, DesignPreviewRequest)
    return _call(get_order_workflow_service(request).generate_design_preview, order_id, revision_note=payload.revision_note)


@router.post("/{order_id}/design-preview/revise")
async def revise_design_preview(order_id: str, request: Request):
    payload = await _body(request, ReviseDesignPreviewRequest)
    return _call(get_order_workflow_service(request).revise_design_preview, order_id, payload.note)


@router.post("/{order_id}/design-preview/approve")
async def approve_design_preview(order_id: str, request: Request):
    payload = await _body(request, ApproveDesignPreviewRequest)
    return _call(get_order_workflow_service(request).approve_design_preview, order_id, preview_id=payload.preview_id, brief_version=payload.brief_version)


@router.get("/{order_id}/handoff")
def get_handoff(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).handoff, order_id)


@router.post("/{order_id}/execution")
async def start_execution(order_id: str, request: Request):
    payload = await _body(request, StartExecutionRequest)
    return _call(get_order_workflow_service(request).start_execution, order_id, ExecutionMode(payload.mode), live=payload.live)


@router.get("/{order_id}/readiness")
def get_readiness(order_id: str, request: Request, mode: str = "production"):
    try:
        execution_mode = ExecutionMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_execution_mode", "message": "Execution mode is invalid."}) from None
    return _call(get_order_workflow_service(request).execution_readiness, order_id, execution_mode)


@router.get("/{order_id}/execution")
def get_execution(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).execution, order_id)


@router.post("/{order_id}/execution/cancel")
def cancel_execution(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).cancel_execution, order_id)


@router.post("/{order_id}/execution/retry")
def retry_execution(order_id: str, request: Request):
    return _call(get_order_workflow_service(request).retry_execution, order_id)


@router.post("/{order_id}/execution/answers")
async def answer_execution_questions(order_id: str, request: Request):
    """Answer a mid-build clarification checkpoint so the paused run can continue."""
    payload = await _body(request, AnswersRequest)
    return _call(
        get_order_workflow_service(request).answer_execution_questions,
        order_id,
        [item.model_dump() for item in payload.answers],
    )


@router.post("/{order_id}/execution/revise")
async def revise_execution(order_id: str, request: Request):
    payload = await _body(request, ReviseExecutionRequest)
    return _call(get_order_workflow_service(request).revise_execution, order_id, payload.revision_note)


@router.get("/{order_id}/events")
def get_events(order_id: str, request: Request):
    return {"events": [item.to_dict() for item in _call(get_order_workflow_service(request).events, order_id)]}


@router.get("/{order_id}/artifacts")
def get_artifacts(order_id: str, request: Request):
    return {"artifacts": [item.to_dict() for item in _call(get_order_workflow_service(request).artifacts, order_id)]}


@router.get("/{order_id}/result")
def get_result(order_id: str, request: Request):
    return {"result": _call(get_order_workflow_service(request).result, order_id)}
