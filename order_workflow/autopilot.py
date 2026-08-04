from __future__ import annotations

from collections.abc import Callable
from typing import Any

MAX_CLARIFICATION_ROUNDS = 10  # safety net; the mandatory-dimension set is small and finite

# A free-text clarification answer (e.g. core-features) can end up copied
# verbatim into a single ProjectBrief.core_features item, which is ShortText
# (max_length=240, order_workflow/models.py) -- cap here so a verbose AI
# answer never blows past a schema limit several layers downstream.
_MAX_TEXT_ANSWER_LENGTH = 240


def _build_answer_prompt(question: dict[str, Any], order_description: str) -> str:
    lines = [
        "A client submitted this project request:",
        order_description,
        "",
        "Answer this clarification question concisely and concretely, as the client would:",
        question["text"],
        f"Reason this is being asked: {question['reason']}",
    ]
    if question.get("options"):
        lines.append(f"Choose exactly one of these options, verbatim: {', '.join(question['options'])}")
    else:
        lines.append(f"Respond in one short sentence, no more than {_MAX_TEXT_ANSWER_LENGTH} characters.")
    lines.append("Respond with only the answer text, no explanation.")
    return "\n".join(lines)


def generate_clarification_answer(question: dict[str, Any], order_description: str, ai_ask: Callable[[str], str]) -> Any:
    """Ask once for a clarification answer and validate/coerce it by question type.

    Never raises: an unusable AI response degrades to a safe default (the first
    real option, or an empty/false value) rather than blocking the whole run --
    unlike section selection's fail-closed design, a clarification answer always
    has a reasonable safe default to fall back to.
    """
    try:
        raw = ai_ask(_build_answer_prompt(question, order_description)).strip()
    except Exception:
        raw = ""

    question_type = question.get("type")
    options = tuple(question.get("options") or ())

    if question_type == "single_select":
        if raw in options:
            return raw
        return options[0] if options else ""
    if question_type == "multi_select":
        matched = [option for option in options if option.casefold() in raw.casefold()]
        return tuple(matched) if matched else (list(options[:1]) if options else [])
    if question_type == "boolean":
        lowered = raw.casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        return False
    # short_text / long_text
    text = raw or "As described in the project request."
    return text[:_MAX_TEXT_ANSWER_LENGTH]


def run_order_to_handoff_automatically(service, order_id: str, ai_ask: Callable[[str], str]) -> dict[str, Any]:
    """Drive an already-created order through OrderWorkflowService's own public
    methods -- clarification, brief generation/approval, design-preview approval
    -- until a handoff exists, with zero manual answer()/approve_*() calls.

    Only calls existing public service methods; no approval gate is bypassed or
    modified. Live execution (start_execution(..., live=True)) is deliberately
    NOT triggered here -- that remains a separate, explicit action.
    """
    snapshot = service.defaults(order_id)

    for _ in range(MAX_CLARIFICATION_ROUNDS):
        if snapshot["order"]["status"] != "clarification_required":
            break
        answered_ids = {item["question_id"] for item in snapshot["answers"]}
        pending = [item for item in snapshot["questions"] if item["id"] not in answered_ids]
        if not pending:
            break
        order_description = snapshot["order"]["description"]
        answers = tuple((item["id"], generate_clarification_answer(item, order_description, ai_ask)) for item in pending)
        snapshot = service.answer(order_id, answers)

    if snapshot["brief"] is None:
        snapshot = service.generate_brief(order_id)

    if snapshot["approval"] is None or not snapshot["approval"].get("approved"):
        brief = snapshot["brief"]
        snapshot = service.approve_brief(order_id, revision=brief["revision"], fingerprint=None)

    if snapshot["design_preview_required"] and not snapshot["handoff_ready"]:
        preview = snapshot["design_preview"]
        snapshot = service.approve_design_preview(order_id, preview_id=preview["preview_id"], brief_version=preview["brief_version"])

    return snapshot
