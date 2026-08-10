from __future__ import annotations

import pytest

from order_workflow.design_preview import DesignPreviewService, LayoutArchetype, design_preview_handoff_lines
from order_workflow.ui_vocabulary import WEB_UI_VOCABULARY, get_pattern
from test_order_workflow_design_preview import _brief

pytestmark = pytest.mark.unit


def test_get_pattern_returns_a_known_entry():
    pattern = get_pattern("Steps")

    assert pattern is not None
    assert pattern.api_symbol == 'aria-current="step"'


def test_get_pattern_returns_none_for_an_unknown_name():
    assert get_pattern("Not A Real Pattern") is None


def test_vocabulary_is_web_only_no_macos_entries_leaked_in():
    names = {pattern.name for pattern in WEB_UI_VOCABULARY}
    # A few macOS-only names from the source glossary that must never appear here.
    assert not names & {"Mac Window", "Traffic Lights (Window Controls)", "Menu Bar", "Dock Badge", "Sheet"}
    assert len(WEB_UI_VOCABULARY) == 44


def test_wizard_flow_step_indicator_is_annotated_with_the_steps_pattern():
    preview = DesignPreviewService().generate(_brief(goal="Create a step onboarding wizard.", core_features=("Step indicator", "Onboarding wizard flow")))

    assert preview.layout_type is LayoutArchetype.WIZARD_FLOW
    assert any("Step indicator — use the 'Steps' pattern" in item for item in preview.screens[0].components)


def test_annotated_component_lines_stay_within_the_240_char_handoff_limit():
    preview = DesignPreviewService().generate(_brief(goal="Create an admin console with settings.", core_features=("Admin settings", "Configuration workspace")))

    lines = design_preview_handoff_lines(preview)
    assert all(len(line) <= 240 for line in lines)
    assert any("use the" in line for line in lines)


def test_not_every_component_is_forced_into_a_vocabulary_match():
    # DASHBOARD's "Trend chart" and "Sortable table" have no real vocabulary entry
    # (the glossary covers interactive/overlay patterns, not charts or tables) and must
    # stay as plain labels rather than being forced into an unrelated pattern.
    preview = DesignPreviewService().generate(_brief(goal="Create a KPI dashboard with charts and a data table.", core_features=("KPI dashboard", "Analytics report")))

    assert preview.layout_type is LayoutArchetype.DASHBOARD
    components = preview.screens[0].components
    assert "Trend chart" in components
    assert "Sortable table" in components
