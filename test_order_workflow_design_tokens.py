"""The approved palette as a file in the workspace, not as a better sentence in the prompt.

Across the archived runs the visual gate rejected work for painting framework defaults --
one run measured 1 of 4 approved colours on the page. Every previous attempt to fix that
made the prompt more specific. This makes the palette present instead of described, and
these tests pin the three properties that makes it worth doing:

  * the tokens carry both themes and paint the ground, so dark mode repaints by construction
  * a project with no approved palette gets no file, rather than a guessed one
  * a failure to write degrades to the old behaviour instead of failing the phase
"""

from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.design_tokens import DESIGN_TOKENS_FILENAME, build_design_tokens_css, write_design_tokens
from order_workflow.models import ElenaDesignConcept, ThemePalette


pytestmark = pytest.mark.unit

LIGHT = ThemePalette(background="#ffffff", surface="#f4f6fb", text="#172033", accent="#356cf6")
DARK = ThemePalette(background="#0e1420", surface="#161d2b", text="#e8edf7", accent="#6f9bff")


def _concept(light: ThemePalette = LIGHT, dark: ThemePalette = DARK) -> ElenaDesignConcept:
    return ElenaDesignConcept(
        visual_direction="calm, high-contrast, generous spacing",
        layout="sidebar navigation with a content column",
        screens=("Shelves", "Book"),
        components=("card", "progress bar"),
        light_theme=light,
        dark_theme=dark,
    )


def test_every_approved_colour_becomes_a_variable():
    css = build_design_tokens_css(_concept())

    for value in (LIGHT.background, LIGHT.surface, LIGHT.text, LIGHT.accent):
        assert value in css
    for name in ("--color-background", "--color-surface", "--color-text", "--color-accent"):
        assert name in css


def test_dark_mode_is_written_rather_than_asked_for():
    """"Under prefers-color-scheme: dark the ground must actually repaint" was a prompt rule
    the gate then measured. Here it is already true."""
    css = build_design_tokens_css(_concept())

    assert "@media (prefers-color-scheme: dark)" in css
    assert DARK.background in css
    assert "background-color: var(--color-background)" in css
    assert "color: var(--color-text)" in css


def test_the_dark_block_comes_after_the_light_one_so_it_wins():
    css = build_design_tokens_css(_concept())

    assert css.index(":root {") < css.index("@media (prefers-color-scheme: dark)")


def test_no_approved_concept_produces_no_file_rather_than_a_guess():
    """With no structured concept there is no reliable mapping from scraped hex values onto
    background/surface/text/accent, and getting `text` wrong would author a contrast failure
    instead of preventing one."""
    assert build_design_tokens_css(None) is None


def test_a_blank_colour_cannot_reach_this_module_at_all():
    """Why build_design_tokens_css carries no emptiness guard: ThemePalette's fields are
    ShortText, so a half-filled palette fails to construct rather than reaching the writer."""
    with pytest.raises(ValueError):
        ThemePalette(background="#ffffff", surface="   ", text="#172033", accent="#356cf6")


def test_the_file_lands_at_the_workspace_root(tmp_path):
    name = write_design_tokens(tmp_path, _concept())

    assert name == DESIGN_TOKENS_FILENAME
    written = (tmp_path / DESIGN_TOKENS_FILENAME).read_text(encoding="utf-8")
    assert "--color-accent" in written


def test_nothing_is_written_and_nothing_is_claimed_without_a_concept(tmp_path):
    assert write_design_tokens(tmp_path, None) is None
    assert list(tmp_path.iterdir()) == []


def test_a_write_failure_degrades_instead_of_failing_the_phase(tmp_path):
    """The palette is still stated in the prompt and still measured by the gate afterwards,
    so an unwritable workspace should cost the improvement, not the run."""
    missing = tmp_path / "not" / "there"

    assert write_design_tokens(missing, _concept()) is None


def test_the_prompt_points_at_the_file_only_when_one_was_written():
    from order_workflow.models import ProjectBrief

    from test_order_workflow_preflight import _approved_contract  # reuses the approved fixture

    brief, handoff = _approved_contract()
    assert isinstance(brief, ProjectBrief)

    from order_workflow.phase_prompts import build_ui_shell_prompt

    with_tokens = build_ui_shell_prompt(brief, handoff, design_tokens_file=DESIGN_TOKENS_FILENAME)
    without = build_ui_shell_prompt(brief, handoff)

    assert DESIGN_TOKENS_FILENAME in with_tokens
    assert "--color-accent" in with_tokens
    # It must say to import it, not merely that it exists: a file nothing imports paints
    # nothing, and the gate would then fail for exactly the old reason.
    assert "import it" in with_tokens
    assert DESIGN_TOKENS_FILENAME not in without


# --- the derived on-accent colour ----------------------------------------------------


def test_contrast_ratio_matches_the_wcag_reference_values():
    from order_workflow.design_tokens import contrast_ratio

    # The two fixed points of the formula: identical colours are 1:1, black on white 21:1.
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)


def test_shorthand_hex_is_understood():
    from order_workflow.design_tokens import contrast_ratio

    assert contrast_ratio("#fff", "#000") == pytest.approx(21.0, abs=0.01)


def test_the_accent_that_actually_broke_a_live_run_no_longer_gets_white_text():
    """2026-08-17, b06-reading-journal: both ui_shell repairs -- 254s, 20% of the run --
    were spent on white text at 2.53:1 over this exact dark-theme accent."""
    from order_workflow.design_tokens import contrast_ratio, readable_on

    accent = "#75a1ff"
    assert contrast_ratio("#ffffff", accent) < 4.5  # the failure the gate reported
    chosen = readable_on(accent)
    assert chosen != "#ffffff"
    assert contrast_ratio(chosen, accent) >= 4.5


def test_a_dark_accent_still_gets_white_text():
    from order_workflow.design_tokens import contrast_ratio, readable_on

    chosen = readable_on("#1f3a93")
    assert chosen == "#ffffff"
    assert contrast_ratio(chosen, "#1f3a93") >= 4.5


def test_both_themes_carry_their_own_on_accent_value():
    """A single on-accent colour cannot serve both themes: the light and dark accents are
    different colours and frequently need opposite text."""
    css = build_design_tokens_css(_concept(light=ThemePalette(background="#ffffff", surface="#f4f6fb", text="#172033", accent="#1f3a93"),
                                           dark=ThemePalette(background="#0e1420", surface="#161d2b", text="#e8edf7", accent="#75a1ff")))

    # Four states now: the default, the system preference, and the app's own two choices.
    # What matters is not how many there are but that each one carries its own value.
    def block(header: str) -> str:
        return css.split(header, 1)[1].split("}", 1)[0]

    assert "#ffffff" in block(":root {")
    assert "#ffffff" in block(':root[data-theme="light"] {')
    assert "#ffffff" not in block(':root:not([data-theme="light"]) {').split("--color-on-accent")[1]
    assert "#ffffff" not in block(':root[data-theme="dark"] {').split("--color-on-accent")[1]


def test_an_app_with_its_own_theme_control_is_not_fighting_the_media_query():
    """The reading journal of 2026-08-27 shipped a theme toggle, and the visual gate found
    near-white text on a white surface: half the page followed the system preference and half
    followed the toggle. Tokens now answer to both, with the app's own choice winning."""
    css = build_design_tokens_css(_concept())

    assert ':root:not([data-theme="light"])' in css
    assert ':root[data-theme="dark"]' in css
    assert ':root[data-theme="light"]' in css
    # The system block has to come before the explicit ones, or the toggle cannot win.
    assert css.index("@media (prefers-color-scheme: dark)") < css.index(':root[data-theme="dark"]')


def test_the_prompt_names_the_on_accent_variable():
    from test_order_workflow_preflight import _approved_contract

    from order_workflow.phase_prompts import build_ui_shell_prompt

    brief, handoff = _approved_contract()
    prompt = build_ui_shell_prompt(brief, handoff, design_tokens_file=DESIGN_TOKENS_FILENAME)

    assert "--color-on-accent" in prompt
    assert "never white" in prompt


# --- the accent as text, not as a fill -----------------------------------------------


def test_the_accent_that_no_repair_could_fix_is_moved_before_the_build_sees_it():
    """2026-08-27/28, b06-reading-journal: `Contrast 4.31:1 (needs 4.5:1) -- #0071e3 on
    #f5f5f7` came back generation after generation. It could not be repaired from inside the
    build: the tokens file says not to write colour literals, the palette check counts the
    approved accent among the colours that must be painted, and the accent is what every
    style pack tells the build to use for links."""
    from order_workflow.design_tokens import accent_text_on, contrast_ratio

    accent, background, surface = "#0071e3", "#f5f5f7", "#ffffff"
    assert contrast_ratio(accent, background) == pytest.approx(4.31, abs=0.01)  # the reported failure

    derived = accent_text_on(accent, (background, surface))

    assert contrast_ratio(derived, background) >= 4.5
    assert contrast_ratio(derived, surface) >= 4.5


def test_an_accent_that_already_reads_as_text_is_left_exactly_alone():
    """The approved colour is the approved colour. Moving one that already clears the floor
    would trade a contrast failure for a palette-adherence one."""
    from order_workflow.design_tokens import accent_text_on

    palette = ThemePalette(background="#0f0f0f", surface="#1a1a1a", text="#fafafa", accent="#ff4b4b")

    assert accent_text_on(palette.accent, (palette.background, palette.surface)) == palette.accent


def test_the_derived_colour_keeps_the_accent_s_hue():
    """It is darkened toward the end that has room, not replaced by black: a link in an
    orange-accented design still reads as orange."""
    from order_workflow.design_tokens import accent_text_on

    derived = accent_text_on("#ff4d00", ("#fdf6e3", "#ffffff"))
    red, green, blue = (int(derived.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))

    assert red > green > blue  # still an orange
    assert red > 120  # and not merely a dark neutral


@pytest.mark.parametrize("pack", [
    pack for pack in vars(__import__("order_workflow.style_library", fromlist=["style_library"])).values()
    if type(pack).__name__ == "StylePack"
])
def test_every_style_pack_can_write_a_legible_link(pack):
    """Measured across the whole library rather than on one palette: 8 of the 36
    accent/ground pairs the packs ship were below 4.5:1 before this, all of them in light
    themes, and each one was a finding the build could not clear."""
    from order_workflow.design_tokens import accent_text_on, contrast_ratio

    for palette in (pack.light_theme, pack.dark_theme):
        if palette is None:
            continue
        derived = accent_text_on(palette.accent, (palette.background, palette.surface))
        assert contrast_ratio(derived, palette.background) >= 4.5, f"{pack.slug} on background"
        assert contrast_ratio(derived, palette.surface) >= 4.5, f"{pack.slug} on surface"


def test_each_theme_block_carries_its_own_accent_text():
    css = build_design_tokens_css(_concept())

    # The declaration, not the mentions of it in the file's own header comment.
    assert css.count("--color-accent-text:") == 4  # default, system preference, and both choices


def test_the_prompt_says_which_accent_variable_is_for_type():
    from test_order_workflow_preflight import _approved_contract

    from order_workflow.phase_prompts import build_ui_shell_prompt

    brief, handoff = _approved_contract()
    prompt = build_ui_shell_prompt(brief, handoff, design_tokens_file=DESIGN_TOKENS_FILENAME)

    assert "--color-accent-text" in prompt
    assert "links" in prompt
