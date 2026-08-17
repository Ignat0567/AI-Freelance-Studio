from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_utils import ask_studio_ai_with_history
from project_docs import build_architecture_mermaid, build_module_map, build_overview_paragraph, build_readme

from .complexity import classify_phase_complexity, describe_phase_complexity, model_for_complexity
from .deployment import DeploymentOutcome, build_and_verify_container
from .docker_qa_runner import run_qa_commands_in_docker, DockerUnavailableError
from .executors import CancellationToken, ExecutionEventSink, ExecutionRequest
from .claude_code_client import CLAUDE_CODE_REPAIR_TIMEOUT, CLAUDE_CODE_TASK_TIMEOUT
from .functional_smoke_check import run_functional_smoke_check_in_docker
from .midbuild_clarification import build_midbuild_questions, corrections_from_answers
from .models import ArtifactKind, ExecutionResult, ExecutionStage, EventKind, EventLevel, ProductType, ProjectBrief, TestSummary, TokenUsage
from .phase_context import PhaseContext, build_phase_context
from .phase_prompts import (
    BackendDecision,
    build_backend_bridge_prompt,
    build_bot_prompt,
    build_core_feature_prompt,
    build_revision_prompt,
    build_ui_shell_prompt,
    decide_backend_need,
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
from .state_continuity_check import run_state_continuity_check_in_docker
from .readiness import LIVE_EXECUTION_OPT_IN_REQUIRED, ReadinessResult
from .visual_check import build_visual_check_runner, palette_from_concept
from .website_generation import detect_cinematic_website_intent
from .workspace import reserve_owned_project_workspace, scan_meaningful_generated_artifacts, summarize_generated_workspace, validate_owned_project_workspace

UI_SHELL_QA_COMMANDS: tuple[str, ...] = ("npm run build",)
CORE_FEATURE_QA_COMMANDS: tuple[str, ...] = ("npm test",)
# docker_qa_runner.detect_base_image() already resolves requirements.txt/pyproject.toml
# projects to a Python image with zero changes needed there -- pip install then a bare
# import is the bot equivalent of "npm run build": proves dependencies resolve and the
# module has no syntax/import-time errors, without needing a real BOT_TOKEN or Telegram
# connectivity (which QA must never attempt -- see build_bot_prompt).
BOT_QA_COMMANDS: tuple[str, ...] = ("pip install -r requirements.txt", 'python -c "import bot"')
MAX_PHASE_REPAIR_ATTEMPTS = 2

_logger = logging.getLogger(__name__)


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


def _checkpoint_path(workspace, key: str) -> Path:
    return workspace.project_path / f".freelancerstudio-checkpoint-{key}.json"


def _load_phase_checkpoint(workspace, stage: ExecutionStage) -> PhaseContext | None:
    """A completed phase's checkpoint, if this workspace already has one -- present when
    execute() is re-run on the same owned workspace (via ProjectExecutionService.retry())
    after a later phase failed. Loading it lets that phase be skipped entirely on retry
    instead of re-running an already-successful, potentially expensive CLI generation.
    Any missing/corrupt checkpoint is treated as "no checkpoint" (falls back to a normal
    fresh run of that phase) rather than failing the whole execution."""
    path = _checkpoint_path(workspace, stage.value)
    if not path.is_file():
        return None
    try:
        return PhaseContext.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        _logger.warning("Ignoring unreadable phase checkpoint at %s", path, exc_info=True)
        return None


def _save_phase_checkpoint(workspace, stage: ExecutionStage, context: PhaseContext) -> None:
    path = _checkpoint_path(workspace, stage.value)
    try:
        path.write_text(context.to_json(), encoding="utf-8")
    except OSError:
        _logger.warning("Could not write phase checkpoint at %s; a retry would redo this phase.", path, exc_info=True)


def _load_backend_decision_checkpoint(workspace) -> BackendDecision | None:
    path = _checkpoint_path(workspace, "backend_decision")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BackendDecision(needs_backend=bool(data["needs_backend"]), reasoning=str(data["reasoning"]), confident=bool(data["confident"]))
    except Exception:
        _logger.warning("Ignoring unreadable backend-decision checkpoint at %s", path, exc_info=True)
        return None


def _save_backend_decision_checkpoint(workspace, decision: BackendDecision) -> None:
    path = _checkpoint_path(workspace, "backend_decision")
    try:
        path.write_text(json.dumps({"needs_backend": decision.needs_backend, "reasoning": decision.reasoning, "confident": decision.confident}), encoding="utf-8")
    except OSError:
        _logger.warning("Could not write backend-decision checkpoint at %s; a retry would re-ask.", path, exc_info=True)


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
        qa_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
        smoke_check_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
        visual_check_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
        state_check_runner: Callable[[tuple[str, ...], Path], QAOutcome] | None = None,
        # Resolved by the caller from the REAL environment, never from `environ` above --
        # that is a synthetic live-opt-in-only dict (see service.py), so reading the deploy
        # flag out of it would silently always be False.
        midbuild_clarification: bool = False,
        container_deploy: bool = False,
        deploy_runner: Callable[..., DeploymentOutcome] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, model_name=model_name, workspace_root=workspace_root, qa_commands=qa_commands, dry_run=True, writable_probe=writable_probe)
        self._opencode_client = opencode_client or UnavailableOpenCodeExecutionClient()
        self._environ = environ
        # The general-purpose prose call: the README overview, and the cinematic-website
        # fallback's own section-copy call. The backend-decision gate used to take a second,
        # separately injectable call of this shape; it is now decided from the brief with no
        # model in the loop at all (see phase_prompts.decide_backend_need).
        self._ai_ask = ai_ask or self._default_ai_ask
        self._qa_runner = qa_runner or _select_qa_runner(environ)
        # Independently injectable from qa_runner: qa_runner may be swapped to the bare-host
        # runner (FREELANCERSTUDIO_PHASED_QA_BACKEND=host), but the functional smoke check
        # always needs a real headless browser, so it always defaults to the Docker/Playwright
        # runner regardless of that setting -- see _run_functional_smoke_check's DockerUnavailableError
        # handling for what happens when Docker itself isn't reachable either.
        self._smoke_check_runner = smoke_check_runner or run_functional_smoke_check_in_docker
        # Left None by default on purpose: the real runner cannot be built here, only
        # per-execution, because it has to carry that brief's approved palette. Tests
        # inject a fake so a unit test never reaches Docker.
        self._visual_check_runner = visual_check_runner
        self._state_check_runner = state_check_runner or run_state_continuity_check_in_docker
        self._midbuild_clarification = midbuild_clarification
        self._container_deploy = container_deploy
        self._deploy_runner = deploy_runner or build_and_verify_container
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
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=request.execution_id, brief_fingerprint=request.brief.approval_fingerprint, title=request.title)
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        validate_owned_project_workspace(workspace, order_id=request.brief.order_id, execution_id=request.execution_id)

        ui_shell_checkpoint = _load_phase_checkpoint(workspace, ExecutionStage.UI_SHELL)
        if ui_shell_checkpoint is not None:
            event_sink.emit(stage=ExecutionStage.UI_SHELL, agent="Studio", progress=30, message="Resuming: ui_shell already completed in an earlier attempt, skipping regeneration.")
            ui_shell = _PhaseOutcome(context=ui_shell_checkpoint, failure=None)
        else:
            ui_shell_complexity, ui_shell_reason = describe_phase_complexity(request.brief, focus_text=f"{request.brief.goal} {' '.join(request.brief.ui_requirements)}")
            ui_shell = self._run_phase(
                stage=ExecutionStage.UI_SHELL,
                prompt=build_ui_shell_prompt(request.brief, request.handoff, additions=request.prompt_additions),
                qa_commands=UI_SHELL_QA_COMMANDS,
                workspace=workspace,
                event_sink=event_sink,
                cancellation=cancellation,
                request=request,
                model=model_for_complexity(ui_shell_complexity),
                model_reason=f"{ui_shell_complexity}: {ui_shell_reason}",
                run_functional_smoke_check=True,
                run_visual_check=True,
            )
            if not ui_shell.success:
                return ui_shell.failure
            _save_phase_checkpoint(workspace, ExecutionStage.UI_SHELL, ui_shell.context)
        self._emit_milestone(event_sink, ExecutionStage.UI_SHELL, "UI shell complete: screens and navigation build cleanly, no backend/auth/external calls wired.")

        # The one point where the client can still correct the shape cheaply: the screens
        # exist and build, and nothing has been wired on top of them yet. Only pauses on a
        # first pass -- a resumed run already carries the answers.
        if self._midbuild_clarification and not request.midbuild_answers:
            questions = build_midbuild_questions(request.brief, shell_summary=ui_shell.context.summary)
            if questions:
                event_sink.emit(
                    stage=ExecutionStage.UI_SHELL,
                    agent="Alex",
                    progress=45,
                    message=f"Paused for {len(questions)} clarification question(s) before wiring the core feature.",
                    level=EventLevel.INFO,
                )
                return ExecutionResult(
                    success=False,
                    outcome="awaiting_user",
                    summary="Paused at the UI-shell checkpoint to confirm assumptions before building on them.",
                    questions=questions,
                    final_stage=ExecutionStage.UI_SHELL,
                    completed_at=request.brief.updated_at,
                )

        core_feature_checkpoint = _load_phase_checkpoint(workspace, ExecutionStage.CORE_FEATURE)
        if core_feature_checkpoint is not None:
            event_sink.emit(stage=ExecutionStage.CORE_FEATURE, agent="Studio", progress=55, message="Resuming: core_feature already completed in an earlier attempt, skipping regeneration.")
            core_feature = _PhaseOutcome(context=core_feature_checkpoint, failure=None)
        else:
            core_feature_complexity, core_feature_reason = describe_phase_complexity(request.brief, focus_text=f"{request.brief.core_features[0]} {request.brief.goal}")
            core_feature = self._run_phase(
                stage=ExecutionStage.CORE_FEATURE,
                prompt=build_core_feature_prompt(
                    request.brief,
                    request.handoff,
                    ui_shell.context,
                    corrections=corrections_from_answers(
                        build_midbuild_questions(request.brief, shell_summary=ui_shell.context.summary),
                        tuple(request.midbuild_answers),
                    ),
                ),
                qa_commands=CORE_FEATURE_QA_COMMANDS,
                workspace=workspace,
                event_sink=event_sink,
                cancellation=cancellation,
                request=request,
                model=model_for_complexity(core_feature_complexity),
                model_reason=f"{core_feature_complexity}: {core_feature_reason}",
                run_functional_smoke_check=True,
                run_state_check=True,
            )
            if not core_feature.success:
                return core_feature.failure
            _save_phase_checkpoint(workspace, ExecutionStage.CORE_FEATURE, core_feature.context)
        self._emit_milestone(event_sink, ExecutionStage.CORE_FEATURE, f"Core feature wired in and passing its own test: {request.brief.core_features[0]}")

        if cancellation.is_cancelled():
            return _cancelled_result(request)
        decision = _load_backend_decision_checkpoint(workspace)
        if decision is not None:
            event_sink.emit(stage=ExecutionStage.BACKEND_DECISION, agent="Studio", progress=65, message="Resuming: reusing the backend-need decision from an earlier attempt.")
        else:
            decision = decide_backend_need(request.brief, request.handoff)
            _save_backend_decision_checkpoint(workspace, decision)
        self._emit_milestone(
            event_sink,
            ExecutionStage.BACKEND_DECISION,
            f"Backend {'needed' if decision.needs_backend else 'not needed'}: {decision.reasoning}",
        )

        if not decision.needs_backend:
            return self._finalize_success(request, event_sink, workspace, note="No backend was required; the UI shell and core feature are the complete deliverable.")

        return self._run_backend_bridge(request, event_sink, cancellation, workspace, ui_shell.context, core_feature.context, decision)

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
        model: str | None = None,
        model_reason: str = "",
        run_functional_smoke_check: bool = False,
        run_visual_check: bool = False,
        run_state_check: bool = False,
    ) -> _PhaseOutcome:
        if cancellation.is_cancelled():
            return _PhaseOutcome(context=None, failure=_cancelled_result(request))
        workspace_path = workspace.project_path
        (workspace_path / f"execution_prompt_{stage.value}.md").write_text(prompt, encoding="utf-8")
        # The routing reason travels with the event so a model choice can be audited from
        # the event stream, rather than having to re-run the classifier to find out why.
        routing = f" (model: {model}{' -- ' + model_reason if model_reason else ''})" if model else ""
        event_sink.emit(stage=stage, agent="Codex", progress=10, message=f"Sending {stage.value} prompt to the coding CLI{routing}")
        try:
            result = self._opencode_client.execute_project_prompt(prompt, workspace_path, event_sink, cancellation, model=model)
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
                model=model,
                fix_timeout=CLAUDE_CODE_REPAIR_TIMEOUT,
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
            # A timeout is worth saying out loud. Flattened into the generic failure code it
            # is indistinguishable from a model-level refusal -- same empty usage, same null
            # rate-limit message -- and the only way to tell them apart is to go read the
            # raw CLI log and compare durations by hand.
            timed_out = getattr(result, "timed_out", False)
            client_errors = tuple(getattr(result, "errors", ()) or ())
            if timed_out:
                error_code = "coding_cli_timeout"
                summary = (
                    f"The coding CLI hit its {CLAUDE_CODE_TASK_TIMEOUT}s limit during the {stage.value} phase "
                    "and was stopped before it finished."
                )
            else:
                # The client already worked out *why* it failed and said so; replacing that
                # with a generic phase message throws away the only actionable part (an
                # expired login, a rejected model, a provider error) and sends whoever reads
                # the execution record digging through the raw CLI log to recover it.
                error_code = client_errors[0] if client_errors else "opencode_execution_failed"
                detail = (getattr(result, "summary", "") or "").strip()
                summary = (
                    f"The {stage.value} phase did not complete: {detail}"
                    if detail
                    else f"Live OpenCode execution did not succeed during the {stage.value} phase."
                )
            return _PhaseOutcome(
                context=None,
                failure=self._phase_failure(
                    request, stage, error_code, summary,
                    usage=result.usage, rate_limit_message=result.rate_limit_message,
                ),
            )
        if repair.qa_outcome is None or not repair.qa_outcome.passed:
            return _PhaseOutcome(context=None, failure=self._phase_failure(request, stage, "qa_failed", f"QA did not pass for the {stage.value} phase. {repair.qa_status_message}", outcome="qa_failed"))

        if run_functional_smoke_check:
            failure = self._run_functional_smoke_check(stage, workspace, event_sink, cancellation, request, model)
            if failure is not None:
                return _PhaseOutcome(context=None, failure=failure)

        if run_visual_check:
            failure = self._run_visual_check(stage, workspace, event_sink, cancellation, request, model)
            if failure is not None:
                return _PhaseOutcome(context=None, failure=failure)

        if run_state_check:
            failure = self._run_state_continuity_check(stage, workspace, event_sink, cancellation, request, model)
            if failure is not None:
                return _PhaseOutcome(context=None, failure=failure)

        context = build_phase_context(stage.value, workspace, repair.qa_outcome)
        return _PhaseOutcome(context=context, failure=None)

    def _run_functional_smoke_check(
        self,
        stage: ExecutionStage,
        workspace,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        request: ExecutionRequest,
        model: str | None,
    ) -> ExecutionResult | None:
        """QA depth level (a): does the app actually boot and render, not just compile?
        Best-effort -- if Docker isn't reachable at all, this is skipped (not failed), so
        it never blocks a host-only QA setup that has no Docker. When Docker IS available
        and the check fails, it goes through the same repair loop as build/test QA."""
        try:
            smoke_repair = run_qa_repair_loop(
                opencode_client=self._opencode_client,
                opencode_succeeded=True,
                workspace_path=workspace.project_path,
                qa_commands=("Functional smoke check: the app must boot, render visible content, "
                             "expose at least one usable interactive element, and produce no console/page errors.",),
                qa_cwd=workspace.project_path,
                qa_runner=self._smoke_check_runner,
                event_sink=event_sink,
                cancellation=cancellation,
                stage=stage,
                agent="BugCatcher",
                max_attempts=MAX_PHASE_REPAIR_ATTEMPTS,
                fix_prompt_builder=_build_qa_fix_prompt,
                model=model,
                fix_timeout=CLAUDE_CODE_REPAIR_TIMEOUT,
            )
        except DockerUnavailableError:
            event_sink.emit(
                stage=stage,
                agent="BugCatcher",
                progress=78,
                message="Functional smoke check skipped: Docker engine is not reachable.",
                level=EventLevel.WARNING,
            )
            return None
        if smoke_repair.cancelled:
            return _cancelled_result(request)
        if smoke_repair.qa_outcome is None or not smoke_repair.qa_outcome.passed:
            return self._phase_failure(
                request,
                stage,
                "functional_smoke_check_failed",
                f"The app did not pass a functional smoke check after the {stage.value} phase. {smoke_repair.qa_status_message}",
                outcome="qa_failed",
            )
        return None

    def _run_state_continuity_check(
        self,
        stage: ExecutionStage,
        workspace,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        request: ExecutionRequest,
        model: str | None,
    ) -> ExecutionResult | None:
        """QA depth level (c): does the app hold its own state?

        Runs on the core-feature phase, not the UI shell: a shell legitimately has
        placeholder state, but once the feature is wired the app is the deliverable, and a
        control that forgets what the user typed the moment they change screens is a facade
        rather than a product. Every gate before this one passes on a facade.
        """
        try:
            state_repair = run_qa_repair_loop(
                opencode_client=self._opencode_client,
                opencode_succeeded=True,
                workspace_path=workspace.project_path,
                qa_commands=("State continuity check: a value the user changes must survive navigating "
                             "to another screen and back, not reset to its initial value.",),
                qa_cwd=workspace.project_path,
                qa_runner=self._state_check_runner,
                event_sink=event_sink,
                cancellation=cancellation,
                stage=stage,
                agent="BugCatcher",
                max_attempts=MAX_PHASE_REPAIR_ATTEMPTS,
                fix_prompt_builder=_build_qa_fix_prompt,
                model=model,
                fix_timeout=CLAUDE_CODE_REPAIR_TIMEOUT,
            )
        except DockerUnavailableError:
            event_sink.emit(
                stage=stage,
                agent="BugCatcher",
                progress=79,
                message="State continuity check skipped: Docker engine is not reachable.",
                level=EventLevel.WARNING,
            )
            return None
        if state_repair.cancelled:
            return _cancelled_result(request)
        if state_repair.qa_outcome is None or not state_repair.qa_outcome.passed:
            return self._phase_failure(
                request,
                stage,
                "state_continuity_failed",
                f"The app loses state the user entered after the {stage.value} phase. {state_repair.qa_status_message}",
                outcome="qa_failed",
            )
        return None

    def _run_visual_check(
        self,
        stage: ExecutionStage,
        workspace,
        event_sink: ExecutionEventSink,
        cancellation: CancellationToken,
        request: ExecutionRequest,
        model: str | None,
    ) -> ExecutionResult | None:
        """QA depth level (b): did the built page adopt the design it was given?

        Runs on the UI-shell phase only. That is the phase whose prompt actually carries
        the style spec, so it is where design drift originates and where the repair is
        cheapest -- and one Playwright container per execution rather than one per phase.
        The tradeoff is real and worth stating: a later phase that repaints the ground
        would not be caught.

        Skipped entirely when the brief carries no colour direction (no Elena concept and
        no hex values in the style spec) -- there is then no approved palette to measure
        against, and asserting one the coding CLI was never given would be inventing a
        standard rather than enforcing one.
        """
        runner = self._visual_check_runner or build_visual_check_runner(
            palette_from_concept(
                request.brief.elena_design_concept,
                style_spec=" ".join(request.handoff.design_preview_summary),
            )
        )
        if runner is None:
            event_sink.emit(
                stage=stage,
                agent="Elena",
                progress=79,
                message="Visual check skipped: this brief carries no approved colour palette to verify against.",
                level=EventLevel.INFO,
            )
            return None
        try:
            visual_repair = run_qa_repair_loop(
                opencode_client=self._opencode_client,
                opencode_succeeded=True,
                workspace_path=workspace.project_path,
                qa_commands=("Visual check: the built page must paint the approved palette, meet WCAG AA "
                             "contrast, and fit a 375px phone viewport without horizontal overflow.",),
                qa_cwd=workspace.project_path,
                qa_runner=runner,
                event_sink=event_sink,
                cancellation=cancellation,
                stage=stage,
                agent="Elena",
                max_attempts=MAX_PHASE_REPAIR_ATTEMPTS,
                fix_prompt_builder=_build_qa_fix_prompt,
                model=model,
                fix_timeout=CLAUDE_CODE_REPAIR_TIMEOUT,
            )
        except DockerUnavailableError:
            event_sink.emit(
                stage=stage,
                agent="Elena",
                progress=79,
                message="Visual check skipped: Docker engine is not reachable.",
                level=EventLevel.WARNING,
            )
            return None
        if visual_repair.cancelled:
            return _cancelled_result(request)
        if visual_repair.qa_outcome is None or not visual_repair.qa_outcome.passed:
            return self._phase_failure(
                request,
                stage,
                "visual_check_failed",
                f"The built page did not match the approved design after the {stage.value} phase. {visual_repair.qa_status_message}",
                outcome="qa_failed",
            )
        return None

    @staticmethod
    def _phase_failure(
        request: ExecutionRequest,
        stage: ExecutionStage,
        error_code: str,
        summary: str,
        *,
        outcome: str = "failed",
        usage: TokenUsage | None = None,
        rate_limit_message: str | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            outcome=outcome,
            summary=summary,
            test_summary=TestSummary(failed=1),
            errors=(error_code,),
            final_stage=stage,
            usage=usage,
            rate_limit_message=rate_limit_message,
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
        bridge_checkpoint = _load_phase_checkpoint(workspace, ExecutionStage.IMPLEMENTATION)
        if bridge_checkpoint is not None:
            event_sink.emit(stage=ExecutionStage.IMPLEMENTATION, agent="Studio", progress=70, message="Resuming: the backend bridge phase already completed in an earlier attempt, skipping regeneration.")
        else:
            prompt = build_backend_bridge_prompt(request.brief, request.handoff, ui_shell_context, core_feature_context, decision.reasoning, self.qa_commands)
            bridge = self._run_phase(
                stage=ExecutionStage.IMPLEMENTATION,
                prompt=prompt,
                qa_commands=self.qa_commands,
                workspace=workspace,
                event_sink=event_sink,
                cancellation=cancellation,
                request=request,
                # Backend/database/auth work is architecturally significant by nature, and this
                # phase only runs at all once _decide_backend_need() already found it necessary
                # -- that decision is itself already a complexity signal, so skip the heuristic.
                model=model_for_complexity("complex"),
            )
            if not bridge.success:
                return bridge.failure
            _save_phase_checkpoint(workspace, ExecutionStage.IMPLEMENTATION, bridge.context)
        return self._finalize_success(request, event_sink, workspace, note=f"A backend was added: {decision.reasoning}")

    def _deploy(self, request: ExecutionRequest, event_sink: ExecutionEventSink, workspace) -> DeploymentOutcome | None:
        """PACKAGING stage: containerise the build and prove it serves over HTTP.

        Returns None when the stage does not apply (switched off, or a project with no
        package.json such as a Telegram bot). Never raises -- a project that builds and
        passes QA is still a good delivery when the host's Docker daemon is unhappy.
        """
        if not self._container_deploy or not (workspace.project_path / "package.json").is_file():
            return None
        event_sink.emit(stage=ExecutionStage.PACKAGING, agent="Studio", progress=90, message="Building production container image")
        # The tag has to be a valid Docker reference: lowercase, no underscores at the start.
        tag = f"freelancerstudio/{request.execution_id.replace('_', '-').casefold()}:latest"
        try:
            outcome = self._deploy_runner(workspace.project_path, image_tag=tag)
        except Exception as exc:
            _logger.warning("Container deploy stage failed for %s", request.execution_id, exc_info=True)
            # Capped to ShortText's 240 limit: this string ends up in ExecutionResult.warnings,
            # and overflowing it would turn a deploy warning into a validation crash.
            return DeploymentOutcome(succeeded=False, image_tag=tag, status_message=f"Deploy stage error: {exc}"[:240])
        event_sink.emit(
            stage=ExecutionStage.PACKAGING,
            agent="Studio",
            progress=95,
            message=outcome.status_message,
            level=EventLevel.INFO if outcome.succeeded else EventLevel.WARNING,
            details=(outcome.logs_tail[:2000],) if not outcome.succeeded and outcome.logs_tail else (),
        )
        return outcome

    def _finalize_success(self, request: ExecutionRequest, event_sink: ExecutionEventSink, workspace, *, note: str) -> ExecutionResult:
        summary = summarize_generated_workspace(workspace)
        meaningful_artifacts = scan_meaningful_generated_artifacts(workspace)
        deployment = self._deploy(request, event_sink, workspace)
        deployment_lines = ""
        if deployment is not None:
            deployment_lines = (
                f"\nContainer deployment: {deployment.status_message}\n"
                f"Image: {deployment.image_tag}\n"
                + (f"Run locally: {deployment.run_command}\n" if deployment.run_command else "")
            )
        delivery_report = (
            "Phased live execution completed.\n"
            f"{note}\n"
            f"Meaningful artifacts detected: {len(meaningful_artifacts)}.\n"
            f"Workspace files inspected: {summary['files_created']}.\n"
            f"{deployment_lines}"
        )
        (workspace.project_path / "delivery_report.md").write_text(delivery_report, encoding="utf-8")

        module_map = build_module_map(workspace.project_path)
        tech_stack = (
            f"Frontend: {request.brief.recommended_stack.frontend}\n"
            f"Backend: {request.brief.recommended_stack.backend}\n"
            f"Storage: {request.brief.recommended_stack.storage}"
        )
        overview = build_overview_paragraph(request.brief.goal, request.brief.target_users)
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
        deployment_note = f" {deployment.status_message}" if deployment is not None else ""
        return ExecutionResult(
            success=True,
            outcome="succeeded",
            summary=f"Project generated by the phased live execution pipeline. {note}{deployment_note}",
            artifact_ids=tuple(item.id for item in artifacts),
            test_summary=TestSummary(skipped=1),
            # A failed deploy is a warning, not a failed delivery: the project itself
            # built, tested and rendered before this stage ever ran.
            warnings=() if deployment is None or deployment.succeeded else (deployment.status_message[:240],),
            final_stage=ExecutionStage.COMPLETED,
            completed_at=request.brief.updated_at,
        )


class ReviseProjectExecutionAdapter(PhasedLiveOpenCodeExecutionAdapter):
    """Level 1 of the reliability/capability roadmap: make one targeted change to a
    project that has ALREADY been successfully delivered, instead of the only option
    that existed before this -- regenerating the whole thing from scratch into a brand
    new workspace with no memory of what was already built.

    Deliberately a thin subclass, not a rewrite: check_readiness(), _run_phase() (build/
    test QA, the functional smoke check, and their shared repair loop), _phase_failure(),
    and _finalize_success() are all inherited unchanged. Only execute() differs -- one
    phase (REVISION) instead of the ui_shell/core_feature/backend-decision sequence, and
    it resolves the workspace using the ORIGINAL execution's id (via
    request.revised_from_execution_id) rather than this new execution's own id, so it
    operates on the exact same files instead of reserving a fresh directory.
    """

    def execute(self, request: ExecutionRequest, event_sink: ExecutionEventSink, cancellation: CancellationToken) -> ExecutionResult:
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        if not request.revision_note or not request.revised_from_execution_id:
            return self._phase_failure(request, ExecutionStage.REVISION, "revision_request_incomplete", "A revision requires both a revision note and the execution being revised.")

        event_sink.emit(stage=ExecutionStage.REVISION, agent="Studio", progress=5, message="Preparing revision")
        source_execution_id = request.revised_from_execution_id
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=source_execution_id, brief_fingerprint=request.brief.approval_fingerprint, title=request.title)
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        validate_owned_project_workspace(workspace, order_id=request.brief.order_id, execution_id=source_execution_id)

        is_bot = request.brief.product_type is ProductType.BOT
        complexity = classify_phase_complexity(request.brief, focus_text=request.revision_note)
        revision = self._run_phase(
            stage=ExecutionStage.REVISION,
            prompt=build_revision_prompt(request.brief, request.handoff, request.revision_note),
            qa_commands=BOT_QA_COMMANDS if is_bot else CORE_FEATURE_QA_COMMANDS,
            workspace=workspace,
            event_sink=event_sink,
            cancellation=cancellation,
            request=request,
            model=model_for_complexity(complexity),
            # All three are Playwright/browser-only -- meaningless for a bot. A revision
            # produces a deliverable exactly like a first build does, so it is gated the
            # same way: the very first revision this pipeline ran was a fix for lost state,
            # and without this the gate that proves such a fix worked would not have run.
            run_functional_smoke_check=not is_bot,
            run_state_check=not is_bot,
        )
        if not revision.success:
            return revision.failure

        self._emit_milestone(event_sink, ExecutionStage.REVISION, f"Revision complete: {request.revision_note[:200]}")
        return self._finalize_success(request, event_sink, workspace, note=f"Revision applied: {request.revision_note[:200]}")


class TelegramBotExecutionAdapter(PhasedLiveOpenCodeExecutionAdapter):
    """Level 2 of the roadmap: a fundamentally different product shape from the web-app
    pipeline this subclasses -- no UI shell, no core-feature/backend-decision split (a
    bot is usually small enough to build in a single pass), and no browser to run the
    functional smoke check against. Reuses _run_phase() (QA + repair loop) and
    _finalize_success() (README/ARCHITECTURE/delivery report -- both are generic
    file-tree summarizers with nothing web-specific in their own logic) unchanged.
    """

    def execute(self, request: ExecutionRequest, event_sink: ExecutionEventSink, cancellation: CancellationToken) -> ExecutionResult:
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        event_sink.emit(stage=ExecutionStage.BOT_BUILD, agent="Studio", progress=5, message="Preparing bot build")
        workspace = reserve_owned_project_workspace(self.workspace_root, order_id=request.brief.order_id, execution_id=request.execution_id, brief_fingerprint=request.brief.approval_fingerprint, title=request.title)
        if cancellation.is_cancelled():
            return _cancelled_result(request)
        validate_owned_project_workspace(workspace, order_id=request.brief.order_id, execution_id=request.execution_id)

        complexity = classify_phase_complexity(request.brief, focus_text=f"{request.brief.goal} {' '.join(request.brief.core_features)}")
        build = self._run_phase(
            stage=ExecutionStage.BOT_BUILD,
            prompt=build_bot_prompt(request.brief, request.handoff),
            qa_commands=BOT_QA_COMMANDS,
            workspace=workspace,
            event_sink=event_sink,
            cancellation=cancellation,
            request=request,
            model=model_for_complexity(complexity),
            # No functional_smoke_check: that check is Playwright/browser-only and a bot
            # has no browser surface for it to load.
        )
        if not build.success:
            return build.failure

        self._emit_milestone(event_sink, ExecutionStage.BOT_BUILD, "Bot build complete: handlers implemented, module imports cleanly.")
        return self._finalize_success(request, event_sink, workspace, note="Telegram bot generated. No backend or browser frontend required.")


_MILESTONE_PROGRESS: dict[ExecutionStage, int] = {
    ExecutionStage.UI_SHELL: 30,
    ExecutionStage.CORE_FEATURE: 55,
    ExecutionStage.BACKEND_DECISION: 65,
    ExecutionStage.REVISION: 70,
    ExecutionStage.BOT_BUILD: 50,
}
