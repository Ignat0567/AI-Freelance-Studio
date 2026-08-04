from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    ClarificationAnswer,
    DesignPreviewService,
    ExecutionRequest,
    ProductionProjectExecutionAdapter,
    ProjectBriefService,
    QuestionType,
    UserOrder,
    UserOrderStatus,
)
from order_workflow.brief_service import _authoritative_spec_features, _looks_like_authoritative_spec

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)

LONG_UNSTRUCTURED_TEXT = (
    "This application should let a small team manage their daily work without much fuss, "
    "covering tasks, notes, and a simple calendar, while staying fast and easy to use. "
) * 20  # well over 2,000 chars, zero numbered headings

STRUCTURED_SPEC = (
    "Technical specification for \"Keeper\" — a desktop PDF assistant.\n"
    "1. Product summary\n"
    "Keeper answers questions strictly from uploaded PDF documents, citing the exact page "
    "and highlighted passage for every answer, and explicitly states when no answer exists.\n"
    "2. Architecture\n"
    "Electron main process plus a local backend sidecar; secrets live only in the OS keychain "
    "via keytar, never on disk or in logs.\n"
    "3. Voice pipeline\n"
    "Offline speech-to-text via whisper.cpp is the default engine; native OS text-to-speech "
    "is used for playback.\n"
    "4. Updates\n"
    "electron-updater checks for new releases and never discards the local document library "
    "or history during an update.\n"
    "5. Design tokens\n"
    "Panel background #14181C, accent highlighter #E1A940, warning color #C1554B.\n"
    "13. Absolutely forbidden\n"
    "Confident answers with no supporting citation are absolutely forbidden — bounding box "
    "coordinates must back every highlighted quote.\n"
)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"authspec-{self.index:04d}"


def _clock():
    return NOW + timedelta(seconds=1)


def _answer_for(question):
    if question.recommended_answer is not None:
        return question.recommended_answer
    if question.type is QuestionType.LONG_TEXT:
        return "Upload PDFs, ask grounded questions, and get exact page citations for every answer."
    if question.type is QuestionType.SHORT_TEXT:
        return "Knowledge workers"
    if question.type is QuestionType.BOOLEAN:
        return False
    if question.type is QuestionType.SINGLE_SELECT:
        return question.options[0] if question.options else ""
    if question.type is QuestionType.MULTI_SELECT:
        return list(question.options[:1]) if question.options else []
    return ""


def _brief_for(description: str):
    ids = SequenceIds()
    order = UserOrder(
        id="order_authspec",
        title="Authoritative spec test",
        description=description,
        product_type="web_app",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    result = clarification.use_recommended_defaults(started.order, started.session)
    for _ in range(10):
        if result.order.status is UserOrderStatus.BRIEF_READY:
            break
        answered_ids = {answer.question_id for answer in result.order.answers}
        pending = tuple(q for q in result.order.questions if q.id not in answered_ids)
        if not pending:
            break
        answers = tuple(ClarificationAnswer(question_id=q.id, value=_answer_for(q)) for q in pending)
        result = clarification.apply_answers(result.order, result.session, answers)

    briefs = ProjectBriefService(id_factory=ids, clock=_clock)
    brief = briefs.generate(result.order, result.session)
    return brief, ids


def test_detects_long_structured_spec():
    assert _looks_like_authoritative_spec(STRUCTURED_SPEC * 3) is True


def test_rejects_short_description():
    assert _looks_like_authoritative_spec("Build me a small todo app.") is False


def test_rejects_long_unstructured_paragraph():
    assert len(LONG_UNSTRUCTURED_TEXT) > 2_000
    assert _looks_like_authoritative_spec(LONG_UNSTRUCTURED_TEXT) is False


def test_boundary_exactly_three_headings_counts():
    text = ("x" * 2_100) + "\n1. One\n2. Two\n3. Three\n"
    assert _looks_like_authoritative_spec(text) is True


def test_authoritative_spec_features_extracts_numbered_headings():
    features = _authoritative_spec_features(STRUCTURED_SPEC)

    assert features == (
        "Product summary",
        "Architecture",
        "Voice pipeline",
        "Updates",
        "Design tokens",
        "Absolutely forbidden",
    )


def test_authoritative_spec_features_falls_back_when_no_headings_survive():
    features = _authoritative_spec_features("no numbered headings here at all")
    assert features == ("no numbered headings here at all",)


def test_brief_generation_takes_authoritative_branch_not_pdf_template():
    long_spec = STRUCTURED_SPEC * 3
    assert _looks_like_authoritative_spec(long_spec) is True

    brief, _ids = _brief_for(long_spec)

    assert brief.goal != "A browser application for conversational search across uploaded PDF documents using text and voice."
    assert "keytar" in brief.goal
    assert "electron-updater" in brief.goal
    assert "#E1A940" in brief.goal
    assert "Product summary" in brief.core_features
    assert "Architecture" in brief.core_features
    assert brief.acceptance_criteria
    assert brief.non_goals == ()
    assert brief.technical_constraints == ()


def test_full_pipeline_prompt_carries_the_entire_spec(tmp_path: Path):
    long_spec = STRUCTURED_SPEC * 3
    brief, ids = _brief_for(long_spec)
    briefs = ProjectBriefService(id_factory=ids, clock=_clock)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    design = DesignPreviewService(id_factory=ids, clock=_clock)
    preview = design.approve(design.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=_clock).create_implementation_handoff(brief, preview)

    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    package = adapter.prepare_execution(ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_authspec"))

    for marker in ("keytar", "electron-updater", "#E1A940", "whisper.cpp", "bounding box", "Absolutely forbidden"):
        assert marker.lower() in package.prompt.lower(), f"missing marker: {marker!r}"
