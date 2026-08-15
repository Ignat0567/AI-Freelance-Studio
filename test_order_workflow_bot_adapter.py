from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    CancellationToken,
    ExecutionRequest,
    OpenCodeExecutionResult,
    ProjectBriefService,
    ReadinessResult,
    UserOrder,
)
from order_workflow.models import ExecutionStage, EventKind
from order_workflow.phase_prompts import build_bot_prompt
from order_workflow.phased_adapter import BOT_QA_COMMANDS, TelegramBotExecutionAdapter
from order_workflow.qa_runner import QACommandResult, QAOutcome

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
BOT_DESCRIPTION = (
    "Build a Telegram bot for tracking daily habits: a user can add a habit, mark it done "
    "for today with one command, and see their current streak for each habit."
)


def _clock():
    return NOW + timedelta(seconds=1)


def _bot_contract():
    order = UserOrder(id="order_bot", title="Habit Bot", description=BOT_DESCRIPTION, product_type="bot", created_at=NOW, updated_at=NOW)
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(clock=_clock)
    brief = briefs.generate(result.order, result.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    # No DesignPreviewService involved -- a bot's elena_design_choice resolves to
    # NOT_APPLICABLE, so create_implementation_handoff() never needs one.
    handoff = AgentHandoffService(clock=_clock).create_implementation_handoff(brief)
    return brief, handoff


class FakeBotOpenCodeClient:
    def __init__(self, *, fail=False) -> None:
        self.fail = fail
        self.prompts: list[str] = []
        self.call_count = 0

    def check_readiness(self):
        return ReadinessResult.ready_result()

    def execute_project_prompt(self, prompt, workspace_path, event_sink, cancellation, model=None):
        self.call_count += 1
        self.prompts.append(prompt)
        Path(workspace_path, "bot.py").write_text("import os\n", encoding="utf-8")
        Path(workspace_path, "requirements.txt").write_text("python-telegram-bot\n", encoding="utf-8")
        if self.fail:
            return OpenCodeExecutionResult(success=False, summary="OpenCode declined the request")
        return OpenCodeExecutionResult(success=True, summary="generated")


def _passing_qa(_qa_commands, _cwd):
    return QAOutcome(passed=True, results=(QACommandResult(command="python -c \"import bot\"", exit_code=0, stdout_tail="ok", stderr_tail="", duration=0.1),))


def _failing_qa(_qa_commands, _cwd):
    return QAOutcome(passed=False, results=(QACommandResult(command="python -c \"import bot\"", exit_code=1, stdout_tail="", stderr_tail="ImportError", duration=0.1),))


class FakeEventSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, *, stage, agent, progress, message, level=None, details=(), kind=EventKind.ACTIVITY):
        self.events.append({"stage": stage, "agent": agent, "progress": progress, "message": message})

    def artifact(self, *, kind, name, summary, reference):
        from order_workflow.models import ExecutionArtifact

        return ExecutionArtifact(id="artifact_fixed001", execution_id="execution_fixed001", kind=kind, name=name, summary=summary, reference=reference, created_at=NOW)


def _safe_prose_ai_ask(_prompt: str) -> str:
    return "A generated project overview paragraph."


def _adapter(tmp_path, *, opencode_client=None, qa_runner=None, smoke_check_runner=None) -> TelegramBotExecutionAdapter:
    return TelegramBotExecutionAdapter(
        provider_name="opencode_bridge",
        model_name="openai/gpt-5.5",
        workspace_root=tmp_path,
        opencode_client=opencode_client or FakeBotOpenCodeClient(),
        qa_runner=qa_runner or _passing_qa,
        ai_ask=_safe_prose_ai_ask,
        environ={"FREELANCERSTUDIO_ENABLE_LIVE_OPENCODE_EXECUTION": "1"},
        # Never a real Docker/Playwright call in a unit test.
        smoke_check_runner=smoke_check_runner or _passing_qa,
        # Mirrors the real construction in service.py -- without this, self.qa_commands
        # falls back to the base class's ("npm test",) default and the README's "Setup /
        # Run" section lies about how to run a Python bot.
        qa_commands=BOT_QA_COMMANDS,
    )


def _request(brief, handoff) -> ExecutionRequest:
    return ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_bot0001")


def test_bot_prompt_forbids_import_time_telegram_connections():
    brief, handoff = _bot_contract()

    prompt = build_bot_prompt(brief, handoff)

    assert "python-telegram-bot" in prompt
    assert "bot.py" in prompt
    assert "requirements.txt" in prompt
    assert "__main__" in prompt
    assert "must NOT attempt to contact Telegram" in prompt
    assert "never a real token" in prompt.casefold()


def test_bot_build_succeeds_with_a_single_phase_and_bot_qa_commands(tmp_path):
    brief, handoff = _bot_contract()
    client = FakeBotOpenCodeClient()
    qa_seen: list[tuple] = []

    def _recording_qa(qa_commands, cwd):
        qa_seen.append(qa_commands)
        return _passing_qa(qa_commands, cwd)

    adapter = _adapter(tmp_path, opencode_client=client, qa_runner=_recording_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert client.call_count == 1
    assert "python-telegram-bot" in client.prompts[0]
    assert qa_seen[0] == BOT_QA_COMMANDS


def test_bot_build_never_runs_the_functional_smoke_check(tmp_path):
    smoke_calls = []

    def _tracking_smoke_check(qa_commands, cwd):
        smoke_calls.append(qa_commands)
        return _passing_qa(qa_commands, cwd)

    brief, handoff = _bot_contract()
    adapter = _adapter(tmp_path, smoke_check_runner=_tracking_smoke_check)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    assert smoke_calls == []  # Playwright/browser check never applies to a bot


def test_bot_build_qa_failure_fails_at_bot_build_stage(tmp_path):
    brief, handoff = _bot_contract()
    adapter = _adapter(tmp_path, qa_runner=_failing_qa)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.final_stage == ExecutionStage.BOT_BUILD


def test_bot_build_opencode_failure_is_reported(tmp_path):
    brief, handoff = _bot_contract()
    adapter = _adapter(tmp_path, opencode_client=FakeBotOpenCodeClient(fail=True))

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is False
    assert result.final_stage == ExecutionStage.BOT_BUILD


def test_bot_build_finalizes_with_readme_and_architecture(tmp_path):
    brief, handoff = _bot_contract()
    adapter = _adapter(tmp_path)

    result = adapter.execute(_request(brief, handoff), FakeEventSink(), CancellationToken())

    assert result.success is True
    from order_workflow.workspace import reserve_owned_project_workspace

    workspace = reserve_owned_project_workspace(tmp_path, order_id=brief.order_id, execution_id="execution_bot0001", brief_fingerprint=brief.approval_fingerprint, title="")
    assert (workspace.project_path / "README.md").is_file()
    assert (workspace.project_path / "ARCHITECTURE.md").is_file()
    assert (workspace.project_path / "bot.py").is_file()
    readme_text = (workspace.project_path / "README.md").read_text(encoding="utf-8")
    assert "npm test" not in readme_text
    assert "pip install -r requirements.txt" in readme_text
