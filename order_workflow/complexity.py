from __future__ import annotations

import re
from typing import Literal

from .models import ProjectBrief

PhaseComplexity = Literal["routine", "complex"]

# Model alias passed to `claude --model` per complexity tier. Deliberately a plain
# constant, not a config file: this is a starting default, not something meant to be
# hand-edited per project yet -- see the roles.yaml discussion this adapted from.
MODEL_FOR_COMPLEXITY: dict[PhaseComplexity, str] = {
    "routine": "sonnet",
    "complex": "opus",
}

_COMPLEXITY_KEYWORDS = (
    "real-time", "realtime", "websocket", "webhook", "payment", "authentication",
    "third-party", "integration", "concurrent", "multi-user", "queue", "streaming",
    "recommendation", "machine learning", "encryption", "oauth", "synchronization",
    "offline", "notification", "geolocation",
    # Real-time graphics, added when ProductType.STATIC_PAGE arrived: a hand-written WebGL
    # scene with its own animation loop, custom shaders and per-frame budget is harder work
    # than a page of components, and none of the words above appear in an order that asks
    # for one. Without these, a Three.js scene routed to the cheap model while a to-do list
    # mentioning "notification" routed to the expensive one.
    "webgl", "three.js", "shader", "3d scene", "volumetric",
)


# A keyword that is being *excluded* is not evidence of difficulty. An order saying "no
# backend, no database, no authentication" contains the word "authentication" and used to
# route its phase to the expensive model for saying it did not want the thing -- observed
# live on 2026-09-10, on an order whose whole point was that it had no accounts.
#
# The scan below is deliberately narrow, because the two mistakes do not cost the same. A
# missed negation overpays for one phase. A wrongly-detected negation sends genuinely hard
# work to the cheap model, which is how a repair loop gets paid for instead. So a negator
# only counts when it sits within a couple of words of the keyword, in the same clause.
#
# Two words, not four: "no icons, no illustrations, and authentication throughout" reaches a
# "no" at four but it belongs to the illustrations. Every real way of excluding a thing --
# "no authentication", "no user authentication", "without authentication", "doesn't need
# authentication" -- lands inside two.
_NEGATORS = frozenset({
    "no", "not", "without", "never", "zero", "none", "neither", "nor", "excluding", "except", "sans",
})
# Scanning backwards stops here: whatever was negated before "but" is not what follows it.
_CLAUSE_BREAKS = frozenset({"but", "however", "although", "though", "whereas", "yet"})
_NEGATION_WINDOW_WORDS = 2
_SENTENCE_BOUNDARIES = ".;:!?\n"
_WORD = re.compile(r"[a-z']+")


def occurrence_is_negated(text: str, start: int) -> bool:
    """Is this keyword occurrence cancelled by a negator just in front of it?

    `text` is already casefolded and `start` is the index the keyword was found at.
    """
    prefix = text[:start]
    cut = max((prefix.rfind(char) for char in _SENTENCE_BOUNDARIES), default=-1)
    words = _WORD.findall(prefix[cut + 1:])
    for word in reversed(words[-_NEGATION_WINDOW_WORDS:]):
        if word in _CLAUSE_BREAKS:
            return False
        if word in _NEGATORS or word.endswith("n't"):
            return True
    return False


def matched_complexity_keywords(text: str) -> tuple[str, ...]:
    """Keywords present in `text` in the affirmative -- negated mentions do not count.

    A keyword counts on its first un-negated occurrence: "no authentication yet, but
    authentication is planned" is about authentication.
    """
    matched = []
    for keyword in _COMPLEXITY_KEYWORDS:
        start = text.find(keyword)
        while start != -1:
            if not occurrence_is_negated(text, start):
                matched.append(keyword)
                break
            start = text.find(keyword, start + 1)
    return tuple(matched)


def substantive_technical_constraints(brief: ProjectBrief) -> tuple[str, ...]:
    """Constraints that say something about *this project*, not about the default stack.

    brief_service injects the recommended stack as technical constraints on every generic
    web app ("React + Vite frontend", "FastAPI backend where required", "SQLite local
    storage"). Those describe the pipeline's own scaffolding choice and carry no signal
    about how hard the work is, so counting them as evidence of complexity made the
    threshold below fire on literally every web_app brief -- including a single static
    page -- and the classifier always answered "complex".
    """
    stack = brief.recommended_stack
    stack_terms = [term.casefold() for term in (stack.frontend, stack.backend, stack.storage) if term]
    substantive = []
    for constraint in brief.technical_constraints:
        text = constraint.casefold()
        if any(term in text for term in stack_terms):
            continue
        substantive.append(constraint)
    return tuple(substantive)


def describe_phase_complexity(brief: ProjectBrief, *, focus_text: str) -> tuple[PhaseComplexity, str]:
    """Classify, and say why. The reason is surfaced in the execution event stream so a
    routing decision can be audited from the outside instead of being taken on trust.
    """
    matched = matched_complexity_keywords(focus_text.casefold())
    if matched:
        return "complex", f"matched {', '.join(repr(word) for word in matched[:3])}"

    substantive = substantive_technical_constraints(brief)
    if len(substantive) >= 3:
        return "complex", f"{len(substantive)} project-specific technical constraints"
    if len(brief.core_features) >= 5:
        return "complex", f"{len(brief.core_features)} core features"
    return "routine", "no complexity keywords, few constraints and features"


def classify_phase_complexity(brief: ProjectBrief, *, focus_text: str) -> PhaseComplexity:
    """Heuristic complexity classifier -- no extra AI call, reuses signal already present
    in the approved brief. `focus_text` is whatever this specific phase is actually about
    (e.g. the UI shell's goal/requirements, or the one core feature being wired in), not
    the whole brief, so a simple project with one complex feature still routes correctly.
    """
    return describe_phase_complexity(brief, focus_text=focus_text)[0]


def model_for_complexity(complexity: PhaseComplexity) -> str:
    return MODEL_FOR_COMPLEXITY[complexity]
