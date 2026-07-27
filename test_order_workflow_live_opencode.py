from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    ExecutionMode,
    ExecutionStatus,
    LiveOpenCodeExecutionAdapter,
    OpenCodeExecutionResult,
    ProductionProjectExecutionAdapter,
    ProjectBriefService,
    ProjectExecutionService,
    ReadinessResult,
    UserOrder,
    live_opencode_execution_enabled,
)
from order_workflow.api_models import CreateOrderRequest
from order_workflow.service import OrderWorkflowService
from order_workflow.workspace import reserve_owned_project_workspace, summarize_generated_workspace


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)
PDF = "Create a browser PDF voice assistant with upload, voice and text chat, grounded answers, page citations and speech playback."
CRM = "Create a CRM for clients, leads, pipeline and deals with task activity tracking."


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"live-{self.index:04d}"


class FakeOpenCodeClient:
    def __init__(self, *, ready=True, fail=False, cancel=False) -> None:
        self.ready = ready
        self.fail = fail
        self.cancel = cancel
        self.prompt = ""
        self.workspace_path = None
        self.cancellation_seen = False

    def check_readiness(self):
        if self.ready:
            return ReadinessResult.ready_result()
        from order_workflow import readiness_blocker

        return ReadinessResult.blocked(readiness_blocker("opencode_unavailable", "OpenCode is not available."))

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation):
        self.prompt = prompt
        self.workspace_path = Path(workspace_path)
        self.cancellation_seen = cancellation.is_cancelled()
        event_sink.emit(stage="implementation", agent="OpenCode", progress=60, message="OpenCode execution started")
        if self.cancel:
            cancellation.cancel()
            return OpenCodeExecutionResult(success=False, summary="cancelled", warnings=("OpenCode could not be cancelled aggressively.",))
        if self.fail:
            raise RuntimeError("traceback token=secret-value")
        (self.workspace_path / "README.md").write_text("Generated project", encoding="utf-8")
        (self.workspace_path / "frontend").mkdir(exist_ok=True)
        return OpenCodeExecutionResult(success=True, summary="generated")


def _clock():
    return NOW + timedelta(seconds=1)


def _contract(description=PDF):
    ids = SequenceIds()
    order = UserOrder(id="order_live", title="Live App", description=description, product_type="web_app", created_at=NOW, updated_at=NOW)
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=ids, clock=_clock)
    brief = briefs.generate(result.order, result.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    design = DesignPreviewService(id_factory=ids, clock=_clock)
    preview = design.approve(design.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=_clock).create_implementation_handoff(brief, preview)
    return brief, handoff


def _workflow(tmp_path, *, environ=None, client=None, production_adapter=None):
    ids = SequenceIds()
    live = LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=client or FakeOpenCodeClient(), environ=environ)
    execution = ProjectExecutionService(id_factory=ids, clock=_clock, production_adapter=production_adapter, live_adapter=live)
    return OrderWorkflowService(id_factory=ids, clock=_clock, execution_service=execution)


def _approve_order(service, description=PDF):
    state = service.create_order(CreateOrderRequest(title="Live App", description=description, product_type="web_app"))
    order_id = state["order"]["id"]
    service.defaults(order_id)
    state = service.generate_brief(order_id)
    brief = state["brief"]
    state = service.approve_brief(order_id, revision=brief["revision"], fingerprint=None)
    preview = state["design_preview"]
    return order_id, service.approve_design_preview(order_id, preview_id=preview["preview_id"], brief_version=preview["brief_version"])


def test_live_opt_in_requires_exact_value():
    assert live_opencode_execution_enabled({}) is False
    assert live_opencode_execution_enabled({"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "0"}) is False
    assert live_opencode_execution_enabled({"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "true"}) is False
    assert live_opencode_execution_enabled({"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"}) is True


def test_live_readiness_default_off_and_available_with_fake_client(tmp_path):
    service = _workflow(tmp_path, environ={}, client=FakeOpenCodeClient())
    order_id, _ = _approve_order(service)
    locked = service.execution_readiness(order_id)
    assert "live_execution_opt_in_required" in {item["code"] for item in locked["blockers"]}
    assert locked["can_run_live"] is False

    enabled = _workflow(tmp_path, environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"}, client=FakeOpenCodeClient())
    enabled_id, _ = _approve_order(enabled)
    ready = enabled.execution_readiness(enabled_id)
    assert ready["can_run_live"] is True


def test_live_adapter_writes_owned_workspace_and_prompt(tmp_path):
    client = FakeOpenCodeClient()
    brief, handoff = _contract(PDF)
    adapter = LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=client, environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"})
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result.outcome == "live_generated"
    assert client.workspace_path is not None
    assert (client.workspace_path / ".freelancerstudio-project.json").is_file()
    assert "left PDF library panel" in client.prompt
    assert "center voice/text chat" in client.prompt
    assert "right source evidence panel" in client.prompt
    assert "token" not in (client.workspace_path / ".freelancerstudio-project.json").read_text(encoding="utf-8").casefold()


def test_workspace_safety_rejects_unsafe_and_non_owned_paths(tmp_path):
    with pytest.raises(ValueError):
        reserve_owned_project_workspace(tmp_path / "missing", order_id="order", execution_id="execution", brief_fingerprint=None)
    existing = tmp_path / "order-execution"
    existing.mkdir()
    with pytest.raises(ValueError):
        reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint=None)
    workspace = reserve_owned_project_workspace(tmp_path, order_id="safe/order", execution_id="execution", brief_fingerprint="abc")
    (workspace.project_path / "README.md").write_text("ok", encoding="utf-8")
    summary = summarize_generated_workspace(workspace)
    assert summary["workspace_path"] == workspace.project_reference
    assert "README.md" in summary["top_level_entries"]


def test_crm_prompt_differs_and_has_no_pdf_panels(tmp_path):
    pdf_brief, pdf_handoff = _contract(PDF)
    crm_brief, crm_handoff = _contract(CRM)
    pdf_client = FakeOpenCodeClient()
    crm_client = FakeOpenCodeClient()
    env = {"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"}
    LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=pdf_client, environ=env).execute(type("R", (), {"brief": pdf_brief, "handoff": pdf_handoff, "execution_id": "execution_pdf"})(), _NoopSink(), _Token())
    LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=crm_client, environ=env).execute(type("R", (), {"brief": crm_brief, "handoff": crm_handoff, "execution_id": "execution_crm"})(), _NoopSink(), _Token())
    assert "left pdf library panel" in pdf_client.prompt.casefold()
    assert "pipeline board" in crm_client.prompt.casefold()
    assert "left pdf library panel" not in crm_client.prompt.casefold()


class _Token:
    def __init__(self):
        self.cancelled = False
    def is_cancelled(self): return self.cancelled
    def cancel(self): self.cancelled = True


class _NoopSink:
    def emit(self, **kwargs): pass
    def artifact(self, *, kind, name, summary, reference):
        from order_workflow import ExecutionArtifact
        return ExecutionArtifact(id=f"artifact_{name.replace('.', '-')}", execution_id="execution_pdf", kind=kind, name=name, summary=summary, reference=reference, simulated=True, created_at=NOW)


def test_live_start_rejected_without_opt_in_and_dry_run_still_works(tmp_path):
    dry = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    service = _workflow(tmp_path, environ={}, client=FakeOpenCodeClient(), production_adapter=dry)
    order_id, _ = _approve_order(service)
    live_start = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    assert live_start["execution"]["status"] == "awaiting_user"
    assert live_start["blockers"][0]["code"] == "live_execution_opt_in_required"

    service2 = _workflow(tmp_path, environ={}, client=FakeOpenCodeClient(), production_adapter=dry)
    order2, _ = _approve_order(service2)
    dry_start = service2.start_execution(order2, ExecutionMode.PRODUCTION, live=False)
    assert dry_start["execution"]["mode"] == "production"
