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


def classify_phase_complexity(brief: ProjectBrief, *, focus_text: str) -> PhaseComplexity:
    """Heuristic complexity classifier -- no extra AI call, reuses signal already present
    in the approved brief. `focus_text` is whatever this specific phase is actually about
    (e.g. the UI shell's goal/requirements, or the one core feature being wired in), not
    the whole brief, so a simple project with one complex feature still routes correctly.
    """
    text = focus_text.casefold()
    if any(keyword in text for keyword in _COMPLEXITY_KEYWORDS):
        return "complex"
    if len(brief.technical_constraints) >= 3 or len(brief.core_features) >= 5:
        return "complex"
    return "routine"


def model_for_complexity(complexity: PhaseComplexity) -> str:
    return MODEL_FOR_COMPLEXITY[complexity]
