from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Lock

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    ClarificationAnswer,
    DesignPreviewService,
    ExecutionMode,
    ExecutionStatus,
    LiveOpenCodeExecutionAdapter,
    OpenCodeExecutionResult,
    ProductionProjectExecutionAdapter,
    ProjectBriefService,
    ProjectExecutionService,
    QuestionType,
    ReadinessResult,
    UserOrder,
    UserOrderStatus,
    live_opencode_execution_enabled,
)
from order_workflow.api_models import CreateOrderRequest
from order_workflow.execution_config import ExecutionConfigurationProvider
from order_workflow.production_adapter import MAX_QA_REPAIR_ATTEMPTS
from order_workflow.qa_runner import QACommandResult, QAOutcome
from order_workflow.service import ConfiguredOpenCodeExecutionClient, OrderWorkflowService, _public_opencode_failure_code
from order_workflow.workspace import ProjectWorkspace, reserve_owned_project_workspace, scan_meaningful_generated_artifacts, summarize_generated_workspace, validate_owned_project_workspace


pytestmark = pytest.mark.unit


def test_public_opencode_failure_mapping_does_not_default_to_request_rejected():
    assert _public_opencode_failure_code("request_rejected") == "opencode_request_rejected"
    assert _public_opencode_failure_code("model_rejected") == "opencode_model_rejected"
    assert _public_opencode_failure_code("authentication_failure") == "opencode_authentication_failure"
    assert _public_opencode_failure_code("provider_error") == "opencode_provider_error"
    assert _public_opencode_failure_code("flag_rejected") == "opencode_flag_rejected"
    assert _public_opencode_failure_code("opencode_json_stream_failure") == "opencode_json_stream_failure"
    assert _public_opencode_failure_code("artifact_validation_failed") == "opencode_artifact_validation_failed"
    assert _public_opencode_failure_code("unclassified") == "opencode_process_failed"
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


def write_trivially_passing_package_json(workspace_path: Path) -> None:
    """QA now really runs `npm test` (the adapter's default qa_commands), so fakes
    that claim to have generated a project need a package.json whose test script
    genuinely exits 0 -- otherwise every "successful" fixture would fail real QA."""
    (workspace_path / "package.json").write_text(json.dumps({"name": "generated", "version": "1.0.0", "scripts": {"test": "exit 0"}}), encoding="utf-8")


def always_passing_qa(_qa_commands, _cwd):
    return QAOutcome(passed=True, results=())


_FAILING_QA = QAOutcome(passed=False, results=(QACommandResult(command="npm test", exit_code=1, stdout_tail="", stderr_tail="assertion failed", duration=0.1),))
_PASSING_QA = QAOutcome(passed=True, results=(QACommandResult(command="npm test", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),))


class ScriptedQARunner:
    """Returns outcomes[0], outcomes[1], ... in order; repeats the last one
    once exhausted. Lets tests script exactly how many QA rounds fail."""

    def __init__(self, outcomes: list[QAOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, _qa_commands, _cwd) -> QAOutcome:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome


class FakeOpenCodeClient:
    def __init__(self, *, ready=True, fail=False, cancel=False) -> None:
        self.ready = ready
        self.fail = fail
        self.cancel = cancel
        self.prompt = ""
        self.prompts: list[str] = []
        self.workspace_path = None
        self.cancellation_seen = False
        self.call_count = 0

    def check_readiness(self):
        if self.ready:
            return ReadinessResult.ready_result()
        from order_workflow import readiness_blocker

        return ReadinessResult.blocked(readiness_blocker("opencode_unavailable", "OpenCode is not available."))

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.call_count += 1
        self.prompt = prompt
        self.prompts.append(prompt)
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
        write_trivially_passing_package_json(self.workspace_path)
        return OpenCodeExecutionResult(success=True, summary="generated")


class RejectingOpenCodeClient(FakeOpenCodeClient):
    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.prompt = prompt
        self.workspace_path = Path(workspace_path)
        event_sink.emit(stage="implementation", agent="OpenCode", progress=60, message="OpenCode execution rejected request")
        return OpenCodeExecutionResult(success=False, summary="OpenCode execution failed: opencode_request_rejected", warnings=("opencode_request_rejected",))


class TimeoutOpenCodeClient(FakeOpenCodeClient):
    def __init__(self, *, files=()) -> None:
        super().__init__()
        self.files = files

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.prompt = prompt
        self.workspace_path = Path(workspace_path)
        for relative in self.files:
            target = self.workspace_path / relative
            if str(relative).endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.name == "package.json":
                    target.write_text(json.dumps({"name": "generated", "version": "1.0.0", "scripts": {"test": "exit 0"}}), encoding="utf-8")
                else:
                    target.write_text("generated", encoding="utf-8")
        event_sink.emit(stage="implementation", agent="OpenCode", progress=60, message="OpenCode timed out")
        if self.files:
            return OpenCodeExecutionResult(
                success=True,
                summary="OpenCode created files but timed out.",
                warnings=("OpenCode created files but did not exit before timeout. Review the generated workspace before QA.",),
                outcome="generated_needs_review",
                timed_out=True,
                meaningful_artifacts=tuple(str(item).rstrip("/") for item in self.files),
            )
        return OpenCodeExecutionResult(
            success=False,
            summary="OpenCode did not finish and no generated project files were detected.",
            warnings=("opencode_execution_timeout",),
            errors=("opencode_execution_timeout",),
            outcome="timed_out_without_artifacts",
            timed_out=True,
        )


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


def _answer_for(question):
    if question.recommended_answer is not None:
        return question.recommended_answer
    if question.type is QuestionType.LONG_TEXT:
        return "Present the studio's work with a hero, a highlights section, and a contact call-to-action."
    if question.type is QuestionType.SHORT_TEXT:
        return "Visitors"
    if question.type is QuestionType.BOOLEAN:
        return False
    if question.type is QuestionType.SINGLE_SELECT:
        return question.options[0] if question.options else ""
    if question.type is QuestionType.MULTI_SELECT:
        return list(question.options[:1]) if question.options else []
    return ""


def _contract_answering_every_question(description):
    """Like `_contract`, but explicitly answers any question `use_recommended_defaults`
    cannot default (e.g. core-features, which always requires a free-text answer)."""
    ids = SequenceIds()
    order = UserOrder(id="order_live", title="Live App", description=description, product_type="web_app", created_at=NOW, updated_at=NOW)
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    for _ in range(5):
        if result.order.status is UserOrderStatus.BRIEF_READY:
            break
        answered_ids = {answer.question_id for answer in result.order.answers}
        pending = tuple(question for question in result.order.questions if question.id not in answered_ids)
        if not pending:
            break
        answers = tuple(ClarificationAnswer(question_id=question.id, value=_answer_for(question)) for question in pending)
        result = clarification.apply_answers(result.order, result.session, answers)
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


def _bridge_config(model="nvidia/deepseek-ai/deepseek-v4-pro"):
    return {
        "_provider_connections": [
            {
                "connection_type": "opencode_oauth_bridge",
                "configured_provider": "nvidia",
                "configured_model": model,
                "readiness_status": "ready",
                "auth_status": "authenticated",
                "enabled": True,
            }
        ]
    }


def _configuration(tmp_path, *, config=None, opt_in=False):
    return ExecutionConfigurationProvider(
        config_loader=lambda: config or {},
        secret_lookup=lambda name, _config=None: "",
        opencode_version_probe=lambda: (True, "1.17.11", "opencode.cmd"),
        active_backend_probe=lambda: "",
        workspace_root=tmp_path,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"} if opt_in else {},
    )


def _no_network_ai_ask(_prompt: str) -> str:
    """Default stub for _configured_workflow: no test in this file exercises the
    cinematic-website branch (which is the only thing that used to call this),
    but documentation generation now calls it on every successful execution too --
    returning "" makes generate_overview_paragraph fall back deterministically
    instead of any test silently attempting a real network call."""
    return ""


def _configured_workflow(tmp_path, *, config=None, opt_in=False, client=None, website_section_ai_ask=None):
    # This whole file is the LiveOpenCodeExecutionAdapter-specific regression suite,
    # so it explicitly pins the legacy pipeline -- the phased pipeline (default since
    # its own introduction) has its own dedicated test file, test_order_workflow_phased_adapter.py.
    ids = SequenceIds()
    return OrderWorkflowService(
        id_factory=ids,
        clock=_clock,
        configuration_provider=_configuration(tmp_path, config=config, opt_in=opt_in),
        opencode_client=client or FakeOpenCodeClient(),
        website_section_ai_ask=website_section_ai_ask or _no_network_ai_ask,
        environ={"FREELANCERSTUDIO_EXECUTION_PIPELINE": "legacy"},
    )


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
    assert finished.result.outcome == "generated"
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


def test_workspace_validation_rejects_mismatched_marker(tmp_path):
    workspace = reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint="abc")
    validate_owned_project_workspace(workspace, order_id="order", execution_id="execution")
    with pytest.raises(ValueError, match="workspace_marker_mismatch"):
        validate_owned_project_workspace(workspace, order_id="order", execution_id="other")
    with pytest.raises(ValueError, match="unsafe_workspace_path"):
        validate_owned_project_workspace(ProjectWorkspace(root=tmp_path, project_path=tmp_path.parent), order_id="order", execution_id="execution")


def test_meaningful_artifact_scan_ignores_metadata_and_returns_safe_paths(tmp_path):
    workspace = reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint="abc")
    for name in ("execution_package.json", "execution_prompt.md", "delivery_report.md", "generated_project_summary.json", "opencode_command.txt", "README_NEXT_STEPS.md"):
        (workspace.project_path / name).write_text("metadata", encoding="utf-8")
    (workspace.project_path / "README.md").write_text("generated", encoding="utf-8")
    (workspace.project_path / "src").mkdir()
    (workspace.project_path / "src" / "main.js").write_text("console.log('ok')", encoding="utf-8")

    artifacts = scan_meaningful_generated_artifacts(workspace)

    assert "README.md" in artifacts
    assert "src/" in artifacts
    assert "src/main.js" in artifacts
    assert all("\\" not in item and ".." not in item for item in artifacts)
    assert "execution_package.json" not in artifacts


def test_meaningful_artifact_scan_ignores_symlink_escape(tmp_path):
    workspace = reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint="abc")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = workspace.project_path / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")

    assert "linked.md" not in scan_meaningful_generated_artifacts(workspace)


def test_meaningful_artifact_scan_skips_entries_that_raise_os_error_on_stat(tmp_path, monkeypatch):
    """Regression test: a real live run hit this on Windows -- npm creates node_modules/.bin
    entries as NTFS junction points rather than true symlinks, so is_symlink() doesn't catch
    them, but stat()-ing them (via is_dir()/is_file()) raises OSError (WinError 1920:
    "The file cannot be accessed by the system"). This used to propagate uncaught out of
    scan_meaningful_generated_artifacts, crashing the whole phase as an "internal error"
    right after a successful, QA-passed build -- the crash had nothing to do with the
    generated code."""
    workspace = reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint="abc")
    (workspace.project_path / "README.md").write_text("generated", encoding="utf-8")
    broken = workspace.project_path / "broken-junction"
    broken.write_text("placeholder", encoding="utf-8")

    real_is_dir = Path.is_dir

    def flaky_is_dir(self, *args, **kwargs):
        if self.name == "broken-junction":
            raise OSError("The file cannot be accessed by the system")
        return real_is_dir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)

    artifacts = scan_meaningful_generated_artifacts(workspace)

    assert "README.md" in artifacts
    assert "broken-junction" not in artifacts


def test_summarize_generated_workspace_skips_entries_that_raise_os_error_on_stat(tmp_path, monkeypatch):
    """Sibling regression test to the one above: _finalize_success() calls
    summarize_generated_workspace() too (for the final delivery report), and it had the
    exact same unprotected is_file() call -- a real live run got past the first fix (ui_shell
    QA passed) only to crash here instead, at the very last phase, right after its own QA
    had already passed."""
    workspace = reserve_owned_project_workspace(tmp_path, order_id="order", execution_id="execution", brief_fingerprint="abc")
    (workspace.project_path / "README.md").write_text("generated", encoding="utf-8")
    broken = workspace.project_path / "broken-junction"
    broken.write_text("placeholder", encoding="utf-8")

    real_is_file = Path.is_file

    def flaky_is_file(self, *args, **kwargs):
        if self.name == "broken-junction":
            raise OSError("The file cannot be accessed by the system")
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)

    summary = summarize_generated_workspace(workspace)

    assert summary["files_created"] == 1


def test_crm_prompt_differs_and_has_no_pdf_panels(tmp_path):
    pdf_brief, pdf_handoff = _contract(PDF)
    crm_brief, crm_handoff = _contract(CRM)
    pdf_client = FakeOpenCodeClient()
    crm_client = FakeOpenCodeClient()
    env = {"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"}
    LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=pdf_client, environ=env).execute(type("R", (), {"brief": pdf_brief, "handoff": pdf_handoff, "execution_id": "execution_pdf", "title": ""})(), _NoopSink(), _Token())
    LiveOpenCodeExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path, opencode_client=crm_client, environ=env).execute(type("R", (), {"brief": crm_brief, "handoff": crm_handoff, "execution_id": "execution_crm", "title": ""})(), _NoopSink(), _Token())
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


def test_default_configured_live_start_uses_opencode_bridge_without_fake_fallback(tmp_path):
    client = FakeOpenCodeClient()
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    readiness = service.execution_readiness(order_id, ExecutionMode.PRODUCTION)
    assert "provider_not_configured" not in {item["code"] for item in readiness["blockers"]}
    assert "live_execution_opt_in_required" not in {item["code"] for item in readiness["blockers"]}
    assert readiness["can_prepare_dry_run"] is True
    assert readiness["can_run_live"] is True

    dry_run = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=False)
    assert dry_run["execution"]["mode"] == "production"
    assert client.prompt == ""

    service2 = _configured_workflow(tmp_path, config=_bridge_config("nvidia/deepseek-ai/deepseek-v4-pro-2"), opt_in=True, client=client)
    order2, _ = _approve_order(service2)
    started = service2.start_execution(order2, ExecutionMode.PRODUCTION, live=True)
    assert started["execution"]["mode"] == "production"
    finished = service2._executions.wait(started["execution"]["id"], 2)
    assert finished.status is ExecutionStatus.SUCCEEDED
    assert client.prompt


def test_default_configured_live_start_blocks_missing_opt_in_and_missing_provider(tmp_path):
    locked = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=False, client=FakeOpenCodeClient())
    locked_id, _ = _approve_order(locked)
    locked_start = locked.start_execution(locked_id, ExecutionMode.PRODUCTION, live=True)
    assert locked_start["execution"]["status"] == "awaiting_user"
    assert "live_execution_opt_in_required" in {item["code"] for item in locked_start["blockers"]}

    missing = _configured_workflow(tmp_path, config={}, opt_in=True, client=FakeOpenCodeClient())
    missing_id, _ = _approve_order(missing)
    missing_start = missing.start_execution(missing_id, ExecutionMode.PRODUCTION, live=True)
    codes = {item["code"] for item in missing_start["blockers"]}
    assert "provider_not_configured" in codes
    assert "model_not_selected" in codes


def test_failed_live_execution_reports_failure_without_generation_claim_or_secrets(tmp_path):
    client = RejectingOpenCodeClient()
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)
    serialized = finished.to_json().casefold()
    delivery = next(item for item in finished.artifacts if item.name == "delivery_report.md")
    workspace_summary = next(item for item in finished.artifacts if item.name == "generated_project_summary.json")
    report_path = client.workspace_path / "delivery_report.md"

    assert finished.status is ExecutionStatus.FAILED
    assert finished.result.success is False
    assert finished.result.test_summary.skipped == 1
    assert "opencode_execution_failed" in finished.result.errors
    assert "opencode_request_rejected" in finished.result.warnings
    assert delivery.summary == "Live OpenCode execution failed. QA not run because OpenCode execution did not succeed."
    assert "completed" not in delivery.summary.casefold()
    assert "generated project summary" not in workspace_summary.summary.casefold()
    assert "generated app files detected: 0" in workspace_summary.summary.casefold()
    assert report_path.is_file()
    assert "Live OpenCode execution failed." in report_path.read_text(encoding="utf-8")
    assert "QA status: QA not run because OpenCode execution did not succeed." in report_path.read_text(encoding="utf-8")
    assert "token" not in serialized
    assert "api_key" not in serialized


def test_timeout_without_files_reports_execution_timeout(tmp_path):
    client = TimeoutOpenCodeClient(files=())
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)
    delivery = next(item for item in finished.artifacts if item.name == "delivery_report.md")
    report_path = client.workspace_path / "delivery_report.md"

    assert finished.status is ExecutionStatus.FAILED
    assert finished.result.outcome == "timed_out_without_artifacts"
    assert finished.result.errors == ("opencode_execution_timeout",)
    assert finished.result.test_summary.skipped == 1
    assert delivery.summary == "OpenCode timed out without generated project files. QA not run because OpenCode execution did not succeed."
    assert "OpenCode did not finish and no generated project files were detected." in report_path.read_text(encoding="utf-8")


def test_timeout_with_readme_is_reviewable_and_honest(tmp_path):
    client = TimeoutOpenCodeClient(files=("README.md", "package.json"))
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)
    delivery = next(item for item in finished.artifacts if item.name == "delivery_report.md")
    report = (client.workspace_path / "delivery_report.md").read_text(encoding="utf-8")

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result.success is True
    assert finished.result.outcome == "generated_needs_review"
    assert finished.result.test_summary.skipped == 1
    assert "OpenCode created files but did not exit before timeout. Review the generated workspace before QA." in finished.result.warnings
    assert delivery.summary == "OpenCode created project files but timed out; manual review required. QA passed."
    assert "OpenCode created project files but did not exit before timeout. The workspace requires review." in report
    assert "Live OpenCode execution completed" not in report


def test_timeout_with_package_and_src_is_reviewable(tmp_path):
    client = TimeoutOpenCodeClient(files=("package.json", "src/"))
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result.outcome == "generated_needs_review"
    assert (client.workspace_path / "package.json").is_file()
    assert (client.workspace_path / "src").is_dir()


def test_successful_live_execution_keeps_success_wording(tmp_path):
    client = FakeOpenCodeClient()
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)
    delivery = next(item for item in finished.artifacts if item.name == "delivery_report.md")

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result.outcome == "generated"
    assert delivery.summary == "Live OpenCode execution completed. QA passed."
    assert (client.workspace_path / "delivery_report.md").is_file()


def test_successful_live_execution_writes_real_readme_and_architecture_docs(tmp_path):
    client = FakeOpenCodeClient()
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    doc_artifacts = [item for item in finished.artifacts if item.kind == "project_documentation"]
    assert {item.name for item in doc_artifacts} == {"README.md", "ARCHITECTURE.md"}

    readme_text = (client.workspace_path / "README.md").read_text(encoding="utf-8")
    architecture_text = (client.workspace_path / "ARCHITECTURE.md").read_text(encoding="utf-8")

    # write_trivially_passing_package_json + FakeOpenCodeClient's own README.md write
    # produce a real generated file tree; the deterministic generator overwrites the
    # fake client's placeholder README (matching website_sections/design_system's own
    # "curated beats freeform" precedent) with real substituted content.
    assert "Generated project" not in readme_text  # the fake client's own placeholder got overwritten
    assert "## Overview" in readme_text
    assert "## Tech Stack" in readme_text
    assert "```mermaid" in architecture_text
    assert "flowchart TD" in architecture_text


def test_failed_live_execution_does_not_write_documentation(tmp_path):
    client = RejectingOpenCodeClient()
    service = _configured_workflow(tmp_path, config=_bridge_config(), opt_in=True, client=client)
    order_id, _ = _approve_order(service)

    started = service.start_execution(order_id, ExecutionMode.PRODUCTION, live=True)
    finished = service._executions.wait(started["execution"]["id"], 2)

    assert finished.status is ExecutionStatus.FAILED
    assert not any(item.kind == "project_documentation" for item in finished.artifacts)
    assert not (client.workspace_path / "README.md").exists()


def test_configured_client_passes_owned_workspace_to_bridge(tmp_path, monkeypatch):
    captured = {}

    class Bridge:
        @classmethod
        def from_dict(cls, connection):
            captured["connection"] = connection
            return cls()

        def execute(self, request):
            captured["request"] = request
            return {"status": "success", "text": "ok"}

    monkeypatch.setattr("order_workflow.service.OpenCodeBridgeConnection", Bridge)
    client = ConfiguredOpenCodeExecutionClient(config_loader=lambda: _bridge_config())
    result = client.execute_project_prompt("build", tmp_path, _NoopSink(), _Token())

    assert result.success is True
    assert captured["request"]["workspace_path"] == str(tmp_path.resolve())


def _fake_section_ai_ask(_prompt: str) -> str:
    from website_sections import SECTION_LIBRARY

    return json.dumps(
        {
            "sections": [
                {
                    "slug": section.slug,
                    "content": {field_name: f"copy for {field_name}" for field_name, _description in section.content_schema},
                }
                for section in SECTION_LIBRARY
            ]
        }
    )


def test_cinematic_brief_routes_through_curated_website_sections(tmp_path):
    from website_sections import SECTION_LIBRARY

    client = FakeOpenCodeClient()
    brief, handoff = _contract_answering_every_question("Build a cinematic WebGL showcase website with scroll storytelling for a design agency.")
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=client,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        website_section_ai_ask=_fake_section_ai_ask,
        qa_runner=always_passing_qa,  # real `npm install && npm run build` is covered separately; keep this unit test fast
    )
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    for section in SECTION_LIBRARY:
        for relative_path in section.files:
            assert (client.workspace_path / "frontend/src" / relative_path).is_file()
    assert "Do NOT rewrite, simplify, or remove the existing animation" in client.prompt
    assert "Implement the approved AI Freelancer Studio project brief" not in client.prompt


def test_non_cinematic_brief_does_not_materialize_curated_sections(tmp_path):
    client = FakeOpenCodeClient()
    brief, handoff = _contract(PDF)
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=client,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
    )
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert not (client.workspace_path / "frontend" / "src" / "sections").exists()
    assert "Implement the approved AI Freelancer Studio project brief" in client.prompt


def test_qa_failure_triggers_repair_call_and_succeeds_once_qa_passes(tmp_path):
    client = FakeOpenCodeClient()
    qa_runner = ScriptedQARunner([_FAILING_QA, _PASSING_QA])
    brief, handoff = _contract(PDF)
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=client,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        qa_runner=qa_runner,
    )
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.result.success is True
    assert qa_runner.calls == 2  # initial QA (fails) + one re-check after repair (passes)
    assert client.call_count == 2  # initial implementation call + one repair call
    assert "QA passed after 1 repair attempt(s)." in finished.result.warnings
    assert "fix the code" in client.prompts[1].lower()


def test_qa_failure_exhausts_repair_attempts_and_reports_failure(tmp_path):
    client = FakeOpenCodeClient()
    qa_runner = ScriptedQARunner([_FAILING_QA])  # never passes
    brief, handoff = _contract(PDF)
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=client,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        qa_runner=qa_runner,
    )
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.FAILED
    assert finished.result.success is False
    assert finished.result.outcome == "qa_failed"
    assert finished.result.errors == ("qa_failed",)
    assert qa_runner.calls == MAX_QA_REPAIR_ATTEMPTS + 1  # initial QA + one re-check per repair attempt
    assert client.call_count == MAX_QA_REPAIR_ATTEMPTS + 1  # initial implementation + one call per repair attempt
    assert f"QA failed after {MAX_QA_REPAIR_ATTEMPTS} repair attempt(s)." in finished.result.warnings


def test_qa_repair_loop_stops_early_when_the_repair_call_itself_fails(tmp_path):
    class FailsOnSecondCall(FakeOpenCodeClient):
        def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
            if self.call_count == 1:  # the repair call (0-indexed count already incremented by super())
                self.call_count += 1
                self.prompt = prompt
                self.prompts.append(prompt)
                self.workspace_path = Path(workspace_path)
                return OpenCodeExecutionResult(success=False, summary="repair attempt failed")
            return super().execute_project_prompt(prompt, workspace_path, event_sink, cancellation)

    client = FailsOnSecondCall()
    qa_runner = ScriptedQARunner([_FAILING_QA, _PASSING_QA])  # would pass on round 2, but the repair call itself fails first
    brief, handoff = _contract(PDF)
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=client,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        qa_runner=qa_runner,
    )
    service = ProjectExecutionService(id_factory=SequenceIds(), clock=_clock, live_adapter=adapter)

    finished = service.wait(service.start(brief, handoff, mode=ExecutionMode.PRODUCTION, live=True).id, 2)

    assert finished.status is ExecutionStatus.FAILED
    assert qa_runner.calls == 1  # loop stopped before re-checking QA, since the repair call itself failed
    assert "QA failed after 1 repair attempt(s)." in finished.result.warnings


def test_empty_qa_commands_is_blocked_by_readiness_before_execution_even_starts(tmp_path):
    # qa_commands=() is a genuinely unreachable state during execute() in practice:
    # the base adapter's own check_readiness() already requires non-empty qa_commands
    # (QA_TOOLS_UNAVAILABLE), so an execution never gets this far with no commands
    # configured. The "no commands configured" vacuous-pass wording in execute()
    # is defensive; the real coverage for it lives in test_order_workflow_qa_runner.py's
    # test_run_qa_commands_empty_input_vacuously_passes.
    adapter = LiveOpenCodeExecutionAdapter(
        provider_name="OpenCode",
        model_name="local-codex",
        workspace_root=tmp_path,
        opencode_client=FakeOpenCodeClient(),
        qa_commands=(),
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
    )

    readiness = adapter.check_readiness(_contract(PDF)[0])

    assert readiness.ready is False
    assert any(item.code == "qa_tools_unavailable" for item in readiness.blockers)
