from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from .brief_service import BriefServiceError, sanitize_public_text, verify_brief_approval
from .models import AgentHandoff, ElenaDesignChoice, ProjectBrief, new_public_id, utc_now


class AgentHandoffService:
    def __init__(
        self,
        *,
        id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def create_implementation_handoff(self, brief: ProjectBrief) -> AgentHandoff:
        if not verify_brief_approval(brief):
            raise BriefServiceError("brief_not_approved")
        if brief.open_questions:
            raise BriefServiceError("brief_has_open_questions")
        if brief.elena_design_choice is ElenaDesignChoice.UNDECIDED:
            raise BriefServiceError("elena_choice_required")
        context = f"{brief.goal} Intended users: {', '.join(brief.target_users)}."
        context = sanitize_public_text(context)[:1_000]
        requirements = tuple(sanitize_public_text(item) for item in brief.core_features)
        constraints = tuple(
            sanitize_public_text(item)
            for item in (*brief.technical_constraints, *brief.assumptions, *brief.non_goals)
        )
        acceptance = tuple(sanitize_public_text(item) for item in brief.acceptance_criteria)
        return AgentHandoff(
            id=new_public_id("handoff", self._id_factory),
            order_id=brief.order_id,
            brief_id=brief.id,
            source_agent="alex",
            target_agent="codex",
            goal=sanitize_public_text(brief.goal),
            context_summary=context,
            requirements=requirements,
            constraints=constraints,
            acceptance_criteria=acceptance,
            artifacts=(f"{brief.id}:r{brief.revision}",),
            open_questions=(),
            requested_action="implement",
            created_at=utc_now(self._clock),
        )
