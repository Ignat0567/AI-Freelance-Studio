from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    CancellationToken,
    DesignPreviewService,
    ExecutionRequest,
    OpenCodeExecutionResult,
    ProjectBriefService,
    ReadinessResult,
    UserOrder,
)
from order_workflow.docker_qa_runner import DockerUnavailableError, run_qa_commands_in_docker
from order_workflow.models import ExecutionStage, EventKind
from order_workflow.phased_adapter import (
    CORE_FEATURE_QA_COMMANDS,
    PhasedLiveOpenCodeExecutionAdapter,
    UI_SHELL_QA_COMMANDS,
    _select_qa_runner,
    resolve_execution_pipeline_mode,
)
from order_workflow.qa_runner import QACommandResult, QAOutcome, run_qa_commands

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)
PDF = "Create a browser PDF voice assistant with upload, voice and text chat, grounded answers, page citations and speech playback."
CINEMATIC = "Create a cinematic showcase website with webgl scroll storytelling for a digital agency."


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"phased-{self.index:04d}"


def _clock():
    return NOW + timedelta(seconds=1)


def _contract(description=PDF):
    ids = SequenceIds()
    order = UserOrder(id="order_phased", title="Phased App", description=description, product_type="web_app", created_at=NOW, updated_at=NOW)
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


class FakePhaseOpenCodeClient:
    def __init__(self, *, ready=True, fail_on_call=None, raise_on_call=None) -> None:
        self.ready = ready
        self.fail_on_call = fail_on_call or ()
        self.raise_on_call = raise_on_call or ()
        self.prompts: list[str] = []
        self.call_count = 0

    def check_readiness(self):
        if self.ready:
            return ReadinessResult.ready_result()
        from order_workflow import readiness_blocker

        return ReadinessResult.blocked(readiness_blocker("opencode_unavailable", "OpenCode is not available."))

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.call_count += 1
        self.prompts.append(prompt)
        if self.call_count in self.raise_on_call:
            raise RuntimeError("provider stack trace")
        Path(workspace_path, f"call-{self.call_count}-marker.txt").write_text("generated", encoding="utf-8")
        if self.call_count in self.fail_on_call:
            return OpenCodeExecutionResult(success=False, summary="OpenCode declined the request")
        return OpenCodeExecutionResult(success=True, summary="generated")


def _passing_qa(_qa_commands, _cwd):
    return QAOutcome(passed=True, results=(QACommandResult(command="npm test", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),))


def _failing_qa(_qa_commands, _cwd):
    return QAOutcome(passed=False, results=(QACommandResult(command="npm test", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),))


class ScriptedQARunner:
    def __init__(self, outcomes: list[QAOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, _qa_commands, _cwd) -> QAOutcome:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, *, stage, agent, progress, message, level=None, details=(), kind=EventKind.ACTIVITY):
        self.events.append({"stage": stage, "agent": agent, "progress": progress, "message": message, "kind": kind})

    def artifact(self, *, kind, name, summary, reference):
        from order_workflow.models import ExecutionArtifact

        return ExecutionArtifact(id="artifact_fixed001", execution_id="execution_fixed001", kind=kind, name=name, summary=summary, reference=reference, created_at=NOW)


def _always_needs_no_backend(_prompt: str) -> str:
    return '{"needs_backend": false, "reasoning": "Single-user client-side app, no sync needed."}'


def _always_needs_backend(_prompt: str) -> str:
    return '{"needs_backend": true, "reasoning": "Multiple users must see synchronized data."}'


def _safe_prose_ai_ask(_prompt: str) -> str:
    """Never touches the network -- used as the general-purpose ai_ask in every test
    so the cinematic-website legacy-delegation path can never make a real API call."""
    return "A generated project overview paragraph."


def _adapter(tmp_path, *, opencode_client=None, qa_runner=None, decision_ai_ask=None, ai_ask=None, environ=None) -> PhasedLiveOpenCodeExecutionAdapter:
    return PhasedLiveOpenCodeExecutionAdapter(
        provider_name="opencode_bridge",
        model_name="openai/gpt-5.5",
        workspace_root=tmp_path,
        opencode_client=opencode_client or FakePhaseOpenCodeClient(),
        qa_runner=qa_runner or _passing_qa,
        ai_ask=ai_ask or _safe_prose_ai_ask,
        decision_ai_ask=decision_ai_ask or _always_needs_no_backend,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1", **(environ or {})},
    )


def _request(brief, handoff) -> ExecutionRequest:
    return ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_test0001")


# --- feature-flag / QA-backend selection helpers ----------------------------


def test_resolve_execution_pipeline_mode_defaults_to_phased():
    assert resolve_execution_pipeline_mode({}) == "phased"
    assert resolve_execution_pipeline_mode({"FREELANCERSTUDIO_EXECUTION_PIPELINE": "anything"}) == "phased"


def test_resolve_execution_pipeline_mode_legacy_opt_out():
    assert resolve_execution_pipeline_mode({"FREELANCERSTUDIO_EXECUTION_PIPELINE": "legacy"}) == "legacy"
    assert resolve_execution_pipeline_mode({"FREELANCERSTUDIO_EXECUTION_PIPELINE": "LEGACY"}) == "legacy"


def test_select_qa_runner_defaults_to_docker():
    assert _select_qa_runner({}) is run_qa_commands_in_docker


def test_select_qa_runner_host_opt_out():
    assert _select_qa_runner({"FREELANCERSTUDIO_PHASED_QA_BACKEND": "host"}) is run_qa_commands


# --- readiness ----------------------------------------------------------------


def test_readiness_requires_live_opt_in(tmp_path):
    brief, _ = _contract()
    adapter = PhasedLiveOpenCodeExecutionAdapter(
        provider_name="opencode_bridge", model_name="openai/gpt-5.5", workspace_root=tmp_path,
        opencode_client=FakePhaseOpenCodeClient(), environ={},
    )

    readiness = adapter.check_readiness(brief)

    assert readiness.ready is False
    assert any(b.code == "live_execution_opt_in_required" for b in readiness.blockers)


# --- happy paths ----------------------------------------------------------------


def test_decision_no_completes_after_two_phases_only(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)
    sink = FakeEventSink()

    result = adapter.execute(_request(brief, handoff), sink, CancellationToken())

    assert result.success is True
    assert result.final_stage == ExecutionStage.COMPLETED
    assert client.call_count == 2  # UI shell + core feature only, no bridge call
    assert "PDF upload" in client.prompts[0] or brief.core_features[0] not in client.prompts[0]  # phase 1 must not name the core feature exclusively as its task


def test_decision_yes_runs_a_third_bridging_phase(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client, decision_ai_ask=_always_needs_backend)
    sink = FakeEventSink()

    result = adapter.execute(_request(brief, handoff), sink, CancellationToken())

    assert result.success is True
    assert client.call_count == 3
    assert "already implemented" in client.prompts[2].lower()


def test_milestone_events_are_emitted_with_the_milestone_kind(tmp_path):
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)
    sink = FakeEventSink()

    adapter.execute(_request(brief, handoff), sink, CancellationToken())

    milestone_events = [event for event in sink.events if event["kind"] == EventKind.MILESTONE]
    milestone_stages = {event["stage"] for event in milestone_events}
    assert ExecutionStage.UI_SHELL in milestone_stages
    assert ExecutionStage.CORE_FEATURE in milestone_stages
    assert ExecutionStage.BACKEND_DECISION in milestone_stages


def test_core_feature_prompt_receives_ui_shell_context_not_the_raw_brief(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert "ui_shell" in client.prompts[1].lower()


def test_each_phase_runs_its_own_qa_commands(tmp_path):
    brief, handoff = _contract()
    seen_commands: list[tuple[str, ...]] = []

    def _recording_qa_runner(qa_commands, _cwd):
        seen_commands.append(qa_commands)
        return QAOutcome(passed=True, results=())

    adapter = _adapter(tmp_path, qa_runner=_recording_qa_runner)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert seen_commands[0] == UI_SHELL_QA_COMMANDS
    assert seen_commands[1] == CORE_FEATURE_QA_COMMANDS


def test_workspace_contains_a_prompt_file_per_phase(tmp_path):
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dirs = [item for item in Path(tmp_path).iterdir() if item.is_dir()]
    assert len(project_dirs) == 1
    project_dir = project_dirs[0]
    assert (project_dir / "execution_prompt_ui_shell.md").is_file()
    assert (project_dir / "execution_prompt_core_feature.md").is_file()
    assert (project_dir / "README.md").is_file()


# --- phase failure and repair -------------------------------------------------


def test_ui_shell_qa_failure_exhausts_repair_and_reports_failure_at_ui_shell_stage(tmp_path):
    brief, handoff = _contract()
    adapter = _adapter(tmp_path, qa_runner=_failing_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.final_stage == ExecutionStage.UI_SHELL
    assert result.outcome == "qa_failed"


def test_core_feature_phase_never_runs_if_ui_shell_fails(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=_failing_qa)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert client.call_count == 1 + 2  # 1 initial + MAX_PHASE_REPAIR_ATTEMPTS(2) re-prompts, all within UI_SHELL


def test_ui_shell_qa_failure_then_repair_succeeds(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    qa_runner = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="npm run build", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=qa_runner)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True


def test_opencode_exception_during_a_phase_fails_at_that_phase(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient(raise_on_call={1})
    adapter = _adapter(tmp_path, opencode_client=client)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.final_stage == ExecutionStage.UI_SHELL
    assert "opencode_execution_failed" in result.errors


def test_opencode_declines_during_core_feature_phase(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient(fail_on_call={2})
    adapter = _adapter(tmp_path, opencode_client=client)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.final_stage == ExecutionStage.CORE_FEATURE


# --- decision-gate fail-closed behavior ----------------------------------------


def test_unparsable_decision_response_fails_closed_to_needing_a_backend(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client, decision_ai_ask=lambda _prompt: "not json")

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert client.call_count == 3  # bridge phase ran


def test_decision_ai_ask_raising_fails_closed_to_needing_a_backend(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()

    def _raising_ai_ask(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    adapter = _adapter(tmp_path, opencode_client=client, decision_ai_ask=_raising_ai_ask)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert client.call_count == 3


# --- cinematic-website compatibility -------------------------------------------


def test_cinematic_website_intent_delegates_entirely_to_the_legacy_adapter(tmp_path):
    brief, handoff = _contract(description=CINEMATIC)
    client = FakePhaseOpenCodeClient()

    def _section_selection_ai_ask(_prompt: str) -> str:
        return (
            '{"sections": ['
            '{"slug": "hero_webgl", "content": {"headline": "h", "subhead": "s", "cta_label": "c"}},'
            '{"slug": "closing_cta", "content": {"headline": "h2", "subhead": "s2", "cta_label": "c2"}}'
            "]}"
        )

    adapter = _adapter(tmp_path, opencode_client=client, ai_ask=_section_selection_ai_ask)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dirs = [item for item in Path(tmp_path).iterdir() if item.is_dir()]
    assert len(project_dirs) == 1
    assert not (project_dirs[0] / "execution_prompt_ui_shell.md").exists()
    assert (project_dirs[0] / "execution_prompt.md").exists()  # the legacy adapter's own artifact name


# --- Docker-unavailable fail-closed behavior -----------------------------------


def test_docker_unavailable_fails_the_phase_with_a_clear_error(tmp_path):
    brief, handoff = _contract()

    def _raising_qa_runner(_qa_commands, _cwd):
        raise DockerUnavailableError("named pipe connection failed")

    adapter = _adapter(tmp_path, qa_runner=_raising_qa_runner)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.outcome == "docker_unavailable"
    assert "docker_engine_unreachable" in result.errors
    assert result.final_stage == ExecutionStage.UI_SHELL


# --- cancellation ---------------------------------------------------------------


def test_cancellation_before_execution_returns_a_cancelled_result(tmp_path):
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)
    cancellation = CancellationToken()
    cancellation.cancel()

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), cancellation)

    assert result.success is False
    assert result.outcome == "cancelled"
