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
from .reconciliation import reconcile_description_with_brief
from .style_library import select_style_pack, wants_dark_ground
from .models import (
    ElenaDesignChoice,
    ElenaDesignConcept,
    ProductType,
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
    return (mapping.get(str(value), sanitize_public_text(str(value))[:240]),)


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


_FEATURE_SEPARATORS = re.compile(r"[\n;]+|^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_MAX_GENERIC_FEATURES = 8
# ShortText's own limit. Applied in one place so nothing downstream has to guess whether it
# is holding a whole capability or the front of one.
_MAX_FEATURE_CHARS = 240


def _fit_feature(text: str) -> str:
    """Trim a capability to the field it lives in, at a word boundary, and mark the cut.

    A hard slice put a mid-word stump in front of the client: the reading-journal order's
    only "feature" ended "...it goes onto one of three shelves: Want to re", and that string
    is what delivery_report.md lists under "What was built", what the build prompt receives
    as its requirement, and what the acceptance criteria are written from.
    """
    if len(text) <= _MAX_FEATURE_CHARS:
        return text
    clipped = text[: _MAX_FEATURE_CHARS - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{clipped or text[: _MAX_FEATURE_CHARS - 1]}…"


def _split_capabilities(text: str) -> tuple[str, ...]:
    """Break an answer listing several capabilities into one entry each.

    Only newlines, semicolons and bullet markers are treated as separators. Commas and
    "and" are deliberately left alone: "start, pause, and reset the timer" is one capability
    expressed with three verbs, and splitting it would invent features nobody asked for.

    Collapsing everything into a single entry -- which is what this did before -- quietly
    distorted the whole pipeline downstream: the core-feature prompt says "wire in exactly
    ONE central feature" and was handed four, acceptance criteria came out as a single
    restatement of the blob, and complexity routing's `len(core_features) >= 5` rule could
    never fire.
    """
    parts = [
        # A trailing item usually reads "...; and log the result" -- the conjunction joined
        # it to the previous clause and means nothing once it stands alone.
        _fit_feature(sanitize_public_text(re.sub(r"^\s*(?:and|or)\s+", "", part, flags=re.IGNORECASE)))
        for part in _FEATURE_SEPARATORS.split(text)
        if part and part.strip()
    ]
    # A fragment too short to be a capability is punctuation noise, not a feature.
    meaningful = [part for part in parts if len(part) > 3]
    return tuple(dict.fromkeys(meaningful))[:_MAX_GENERIC_FEATURES]


def _generic_features(order: UserOrder) -> tuple[str, ...]:
    answer = _answers(order).get("core-features")
    source = answer if isinstance(answer, str) and answer.strip() else order.description[:1_500]
    split = _split_capabilities(source)
    if split:
        return split
    return (_fit_feature(sanitize_public_text(source)),)


_SECTION_HEADING = re.compile(r"^\s*\d+\.\s+(.+)$", re.MULTILINE)


def _looks_like_authoritative_spec(description: str) -> bool:
    """A long, already-numbered technical spec should be passed through mostly
    verbatim rather than reduced to a handful of inferred signals. Both length and
    structure are required so a very long but unstructured paragraph still falls
    through to the ordinary signal-based branches."""
    return len(description.strip()) > 2_000 and len(_SECTION_HEADING.findall(description)) >= 3


def _authoritative_spec_features(description: str) -> tuple[str, ...]:
    headings = _unique(sanitize_public_text(heading)[:240] for heading in _SECTION_HEADING.findall(description))
    if headings:
        return headings[:40]
    return (sanitize_public_text(description[:240]) or "See the full specification in Goal.",)


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
    return tuple(f"A user can complete this core action: {feature.rstrip('.')}."[:240] for feature in features)


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


_CARD_PAGE_CUES = ("mini card", "profile card", "business card", "name card", "calling card")
_NOT_A_CARD_CUES = ("webgl", "cinematic", "three.js", "3d scene", "living nature", "full-viewport")


def _looks_like_card_page(text: str) -> bool:
    hay = (text or "").casefold()
    if any(cue in hay for cue in _NOT_A_CARD_CUES):
        return False
    if any(cue in hay for cue in _CARD_PAGE_CUES):
        return True
    return bool(re.search(r"\bcard\b", hay))


def _static_page_subject(title: str, described: str) -> str:
    raw = (title or "").strip() or (described or "").strip()
    if not raw:
        return "this page"
    head = re.split(r"[.!?\n]", raw, maxsplit=1)[0].strip()
    words = head.split()
    if len(words) > 6:
        words = words[:6]
    return " ".join(words)[:80]


def _elena_placeholder(
    choice: ElenaDesignChoice,
    *,
    product_type: ProductType = ProductType.WEB_APP,
    described: str = "",
    title: str = "",
) -> ElenaDesignConcept | None:
    if choice is not ElenaDesignChoice.SHOW_ELENA_CONCEPT or product_type is ProductType.BOT:
        # A bot has no screens/light-dark theme -- there is nothing for Elena's visual
        # design concept to describe. In practice, product_type BOT orders never reach
        # SHOW_ELENA_CONCEPT in the first place (see clarification.py's ui_required
        # signal), but this stays correct even if that ever changes.
        #
        return None
    # The palette comes from the style the order describes, not from a constant. It used to
    # be this exact pale blue for every web_app ever ordered -- which meant an order asking
    # for "a deep near-black ground" was told, in its own prompt, to paint #eef4fb, had that
    # written into its workspace as binding CSS variables, and then passed a visual gate
    # measuring adherence to it. Fixing style selection alone did not reach this: the pack
    # decided the prose in the prompt while the palette stayed hardcoded.
    style = select_style_pack(described)
    # A design described as dark is dark by default, not only for visitors whose system
    # already is. light_theme is the slot that becomes `:root`, so that is where the
    # described palette has to go; the dark slot keeps it dark rather than flipping back.
    dark_by_default = wants_dark_ground(described)
    default_theme = style.dark_theme if dark_by_default else style.light_theme
    if product_type is ProductType.STATIC_PAGE:
        # Palette is still not a gate here (see build_static_page_prompt). A card brief
        # keeps the living plate. Every other single-file order gets overlay chrome on
        # an atmosphere of *this* brief -- Alethia looked right because the client
        # overrode Elena; the Mini Card constants must not win by default.
        if _looks_like_card_page(f"{title} {described}"):
            return ElenaDesignConcept(
                visual_direction="Living client plate: slow camera drift, breathing light, water glints; frosted card for copy.",
                layout="Full-viewport animated background with a centered frosted content card for title, sentence, and action.",
                screens=("Single living page",),
                components=("Animated background plate", "Frosted content card", "Primary action"),
                light_theme=default_theme,
                dark_theme=style.dark_theme,
                accessibility_notes=(
                    "Keep all text on a frosted or solid panel so contrast does not depend on the moving plate.",
                    "Honor prefers-reduced-motion by freezing the plate and any sparkle loop.",
                ),
            )
        subject = _static_page_subject(title, described)
        mood = "Dark atmospheric" if dark_by_default else style.name
        direction = (
            f"{mood} overlay site for {subject}: full-viewport atmosphere of that world, "
            "editorial chrome, client copy on panels."
        )[:240]
        return ElenaDesignConcept(
            visual_direction=direction,
            layout=(
                "Full-viewport atmosphere of the client's world. Overlay: mark and name at top left, "
                "text nav, one CTA, headline and supporting line on a frosted or solid panel. "
                "Motion stays alive; freeze when reduced-motion is set. If elena_background.webp is "
                "present it is the atmosphere, not a lone centered card."
            ),
            screens=("Single living page",),
            components=("Living atmosphere", "Overlay name and nav", "Headline panel", "Primary CTA"),
            light_theme=default_theme,
            dark_theme=style.dark_theme,
            accessibility_notes=(
                "Keep all text on a frosted or solid panel so contrast does not depend on the moving scene.",
                "Honor prefers-reduced-motion by freezing atmosphere and sparkle loops.",
            ),
        )
    return ElenaDesignConcept(
        visual_direction=style.name,
        layout="A focused responsive workspace with clear intake, content, action, and status regions.",
        screens=("Primary workflow", "Loading and empty states", "Actionable blocker state"),
        components=("Navigation", "Content workspace", "Primary actions", "Status feedback"),
        light_theme=default_theme,
        dark_theme=style.dark_theme,
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
        stack = RecommendedStack(frontend="React + Vite", backend="FastAPI", storage="SQLite")
        if order.product_type is ProductType.STATIC_PAGE:
            # Its own top-level branch for the same reason the bot branch has one: the
            # web-app branches below all assume an npm project with a framework, and every
            # constraint they inject (React + Vite, FastAPI, SQLite) is false here. A
            # creative single-file page also brings its own art direction, which is why no
            # visual palette is imposed -- see build_static_page_prompt.
            features = _generic_features(order)
            goal = sanitize_public_text(order.description[:20_000]) or f"A single-page interactive experience supporting {features[0].rstrip('.').casefold()}."
            non_goals = ("A build step, a framework, or any server-side component",)
            # Deliberately empty. The single-file/CDN/live-canvas rules belong to the pipeline,
            # not to this project, and they are already stated -- and enforced -- by
            # build_static_page_prompt and static_page_check. Listing them here as
            # "technical constraints" made the complexity classifier read three
            # pipeline-scaffolding lines as evidence that the work is hard, which is exactly
            # the bug substantive_technical_constraints() was written to remove for web apps
            # (see docs/model-routing-and-self-healing.md). The shape of the deliverable is
            # carried by recommended_stack below, where it describes the project honestly.
            technical = ()
            ui = ()
            acceptance = _generic_acceptance(features)
            stack = RecommendedStack(frontend="Single-file HTML + inline JS (no framework, no build)", backend="None", storage="None")
        elif order.product_type is ProductType.BOT:
            # A single, top-level branch ahead of the web-app branches below, rather than
            # threading product_type checks into each of them -- a bot order never falls
            # through to the authoritative-spec/PDF/generic web-app logic, which all
            # assume a browser frontend exists.
            features = _generic_features(order)
            goal = sanitize_public_text(order.description[:20_000]) or f"A Telegram bot supporting {features[0].rstrip('.').casefold()}."
            non_goals = ("Features not explicitly included in the approved first version",)
            technical = (
                "Python 3 with python-telegram-bot",
                "BOT_TOKEN read from an environment variable, never hardcoded or logged",
                "No database unless a specific feature requires persistence",
            )
            ui = ()
            acceptance = _generic_acceptance(features)
            stack = RecommendedStack(frontend="None (Telegram bot, no browser UI)", backend="Python + python-telegram-bot", storage="None unless a feature requires persistence")
        elif _looks_like_authoritative_spec(order.description):
            features = _authoritative_spec_features(order.description)
            goal = sanitize_public_text(order.description[:20_000]) or f"A small browser application supporting {features[0].rstrip('.').casefold()}."
            non_goals = ()
            technical = ()
            ui = ()
            acceptance = _generic_acceptance(features)
        elif signals.pdf_documents:
            features = _pdf_features(signals)
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
            features = _generic_features(order)
            goal = sanitize_public_text(order.description[:20_000]) or f"A small browser application supporting {features[0].rstrip('.').casefold()}."
            non_goals = ("Features not explicitly included in the approved first version",)
            technical = ("React + Vite frontend", "FastAPI backend where required", "SQLite local storage")
            ui = ("Accessible browser interface with clear loading, error, and empty states",) if signals.ui_required else ()
            acceptance = _generic_acceptance(features)

        now = utc_now(self._clock)
        brief = ProjectBrief(
            id=new_public_id("brief", self._id_factory),
            order_id=order.id,
            product_type=order.product_type,
            goal=goal,
            target_users=_target_users(order, signals.target_users),
            core_features=features,
            assumptions=_unique(session.assumptions),
            non_goals=tuple(non_goals),
            technical_constraints=tuple(technical),
            ui_requirements=ui,
            acceptance_criteria=acceptance,
            open_questions=(),
            recommended_stack=stack,
            elena_design_choice=session.elena_choice,
            elena_design_concept=_elena_placeholder(
                session.elena_choice,
                product_type=order.product_type,
                described=f"{order.title} {order.description} {goal}",
                title=order.title,
            ),
            created_at=now,
            updated_at=now,
        )
        # Appended after construction because the check compares the finished brief against
        # the words the client wrote -- both sides have to exist before they can disagree.
        contradictions = reconcile_description_with_brief(order, brief)
        if contradictions:
            return brief.model_copy(update={"assumptions": _unique((*brief.assumptions, *contradictions))})
        return brief

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
                "elena_design_concept": _elena_placeholder(
                    choice,
                    product_type=brief.product_type,
                    described=brief.goal,
                    title=brief.goal.split(".")[0][:80],
                ),
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
