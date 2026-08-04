from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

import pytest

from order_workflow.api_models import CreateOrderRequest
from order_workflow.autopilot import generate_clarification_answer, run_order_to_handoff_automatically
from order_workflow.service import OrderWorkflowService

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)

# Resolves fully via use_recommended_defaults() alone -- no AI call is ever needed.
FULLY_DEFAULTABLE = "Build a small internal tool for tracking client feedback."
# core-features is LONG_TEXT with no recommended_answer -- always needs a real answer.
NEEDS_CORE_FEATURES_ANSWER = "Build a cinematic WebGL showcase website with scroll storytelling for a design agency."


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"autopilot-{self.index:04d}"


def _service() -> OrderWorkflowService:
    return OrderWorkflowService(id_factory=SequenceIds(), clock=lambda: NOW)


def _create_order(service: OrderWorkflowService, description: str) -> str:
    state = service.create_order(CreateOrderRequest(title="Test order", description=description, product_type="web_app"))
    return state["order"]["id"]


def test_fully_defaultable_order_reaches_handoff_without_any_ai_call():
    calls = []
    service = _service()
    order_id = _create_order(service, FULLY_DEFAULTABLE)

    final = run_order_to_handoff_automatically(service, order_id, ai_ask=lambda prompt: calls.append(prompt) or "unused")

    assert calls == []
    assert final["handoff_ready"] is True
    assert final["brief"] is not None
    assert final["approval"]["approved"] is True


def test_order_needing_core_features_uses_the_ai_answer_and_it_reaches_the_brief():
    calls = []
    answer_text = "Browse a gallery of past client work, view case studies with real metrics, and submit a project inquiry."

    def ai_ask(prompt: str) -> str:
        calls.append(prompt)
        if "essential actions" in prompt.lower():
            return answer_text
        return "A reasonable answer."

    service = _service()
    order_id = _create_order(service, NEEDS_CORE_FEATURES_ANSWER)

    final = run_order_to_handoff_automatically(service, order_id, ai_ask)

    assert len(calls) == 1  # exactly one clarification question needed an AI answer
    assert final["handoff_ready"] is True
    assert answer_text in final["brief"]["core_features"]


def test_run_order_to_handoff_works_when_a_human_already_answered_one_dimension():
    # Simulates a human pre-answering elena-design (opting out of the design
    # preview) before autopilot takes over the rest -- proves the approve_brief
    # -creates-handoff-directly code path (no design preview to approve).
    service = _service()
    order_id = _create_order(service, FULLY_DEFAULTABLE)
    service.answer(order_id, (("elena-design", "Proceed directly to implementation"),))

    final = run_order_to_handoff_automatically(service, order_id, ai_ask=lambda _prompt: "unused")

    assert final["design_preview_required"] is False
    assert final["handoff_ready"] is True
    assert final["design_preview"] is None


def test_run_order_to_handoff_approves_the_design_preview_when_required():
    service = _service()
    order_id = _create_order(service, FULLY_DEFAULTABLE)

    final = run_order_to_handoff_automatically(service, order_id, ai_ask=lambda _prompt: "unused")

    assert final["design_preview_required"] is True
    assert final["design_preview"] is not None
    assert final["design_preview"]["approved"] is True
    assert final["handoff_ready"] is True


def test_generate_clarification_answer_rejects_invalid_single_select_and_falls_back():
    question = {
        "id": "test-question",
        "text": "Which color?",
        "type": "single_select",
        "options": ["Red", "Green", "Blue"],
        "reason": "testing",
    }

    answer = generate_clarification_answer(question, "some order", ai_ask=lambda _prompt: "Purple (not a real option)")

    assert answer == "Red"  # falls back to the first real option, not the AI's invented one


def test_generate_clarification_answer_accepts_a_real_single_select_option():
    question = {
        "id": "test-question",
        "text": "Which color?",
        "type": "single_select",
        "options": ["Red", "Green", "Blue"],
        "reason": "testing",
    }

    answer = generate_clarification_answer(question, "some order", ai_ask=lambda _prompt: "Green")

    assert answer == "Green"


def test_generate_clarification_answer_never_raises_when_ai_ask_itself_raises():
    question = {"id": "q", "text": "Describe the core features.", "type": "long_text", "options": (), "reason": "testing"}

    def _boom(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    answer = generate_clarification_answer(question, "some order", ai_ask=_boom)

    assert answer  # a safe non-empty fallback, not a crash
