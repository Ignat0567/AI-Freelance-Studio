"""Ask the client about corrections *while* the build is happening, not only before it.

Clarification (`clarification.py`) runs entirely before the brief exists, capped at three
rounds, against a client who has nothing to look at yet. Anything they could not picture in
the abstract got resolved by a recommended default and recorded as an assumption -- and the
first time they see the consequence is when the finished project lands.

This module opens one checkpoint at the UI-shell boundary: the screens exist and build
cleanly, nothing downstream has been wired to them yet, so a correction here is cheap. The
questions are grounded in two real sources, never invented:

  * the assumptions the brief recorded -- decisions the client never actually made
  * an open door for anything the shell got wrong that no question anticipated

Deliberately not a model call. "Which of your own assumptions should the client confirm"
is a lookup, not a judgement, and a model asked to invent questions about a project it just
built produces plausible filler.

The gate is opt-in (FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION=1). An unattended run --
CI, a scripted demo, an overnight batch -- must never stop halfway waiting for a human who
is not there.
"""

from __future__ import annotations

import os

from .models import ClarificationAnswer, ClarificationQuestion, ProjectBrief, QuestionType

MIDBUILD_QUESTION_PREFIX = "midbuild-"
SHELL_CORRECTIONS_QUESTION_ID = f"{MIDBUILD_QUESTION_PREFIX}shell-corrections"
_ASSUMPTION_QUESTION_LIMIT = 3
_KEEP = "Keep it as assumed"
_CHANGE = "Change it"


def midbuild_clarification_enabled(environ: dict[str, str] | None = None) -> bool:
    return (environ or os.environ).get("FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION", "").strip() == "1"


# ClarificationQuestion.text is ShortText (240). An assumption is ShortText too, so a long
# one plus the wrapper below overflows -- and the wrapper is what pushes it over, so the
# wrapper is what has to make room. A whole web_app run died here after 40 minutes and a
# completed UI shell: an assumption of 196 characters became a 254-character question, the
# ValidationError reached the execution service's catch-all, and the record said only
# "internal error". Checking that assumptions fit their own field was not enough; nothing
# checked that they still fit once another component wrapped them in a sentence.
_QUESTION_WRAPPER_CHARS = len("The shell was built assuming: . Is that still right?")
_MAX_ASSUMPTION_CHARS = 240 - _QUESTION_WRAPPER_CHARS


def _fits_in_question(assumption: str) -> str:
    text = assumption.strip().rstrip(".")
    if len(text) <= _MAX_ASSUMPTION_CHARS:
        return text
    # Cut at a word boundary so the question stays readable, and mark the cut so nobody
    # mistakes a truncated assumption for the whole of one.
    clipped = text[: _MAX_ASSUMPTION_CHARS - 1].rsplit(" ", 1)[0]
    return f"{clipped}…"


def build_midbuild_questions(brief: ProjectBrief, *, shell_summary: str = "") -> tuple[ClarificationQuestion, ...]:
    """One checkpoint's worth of questions, derived from the approved brief.

    Returns () when there is nothing honest to ask, which the caller treats as "do not
    pause" -- a checkpoint that always fires, even with nothing to say, trains the client
    to click through it.
    """
    questions: list[ClarificationQuestion] = []

    for index, assumption in enumerate(brief.assumptions[:_ASSUMPTION_QUESTION_LIMIT]):
        text = _fits_in_question(assumption)
        if not text:
            continue
        questions.append(
            ClarificationQuestion(
                id=f"{MIDBUILD_QUESTION_PREFIX}assumption-{index + 1}",
                text=f"The shell was built assuming: {text}. Is that still right?",
                type=QuestionType.SINGLE_SELECT,
                options=(_KEEP, _CHANGE),
                required=False,
                reason="This was filled in with a recommended default during clarification, not chosen by you.",
                recommended_answer=_KEEP,
            )
        )

    if not questions:
        # Nothing was assumed on this brief, so there is no silent decision to surface and
        # no reason to interrupt.
        return ()

    built = f" The shell now has: {shell_summary.strip()}." if shell_summary.strip() else ""
    questions.append(
        ClarificationQuestion(
            id=SHELL_CORRECTIONS_QUESTION_ID,
            text=(
                "Anything to correct in the screens before the core feature is wired in?"
                f"{built} Leave blank to continue as built."
            )[:240],
            type=QuestionType.LONG_TEXT,
            required=False,
            reason="Changing the shell now is cheaper than changing it after the feature is built on top of it.",
        )
    )
    return tuple(questions)


def corrections_from_answers(
    questions: tuple[ClarificationQuestion, ...],
    answers: tuple[ClarificationAnswer, ...],
) -> tuple[str, ...]:
    """Turn the answers into instruction lines for the next phase's prompt.

    Only answers that actually ask for something survive: "keep it as assumed" and an empty
    free-text box are the client confirming the build so far, and forwarding those to the
    coding CLI as if they were instructions would be noise it has to interpret.
    """
    by_id = {question.id: question for question in questions}
    corrections: list[str] = []
    for answer in answers:
        question = by_id.get(answer.question_id)
        if question is None:
            continue
        value = answer.value
        if isinstance(value, bool):
            continue
        if isinstance(value, tuple):
            value = ", ".join(str(item) for item in value)
        text = str(value).strip()
        if not text or text == _KEEP:
            continue
        if question.id == SHELL_CORRECTIONS_QUESTION_ID:
            corrections.append(f"The client asks for this correction to the existing screens: {text}")
        elif text == _CHANGE:
            # "Change it" with no replacement named -- surface the assumption itself so the
            # coding CLI knows which decision is now open rather than guessing.
            assumption = question.text.split("assuming:", 1)[-1].rsplit(". Is that", 1)[0].strip()
            corrections.append(f"The client rejected this assumption and wants it revisited: {assumption}")
        else:
            corrections.append(f"{question.text} -> {text}")
    return tuple(corrections)
