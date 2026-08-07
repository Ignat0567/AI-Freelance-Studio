from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest

from order_workflow import (
    AgentHandoffService,
    DesignPreviewError,
    DesignPreviewService,
    ElenaDesignChoice,
    ExecutionMode,
    ExecutionRequest,
    LayoutArchetype,
    ProductionProjectExecutionAdapter,
    ProjectBrief,
    ProjectBriefService,
    RecommendedStack,
    design_preview_handoff_lines,
    verify_design_preview_approval,
)
from order_workflow.phase_prompts import build_ui_shell_prompt
from order_workflow.style_library import STYLE_LIBRARY


pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc)


class SequenceIds:
    def __init__(self) -> None:
        self.index = 0
        self.lock = Lock()

    def __call__(self) -> str:
        with self.lock:
            self.index += 1
            return f"design-{self.index:04d}"


def _clock():
    return NOW + timedelta(seconds=1)


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_design",
        "order_id": "order_design",
        "goal": "Create a browser-based operational app.",
        "target_users": ("Small internal team",),
        "core_features": ("Track work items", "Show status summary"),
        "acceptance_criteria": ("A user can complete the primary workflow.",),
        "recommended_stack": RecommendedStack(),
        "elena_design_choice": ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    brief = ProjectBrief(**values)
    service = ProjectBriefService(id_factory=SequenceIds(), clock=_clock)
    return service.approve(brief, service.prepare_approval(brief))


def _preview(brief):
    return DesignPreviewService(id_factory=SequenceIds(), clock=_clock).generate(brief)


@pytest.mark.parametrize(
    ("goal", "features", "expected"),
    [
        ("Create a landing page for a SaaS launch with pricing and CTA.", ("Hero section", "Pricing", "Contact form"), LayoutArchetype.SINGLE_PAGE_LANDING),
        ("Create an analytics dashboard for business metrics.", ("KPI cards", "Charts", "Data table"), LayoutArchetype.DASHBOARD),
        ("Create a CRM for clients, leads, pipeline and deals.", ("Pipeline board", "Client details", "Tasks"), LayoutArchetype.MASTER_DETAIL),
        ("Create a booking app for services and appointments.", ("Calendar", "Time slots", "Booking form"), LayoutArchetype.CALENDAR_BOOKING),
        ("Create an e-commerce store.", ("Product grid", "Cart", "Checkout"), LayoutArchetype.CATALOG_CHECKOUT),
    ],
)
def test_generic_design_generation_selects_distinct_archetypes(goal, features, expected):
    preview = _preview(_brief(goal=goal, core_features=features))

    assert preview.layout_type is expected
    assert preview.screens[0].layout.type is expected


def test_generic_app_uses_dashboard_fallback_without_pdf_panels():
    preview = _preview(_brief(goal="Create a team task tracker.", core_features=("Tasks", "Statuses", "Comments")))

    serialized = preview.to_json().casefold()
    assert preview.layout_type is LayoutArchetype.DASHBOARD
    assert "pdf" not in serialized
    assert "source evidence" not in serialized


def test_pdf_voice_assistant_preview_has_specific_three_zone_design_and_non_goals():
    brief = _brief(
        goal="Create a browser PDF voice assistant with grounded answers, citations, source pages and speech playback.",
        core_features=("PDF upload", "Voice and text chat", "Document-grounded answers", "Page citations", "Speech output"),
        non_goals=("OCR for scanned PDFs unless requested",),
    )

    preview = _preview(brief)
    serialized = preview.to_json().casefold()

    assert preview.layout_type is LayoutArchetype.THREE_PANEL_WORKSPACE
    for expected in [
        "left pdf library panel",
        "drag-and-drop upload",
        "assistant instruction textarea",
        "center voice/text chat",
        "microphone button",
        "recognized speech text",
        "speech playback",
        "right source evidence panel",
        "page reference",
        "highlighted quote",
        "citation pinning",
        "no-answer state",
        "respect non-goal: ocr for scanned pdfs unless requested",
    ]:
        assert expected in serialized


def test_revision_and_approval_are_immutable_and_bound_to_brief_fingerprint():
    brief = _brief()
    service = DesignPreviewService(id_factory=SequenceIds(), clock=_clock)
    original = service.generate(brief)
    revised = service.request_revision(original, brief, "Make the status panel more prominent")
    regenerated = service.generate(brief, revision_note="Make the status panel more prominent", previous=revised)
    approved = service.approve(regenerated, brief)

    assert original.approved is False
    assert revised.status == "revision_requested"
    assert regenerated.preview_id != original.preview_id
    assert approved.approved is True
    assert approved.approved_version == brief.revision
    assert verify_design_preview_approval(approved, brief) is True
    stale_brief = brief.model_copy(update={"revision": brief.revision + 1, "approved_revision": brief.revision + 1})
    assert verify_design_preview_approval(approved, stale_brief) is False
    with pytest.raises(DesignPreviewError):
        service.approve(original.model_copy(update={"brief_version": brief.revision + 1}), brief)


def test_handoff_and_production_prompt_include_approved_preview_without_raw_html_or_secrets(tmp_path):
    brief = _brief(
        goal="Create a browser PDF voice assistant with grounded answers, citations, source pages and speech playback.",
        core_features=("PDF upload", "Voice and text chat", "Page citations"),
    )
    design_service = DesignPreviewService(id_factory=SequenceIds(), clock=_clock)
    preview = design_service.approve(design_service.generate(brief), brief)

    handoff = AgentHandoffService(id_factory=SequenceIds(), clock=_clock).create_implementation_handoff(brief, preview)
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    package = adapter.prepare_execution(ExecutionRequest(brief=brief, handoff=handoff, execution_id="execution_design"))
    prompt = package.prompt.casefold()

    assert handoff.design_preview_id == preview.preview_id
    assert "three_panel_workspace" in prompt
    assert "left pdf library panel" in prompt
    assert "center voice/text chat" in prompt
    assert "right source evidence panel" in prompt
    assert "citation highlighting" in prompt
    assert "no-answer state" in prompt
    assert "speech playback" in prompt
    assert "<html" not in prompt
    assert "api_key" not in prompt
    assert "backend token" not in prompt


def test_prompts_differ_between_pdf_crm_and_booking(tmp_path):
    design_service = DesignPreviewService(id_factory=SequenceIds(), clock=_clock)
    handoffs = AgentHandoffService(id_factory=SequenceIds(), clock=_clock)
    adapter = ProductionProjectExecutionAdapter(provider_name="OpenCode", model_name="local-codex", workspace_root=tmp_path)
    prompts = []
    for brief in (
        _brief(goal="Create a PDF assistant with citations.", core_features=("PDF upload", "Source citations")),
        _brief(goal="Create a CRM for clients, leads, pipeline and deals.", core_features=("Pipeline board", "Client details")),
        _brief(goal="Create a booking app with services, calendar and time slots.", core_features=("Service list", "Calendar", "Booking form")),
    ):
        preview = design_service.approve(design_service.generate(brief), brief)
        handoff = handoffs.create_implementation_handoff(brief, preview)
        prompts.append(adapter.prepare_execution(ExecutionRequest(brief=brief, handoff=handoff, execution_id=f"execution_{len(prompts)}")).prompt.casefold())

    assert "left pdf library panel" in prompts[0]
    assert "pipeline board" in prompts[1]
    assert "calendar and time slots" in prompts[2]
    assert len(set(prompts)) == 3


def test_style_selection_matches_brief_keywords():
    gaming = _preview(_brief(goal="A dark, high-energy leaderboard for a hacker/tech esports tournament with a cyberpunk feel.", core_features=("Live rankings", "Match schedule")))
    wellness = _preview(_brief(goal="A calm yoga and meditation booking app with a wellness, health-focused feel.", core_features=("Class schedule", "Booking")))
    saas = _preview(_brief(goal="A modern SaaS product dashboard for a B2B startup.", core_features=("Metrics", "Settings")))

    assert gaming.style_name == "Cyberpunk Neon"
    assert wellness.style_name in {"Organic Wellness", "Soft Neumorphism"}
    assert saas.style_name == "Corporate Gradient Mesh"


def test_no_keyword_match_falls_back_to_liquid_glass():
    preview = _preview(_brief(goal="Create a simple internal tool for tracking work items.", core_features=("Track work items",)))

    assert preview.style_name == "Liquid Glass"


def test_style_choice_is_stable_across_a_revision_of_the_same_brief():
    brief = _brief(goal="A dark, hacker/tech esports leaderboard site.", core_features=("Live rankings",))
    first = _preview(brief)
    design_service = DesignPreviewService(id_factory=SequenceIds(), clock=_clock)
    revised = design_service.generate(brief, previous=first)

    assert revised.style_name == first.style_name == "Cyberpunk Neon"


def test_style_spec_survives_the_240_char_handoff_line_limit_without_truncation_loss():
    preview = _preview(_brief(goal="A dark, hacker/tech esports leaderboard site.", core_features=("Live rankings",)))
    style = next(item for item in STYLE_LIBRARY if item.name == preview.style_name)
    lines = design_preview_handoff_lines(preview)

    style_lines = [line for line in lines if line.startswith("Style spec: ")]
    assert all(len(line) <= 240 for line in lines)
    reconstructed = " ".join(line[len("Style spec: "):] for line in style_lines)
    for word in style.spec.split():
        assert word in reconstructed, f"lost word {word!r} from style spec when wrapping into handoff lines"


def test_ui_shell_prompt_includes_the_visual_style_section():
    brief = _brief(goal="A dark, hacker/tech esports leaderboard site.", core_features=("Live rankings",))
    design_service = DesignPreviewService(id_factory=SequenceIds(), clock=_clock)
    handoffs = AgentHandoffService(id_factory=SequenceIds(), clock=_clock)
    preview = design_service.approve(design_service.generate(brief), brief)
    handoff = handoffs.create_implementation_handoff(brief, preview)

    prompt = build_ui_shell_prompt(brief, handoff)

    assert "Visual design direction" in prompt
    assert "Cyberpunk Neon" in prompt
    assert "neon" in prompt.casefold()
