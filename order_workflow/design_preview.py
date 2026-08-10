from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import Enum
import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .brief_service import BriefServiceError, sanitize_public_text, verify_brief_approval
from .models import LongText, ProductType, ProjectBrief, ShortText, StrictDomainModel, new_public_id, utc_now
from .style_library import STYLE_LIBRARY, StylePack
from .ui_vocabulary import get_pattern


class DesignPreviewError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DesignPreviewStatus(str, Enum):
    DRAFT = "draft"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"


class LayoutArchetype(str, Enum):
    SINGLE_PAGE_LANDING = "single_page_landing"
    DASHBOARD = "dashboard"
    MASTER_DETAIL = "master_detail"
    THREE_PANEL_WORKSPACE = "three_panel_workspace"
    CHAT_WORKSPACE = "chat_workspace"
    CALENDAR_BOOKING = "calendar_booking"
    CATALOG_CHECKOUT = "catalog_checkout"
    ADMIN_CONSOLE = "admin_console"
    WIZARD_FLOW = "wizard_flow"
    DOCUMENT_WORKSPACE = "document_workspace"


class PreviewLayout(StrictDomainModel):
    type: LayoutArchetype
    regions: tuple[str, ...]


class PreviewScreen(StrictDomainModel):
    screen_id: str
    name: str
    purpose: str
    layout: PreviewLayout
    components: tuple[str, ...]
    states: tuple[str, ...]

    @model_validator(mode="after")
    def validate_screen(self) -> "PreviewScreen":
        if not self.components or not self.states:
            raise ValueError("preview screen requires components and states")
        return self


class DesignPreview(StrictDomainModel):
    preview_id: str
    brief_id: str
    brief_version: int = Field(ge=1)
    status: DesignPreviewStatus = DesignPreviewStatus.DRAFT
    product_type: Literal[ProductType.WEB_APP] = ProductType.WEB_APP
    concept_name: str
    layout_type: LayoutArchetype
    visual_direction: str
    style_name: ShortText
    style_spec: LongText
    screens: tuple[PreviewScreen, ...]
    user_flows: tuple[str, ...]
    empty_states: tuple[str, ...]
    error_states: tuple[str, ...]
    accessibility_notes: tuple[str, ...]
    implementation_notes: tuple[str, ...]
    revision_notes: tuple[str, ...] = ()
    approved: bool = False
    approval_fingerprint: str | None = None
    approved_version: int | None = None
    approved_at: datetime | None = None

    @model_validator(mode="after")
    def validate_preview(self) -> "DesignPreview":
        if not self.screens or not self.user_flows or not self.accessibility_notes or not self.implementation_notes:
            raise ValueError("preview requires screens, flows, accessibility, and implementation notes")
        approval_fields = (self.approval_fingerprint, self.approved_version, self.approved_at)
        if self.approved != (self.status is DesignPreviewStatus.APPROVED):
            raise ValueError("approved flag must match status")
        if any(item is not None for item in approval_fields) and not all(item is not None for item in approval_fields):
            raise ValueError("preview approval metadata must be complete")
        if self.approved and self.approved_version != self.brief_version:
            raise ValueError("preview approval must bind the current brief version")
        return self


class DesignPreviewService:
    def __init__(
        self,
        *,
        id_factory: Callable[[], object] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def generate(self, brief: ProjectBrief, *, revision_note: str | None = None, previous: DesignPreview | None = None) -> DesignPreview:
        if brief.open_questions:
            raise DesignPreviewError("brief_has_open_questions")
        if previous is not None and previous.brief_id != brief.id:
            raise DesignPreviewError("design_preview_brief_mismatch")
        kind = _select_layout(brief)
        spec = _preview_spec(kind, brief)
        style_pack = _style_by_name(previous.style_name) if previous else None
        if style_pack is None:
            style_pack = _select_style(brief)
        notes = tuple(previous.revision_notes if previous else ())
        if revision_note and revision_note.strip():
            notes = (*notes, sanitize_public_text(revision_note)[:240])
        return DesignPreview(
            preview_id=new_public_id("preview", self._id_factory),
            brief_id=brief.id,
            brief_version=brief.revision,
            status=DesignPreviewStatus.DRAFT,
            product_type=brief.product_type if hasattr(brief, "product_type") else ProductType.WEB_APP,
            concept_name=spec["concept_name"],
            layout_type=kind,
            visual_direction=spec["visual_direction"],
            style_name=style_pack.name,
            style_spec=style_pack.spec,
            screens=spec["screens"],
            user_flows=spec["user_flows"],
            empty_states=spec["empty_states"],
            error_states=spec["error_states"],
            accessibility_notes=spec["accessibility_notes"],
            implementation_notes=(*spec["implementation_notes"], *_non_goal_notes(brief)),
            revision_notes=notes,
        )

    def request_revision(self, preview: DesignPreview, brief: ProjectBrief, note: str) -> DesignPreview:
        if preview.brief_id != brief.id or preview.brief_version != brief.revision:
            raise DesignPreviewError("design_preview_stale")
        note = sanitize_public_text(note)
        if not note:
            raise DesignPreviewError("design_revision_note_required")
        return preview.model_copy(
            update={
                "status": DesignPreviewStatus.REVISION_REQUESTED,
                "approved": False,
                "approval_fingerprint": None,
                "approved_version": None,
                "approved_at": None,
                "revision_notes": (*preview.revision_notes, note[:240]),
            }
        )

    def approve(self, preview: DesignPreview, brief: ProjectBrief) -> DesignPreview:
        if not verify_brief_approval(brief):
            raise BriefServiceError("brief_not_approved")
        if preview.brief_id != brief.id or preview.brief_version != brief.revision:
            raise DesignPreviewError("design_preview_stale")
        fingerprint = design_preview_approval_fingerprint(preview, brief)
        return preview.model_copy(
            update={
                "status": DesignPreviewStatus.APPROVED,
                "approved": True,
                "approval_fingerprint": fingerprint,
                "approved_version": brief.revision,
                "approved_at": utc_now(self._clock),
            }
        )


def verify_design_preview_approval(preview: DesignPreview, brief: ProjectBrief) -> bool:
    return (
        preview.approved
        and preview.status is DesignPreviewStatus.APPROVED
        and preview.brief_id == brief.id
        and preview.brief_version == brief.revision
        and preview.approved_version == brief.revision
        and preview.approval_fingerprint == design_preview_approval_fingerprint(preview, brief)
    )


def design_preview_approval_fingerprint(preview: DesignPreview, brief: ProjectBrief) -> str:
    unapproved = preview.model_copy(
        update={
            "status": DesignPreviewStatus.DRAFT,
            "approved": False,
            "approval_fingerprint": None,
            "approved_version": None,
            "approved_at": None,
        }
    )
    return hashlib.sha256(f"{brief.approval_fingerprint}:{unapproved.to_json()}".encode("utf-8")).hexdigest()


def _wrap_text(text: str, limit: int = 220) -> list[str]:
    """Word-wrap into <=limit-length chunks -- design_preview_handoff_lines()'s output
    ultimately becomes AgentHandoff.constraints (tuple[ShortText, ...], 240 chars each), so
    a rich multi-paragraph style spec must be split rather than truncated to survive."""
    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > limit and current:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def design_preview_handoff_lines(preview: DesignPreview) -> tuple[str, ...]:
    lines = [
        f"Design preview {preview.preview_id}: {preview.concept_name}",
        f"Layout archetype: {preview.layout_type.value}",
        f"Visual direction: {preview.visual_direction}",
        f"Visual style: {preview.style_name}",
        *[f"Style spec: {chunk}" for chunk in _wrap_text(preview.style_spec)],
    ]
    for screen in preview.screens:
        lines.append(f"Screen {screen.name}: {screen.purpose}")
        lines.extend(f"Region: {region}" for region in screen.layout.regions)
        lines.extend(f"Component/state: {item}" for item in (*screen.components, *screen.states))
    lines.extend(f"User flow: {item}" for item in preview.user_flows)
    lines.extend(f"Empty state: {item}" for item in preview.empty_states)
    lines.extend(f"Error state: {item}" for item in preview.error_states)
    lines.extend(f"Accessibility: {item}" for item in preview.accessibility_notes)
    lines.extend(f"Implementation note: {item}" for item in preview.implementation_notes)
    return tuple(sanitize_public_text(item)[:240] for item in lines)


def _brief_text(brief: ProjectBrief) -> str:
    return " ".join(
        str(item)
        for item in (
            brief.goal,
            *brief.target_users,
            *brief.core_features,
            *brief.ui_requirements,
            *brief.acceptance_criteria,
            *brief.non_goals,
            *brief.assumptions,
        )
    ).casefold()


def _select_layout(brief: ProjectBrief) -> LayoutArchetype:
    text = _brief_text(brief)
    if _has(text, "pdf", "document", "citation", "source page", "grounded answer", "voice chat"):
        return LayoutArchetype.THREE_PANEL_WORKSPACE
    if _has(text, "appointment", "booking", "calendar", "time slot", "service selection"):
        return LayoutArchetype.CALENDAR_BOOKING
    if _has(text, "product", "cart", "checkout", "order", "catalog"):
        return LayoutArchetype.CATALOG_CHECKOUT
    if _has(text, "client", "lead", "pipeline", "deal", "crm"):
        return LayoutArchetype.MASTER_DETAIL
    if _has(text, "landing", "hero", "pricing", "cta", "contact form"):
        return LayoutArchetype.SINGLE_PAGE_LANDING
    if _has(text, "kpi", "chart", "analytics", "dashboard", "report"):
        return LayoutArchetype.DASHBOARD
    if _has(text, "admin", "settings", "bot flow", "messages"):
        return LayoutArchetype.ADMIN_CONSOLE
    if _has(text, "step", "onboarding", "wizard"):
        return LayoutArchetype.WIZARD_FLOW
    return LayoutArchetype.DASHBOARD


def _style_by_name(name: str) -> StylePack | None:
    return next((style for style in STYLE_LIBRARY if style.name == name), None)


def _select_style(brief: ProjectBrief) -> StylePack:
    text = _brief_text(brief)
    best_style: StylePack | None = None
    best_score = 0
    for style in STYLE_LIBRARY:
        score = sum(1 for keyword in style.when_to_use if keyword in text)
        if score > best_score:
            best_score = score
            best_style = style
    return best_style or STYLE_LIBRARY[0]


def _has(text: str, *needles: str) -> bool:
    return any(item in text for item in needles)


def _named(label: str, pattern_name: str) -> str:
    """Annotate a component label with its precise UI pattern name + ARIA/HTML/API symbol
    from the namethatui web vocabulary (order_workflow/ui_vocabulary.py), so the coding
    prompt reads e.g. "Lead card -- use the 'Card' pattern (<Card>)" instead of leaving the
    coding agent to guess an implementation. Not every component has a real vocabulary
    match -- the glossary covers interactive/overlay patterns (tabs, toasts, disclosure,
    pickers...), not generic layout elements like tables or charts -- those stay plain
    labels rather than being forced into a wrong pattern.
    """
    pattern = get_pattern(pattern_name)
    if pattern is None:
        raise ValueError(f"unknown UI vocabulary pattern: {pattern_name!r}")
    return f"{label} — use the '{pattern.name}' pattern ({pattern.api_symbol})"


def _screen(archetype: LayoutArchetype, *, regions: Iterable[str], components: Iterable[str], states: Iterable[str], name: str = "Main workspace", purpose: str = "Support the primary user workflow.") -> PreviewScreen:
    return PreviewScreen(
        screen_id="main",
        name=name,
        purpose=purpose,
        layout=PreviewLayout(type=archetype, regions=tuple(regions)),
        components=tuple(components),
        states=tuple(states),
    )


def _preview_spec(kind: LayoutArchetype, brief: ProjectBrief) -> dict[str, object]:
    common_accessibility = (
        "Use semantic landmarks, labelled form controls, and visible keyboard focus.",
        "Maintain accessible contrast and preserve key actions without relying on color alone.",
    )
    if kind is LayoutArchetype.THREE_PANEL_WORKSPACE:
        return {
            "concept_name": "Document-grounded assistant workspace",
            "visual_direction": "Calm productivity workspace with clear evidence, conversation, and document zones.",
            "screens": (_screen(kind, regions=("Left PDF library panel with drag-and-drop upload, document list, processing status, and assistant instruction textarea", "Center voice/text chat panel with microphone button, recognized text state, assistant answers, and speech playback waveform indicator", "Right source evidence panel with source document, page reference, highlighted quote, citation pinning, and no-answer state"), components=(_named("PDF drag-and-drop upload", "Drag & Drop"), _named("Document processing status", "Skeleton vs. Spinner"), "Assistant instruction textarea", "Text and voice chat", "Microphone button", "Recognized speech text", "Speech playback or waveform indicator", "Source document panel", "Page reference", "Highlighted quote", "Citation pinning"), states=("Empty document library", "Processing document", "Recognized text pending submission", "No-answer state when sources do not contain an answer", "Citation highlighted and pinned"), purpose="Let users upload documents, ask by voice or text, and inspect source-backed answers."),),
            "user_flows": ("Upload one or more PDFs, wait for processing, then ask a grounded question.", "Review answer citations, open the highlighted source quote, and pin useful references.", "Use voice input and speech playback when browser capabilities are available."),
            "empty_states": ("Show PDF drop zone and sample instructions before documents are uploaded.", "Show a no-answer explanation when uploaded documents do not contain supporting evidence."),
            "error_states": ("Show actionable browser capability blocker for unavailable microphone or speech synthesis.", "Show document processing failure without raw stack traces."),
            "accessibility_notes": common_accessibility + ("Expose microphone, playback, and citation controls to keyboard and screen readers.",),
            "implementation_notes": ("Build a left PDF library panel, center voice/text chat, and right source evidence panel.", "Include citation highlighting, no-answer state, recognized speech state, and speech playback indicator."),
        }
    specs = {
        LayoutArchetype.MASTER_DETAIL: ("CRM pipeline workspace", "Pipeline board, selected client/deal detail, and task or activity panel", ("Pipeline board", "Client or deal detail panel", "Task and activity panel"), (_named("Lead card", "Card"), _named("Deal stage", "Badge vs. Chip vs. Pill vs. Tag"), "Activity timeline", "Task composer"), ("No selected client", "Empty pipeline", "Overdue task warning")),
        LayoutArchetype.CALENDAR_BOOKING: ("Booking scheduler", "Service list, calendar slots, booking form, and admin schedule", ("Service selection", "Calendar and time slots", "Booking details form", "Admin schedule"), (_named("Service card", "Card"), _named("Time slot picker", "Date Picker"), _named("Booking form", "Form Field"), "Schedule summary"), ("No available slots", "Pending confirmation", "Booking conflict")),
        LayoutArchetype.CATALOG_CHECKOUT: ("Catalog checkout flow", "Product catalog, product detail, cart, and checkout", ("Product catalog", "Product detail", "Cart summary", "Checkout form"), ("Product grid", _named("Variant selector", "Toggle Group (Segmented Control)"), "Cart line items", "Checkout CTA"), ("Empty cart", "Out of stock", "Payment error")),
        LayoutArchetype.SINGLE_PAGE_LANDING: ("Conversion landing page", "Hero, benefits, pricing, call to action, and contact form", ("Hero section", "Benefits", "Pricing", "CTA", "Contact form"), ("Hero headline", _named("Benefit cards", "Card"), _named("Pricing tiers", "Card"), _named("Contact form", "Form Field")), ("Form submitted", "Validation error", "No pricing plan selected")),
        LayoutArchetype.DASHBOARD: ("Operational dashboard", "KPI cards, charts, filters, and data table", ("KPI cards", "Charts", "Filters", "Data table", "Status panel"), (_named("Metric card", "Card"), "Trend chart", _named("Filter bar", "Toggle Group (Segmented Control)"), "Sortable table"), ("No data", "Loading metrics", "Filter returns no results")),
        LayoutArchetype.ADMIN_CONSOLE: ("Admin console", "Navigation, configuration workspace, analytics, and settings", ("Admin navigation", "Primary configuration workspace", "Analytics panel", "Settings drawer"), ("Navigation item", _named("Configuration form", "Form Field"), _named("Analytics card", "Card"), _named("Settings toggle", "Switch vs. Checkbox vs. Radio")), ("No configuration selected", "Unsaved changes", "Invalid setting")),
        LayoutArchetype.WIZARD_FLOW: ("Guided task wizard", "Step navigation, main task area, review panel, and result state", ("Step navigation", "Main task area", "Review panel", "Result panel"), (_named("Step indicator", "Steps"), _named("Form section", "Form Field"), "Review summary", "Submit action"), ("Step incomplete", "Validation error", "Completed result")),
    }
    concept, direction, regions, components, states = specs[kind]
    return {
        "concept_name": concept,
        "visual_direction": f"{direction} for {brief.target_users[0]}.",
        "screens": (_screen(kind, regions=regions, components=components, states=states),),
        "user_flows": ("Start from the primary navigation, complete the main task, review status feedback, and recover from empty/error states.",),
        "empty_states": (states[0],),
        "error_states": (states[-1], "Show validation and operational failures without raw stack traces."),
        "accessibility_notes": common_accessibility,
        "implementation_notes": (f"Use the {kind.value} layout archetype.", f"Include regions: {', '.join(regions)}."),
    }


def _non_goal_notes(brief: ProjectBrief) -> tuple[str, ...]:
    return tuple(f"Respect non-goal: {item}" for item in brief.non_goals)
