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
    TokenUsage,
    UserOrder,
)
from order_workflow.claude_code_client import CLAUDE_CODE_REPAIR_TIMEOUT
from order_workflow.docker_qa_runner import DockerUnavailableError, run_qa_commands_in_docker
from order_workflow.models import ExecutionStage, EventKind
from order_workflow.phased_adapter import (
    CORE_FEATURE_QA_COMMANDS,
    MAX_PHASE_REPAIR_ATTEMPTS,
    PhasedLiveOpenCodeExecutionAdapter,
    ReviseProjectExecutionAdapter,
    UI_SHELL_QA_COMMANDS,
    _select_qa_runner,
    resolve_execution_pipeline_mode,
)
from order_workflow.qa_runner import QACommandResult, QAOutcome, run_qa_commands
from order_workflow.workspace import reserve_owned_project_workspace

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)
PDF = "Create a browser PDF voice assistant with upload, voice and text chat, grounded answers, page citations and speech playback."
CINEMATIC = "Create a cinematic showcase website with webgl scroll storytelling for a digital agency."


def _shared_audience(brief):
    """A brief whose approved audience is more than one person, so decide_backend_need
    resolves to "backend needed" from the brief alone. This is how tests reach the third
    bridging phase now that the gate no longer consults a model.

    Set on the brief rather than in the order description on purpose: the PDF description
    these tests build from takes brief_service's fixed-spec branch, which rewrites the goal
    and feature list and would drop any extra sentence added here.
    """
    return brief.model_copy(update={"target_users": ("A small internal team",)})


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

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
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


def _safe_prose_ai_ask(_prompt: str) -> str:
    """Never touches the network -- used as the general-purpose ai_ask in every test
    so the cinematic-website legacy-delegation path can never make a real API call."""
    return "A generated project overview paragraph."


def _adapter(tmp_path, *, opencode_client=None, qa_runner=None, ai_ask=None, environ=None, smoke_check_runner=None, visual_check_runner=None, state_check_runner=None) -> PhasedLiveOpenCodeExecutionAdapter:
    return PhasedLiveOpenCodeExecutionAdapter(
        provider_name="opencode_bridge",
        model_name="openai/gpt-5.5",
        workspace_root=tmp_path,
        opencode_client=opencode_client or FakePhaseOpenCodeClient(),
        qa_runner=qa_runner or _passing_qa,
        ai_ask=ai_ask or _safe_prose_ai_ask,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1", **(environ or {})},
        # Never a real Docker/Playwright call in a unit test by default -- the functional
        # smoke check and the visual check each get their own dedicated coverage further
        # down. Without the visual runner injected, the adapter would build the real
        # Docker-backed one from the brief's approved palette and every test would hit it.
        smoke_check_runner=smoke_check_runner or _passing_qa,
        visual_check_runner=visual_check_runner or _passing_qa,
        state_check_runner=state_check_runner or _passing_qa,
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
    brief = _shared_audience(brief)
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)
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


class RateLimitedOpenCodeClient:
    """A coding client that fails the very first call the way Claude Code CLI's own
    JSON payload does on a real 429: is_error, plus usage/cost for the call that hit
    the limit, plus the CLI's own human-readable reset message."""

    def check_readiness(self):
        return ReadinessResult.ready_result()

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
        return OpenCodeExecutionResult(
            success=False,
            summary="Claude Code execution failed: You've hit your session limit",
            errors=("claude_code_process_failed",),
            usage=TokenUsage(total_cost_usd=1.53, input_tokens=2249, output_tokens=32640, cache_read_input_tokens=489115, cache_creation_input_tokens=45938),
            rate_limit_message="You've hit your session limit · resets 3:40pm (Europe/Berlin)",
        )


def test_a_rate_limited_phase_failure_carries_usage_and_the_reset_message(tmp_path):
    # Regression: found live -- a real 429 during the ui_shell phase reported
    # rate_limit_message: null even though Claude Code CLI's own JSON payload had it,
    # because _phase_failure() never threaded usage/rate_limit_message through from the
    # OpenCodeExecutionResult, unlike the (non-phased) LiveOpenCodeExecutionAdapter path.
    brief, handoff = _contract()
    adapter = _adapter(tmp_path, opencode_client=RateLimitedOpenCodeClient())

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.rate_limit_message == "You've hit your session limit · resets 3:40pm (Europe/Berlin)"
    assert result.usage is not None
    assert result.usage.total_cost_usd == pytest.approx(1.53)
    assert result.usage.output_tokens == 32640


def test_core_feature_phase_never_runs_if_ui_shell_fails(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=_failing_qa)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert client.call_count == 1 + MAX_PHASE_REPAIR_ATTEMPTS  # 1 initial + one re-prompt per attempt, all within UI_SHELL


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


class TimingOutRepairClient(FakePhaseOpenCodeClient):
    """Every repair call is stopped by its own clock; the first build call succeeds.

    Mirrors a real run: the ui_shell build finished, a gate failed, and the repair call
    was killed at its wall-clock limit with nothing captured on either stream.
    """

    def __init__(self, *, timeout_on_repairs: int = 99) -> None:
        super().__init__()
        self.timeout_on_repairs = timeout_on_repairs
        self.repair_calls = 0
        self.repair_timeouts: list[int | None] = []

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
        # A per-call budget is what marks a repair: phase builds are sent without one and
        # take the client's own default.
        if timeout is not None:
            self.repair_calls += 1
            self.call_count += 1
            self.repair_timeouts.append(timeout)
            if self.repair_calls <= self.timeout_on_repairs:
                return OpenCodeExecutionResult(
                    success=False,
                    summary="Claude Code execution timed out after 450s.",
                    errors=("claude_code_execution_timeout",),
                    timed_out=True,
                )
        return super().execute_project_prompt(prompt, workspace_path, event_sink, cancellation, model=model)


def test_a_timed_out_repair_still_spends_the_remaining_attempt(tmp_path):
    # Regression, found on a live run: the repair loop treated *any* unsuccessful fix call
    # as terminal and broke out, so a repair killed by its own timeout silently consumed
    # the phase's whole repair budget after one attempt. Running out of clock says nothing
    # about whether the code is fixable, so the second attempt has to still happen.
    brief, handoff = _contract()
    client = TimingOutRepairClient(timeout_on_repairs=1)
    qa_runner = ScriptedQARunner([
        # fail -> repair 1 times out -> the re-check below still fails -> repair 2 lands
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),)),
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="still broken", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="npm run build", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    sink = FakeEventSink()
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=qa_runner)

    result = adapter.execute(_request(brief, handoff), sink, CancellationToken())

    messages = [event["message"] for event in sink.events]
    assert "The repair call ran out of time and was stopped before it finished." in messages
    assert f"QA failed; asking Codex to fix (attempt 2 of {MAX_PHASE_REPAIR_ATTEMPTS})" in messages
    assert result.success is True
    # The shorter repair budget actually reaches the client, rather than the build's.
    assert client.repair_timeouts == [CLAUDE_CODE_REPAIR_TIMEOUT, CLAUDE_CODE_REPAIR_TIMEOUT]


def test_a_timed_out_repair_that_already_landed_its_fix_is_not_repeated(tmp_path):
    # The CLI writes files through tool calls as it works, so a call killed by its clock can
    # leave a complete fix behind. Re-asking the gate costs seconds; spending the remaining
    # repair attempt costs the whole budget and starts from a state nobody has checked.
    brief, handoff = _contract()
    client = TimingOutRepairClient()  # every repair call times out
    qa_runner = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="npm run build", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    sink = FakeEventSink()
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=qa_runner)

    result = adapter.execute(_request(brief, handoff), sink, CancellationToken())

    assert result.success is True
    assert client.repair_calls == 1  # the second attempt was never needed
    assert f"QA failed; asking Codex to fix (attempt 2 of {MAX_PHASE_REPAIR_ATTEMPTS})" not in [event["message"] for event in sink.events]


def test_a_repair_timeout_is_reported_instead_of_blaming_the_gate(tmp_path):
    # The failure summary used to read as a verdict on the code ("QA failed after 1 repair
    # attempt(s)") when what actually happened was that the repair never finished. A person
    # reading that went looking at the project instead of at the provider call.
    brief, handoff = _contract()
    client = TimingOutRepairClient()
    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=_failing_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert "stopped by its time limit" in result.summary
    assert client.repair_calls == MAX_PHASE_REPAIR_ATTEMPTS  # every attempt spent, not just the first


def test_a_successful_run_records_the_gates_and_repairs_it_actually_needed(tmp_path):
    # Regression on measurability, not behaviour: test_summary used to be a placeholder
    # (skipped=1 on success, failed=1 on failure), so five archived transcripts all reported
    # repair_attempts: 0 while their event streams showed one and two repairs. The number the
    # MVP criterion is measured in had to be counted by hand out of prose.
    brief, handoff = _contract()
    qa_runner = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="npm run build", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    adapter = _adapter(tmp_path, opencode_client=FakePhaseOpenCodeClient(), qa_runner=qa_runner)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert result.test_summary.repair_attempts == 1
    assert result.test_summary.passed >= 1
    assert result.test_summary.failed == 0


def test_a_failed_run_still_reports_the_gates_it_got_through(tmp_path):
    # "Failed at the last gate after two repairs, having passed the earlier ones" and
    # "failed" are different facts, and yield is made of the difference.
    brief, handoff = _contract()
    adapter = _adapter(tmp_path, opencode_client=FakePhaseOpenCodeClient(), qa_runner=_failing_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.test_summary.failed >= 1
    assert result.test_summary.repair_attempts == MAX_PHASE_REPAIR_ATTEMPTS


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


def test_unclassifiable_audience_fails_closed_to_needing_a_backend(tmp_path):
    brief, handoff = _contract()
    brief = brief.model_copy(update={"target_users": ("Whoever my cousin invites",)})
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert client.call_count == 3  # bridge phase ran


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


# --- functional smoke check (QA depth level (a)) --------------------------------


def test_smoke_check_failure_fails_the_phase_after_exhausting_repair(tmp_path):
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client, smoke_check_runner=_failing_qa)

    result = adapter.execute(_request(*_contract()), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.outcome == "qa_failed"
    assert "functional_smoke_check_failed" in result.errors
    assert result.final_stage == ExecutionStage.UI_SHELL


def test_smoke_check_failure_goes_through_the_repair_loop_before_giving_up(tmp_path):
    client = FakePhaseOpenCodeClient()
    smoke_runner = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="smoke check", exit_code=1, stdout_tail="no interactive elements found", stderr_tail="", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="smoke check", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    adapter = _adapter(tmp_path, opencode_client=client, smoke_check_runner=smoke_runner)

    result = adapter.execute(_request(*_contract()), FakeEventSink(), CancellationToken())

    assert result.success is True
    # 2 calls for ui_shell's repair cycle (fail, then pass) + 1 more for core_feature's own
    # smoke check, which also runs and gets the scripted runner's last (passing) outcome.
    assert smoke_runner.calls == 3
    # the repair attempt re-prompted the coding client with the smoke-check failure details
    assert any("no interactive elements found" in prompt for prompt in client.prompts)


def test_smoke_check_is_skipped_not_failed_when_docker_is_unavailable(tmp_path):
    def _unavailable(_qa_commands, _cwd):
        raise DockerUnavailableError("no docker daemon")

    client = FakePhaseOpenCodeClient()
    sink = FakeEventSink()
    adapter = _adapter(tmp_path, opencode_client=client, smoke_check_runner=_unavailable)

    result = adapter.execute(_request(*_contract()), sink, CancellationToken())

    assert result.success is True
    assert any("Functional smoke check skipped" in event["message"] for event in sink.events)


# --- phase checkpoint / resume ---------------------------------------------------


class FlakyOnceClient(FakePhaseOpenCodeClient):
    """Declines exactly once, the first time call_count reaches `fail_at_call`, then
    succeeds on every later call -- models a transient failure (rate limit, timeout)
    that clears by the time a retry happens, without needing to predict exactly how
    many client calls a whole phase (including QA-repair re-prompts) will consume."""

    def __init__(self, *, fail_at_call: int) -> None:
        super().__init__()
        self._fail_at_call = fail_at_call
        self._already_failed = False

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None, timeout=None):
        self.call_count += 1
        self.prompts.append(prompt)
        Path(workspace_path, f"call-{self.call_count}-marker.txt").write_text("generated", encoding="utf-8")
        if self.call_count == self._fail_at_call and not self._already_failed:
            self._already_failed = True
            return OpenCodeExecutionResult(success=False, summary="Simulated transient failure.")
        return OpenCodeExecutionResult(success=True, summary="generated")


def test_resume_skips_a_completed_ui_shell_phase_after_core_feature_fails(tmp_path):
    brief, handoff = _contract()
    client = FlakyOnceClient(fail_at_call=2)  # call 1 = ui_shell (ok), call 2 = core_feature (fails once)
    adapter = _adapter(tmp_path, opencode_client=client)
    request = _request(brief, handoff)

    first = adapter.execute(request, FakeEventSink(), CancellationToken())
    assert first.success is False
    assert first.final_stage == ExecutionStage.CORE_FEATURE
    assert client.call_count == 2

    second_sink = FakeEventSink()
    second = adapter.execute(request, second_sink, CancellationToken())

    assert second.success is True
    # ui_shell was never re-invoked on the coding client -- only core_feature (call 3) ran again.
    assert client.call_count == 3
    assert any("Resuming: ui_shell already completed" in event["message"] for event in second_sink.events)


def test_resume_skips_ui_shell_and_core_feature_after_backend_bridge_fails(tmp_path):
    brief, handoff = _contract()
    brief = _shared_audience(brief)
    client = FlakyOnceClient(fail_at_call=3)  # 1=ui_shell, 2=core_feature, 3=backend bridge (fails once)
    adapter = _adapter(tmp_path, opencode_client=client)
    request = _request(brief, handoff)

    first = adapter.execute(request, FakeEventSink(), CancellationToken())
    assert first.success is False
    assert first.final_stage == ExecutionStage.IMPLEMENTATION
    assert client.call_count == 3

    second_sink = FakeEventSink()
    second = adapter.execute(request, second_sink, CancellationToken())

    assert second.success is True
    # only the backend-bridge phase (call 4) re-ran; ui_shell and core_feature were skipped.
    assert client.call_count == 4
    messages = [event["message"] for event in second_sink.events]
    assert any("Resuming: ui_shell already completed" in m for m in messages)
    assert any("Resuming: core_feature already completed" in m for m in messages)
    assert any("Resuming: reusing the backend-need decision" in m for m in messages)


def test_a_brand_new_execution_is_unaffected_by_checkpointing(tmp_path):
    # First-time runs must behave exactly as before: no checkpoint files exist yet, so
    # every phase runs normally and none of the "Resuming" messages ever appear.
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)
    sink = FakeEventSink()

    result = adapter.execute(_request(brief, handoff), sink, CancellationToken())

    assert result.success is True
    assert client.call_count == 2  # ui_shell + core_feature, no backend needed
    assert not any("Resuming" in event["message"] for event in sink.events)


def test_corrupt_checkpoint_file_is_ignored_and_the_phase_reruns(tmp_path):
    # A checkpoint that fails to parse must never crash or silently corrupt the run --
    # it degrades to "no checkpoint", exactly like a first-time execution.
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _adapter(tmp_path, opencode_client=client)
    request = _request(brief, handoff)

    workspace = reserve_owned_project_workspace(tmp_path, order_id=brief.order_id, execution_id=request.execution_id, brief_fingerprint=brief.approval_fingerprint, title="")
    (workspace.project_path / ".freelancerstudio-checkpoint-ui_shell.json").write_text("not valid json", encoding="utf-8")

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is True
    assert client.call_count == 2  # ui_shell really ran (corrupt checkpoint was ignored), plus core_feature


# --- revision (Level 1: revise an already-delivered project) --------------------


def _revision_adapter(tmp_path, *, opencode_client=None, qa_runner=None, ai_ask=None, environ=None, smoke_check_runner=None, state_check_runner=None) -> ReviseProjectExecutionAdapter:
    return ReviseProjectExecutionAdapter(
        provider_name="opencode_bridge",
        model_name="openai/gpt-5.5",
        workspace_root=tmp_path,
        opencode_client=opencode_client or FakePhaseOpenCodeClient(),
        qa_runner=qa_runner or _passing_qa,
        ai_ask=ai_ask or _safe_prose_ai_ask,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1", **(environ or {})},
        # A revision is gated like a first build, so both browser checks have to be faked
        # here too or every revision test reaches real Docker.
        smoke_check_runner=smoke_check_runner or _passing_qa,
        state_check_runner=state_check_runner or _passing_qa,
    )


def _revision_request(brief, handoff, *, revision_note="Add a dark mode toggle", revised_from_execution_id="execution_original0001") -> ExecutionRequest:
    return ExecutionRequest(
        brief=brief,
        handoff=handoff,
        execution_id="execution_revision0001",
        revision_note=revision_note,
        revised_from_execution_id=revised_from_execution_id,
    )


def test_revision_reuses_the_original_executions_workspace_not_a_new_one(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _revision_adapter(tmp_path, opencode_client=client)
    request = _revision_request(brief, handoff)

    # Simulate the original, already-delivered execution's workspace existing beforehand.
    original_workspace = reserve_owned_project_workspace(tmp_path, order_id=brief.order_id, execution_id=request.revised_from_execution_id, brief_fingerprint=brief.approval_fingerprint, title="")
    (original_workspace.project_path / "package.json").write_text("{}", encoding="utf-8")

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is True
    # the coding client actually wrote into the ORIGINAL directory, not a new one
    assert (original_workspace.project_path / "call-1-marker.txt").is_file()
    # the pre-existing file from "before the revision" is still there, proving this
    # operated on the SAME directory rather than reserving a brand-new one.
    assert (original_workspace.project_path / "package.json").is_file()
    # a fresh workspace keyed off the NEW execution_id must never have been created
    assert not (tmp_path / f"order_phased-{request.execution_id}").exists()


def test_revision_prompt_includes_the_revision_note_and_original_goal(tmp_path):
    brief, handoff = _contract()
    client = FakePhaseOpenCodeClient()
    adapter = _revision_adapter(tmp_path, opencode_client=client)
    request = _revision_request(brief, handoff, revision_note="Add a dark mode toggle to the settings screen")

    adapter.execute(request, FakeEventSink(), CancellationToken())

    assert len(client.prompts) >= 1
    assert "Add a dark mode toggle to the settings screen" in client.prompts[0]
    assert "already exists, fully built" in client.prompts[0]


def test_revision_fails_cleanly_without_a_revision_note(tmp_path):
    brief, handoff = _contract()
    adapter = _revision_adapter(tmp_path)
    request = _revision_request(brief, handoff, revision_note=None)

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is False
    assert "revision_request_incomplete" in result.errors


def test_revision_fails_cleanly_without_a_source_execution_id(tmp_path):
    brief, handoff = _contract()
    adapter = _revision_adapter(tmp_path)
    request = _revision_request(brief, handoff, revised_from_execution_id=None)

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is False
    assert "revision_request_incomplete" in result.errors


def test_revision_runs_the_functional_smoke_check_too(tmp_path):
    brief, handoff = _contract()
    adapter = _revision_adapter(tmp_path, smoke_check_runner=_failing_qa)
    request = _revision_request(brief, handoff)
    reserve_owned_project_workspace(tmp_path, order_id=brief.order_id, execution_id=request.revised_from_execution_id, brief_fingerprint=brief.approval_fingerprint, title="")

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is False
    assert "functional_smoke_check_failed" in result.errors


def test_revision_success_finalizes_with_readme_and_architecture_artifacts(tmp_path):
    brief, handoff = _contract()
    adapter = _revision_adapter(tmp_path)
    request = _revision_request(brief, handoff)
    workspace = reserve_owned_project_workspace(tmp_path, order_id=brief.order_id, execution_id=request.revised_from_execution_id, brief_fingerprint=brief.approval_fingerprint, title="")

    result = adapter.execute(request, FakeEventSink(), CancellationToken())

    assert result.success is True
    assert (workspace.project_path / "README.md").is_file()
    assert (workspace.project_path / "ARCHITECTURE.md").is_file()
    assert (workspace.project_path / "delivery_report.md").is_file()


def test_delivery_report_reflects_what_this_run_actually_needed(tmp_path):
    """Integration point for delivery_report.py: the report a real run writes must be built
    from that run's own gate log, not a generic template."""
    brief, handoff = _contract()
    qa_runner = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="npm run build", exit_code=1, stdout_tail="", stderr_tail="broken", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="npm run build", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),)),
    ])
    adapter = _adapter(tmp_path, opencode_client=FakePhaseOpenCodeClient(), qa_runner=qa_runner)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    workspace_root = next(tmp_path.rglob("delivery_report.md"))
    report = workspace_root.read_text(encoding="utf-8")
    assert "## What was built" in report
    assert "## What we found and fixed" in report
    assert "1 repair attempt." in report
    assert "## Proof it runs" in report
    assert "## How to run it" in report


def test_the_delivered_folder_carries_the_checks_own_output(tmp_path):
    """delivery_report.md claims the checks passed; qa_evidence.md is what they printed while
    passing. The gap between those two is the difference between a freelancer's word and
    this pipeline's actual selling point."""
    brief, handoff = _contract()

    def _measuring_qa(_qa_commands, _cwd):
        return QAOutcome(passed=True, results=(
            QACommandResult(command="visual check", exit_code=0,
                            stdout_tail="Palette: 4/4 approved colours painted (100%).", stderr_tail="", duration=0.1),
        ))

    adapter = _adapter(tmp_path, opencode_client=FakePhaseOpenCodeClient(), qa_runner=_measuring_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    evidence = next(tmp_path.rglob("qa_evidence.md")).read_text(encoding="utf-8")
    assert "Palette: 4/4 approved colours painted (100%)." in evidence
    report = next(tmp_path.rglob("delivery_report.md")).read_text(encoding="utf-8")
    assert "qa_evidence.md" in report


def test_a_visual_gate_repair_reaches_the_delivered_report_and_evidence(tmp_path):
    """The regression this file exists to prevent. On the 2026-08-18 live run the visual gate
    demanded a repair and the delivered report mentioned only the backend: three of the four
    gates fed their repair counts into the totals without a label, so they produced no line in
    the report and no section in qa_evidence.md. The client was told less than had happened,
    and the evidence file omitted the palette and contrast numbers -- the most convincing
    measurements the pipeline takes."""
    brief, handoff = _contract()
    visual = ScriptedQARunner([
        QAOutcome(passed=False, results=(QACommandResult(command="visual check", exit_code=1, stdout_tail="Palette: 1/4 approved colours painted (25%).", stderr_tail="", duration=0.1),)),
        QAOutcome(passed=True, results=(QACommandResult(command="visual check", exit_code=0, stdout_tail="Palette: 4/4 approved colours painted (100%).", stderr_tail="", duration=0.1),)),
    ])
    adapter = _adapter(tmp_path, opencode_client=FakePhaseOpenCodeClient(), visual_check_runner=visual)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    report = next(tmp_path.rglob("delivery_report.md")).read_text(encoding="utf-8")
    assert "design and accessibility check" in report
    assert "1 repair attempt." in report

    evidence = next(tmp_path.rglob("qa_evidence.md")).read_text(encoding="utf-8")
    assert "Palette: 4/4 approved colours painted (100%)." in evidence


# --- what the delivered documents say the delivery is -------------------------


def test_the_readme_is_titled_with_the_order_not_the_workspace_folder(tmp_path):
    """Every README in the archive opens with
    `# Reading-Journal-order_8b9f37c8-...-execution_26462dbb-...` -- the internal folder
    name, two UUIDs, as the first line a client reads."""
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)

    adapter.execute(
        ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_test0001", title="Reading Journal"),
        FakeEventSink(),
        CancellationToken(),
    )

    project_dir = [item for item in Path(tmp_path).iterdir() if item.is_dir()][0]
    readme = (project_dir / "README.md").read_text(encoding="utf-8")
    assert readme.splitlines()[0] == "# Reading Journal"
    assert "execution_test0001" not in readme.splitlines()[0]


def test_an_order_with_no_title_still_gets_a_heading(tmp_path):
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dir = [item for item in Path(tmp_path).iterdir() if item.is_dir()][0]
    readme = (project_dir / "README.md").read_text(encoding="utf-8")
    assert readme.splitlines()[0].startswith("# ")


def test_a_delivery_with_no_backend_does_not_claim_a_server_and_a_database(tmp_path):
    """The web-app brief recommends React + Vite / FastAPI / SQLite before the run starts.
    The backend-decision phase then concludes no backend is needed -- as it did in every
    archived web_app run -- and the delivery is a static bundle in a `serve` container. The
    README named a server and a database the folder does not contain."""
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dir = [item for item in Path(tmp_path).iterdir() if item.is_dir()][0]
    readme = (project_dir / "README.md").read_text(encoding="utf-8")
    stack = readme.split("## Tech Stack", 1)[1].split("##", 1)[0]
    assert "FastAPI" not in stack
    assert "SQLite" not in stack
    assert "Backend: none" in stack
    # The half that was true stays: the frontend is what got built.
    assert brief.recommended_stack.frontend in stack


def test_the_delivered_list_names_the_source_the_client_paid_for(tmp_path):
    """The reading journal's client read "Delivered: delivery_screenshot.png,
    design-tokens.css, Dockerfile, index.html, package-lock.json, package.json and 2 more":
    a screenshot and a lockfile by name, with the fourteen source files inside "2 more".
    Directories were skipped entirely, because the list was files only."""
    brief, handoff = _contract()
    adapter = _adapter(tmp_path)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dir = [item for item in Path(tmp_path).iterdir() if item.is_dir()][0]
    (project_dir / "src").mkdir(exist_ok=True)
    from order_workflow.phased_adapter import _delivered_files

    class _Workspace:
        project_path = project_dir

    (project_dir / "src" / "App.jsx").write_text("export default () => null;", encoding="utf-8")
    (project_dir / "node_modules").mkdir(exist_ok=True)
    (project_dir / "node_modules" / "junk.js").write_text("//", encoding="utf-8")

    listed = _delivered_files(_Workspace())

    assert "src/ (1 file)" in listed
    assert listed[0].endswith(")")  # directories first: they are the substance
    assert not any(name.startswith("node_modules") for name in listed)


def test_features_do_not_repeat_the_overview_paragraph(tmp_path):
    """An order written as one paragraph yields a single "core feature" that is the goal,
    cut to fit its field -- so the README's Features section repeated its own Overview,
    ending mid-word in an ellipsis. The delivery report has dropped that bullet since
    2026-08-27; the README kept printing it."""
    brief, handoff = _contract()
    goal_as_requirement = " ".join(brief.goal.split())[:200] + "…"
    handoff = handoff.model_copy(update={"requirements": (goal_as_requirement,)})
    adapter = _adapter(tmp_path)

    adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    project_dir = [item for item in Path(tmp_path).iterdir() if item.is_dir()][0]
    readme = (project_dir / "README.md").read_text(encoding="utf-8")
    features = readme.split("## Features", 1)[1].split("##", 1)[0]

    assert "…" not in features
    assert goal_as_requirement not in features
