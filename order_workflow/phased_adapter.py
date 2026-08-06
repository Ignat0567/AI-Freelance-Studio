from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_utils import ask_studio_ai_with_history
from project_docs import build_architecture_mermaid, build_module_map, build_readme, generate_overview_paragraph

from .docker_qa_runner import run_qa_commands_in_docker, DockerUnavailableError
from .executors import CancellationToken, ExecutionEventSink, ExecutionRequest
from .models import ArtifactKind, ExecutionResult, ExecutionStage, EventKind, EventLevel, ProjectBrief, TestSummary
from .phase_context import PhaseContext, build_phase_context
from .phase_prompts import (
    BackendDecision,
    build_backend_bridge_prompt,
    build_backend_decision_prompt,
    build_core_feature_prompt,
    build_ui_shell_prompt,
    parse_backend_decision,
)
from .phase_repair import run_qa_repair_loop
from .production_adapter import (
    LiveOpenCodeExecutionAdapter,
    OpenCodeExecutionClient,
    ProductionProjectExecutionAdapter,
    UnavailableOpenCodeExecutionClient,
    _build_qa_fix_prompt,
    _cancelled_result,
    live_opencode_execution_enabled,
)
from .qa_runner import QAOutcome, run_qa_commands
from .readiness import LIVE_EXECUTION_OPT_IN_REQUIRED, ReadinessResult
from .website_generation import detect_cinematic_website_intent
from .workspace import reserve_owned_project_workspace, scan_meaningful_generated_artifacts, summarize_generated_workspace, validate_owned_project_workspace

UI_SHELL_QA_COMMANDS: tuple[str, ...] = ("npm run build",)
CORE_FEATURE_QA_COMMANDS: tuple[str, ...] = ("npm test",)
MAX_PHASE_REPAIR_ATTEMPTS = 2


def resolve_execution_pipeline_mode(environ: dict[str, str] | None = None) -> str:
    """"phased" is the default; only the literal value "legacy" opts back into the one-shot adapter."""
    raw = (environ or os.environ).get("FREELANCERSTUDIO_EXECUTION_PIPELINE", "").strip().lower()
    return "legacy" if raw == "legacy" else "phased"


def _select_qa_runner(environ: dict[str, str] | None = None) -> Callable[[tuple[str, ...], Path], QAOutcome]:
    backend = (environ or os.environ).get("FREELANCERSTUDIO_PHASED_QA_BACKEND", "docker").strip().lower()
    if backend == "host":
        return run_qa_commands
    return run_qa_commands_in_docker


@dataclass(frozen=True, slots=True)
class _PhaseOutcome:
    context: PhaseContext | None
    failure: ExecutionResult | None

    @property
    def success(self) -> bool:
        return self.failure is None


class PhasedLiveOpenCodeExecutionAdapter(ProductionProjectExecutionAdapter):
    """Restructures live generation into UI-SHELL -> CORE-FEATURE -> BACKEND-DECISION-GATE,
    each its own OpenCode call with its own scoped prompt and its own QA-gated checkpoint,
    instead of one monolithic prompt covering the whole app. Cinematic-website orders are
    delegated whole to the existing LiveOpenCodeExecutionAdapter, unchanged, since this slice
    does not special-case that curated flow. Phases 4-7 (auth/persistence, integrations,
    monetization, packaging) are not built yet: when the decision gate determines a backend
    is needed, one bridging OpenCode call (reusing the legacy pipeline's own prompt shape)
    stands in for them until those phases exist.
    """

    def __init__(
        self,
        *,
        provider_name: str | None,
        model_name: str | None,
        workspace_root: str | Path | None,
        opencode_client: OpenCodeExecutionClient | None = None,
        qa_commands: tuple[str, ...] = ("npm test",),
        environ: dict[str, str] | None = None,
        writable_probe: Callable[[Path], bool] | None = None,
        ai_ask: Callable[[str], str] | None = None,
        decision_ai_ask: Callable[[str], str] | None = None,
        qa_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, model_name=model_name, workspace_root=workspace_root, qa_commands=qa_commands, dry_run=True, writable_probe=writable_probe)
        self._opencode_client = opencode_client or UnavailableOpenCodeExecutionClient()
        self._environ = environ
        # ai_ask is the general-purpose prose call (README overview, and the cinematic-website
        # fallback's own section-copy call); decision_ai_ask is specifically for the strict-JSON
        # backend decision gate. They default to the same real call but are independently
        # injectable so tests never need a fake that has to serve both shapes at once.
        self._ai_ask = ai_ask or self._default_ai_ask
        self._decision_ai_ask = decision_ai_ask or self._ai_ask
        self._qa_runner = qa_runner or _select_qa_runner(environ)
        self._legacy_fallback = LiveOpenCodeExecutionAdapter(
            provider_name=provider_name,
            model_name=model_name,
            workspace_root=workspace_root,
            opencode_client=opencode_client,
            qa_commands=qa_commands,
            environ=environ,
            writable_probe=writable_probe,
            website_section_ai_ask=self._ai_ask,
        )

    def _default_ai_ask(self, prompt: str) -> str:
        return ask_studio_ai_with_history(self.provider_name, self.model_name, prompt, [], temperature=0.3)

    def check_readiness(self, brief: ProjectBrief) -> ReadinessResult:
        blockers = list(super().check_readiness(brief).blockers)
        if not live_opencode_execution_enabled(self._environ):
            blockers.append(LIVE_EXECUTION_OPT_IN_REQUIRED)
        blockers.extend(self._opencode_client.check_readiness().blockers)
        if blockers:
            return ReadinessResult.blocked(*blockers)
        return ReadinessResult.ready_result()

    def execute(self, request: ExecutionRequest, event_sink: ExecutionEventSink, cancellation: CancellationToken) -> ExecutionResult:
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        if detect_cinematic_website_intent(request.brief):
            return self._legacy_fallback.execute(request, event_sink, cancellation)

        event_sink.emit(stage=ExecutionStage.PLANNING, agent="Studio", progress=5, message="Preparing phased execution")
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=request.execution_id, brief_fingerprint=request.brief.approval_fingerprint)
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        validate_owned_project_workspace(workspace, order_id=request.brief.order_id, execution_id=request.execution_id)

        ui_shell = self._run_phase(
            stage=ExecutionStage.UI_SHELL,
            prompt=build_ui_shell_prompt(request.brief, request.handoff),
            qa_commands=UI_SHELL_QA_COMMANDS,
            workspace=workspace,
            event_sink=event_sink,
            cancellation=cancellation,
            request=request,
        )
        if not ui_shell.success:
            return ui_shell.failure
        self._emit_milestone(event_sink, ExecutionStage.UI_SHELL, "UI shell complete: screens and navigation build cleanly, no backend/auth/external calls wired.")

        core_feature = self._run_phase(
            stage=ExecutionStage.CORE_FEATURE,
            prompt=build_core_feature_prompt(request.brief, request.handoff, ui_shell.context),
            qa_commands=CORE_FEATURE_QA_COMMANDS,
            workspace=workspace,
            event_sink=event_sink,
            cancellation=cancellation,
            request=request,
        )
        if not core_feature.success:
            return core_feature.failure
        self._emit_milestone(event_sink, ExecutionStage.CORE_FEATURE, f"Core feature wired in and passing its own test: {request.brief.core_features[0]}")

        if cancellation.is_cancelled():
            return _cancelled_result(request)
        decision = self._decide_backend_need(request, ui_shell.context, core_feature.context)
        self._emit_milestone(
            event_sink,
            ExecutionStage.BACKEND_DECISION,
            f"Backend {'needed' if decision.needs_backend else 'not needed'}: {decision.reasoning}",
        )

        if not decision.needs_backend:
            return self._finalize_success(request, event_sink, workspace, note="No backend was required; the UI shell and core feature are the complete deliverable.")

        return self._run_backend_bridge(request, event_sink, cancellation, workspace, ui_shell.context, core_feature.context, decision)

    def _decide_backend_need(self, request: ExecutionRequest, ui_shell_context: PhaseContext, core_feature_context: PhaseContext) -> BackendDecision:
        prompt = build_backend_decision_prompt(request.brief, request.handoff, ui_shell_context, core_feature_context)
        try:
            raw_response = self._decision_ai_ask(prompt)
        except Exception:
            raw_response = ""
        return parse_backend_decision(raw_response)

    def _emit_milestone(self, event_sink: ExecutionEventSink, stage: ExecutionStage, message: str) -> None:
        event_sink.emit(
            stage=stage,
            agent="Alex",
            progress=_MILESTONE_PROGRESS.get(stage, 50),
            message=message,
            level=EventLevel.INFO,
            kind=EventKind.MILESTONE,
        )

    def _run_phase(
        self,
        *,
        stage: ExecutionStage,
        prompt: str,
        qa_commands: tuple[str, ...],
        workspace,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        request: ExecutionRequest,
    ) -> _PhaseOutcome:
        if cancellation.is_cancelled():
            return _PhaseOutcome(context=None, failure=_cancelled_result(request))
        workspace_path = workspace.project_path
        (workspace_path / f"execution_prompt_{stage.value}.md").write_text(prompt, encoding="utf-8")
        event_sink.emit(stage=stage, agent="Codex", progress=10, message=f"Sending {stage.value} prompt to OpenCode")
        try:
            result = self._opencode_client.execute_project_prompt(prompt, workspace_path, event_sink, cancellation)
        except Exception:
            return _PhaseOutcome(context=None, failure=self._phase_failure(request, stage, "opencode_execution_failed", f"Live OpenCode execution failed during the {stage.value} phase."))
        if cancellation.is_cancelled():
            return _PhaseOutcome(context=None, failure=_cancelled_result(request))

        try:
            repair = run_qa_repair_loop(
                opencode_client=self._opencode_client,
                opencode_succeeded=result.success,
                workspace_path=workspace_path,
                qa_commands=qa_commands,
                qa_cwd=workspace_path,
                qa_runner=self._qa_runner,
                event_sink=event_sink,
                cancellation=cancellation,
                stage=stage,
                agent="BugCatcher",
                max_attempts=MAX_PHASE_REPAIR_ATTEMPTS,
                fix_prompt_builder=_build_qa_fix_prompt,
            )
        except DockerUnavailableError as exc:
            event_sink.emit(
                stage=stage,
                agent="BugCatcher",
                progress=70,
                message="Docker engine is not reachable. Start Docker Desktop, or set FREELANCERSTUDIO_PHASED_QA_BACKEND=host to run QA on the bare host instead (less isolated).",
                level=EventLevel.ERROR,
                details=(str(exc)[:2000],),
            )
            return _PhaseOutcome(context=None, failure=self._phase_failure(request, stage, "docker_engine_unreachable", "Docker engine unavailable; phase QA could not run.", outcome="docker_unavailable"))
        if repair.cancelled:
            return _PhaseOutcome(context=None, failure=_cancelled_result(request))
        if not result.success:
            return _PhaseOutcome(context=None, failure=self._phase_failure(request, stage, "opencode_execution_failed", f"Live OpenCode execution did not succeed during the {stage.value} phase."))
        if repair.qa_outcome is None or not repair.qa_outcome.passed:
            return _PhaseOutcome(context=None, failure=self._phase_failure(request, stage, "qa_failed", f"QA did not pass for the {stage.value} phase. {repair.qa_status_message}", outcome="qa_failed"))

        context = build_phase_context(stage.value, workspace, repair.qa_outcome)
        return _PhaseOutcome(context=context, failure=None)

    @staticmethod
    def _phase_failure(request: ExecutionRequest, stage: ExecutionStage, error_code: str, summary: str, *, outcome: str = "failed") -> ExecutionResult:
        return ExecutionResult(
            success=False,
            outcome=outcome,
            summary=summary,
            test_summary=TestSummary(failed=1),
            errors=(error_code,),
            final_stage=stage,
            completed_at=request.brief.updated_at,
        )

    def _run_backend_bridge(
        self,
        request: ExecutionRequest,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        workspace,
        ui_shell_context: PhaseContext,
        core_feature_context: PhaseContext,
        decision: BackendDecision,
    ) -> ExecutionResult:
        prompt = build_backend_bridge_prompt(request.brief, request.handoff, ui_shell_context, core_feature_context, decision.reasoning, self.qa_commands)
        bridge = self._run_phase(
            stage=ExecutionStage.IMPLEMENTATION,
            prompt=prompt,
            qa_commands=self.qa_commands,
            workspace=workspace,
            event_sink=event_sink,
            cancellation=cancellation,
            request=request,
        )
        if not bridge.success:
            return bridge.failure
        return self._finalize_success(request, event_sink, workspace, note=f"A backend was added: {decision.reasoning}")

    def _finalize_success(self, request: ExecutionRequest, event_sink: ExecutionEventSink, workspace, *, note: str) -> ExecutionResult:
        summary = summarize_generated_workspace(workspace)
        meaningful_artifacts = scan_meaningful_generated_artifacts(workspace)
        delivery_report = (
            "Phased live execution completed.\n"
            f"{note}\n"
            f"Meaningful artifacts detected: {len(meaningful_artifacts)}.\n"
            f"Workspace files inspected: {summary['files_created']}.\n"
        )
        (workspace.project_path / "delivery_report.md").write_text(delivery_report, encoding="utf-8")

        module_map = build_module_map(workspace.project_path)
        tech_stack = (
            f"Frontend: {request.brief.recommended_stack.frontend}\n"
            f"Backend: {request.brief.recommended_stack.backend}\n"
            f"Storage: {request.brief.recommended_stack.storage}"
        )
        overview = generate_overview_paragraph(request.brief.goal, request.handoff.requirements, self._ai_ask)
        readme_text = build_readme(
            project_name=workspace.project_reference,
            goal=request.brief.goal,
            tech_stack=tech_stack,
            features=request.handoff.requirements,
            setup_commands=self.qa_commands,
            module_map=module_map,
            overview=overview,
        )
        architecture_text = build_architecture_mermaid(module_map, workspace.project_reference)
        (workspace.project_path / "README.md").write_text(readme_text, encoding="utf-8")
        (workspace.project_path / "ARCHITECTURE.md").write_text(architecture_text, encoding="utf-8")

        artifacts = (
            event_sink.artifact(kind=ArtifactKind.PROJECT_SUMMARY, name="generated_project_summary.json", summary=f"Workspace contains {summary['files_created']} non-marker files.", reference="generated-project-summary-json"),
            event_sink.artifact(kind=ArtifactKind.DELIVERY_REPORT, name="delivery_report.md", summary=note, reference="delivery-report-md"),
            event_sink.artifact(kind=ArtifactKind.PROJECT_DOCUMENTATION, name="README.md", summary="Generated project README with a real feature/tech-stack overview.", reference="readme-md"),
            event_sink.artifact(kind=ArtifactKind.PROJECT_DOCUMENTATION, name="ARCHITECTURE.md", summary="Real Mermaid architecture diagram built from the generated file tree.", reference="architecture-md"),
        )
        event_sink.emit(stage=ExecutionStage.COMPLETED, agent="Product Judge", progress=100, message="Delivery summary prepared")
        return ExecutionResult(
            success=True,
            outcome="succeeded",
            summary=f"Project generated by the phased live execution pipeline. {note}",
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(skipped=1),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=request.brief.updated_at,
        )


_MILESTONE_PROGRESS: dict[ExecutionStage, int] = {
    ExecutionStage.UI_SHELL: 30,
    ExecutionStage.CORE_FEATURE: 55,
    ExecutionStage.BACKEND_DECISION: 65,
}
