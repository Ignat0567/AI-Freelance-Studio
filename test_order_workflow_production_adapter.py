from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    ExecutionMode,
    ExecutionRequest,
    ExecutionStatus,
    ProjectBriefService,
    ProjectExecutionService,
    ProductionProjectExecutionAdapter,
    UserOrder,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)
REQUEST = (
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
            return f"prod-{self.index:04d}"


def _clock():
    return NOW + timedelta(seconds=1)


def _approved_contract(order_id="order_pdf"):
    ids = SequenceIds()
    order = UserOrder(
        id=order_id,
        title="PDF Voice Assistant",
        description=REQUEST,
        product_type="web_app",
        preferred_language="ru",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=ids, clock=_clock)
    brief = briefs.generate(result.order, result.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    design_service = DesignPreviewService(id_factory=ids, clock=_clock)
    preview = design_service.approve(design_service.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=_clock).create_implementation_handoff(brief, preview)
    return brief, handoff


def test_production_adapter_reports_missing_setup_blockers(tmp_path):
    brief, _handoff = _approved_contract()
    adapter = ProductionProjectExecutionAdapter(provider_name="", model_name=None, workspace_root=tmp_path / "missing", qa_commands=())

    readiness = adapter.check_readiness(brief)

    assert readiness.ready is False
    assert {item.code for item in readiness.blockers} == {
        "provider_not_configured",
        "model_not_selected",
        "workspace_root_unavailable",
        "qa_tools_unavailable",
    }


def test_production_adapter_reports_unwritable_workspace(tmp_path):
    brief, _handoff = _approved_contract()
    adapter = ProductionProjectExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        writable_probe=lambda _path: False,
    )

    readiness = adapter.check_readiness(brief)

    assert readiness.ready is False
    assert [item.code for item in readiness.blockers] == ["workspace_not_writable"]


def test_production_adapter_prepares_package_without_live_execution(tmp_path):
    brief, handoff = _approved_contract()
    adapter = ProductionProjectExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        qa_commands=("npm test", "python -m pytest"),
    )

    package = adapter.prepare_execution(ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_prod"))

    assert package.mode == "dry_run"
    assert package.provider_name == "OpenCode"
    assert package.model_name == "local-codex"
    assert package.workspace_root == tmp_path.name
    assert package.project_path.startswith("order_pdf-")
    assert "Implement the approved" in package.prompt
    assert "After creating the requested project files, stop and exit. Do not keep rewriting files." in package.prompt
    assert "npm test" in package.prompt


def test_production_mode_uses_injected_dry_run_adapter_and_never_fake(tmp_path):
    brief, handoff = _approved_contract()
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    service = ProjectExecutionService(mode=ExecutionMode.PRODUCTION, production_adapter=adapter, id_factory=SequenceIds(), clock=_clock)

    started = service.start(brief, handoff)
    finished = service.wait(started.id, 2)

    assert finished.mode is ExecutionMode.PRODUCTION
    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result is not None
    assert finished.result.outcome == "dry_run_prepared"
    assert finished.result.test_summary.skipped == 1
    assert any("Dry-run only" in item for item in finished.result.warnings)
    assert {item.name for item in finished.artifacts} == {
        "production_handoff.json",
        "execution_prompt.md",
        "qa_plan.json",
        "dry_run_report.md",
    }
    assert all(item.simulated for item in finished.artifacts)
