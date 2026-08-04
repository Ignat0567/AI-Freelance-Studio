from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import Enum
import hashlib
import re
from typing import Literal
from uuid import uuid4

from pydantic import model_validator

from .clarification import (
    ClarificationSession,
    RequirementDimension,
    infer_requirement_signals,
)
from .models import (
    ElenaDesignChoice,
    ElenaDesignConcept,
    ProjectBrief,
    RecommendedStack,
    StrictDomainModel,
    ThemePalette,
    UserOrder,
    UserOrderStatus,
    new_public_id,
    utc_now,
)


class BriefServiceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BriefRevisionKind(str, Enum):
    ADD_REQUIREMENT = "add_requirement"
    REMOVE_REQUIREMENT = "remove_requirement"
    CHANGE_ASSUMPTION = "change_assumption"
    CHANGE_ACCEPTANCE_CRITERION = "change_acceptance_criterion"
    CHANGE_ELENA_CHOICE = "change_elena_choice"


class BriefRevisionOperation(StrictDomainModel):
    kind: BriefRevisionKind
    value: str | None = None
    previous_value: str | None = None
    elena_choice: ElenaDesignChoice | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "BriefRevisionOperation":
        if self.kind is BriefRevisionKind.CHANGE_ELENA_CHOICE:
            if self.elena_choice is None or self.value is not None or self.previous_value is not None:
                raise ValueError("Elena revision requires only elena_choice")
            return self
        if self.elena_choice is not None:
            raise ValueError("revision value is invalid")
        if self.kind is BriefRevisionKind.ADD_REQUIREMENT:
            valid = bool(self.value) and self.previous_value is None
        elif self.kind is BriefRevisionKind.REMOVE_REQUIREMENT:
            valid = self.value is None and bool(self.previous_value)
        else:
            valid = bool(self.value) and bool(self.previous_value)
        if not valid or any(len(item.strip()) > 2_000 for item in (self.value, self.previous_value) if item):
            raise ValueError("revision value is invalid")
        return self


class BriefRevisionRequest(StrictDomainModel):
    operations: tuple[BriefRevisionOperation, ...]

    @model_validator(mode="after")
    def validate_operations(self) -> "BriefRevisionRequest":
        if not self.operations or len(self.operations) > 20:
            raise ValueError("revision must contain between 1 and 20 operations")
        return self


class BriefRevisionRecord(StrictDomainModel):
    previous_brief_id: str
    revised_brief_id: str
    revision: int
    changes: tuple[str, ...]
    created_at: datetime


class BriefApprovalBinding(StrictDomainModel):
    brief_id: str
    revision: int
    fingerprint: str
    prepared_at: datetime
    decision: Literal["approve"] = "approve"


_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|token|password|secret)\s*[:=]\s*\S+"
    r"|\bauthorization\s*:\s*bearer\s+\S+|\bbearer\s+[A-Za-z0-9._-]{12,}"
    r"|\bsk-[A-Za-z0-9_-]{12,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|https?://[^\s/:]+:[^\s/@]+@[^\s]+"
)


def sanitize_public_text(value: str) -> str:
    return _SECRET_PATTERN.sub("[redacted]", str(value)).strip()


def _reject_secret(value: str) -> str:
    normalized = str(value).strip()
    if _SECRET_PATTERN.search(normalized):
        raise BriefServiceError("secret_like_content_rejected")
    return normalized


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in values if item and item.strip()))


def _answers(order: UserOrder) -> dict[str, object]:
    return {answer.question_id: answer.value for answer in order.answers}


def _target_users(order: UserOrder, target_signal: str | None) -> tuple[str, ...]:
    value = _answers(order).get("target-users") or target_signal or "Single local user"
    mapping = {
        "Only me": "Single local user",
        "My team": "A small internal team",
        "My customers": "Customers using the application",
        "Public users": "Public users",
    }
    return (mapping.get(str(value), sanitize_public_text(str(value))),)


def _pdf_features(signals) -> tuple[str, ...]:
    features = [
        "PDF upload",
        "Document processing status",
        "Document list",
        "Document removal",
        "Text extraction",
        "Document chunking and local indexing",
    ]
    if signals.grounded_answers:
        features.extend(("Document-grounded answers", "Honest no-answer response when information is absent"))
    if signals.citations:
        features.append("Document name and page citations")
    if signals.ocr_supported:
        features.append("OCR for scanned PDF documents")
    if signals.documents_persist:
        features.append("Persistent local document library")
    if "text" in signals.input_methods:
        features.append("Typed questions")
    if "voice" in signals.input_methods:
        features.extend(("Push-to-talk questions", "Visible recognized speech text"))
    if "speech" in signals.output_methods:
        features.extend(("Speech output", "Stop speech action"))
    features.append("Provider and browser capability readiness checks")
    return _unique(features)


def _generic_features(order: UserOrder) -> tuple[str, ...]:
    answer = _answers(order).get("core-features")
    if isinstance(answer, str) and answer.strip():
        return (sanitize_public_text(answer),)
    return (sanitize_public_text(order.description[:1_500]),)


def _pdf_acceptance(signals) -> tuple[str, ...]:
    criteria = [
        "A user can upload a text-based PDF.",
        "Document processing status is visible.",
        "Uploaded documents are listed and can be removed.",
    ]
    if "text" in signals.input_methods:
        criteria.append("A user can submit a typed question.")
    if signals.grounded_answers:
        criteria.extend(
            (
                "Answers are grounded only in uploaded documents.",
                "The assistant clearly states when the uploaded documents do not contain an answer.",
            )
        )
    if signals.citations:
        criteria.append("Each supported answer shows its document name and page reference.")
    if "voice" in signals.input_methods:
        criteria.extend(
            (
                "Push-to-talk voice input is available when the browser supports speech recognition.",
                "Recognized text is visible before or during question submission.",
            )
        )
    if "speech" in signals.output_methods:
        criteria.extend(("A user can have an answer spoken aloud.", "A user can stop speech playback."))
    criteria.extend(
        (
            "Provider or browser capability failures are shown as actionable blockers.",
            "Raw exceptions and stack traces are never shown to the user.",
        )
    )
    return _unique(criteria)


def _generic_acceptance(features: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"A user can complete this core action: {feature.rstrip('.')}." for feature in features)


def _brief_fingerprint(brief: ProjectBrief) -> str:
    return hashlib.sha256(brief.to_json().encode("utf-8")).hexdigest()


def verify_brief_approval(brief: ProjectBrief) -> bool:
    if (
        brief.approved_at is None
        or brief.approved_revision != brief.revision
        or brief.approval_fingerprint is None
    ):
        return False
    unapproved = brief.model_copy(
        update={
            "approved_at": None,
            "approved_revision": None,
            "approval_fingerprint": None,
        }
    )
    return _brief_fingerprint(unapproved) == brief.approval_fingerprint


def _elena_placeholder(choice: ElenaDesignChoice) -> ElenaDesignConcept | None:
    if choice is not ElenaDesignChoice.SHOW_ELENA_CONCEPT:
        return None
    return ElenaDesignConcept(
        visual_direction="Liquid Glass",
        layout="A focused responsive workspace with clear intake, content, action, and status regions.",
        screens=("Primary workflow", "Loading and empty states", "Actionable blocker state"),
        components=("Navigation", "Content workspace", "Primary actions", "Status feedback"),
        light_theme=ThemePalette(background="#eef4fb", surface="#ffffff", text="#172033", accent="#356cf6"),
        dark_theme=ThemePalette(background="#101725", surface="#182236", text="#f4f7ff", accent="#75a1ff"),
        accessibility_notes=(
            "Maintain accessible contrast and visible keyboard focus.",
            "Respect reduced-motion preferences and do not rely on color alone.",
        ),
    )


class ProjectBriefService:
    def __init__(
        self,
        *,
        id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def generate(self, order: UserOrder, session: ClarificationSession) -> ProjectBrief:
        if order.id != session.order_id:
            raise BriefServiceError("brief_order_mismatch")
        if order.status is not UserOrderStatus.BRIEF_READY:
            raise BriefServiceError("clarification_incomplete")
        mandatory = {
            RequirementDimension.TARGET_USERS,
            RequirementDimension.CORE_FEATURES,
            RequirementDimension.LANGUAGE_SUPPORT,
            RequirementDimension.DESIGN_PREFERENCE,
        }
        if any(item in mandatory for item in session.remaining_dimensions):
            raise BriefServiceError("mandatory_questions_unresolved")
        if session.elena_choice is ElenaDesignChoice.UNDECIDED:
            raise BriefServiceError("elena_choice_required")

        signals = infer_requirement_signals(order)
        features = _pdf_features(signals) if signals.pdf_documents else _generic_features(order)
        if signals.pdf_documents:
            goal = "A browser application for conversational search across uploaded PDF documents using text and voice."
            non_goals = [
                "Handwritten documents",
                "Word and Excel document support",
                "Continuous listening and wake words",
                "Voice cloning",
                "Multi-user accounts",
                "Cloud synchronization",
                "Mobile application",
                "Desktop packaging",
            ]
            if not signals.ocr_supported:
                non_goals.insert(0, "OCR and scanned documents without selectable text")
            technical = [
                "React + Vite browser frontend",
                "FastAPI backend",
                "Local PDF parsing, chunking, and vector indexing",
                "Provider abstraction for grounded LLM answers",
                "Browser speech recognition and speech synthesis for the first MVP",
            ]
            if signals.processing_mode == "Fully local":
                technical.append("Document content and answer generation remain on the local device")
            elif signals.processing_mode == "Cloud-assisted processing":
                technical.append("Cloud-assisted document processing requires an explicitly configured provider")
            else:
                technical.append("Only retrieved document fragments may be sent to an external LLM")
            if signals.speech_languages:
                technical.append(f"Speech recognition and synthesis locales: {', '.join(signals.speech_languages)}")
            ui = (
                "Show upload and document-processing state clearly",
                "Keep document citations visible with each answer",
                "Show recognized speech text and provide a stop-speech control",
                "Display actionable capability blockers without raw exceptions",
            )
            acceptance = _pdf_acceptance(signals)
        else:
            goal = sanitize_public_text(order.description[:1_500]) or f"A small browser application supporting {features[0].rstrip('.').casefold()}."
            non_goals = ("Features not explicitly included in the approved first version",)
            technical = ("React + Vite frontend", "FastAPI backend where required", "SQLite local storage")
            ui = ("Accessible browser interface with clear loading, error, and empty states",) if signals.ui_required else ()
            acceptance = _generic_acceptance(features)

        now = utc_now(self._clock)
        return ProjectBrief(
            id=new_public_id("brief", self._id_factory),
            order_id=order.id,
            goal=goal,
            target_users=_target_users(order, signals.target_users),
            core_features=features,
            assumptions=_unique(session.assumptions),
            non_goals=tuple(non_goals),
            technical_constraints=tuple(technical),
            ui_requirements=ui,
            acceptance_criteria=acceptance,
            open_questions=(),
            recommended_stack=RecommendedStack(frontend="React + Vite", backend="FastAPI", storage="SQLite"),
            elena_design_choice=session.elena_choice,
            elena_design_concept=_elena_placeholder(session.elena_choice),
            created_at=now,
            updated_at=now,
        )

    def revise(
        self,
        brief: ProjectBrief,
        request: BriefRevisionRequest,
    ) -> tuple[ProjectBrief, BriefRevisionRecord]:
        features = list(brief.core_features)
        assumptions = list(brief.assumptions)
        criteria = list(brief.acceptance_criteria)
        choice = brief.elena_design_choice
        changes: list[str] = []
        for operation in request.operations:
            value = _reject_secret(operation.value or "") if operation.value else ""
            previous = _reject_secret(operation.previous_value or "") if operation.previous_value else ""
            if operation.kind is BriefRevisionKind.ADD_REQUIREMENT:
                if value in features:
                    raise BriefServiceError("revision_duplicate_requirement")
                features.append(value)
                criteria.append(f"A user can use this approved capability: {value.rstrip('.')}.")
            elif operation.kind is BriefRevisionKind.REMOVE_REQUIREMENT:
                if previous not in features:
                    raise BriefServiceError("revision_target_not_found")
                features.remove(previous)
                generated_criterion = f"A user can use this approved capability: {previous.rstrip('.')}."
                criteria = [item for item in criteria if item != generated_criterion]
            elif operation.kind is BriefRevisionKind.CHANGE_ASSUMPTION:
                if previous not in assumptions:
                    raise BriefServiceError("revision_target_not_found")
                assumptions[assumptions.index(previous)] = value
            elif operation.kind is BriefRevisionKind.CHANGE_ACCEPTANCE_CRITERION:
                if previous not in criteria:
                    raise BriefServiceError("revision_target_not_found")
                criteria[criteria.index(previous)] = value
            elif operation.kind is BriefRevisionKind.CHANGE_ELENA_CHOICE:
                choice = operation.elena_choice
            else:
                raise BriefServiceError("unsupported_revision_operation")
            changes.append(operation.kind.value)

        if choice is ElenaDesignChoice.UNDECIDED:
            raise BriefServiceError("elena_choice_required")
        now = utc_now(self._clock)
        revised = ProjectBrief(
            **{
                **brief.to_dict(),
                "id": new_public_id("brief", self._id_factory),
                "revision": brief.revision + 1,
                "core_features": tuple(features),
                "assumptions": tuple(assumptions),
                "acceptance_criteria": tuple(criteria),
                "elena_design_choice": choice,
                "elena_design_concept": _elena_placeholder(choice),
                "approved_at": None,
                "approved_revision": None,
                "approval_fingerprint": None,
                "updated_at": now,
            }
        )
        record = BriefRevisionRecord(
            previous_brief_id=brief.id,
            revised_brief_id=revised.id,
            revision=revised.revision,
            changes=tuple(changes),
            created_at=now,
        )
        return revised, record

    def prepare_approval(self, brief: ProjectBrief) -> BriefApprovalBinding:
        if brief.approved_at is not None:
            raise BriefServiceError("brief_already_approved")
        if brief.open_questions:
            raise BriefServiceError("brief_has_open_questions")
        if brief.elena_design_choice is ElenaDesignChoice.UNDECIDED:
            raise BriefServiceError("elena_choice_required")
        if _SECRET_PATTERN.search(brief.to_json()):
            raise BriefServiceError("secret_like_content_rejected")
        return BriefApprovalBinding(
            brief_id=brief.id,
            revision=brief.revision,
            fingerprint=_brief_fingerprint(brief),
            prepared_at=utc_now(self._clock),
        )

    def approve(self, brief: ProjectBrief, binding: BriefApprovalBinding) -> ProjectBrief:
        if (
            binding.brief_id != brief.id
            or binding.revision != brief.revision
            or binding.fingerprint != _brief_fingerprint(brief)
        ):
            raise BriefServiceError("approval_binding_stale")
        return brief.model_copy(
            update={
                "approved_at": utc_now(self._clock),
                "approved_revision": brief.revision,
                "approval_fingerprint": binding.fingerprint,
            }
        )
