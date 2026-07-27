from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

import pytest

from order_workflow import (
    ExecutionMode,
    FakeProjectExecutionAdapter,
    ProductionProjectExecutionAdapter,
    ProjectExecutionService,
)
from order_workflow.api_models import CreateOrderRequest
from order_workflow.service import OrderWorkflowService
from order_workflow.service import OrderWorkflowError


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)
PDF_DESCRIPTION = (
    "Create a browser-based voice assistant that allows the user to upload PDF documents, "
    "ask questions by voice or text, receive grounded answers with page citations, and hear answers spoken aloud."
)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"ready-{self.index:04d}"


def _service(*, production_adapter=None):
    ids = SequenceIds()
    execution = ProjectExecutionService(
        id_factory=ids,
        clock=lambda: NOW,
        fake_adapter=FakeProjectExecutionAdapter(),
        production_adapter=production_adapter,
    )
    return OrderWorkflowService(id_factory=ids, clock=lambda: NOW, execution_service=execution)


def _create(service, description=PDF_DESCRIPTION):
    state = service.create_order(CreateOrderRequest(title="Readiness App", description=description, product_type="web_app"))
    return state["order"]["id"]


def _brief_ready(service, order_id):
    service.defaults(order_id)
    return service.generate_brief(order_id)


def _approve_all(service, order_id):
    state = _brief_ready(service, order_id)
    brief = state["brief"]
    state = service.approve_brief(order_id, revision=brief["revision"], fingerprint=None)
    preview = state["design_preview"]
    return service.approve_design_preview(order_id, preview_id=preview["preview_id"], brief_version=preview["brief_version"])


def test_readiness_before_brief_approval_does_not_start_execution():
    service = _service()
    order_id = _create(service)

    readiness = service.execution_readiness(order_id)

    assert readiness["ready"] is False
    assert readiness["can_run_simulation"] is False
    assert readiness["can_run_live"] is False
    assert readiness["blockers"][0]["code"] == "brief_not_approved"
    with pytest.raises(OrderWorkflowError):
        service.execution(order_id)


def test_readiness_before_design_preview_approval_blocks_execution():
    service = _service()
    order_id = _create(service)
    state = _brief_ready(service, order_id)
    service.approve_brief(order_id, revision=state["brief"]["revision"], fingerprint=None)

    readiness = service.execution_readiness(order_id)

    assert readiness["can_run_simulation"] is False
    assert "design_preview_not_approved" in {item["code"] for item in readiness["blockers"]}


def test_approved_order_reports_simulation_ready_and_missing_provider():
    service = _service()
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id)
    serialized = str(readiness).casefold()

    assert readiness["simulation_ready"] is True
    assert readiness["can_run_simulation"] is True
    assert readiness["production_dry_run_ready"] is False
    assert readiness["production_live_ready"] is False
    assert "provider_not_configured" in {item["code"] for item in readiness["blockers"]}
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_production_readiness_reports_model_workspace_and_qa_blockers(tmp_path):
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="", workspace_root=tmp_path / "missing", qa_commands=())
    service = _service(production_adapter=adapter)
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id)

    assert {item["code"] for item in readiness["blockers"]} >= {
        "model_not_selected",
        "workspace_root_unavailable",
        "qa_tools_unavailable",
    }
    assert readiness["can_prepare_dry_run"] is False


def test_production_dry_run_ready_but_live_unavailable(tmp_path):
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    service = _service(production_adapter=adapter)
    order_id = _create(service)
    _approve_all(service, order_id)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)

    assert readiness["ready"] is True
    assert readiness["can_prepare_dry_run"] is True
    assert readiness["can_run_live"] is False
    assert any(item["code"] == "live_execution" and item["status"] == "unavailable" for item in readiness["checks"])
