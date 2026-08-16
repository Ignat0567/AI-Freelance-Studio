from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from order_workflow.models import ElenaDesignConcept, ThemePalette
from order_workflow.visual_check import (
    COLOR_MATCH_TOLERANCE,
    MIN_CONTRAST_NORMAL,
    ExpectedPalette,
    _build_script,
    build_visual_check_runner,
    palette_from_concept,
    run_visual_check_in_docker,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _concept(**changes) -> ElenaDesignConcept:
    values = {
        "visual_direction": "Liquid Glass",
        "layout": "A focused responsive workspace.",
        "screens": ("Primary workflow",),
        "components": ("Navigation",),
        "light_theme": ThemePalette(background="#EEF4FB", surface="#FFFFFF", text="#172033", accent="#356CF6"),
        "dark_theme": ThemePalette(background="#101725", surface="#182236", text="#F4F7FF", accent="#75A1FF"),
    }
    values.update(changes)
    return ElenaDesignConcept(**values)


# --- what the palette is built from ---------------------------------------------------


def test_palette_comes_from_the_structured_elena_concept():
    palette = palette_from_concept(_concept())

    assert palette is not None
    assert palette.background == "#eef4fb"
    assert set(palette.colors) == {"#eef4fb", "#ffffff", "#172033", "#356cf6"}


def test_palette_falls_back_to_hex_values_scraped_from_the_style_spec():
    # The nine style packs keep their real values in prose, not in a structured field.
    spec = "Dark: page background near-black (`#08080b`); text primary `#f5f5f7`; accent `#6ea8fe`."

    palette = palette_from_concept(None, style_spec=spec)

    assert palette is not None
    assert palette.background == "#08080b"
    assert "#6ea8fe" in palette.colors


def test_three_digit_hex_values_are_expanded():
    palette = palette_from_concept(None, style_spec="ground `#fff` and ink `#012`")

    assert palette is not None
    assert palette.colors[:2] == ("#ffffff", "#001122")


def test_a_brief_with_no_colour_direction_yields_no_palette():
    assert palette_from_concept(None, style_spec="No colours are specified anywhere here.") is None


def test_no_palette_means_no_runner_so_the_gate_can_skip():
    # Asserting a palette the coding CLI was never given would be inventing a standard,
    # not enforcing one.
    assert build_visual_check_runner(None) is None
    assert build_visual_check_runner(ExpectedPalette(background="#000000", colors=("#000000",))) is not None


# --- what actually gets asserted in the browser ---------------------------------------


def test_the_generated_script_carries_the_expected_palette_and_thresholds():
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a", "#6ea8fe")))

    assert "#0b0f1a" in script
    assert "#6ea8fe" in script
    assert str(MIN_CONTRAST_NORMAL) in script
    assert str(COLOR_MATCH_TOLERANCE) in script


def test_the_script_measures_rather_than_judges():
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    # Contrast is computed from luminance, not opined on.
    assert "0.2126" in script and "0.7152" in script and "0.0722" in script
    # Mobile overflow is a subtraction, not an impression.
    assert "scrollWidth" in script and "clientWidth" in script
    assert "375" in script
    # Dark mode is exercised for real.
    assert "colorScheme: 'dark'" in script


def test_the_script_reports_deltas_the_repair_prompt_can_act_on():
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    # A repair prompt needs "this, not that" -- not "looks wrong".
    assert "but the approved palette specifies" in script
    assert "Largest colours actually painted" in script
    assert "Darken this text colour or lighten its background" in script


def test_contrast_failures_are_grouped_by_colour_pair():
    # One muted token reused across a dozen labels is one fix. Listing it a dozen times
    # buries every other finding under duplicates.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "groupContrast" in script
    assert "affecting ${where}" in script or "affecting" in script


def test_dark_mode_contrast_is_only_reported_when_the_page_actually_repaints():
    # A page that ignores prefers-color-scheme reports its light-mode contrast again
    # under "dark mode", which is the same defect counted twice.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "darkChanged && dark.contrastFailures.length > 0" in script


def test_transparent_backgrounds_are_walked_through_to_the_real_ground():
    # Reading backgroundColor off the text node alone reports rgba(0,0,0,0) and would
    # compute a nonsense contrast ratio against transparent black.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "effectiveBackground" in script
    assert "parts[3] === 0" in script


# --- the docker wrapper ----------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, qa_commands, cwd, *, image=None, timeout_seconds=None, docker_client_factory=None):
        self.calls.append({"commands": qa_commands, "cwd": cwd, "image": image})
        from order_workflow.qa_runner import QACommandResult, QAOutcome

        return QAOutcome(passed=True, results=(QACommandResult(command="visual", exit_code=0, stdout_tail="VISUAL CHECK PASSED", stderr_tail="", duration=1.0),))


def test_the_check_script_is_cleaned_up_from_the_workspace(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr("order_workflow.visual_check.run_qa_commands_in_docker", recorder)

    run_visual_check_in_docker((), tmp_path, palette=ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert recorder.calls, "the docker runner should have been invoked"
    leftovers = list(tmp_path.glob("___freelancerstudio_visual_check*"))
    assert leftovers == [], "the generated script must not be left in the delivered project"


def test_the_check_runs_in_the_playwright_image(tmp_path, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr("order_workflow.visual_check.run_qa_commands_in_docker", recorder)

    run_visual_check_in_docker((), tmp_path, palette=ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "playwright" in recorder.calls[0]["image"]


def test_the_bound_runner_matches_the_repair_loop_signature(tmp_path, monkeypatch):
    # run_qa_repair_loop calls qa_runner(qa_commands, cwd) positionally; the palette has
    # to travel by closure or the gate cannot be dropped into the existing machinery.
    recorder = _Recorder()
    monkeypatch.setattr("order_workflow.visual_check.run_qa_commands_in_docker", recorder)
    runner = build_visual_check_runner(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    outcome = runner(("label",), Path(tmp_path))

    assert outcome.passed is True


# --- the shared Playwright pin ---------------------------------------------------------


def test_both_browser_checks_use_the_same_pinned_image_and_npm_version():
    # They were pinned independently once, which is how one of them could have been bumped
    # for a Node-version fix while the other silently kept failing.
    from order_workflow import functional_smoke_check, visual_check as vc

    assert vc.PLAYWRIGHT_IMAGE is functional_smoke_check.PLAYWRIGHT_IMAGE
    assert vc.PLAYWRIGHT_NPM_VERSION is functional_smoke_check.PLAYWRIGHT_NPM_VERSION


def test_the_pinned_image_tag_and_npm_version_agree():
    # A mismatch makes Playwright download a browser at QA time instead of reusing the one
    # baked into the image.
    from order_workflow.docker_qa_runner import PLAYWRIGHT_IMAGE as image, PLAYWRIGHT_NPM_VERSION as npm_version

    assert f"v{npm_version}-" in image, f"{image} does not carry playwright {npm_version}"


def test_the_pinned_image_is_new_enough_for_a_modern_frontend_toolchain():
    # v1.48.0-jammy shipped Node 20.18.0. Vite 8 pulls rolldown, which requires
    # ^20.19.0 || >=22.12.0, so its native binding was never installed and `npm run preview`
    # died on startup -- reported by both gates as a broken page. Pinning back to a jammy
    # tag would bring that back silently.
    from order_workflow.docker_qa_runner import PLAYWRIGHT_IMAGE as image

    assert "jammy" not in image, "jammy images ship Node 20.18, which Vite 8's toolchain rejects"
    assert "noble" in image


# --- layout defects --------------------------------------------------------------------


def test_layout_checks_are_limited_to_objectively_wrong_things():
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    # Overlapping text, silently clipped content, and a box laid out past the viewport.
    # "Badly composed" is not measurable and is deliberately absent.
    assert "overlaps" in script and "clipped" in script and "pastViewport" in script


def test_only_statically_positioned_boxes_are_judged_for_overlap_and_overflow():
    # An absolutely positioned box that overlaps or sits off-screen is a placement decision:
    # badges, tooltips, modals -- and the skip link every accessible page opens with, which
    # lives at left:-9999px. Failing that would send the repair loop to delete an
    # accessibility feature. This was a real false positive before it was narrowed.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "style.position === 'static' && (rect.left < -1" in script
    assert "style.position === 'static' && textBoxes.length" in script
    assert "accessibility feature" in script


def test_overlap_needs_to_be_substantial_before_it_counts():
    # Boxes brushing borders by a pixel are normal; half-covered text is not.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "> 0.25" in script
    assert "a.node.contains(b.node) || b.node.contains(a.node)" in script


def test_a_scrollable_region_is_not_reported_as_clipped():
    # There the overflow is reachable rather than lost.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "scrollable" in script
    assert "textOverflow !== 'ellipsis'" in script


def test_layout_is_judged_at_both_widths():
    # A layout that is fine at 1280 and broken at 375 is the usual shape of the complaint.
    script = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    assert "'desktop (1280px)'" in script and "'phone (375px)'" in script
