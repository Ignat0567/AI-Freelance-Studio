from __future__ import annotations

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
)


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
    text = focus_text.casefold()
    matched = [keyword for keyword in _COMPLEXITY_KEYWORDS if keyword in text]
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
