from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    CancellationToken,
    ExecutionRequest,
    OpenCodeExecutionResult,
    DesignPreviewService,
    ProjectBriefService,
    ReadinessResult,
    UserOrder,
)
from order_workflow.midbuild_clarification import (
    SHELL_CORRECTIONS_QUESTION_ID,
    build_midbuild_questions,
    corrections_from_answers,
    midbuild_clarification_enabled,
)
from order_workflow.models import (
    ClarificationAnswer,
    ElenaDesignChoice,
    ExecutionArtifact,
    ExecutionStage,
    EventKind,
    ProjectBrief,
)
from order_workflow.phase_prompts import build_core_feature_prompt
from order_workflow.phase_context import PhaseContext
from order_workflow.phased_adapter import PhasedLiveOpenCodeExecutionAdapter
from order_workflow.qa_runner import QACommandResult, QAOutcome

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
PDF = "Create a browser PDF voice assistant with upload, voice and text chat, grounded answers, page citations and speech playback."


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_mid",
        "order_id": "order_mid",
        "goal": "Track daily habits in the browser.",
        "target_users": ("Single local user",),
        "core_features": ("Add a habit and mark it done",),
        "acceptance_criteria": ("A user can add a habit.",),
        "assumptions": ("The first version is for single-user local usage.",),
        "elena_design_choice": ElenaDesignChoice.PROCEED_DIRECTLY,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProjectBrief(**values)


# --- the gate is off unless asked for ------------------------------------------------


def test_midbuild_clarification_is_off_unless_explicitly_enabled():
    # An unattended run -- CI, a scripted demo, an overnight batch -- must never stop
    # halfway waiting for a human who is not there.
    assert midbuild_clarification_enabled({}) is False
    assert midbuild_clarification_enabled({"FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION": "0"}) is False
    assert midbuild_clarification_enabled({"FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION": "1"}) is True


# --- what gets asked -------------------------------------------------------------------


def test_questions_are_grounded_in_the_briefs_own_assumptions():
    questions = build_midbuild_questions(_brief(), shell_summary="3 screens")

    assumption_questions = [q for q in questions if q.id.startswith("midbuild-assumption-")]
    assert len(assumption_questions) == 1
    assert "single-user local usage" in assumption_questions[0].text
    assert assumption_questions[0].required is False  # never blocks on an unanswered nicety


def test_a_brief_with_no_assumptions_asks_nothing():
    # A checkpoint that always fires, even with nothing to say, trains the client to click
    # through it without reading.
    assert build_midbuild_questions(_brief(assumptions=())) == ()


def test_the_open_correction_question_mentions_what_was_actually_built():
    questions = build_midbuild_questions(_brief(), shell_summary="a dashboard with 3 screens")

    open_question = [q for q in questions if q.id == SHELL_CORRECTIONS_QUESTION_ID]
    assert len(open_question) == 1
    assert "dashboard with 3 screens" in open_question[0].text


def test_only_a_bounded_number_of_assumptions_is_surfaced():
    questions = build_midbuild_questions(_brief(assumptions=tuple(f"Assumption {i}" for i in range(9))))

    assert len([q for q in questions if q.id.startswith("midbuild-assumption-")]) == 3


# --- what the answers turn into ---------------------------------------------------------


def test_confirming_the_build_produces_no_corrections():
    # The free-text question is optional, and ClarificationAnswer rejects a blank value at
    # the model level, so "nothing to change" arrives as an omitted answer.
    questions = build_midbuild_questions(_brief(), shell_summary="3 screens")
    answers = (ClarificationAnswer(question_id="midbuild-assumption-1", value="Keep it as assumed"),)

    # Forwarding "no change" to the coding CLI as if it were an instruction is noise it has
    # to interpret.
    assert corrections_from_answers(questions, answers) == ()


def test_free_text_becomes_an_explicit_correction():
    questions = build_midbuild_questions(_brief(), shell_summary="3 screens")
    answers = (ClarificationAnswer(question_id=SHELL_CORRECTIONS_QUESTION_ID, value="Move the streak above the fold"),)

    corrections = corrections_from_answers(questions, answers)

    assert len(corrections) == 1
    assert "Move the streak above the fold" in corrections[0]


def test_rejecting_an_assumption_names_the_assumption_itself():
    questions = build_midbuild_questions(_brief(), shell_summary="3 screens")
    answers = (ClarificationAnswer(question_id="midbuild-assumption-1", value="Change it"),)

    corrections = corrections_from_answers(questions, answers)

    # "Change it" alone tells the coding CLI nothing; the decision now in question has to
    # travel with it.
    assert len(corrections) == 1
    assert "single-user local usage" in corrections[0]


def test_answers_to_questions_that_were_never_asked_are_ignored():
    questions = build_midbuild_questions(_brief(), shell_summary="3 screens")
    answers = (ClarificationAnswer(question_id="midbuild-assumption-99", value="Change it"),)

    assert corrections_from_answers(questions, answers) == ()


def test_corrections_reach_the_core_feature_prompt_ahead_of_the_feature():
    from order_workflow.models import AgentHandoff

    handoff = AgentHandoff(
        id="handoff_mid", order_id="order_mid", brief_id="brief_mid", source_agent="alex", target_agent="codex",
        goal="g", context_summary="c", requirements=("Add a habit",), acceptance_criteria=("A user can add a habit.",),
        created_at=NOW,
    )
    context = PhaseContext(phase="ui_shell", summary="3 screens", files=(), qa_status="passed")

    prompt = build_core_feature_prompt(_brief(), handoff, context, corrections=("Move the streak above the fold",))

    assert "Move the streak above the fold" in prompt
    assert prompt.index("Move the streak above the fold") < prompt.index("Now wire in exactly ONE central feature")


# --- the pause itself --------------------------------------------------------------------


def _contract():
    order = UserOrder(id="order_phased", title="Phased App", description=PDF, product_type="web_app", created_at=NOW, updated_at=NOW)
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(clock=lambda: NOW + timedelta(seconds=1))
    brief = briefs.generate(result.order, result.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    # This brief asks for an Elena concept, so the handoff will not be issued without an
    # approved design preview.
    design = DesignPreviewService(clock=lambda: NOW + timedelta(seconds=1))
    preview = design.approve(design.generate(brief), brief)
    handoff = AgentHandoffService(clock=lambda: NOW + timedelta(seconds=1)).create_implementation_handoff(brief, preview)
    return brief, handoff


class FakeClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.prompts: list[str] = []

    def check_readiness(self):
        return ReadinessResult.ready_result()

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.call_count += 1
        self.prompts.append(prompt)
        return OpenCodeExecutionResult(success=True, summary="generated")


def _passing_qa(_commands, _cwd):
    return QAOutcome(passed=True, results=(QACommandResult(command="x", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),))


class FakeSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, *, stage, agent, progress, message, level=None, details=(), kind=EventKind.ACTIVITY):
        self.events.append({"stage": stage, "agent": agent, "message": message})

    def artifact(self, *, kind, name, summary, reference):
        return ExecutionArtifact(id="artifact_fixed001", execution_id="execution_fixed001", kind=kind, name=name, summary=summary, reference=reference, created_at=NOW)


def _adapter(tmp_path, client, *, midbuild: bool):
    return PhasedLiveOpenCodeExecutionAdapter(
        provider_name="opencode_bridge",
        model_name="openai/gpt-5.5",
        workspace_root=tmp_path,
        opencode_client=client,
        qa_runner=_passing_qa,
        smoke_check_runner=_passing_qa,
        visual_check_runner=_passing_qa,
        state_check_runner=_passing_qa,
        ai_ask=lambda _p: "overview",
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        midbuild_clarification=midbuild,
    )


def test_the_run_pauses_after_the_ui_shell_when_the_gate_is_on(tmp_path):
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=True)

    result = adapter.execute(ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00001"), FakeSink(), CancellationToken())

    assert result.outcome == "awaiting_user"
    assert result.questions, "the pause has to carry the questions with it"
    assert result.final_stage is ExecutionStage.UI_SHELL
    assert client.call_count == 1, "the core feature must not have been built yet"


def test_the_run_does_not_pause_when_the_gate_is_off(tmp_path):
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=False)

    result = adapter.execute(ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00002"), FakeSink(), CancellationToken())

    assert result.outcome != "awaiting_user"
    assert client.call_count >= 2


def test_a_resumed_run_does_not_pause_again_and_carries_the_correction(tmp_path):
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=True)
    request = ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00003")

    first = adapter.execute(request, FakeSink(), CancellationToken())
    assert first.outcome == "awaiting_user"
    asked = first.questions

    resumed = adapter.execute(
        ExecutionRequest(
            brief=brief,
            handoff=handoff,
            execution_id="execution_mid00003",
            midbuild_answers=(ClarificationAnswer(question_id=asked[-1].id, value="Put the upload button in the header"),),
        ),
        FakeSink(),
        CancellationToken(),
    )

    assert resumed.outcome != "awaiting_user"
    core_feature_prompt = next(p for p in client.prompts if "Now wire in exactly ONE central feature" in p)
    assert "Put the upload button in the header" in core_feature_prompt


def test_resuming_reuses_the_ui_shell_checkpoint_instead_of_rebuilding_it(tmp_path):
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=True)
    request = ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00004")

    adapter.execute(request, FakeSink(), CancellationToken())
    calls_after_pause = client.call_count

    sink = FakeSink()
    adapter.execute(
        ExecutionRequest(
            brief=brief, handoff=handoff, execution_id="execution_mid00004",
            midbuild_answers=(ClarificationAnswer(question_id="midbuild-assumption-1", value="Keep it as assumed"),),
        ),
        sink,
        CancellationToken(),
    )

    assert calls_after_pause == 1
    assert any("Resuming: ui_shell already completed" in e["message"] for e in sink.events)


# --- who gets asked, and who must never be ------------------------------------------


def test_an_attended_run_checks_in_without_needing_the_env_flag():
    """The pause was built as a quality mechanism and switched off because an unattended run
    must never block on a human. That reason does not apply when a person is watching -- and
    the checkpoint's other effect is that a client who corrected the real shell accepts the
    result they helped choose."""
    from order_workflow.executors import ExecutionRequest

    assert ExecutionRequest.__dataclass_fields__["attended"].default is False


def test_the_browser_client_declares_itself_attended():
    """The chain is only worth anything if the UI actually sets it: nothing downstream can
    infer attendance, because a bench run and a client's run hit the same endpoint."""
    from pathlib import Path

    source = Path("frontend/src/features/order-workflow/orderWorkflowApi.js").read_text(encoding="utf-8")

    assert "attended: true" in source


def test_automated_callers_stay_unattended_by_default():
    """The bench runner and the demo scripts post to the same endpoint. If the default
    flipped, an overnight set would stop at the first checkpoint and wait until morning."""
    from order_workflow.api_models import StartExecutionRequest

    assert StartExecutionRequest(mode="production", live=True).attended is False


def test_the_bench_runner_does_not_ask_to_be_attended():
    """What the benchmark actually sends. This used to assert that the word "attended" did
    not occur anywhere in run_bench.py, which a comment about *unattended* runs was enough to
    break -- and which would have passed just as happily on `"attended": False` spelled with
    a different key."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("bench/run_bench.py").read_text(encoding="utf-8"))
    payloads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "request"
        and len(node.args) >= 3
        and isinstance(node.args[2], ast.Dict)
    ]
    assert payloads, "the runner no longer posts a literal execution payload; check this test still measures something"
    for call in payloads:
        keys = [key.value for key in call.args[2].keys if isinstance(key, ast.Constant)]
        assert "attended" not in keys


def test_an_attended_request_pauses_even_with_the_env_gate_off(tmp_path):
    """The behaviour the whole chain exists for: a person pressed the button, so the run
    checks in with them, without anyone having set an environment variable first."""
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=False)

    result = adapter.execute(
        ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00010", attended=True),
        FakeSink(),
        CancellationToken(),
    )

    assert result.outcome == "awaiting_user"
    assert result.questions
    assert client.call_count == 1, "the core feature must not have been built before asking"


def test_an_unattended_request_still_runs_straight_through(tmp_path):
    """An overnight bench set must not stop at the first checkpoint and wait until morning."""
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=False)

    result = adapter.execute(
        ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00011", attended=False),
        FakeSink(),
        CancellationToken(),
    )

    assert result.outcome != "awaiting_user"
    assert client.call_count >= 2


def test_a_resumed_attended_run_does_not_ask_twice(tmp_path):
    """Resuming carries the answers; asking again would make the checkpoint a loop."""
    brief, handoff = _contract()
    client = FakeClient()
    adapter = _adapter(tmp_path, client, midbuild=False)
    first = adapter.execute(
        ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_mid00012", attended=True),
        FakeSink(),
        CancellationToken(),
    )
    assert first.outcome == "awaiting_user"

    resumed = adapter.execute(
        ExecutionRequest(
            brief=brief,
            handoff=handoff,
            execution_id="execution_mid00012",
            attended=True,
            midbuild_answers=(ClarificationAnswer(question_id=first.questions[0].id, value="yes"),),
        ),
        FakeSink(),
        CancellationToken(),
    )

    assert resumed.outcome != "awaiting_user"


# --- the length trap that killed a 40-minute run --------------------------------------


def _brief_with_assumption(assumption: str):
    from datetime import datetime, timezone

    from order_workflow.models import ElenaDesignChoice, ProductType, ProjectBrief, RecommendedStack

    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    return ProjectBrief(
        id="brief_len",
        order_id="order_len",
        product_type=ProductType.WEB_APP,
        goal="A presentation site.",
        target_users=("A small internal team",),
        core_features=("Show the pipeline",),
        acceptance_criteria=("It renders",),
        assumptions=(assumption,),
        recommended_stack=RecommendedStack(),
        elena_design_choice=ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        created_at=now,
        updated_at=now,
    )


THE_ASSUMPTION_THAT_CRASHED_A_RUN = (
    'Your description says nobody signs in, but the audience was recorded as "A small internal '
    'team". A shared audience makes the build add a server and a database. Narrow it if this is '
    "for one person."
)


def test_a_maximum_length_assumption_still_produces_a_valid_question():
    """2026-08-21: a 196-character assumption became a 254-character question, ClarificationQuestion
    refused it, the ValidationError reached the execution service's catch-all, and a web_app run
    died after 40 minutes with a completed UI shell and a record that said only "internal error".

    Assumptions are ShortText, so the longest one is 240 -- and the wrapper adds ~51 on top."""
    from order_workflow.midbuild_clarification import build_midbuild_questions

    questions = build_midbuild_questions(_brief_with_assumption("x" * 240))

    assert questions
    assert all(len(question.text) <= 240 for question in questions)


def test_the_exact_assumption_from_the_failed_run_is_handled():
    from order_workflow.midbuild_clarification import build_midbuild_questions

    questions = build_midbuild_questions(_brief_with_assumption(THE_ASSUMPTION_THAT_CRASHED_A_RUN))

    assert questions
    assert all(len(question.text) <= 240 for question in questions)


def test_a_clipped_assumption_says_that_it_was_clipped():
    """Otherwise a truncated assumption reads as the whole of one, and the client confirms
    something narrower than what was actually assumed."""
    from order_workflow.midbuild_clarification import build_midbuild_questions

    # 200 characters: longer than the wrapper leaves room for, but still a legal assumption --
    # ShortText caps the field itself at 240, so a longer one could never reach here.
    questions = build_midbuild_questions(_brief_with_assumption(("word " * 40).strip()))

    assert "\u2026" in questions[0].text


def test_a_short_assumption_is_not_touched():
    from order_workflow.midbuild_clarification import build_midbuild_questions

    questions = build_midbuild_questions(_brief_with_assumption("Elena will prepare a design concept."))

    assert "Elena will prepare a design concept" in questions[0].text
    assert "\u2026" not in questions[0].text


def test_questions_are_built_even_when_the_pause_is_off():
    """The crash did not need the checkpoint enabled: build_core_feature_prompt calls this to
    compute corrections on every web_app run, so the length trap was on the main path, not
    behind an opt-in flag."""
    from pathlib import Path

    source = Path("order_workflow/phased_adapter.py").read_text(encoding="utf-8")
    core_feature_block = source.split("core_feature_checkpoint = _load_phase_checkpoint")[1][:1200]

    assert "build_midbuild_questions" in core_feature_block
