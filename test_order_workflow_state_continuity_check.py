from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.state_continuity_check import (
    _CHECK_SCRIPT,
    run_state_continuity_check_in_docker,
)

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, qa_commands, cwd, *, image=None, timeout_seconds=None, docker_client_factory=None):
        self.calls.append({"commands": qa_commands, "cwd": cwd, "image": image})
        from order_workflow.qa_runner import QACommandResult, QAOutcome

        return QAOutcome(
            passed=True,
            results=(QACommandResult(command="state", exit_code=0, stdout_tail="STATE CONTINUITY CHECK PASSED", stderr_tail="", duration=1.0),),
        )


# --- what the script actually asserts --------------------------------------------------


def test_the_check_drives_controls_through_playwright_not_raw_value_assignment():
    # React's controlled inputs ignore a bare `.value = x` (no synthetic event fires), so an
    # in-page mutation would silently do nothing and every app would look broken.
    assert "handle.fill(" in _CHECK_SCRIPT
    assert "handle.selectOption(" in _CHECK_SCRIPT
    assert "handle.click()" in _CHECK_SCRIPT


def test_the_check_navigates_away_and_back_rather_than_reloading():
    # A reload would test persistence, which a shell legitimately may not have. The property
    # here is narrower: state must survive moving between screens.
    assert "goTo(page, elsewhere)" in _CHECK_SCRIPT
    assert "goTo(page, route)" in _CHECK_SCRIPT


def test_an_app_with_one_screen_is_skipped_not_failed():
    # With nothing to navigate between, the property is vacuous rather than violated.
    assert "destinations.length === 0" in _CHECK_SCRIPT
    assert "STATE CONTINUITY CHECK SKIPPED" in _CHECK_SCRIPT


def test_a_control_that_refused_the_edit_is_not_reported_as_a_revert():
    # If the value never changed, there is nothing to lose on navigation, and reporting it
    # would blame the wrong defect.
    assert "updated.value !== control.value ? updated : null" in _CHECK_SCRIPT


def test_disabled_and_readonly_controls_are_left_alone():
    assert "!node.disabled" in _CHECK_SCRIPT
    assert "!node.readOnly" in _CHECK_SCRIPT


def test_a_draft_or_search_field_is_not_required_to_survive_navigation():
    """The reading-journal order spent its whole repair budget on four findings that were
    not defects: three fields of an "Add book" form and a search box, each reported for
    going empty after navigation. The repair satisfied them by writing the draft book and
    the search query into localStorage, so the delivered app reopens with a stale filter."""
    from order_workflow.state_continuity_check import _TRANSIENT_INPUT_JS

    assert "search|filter|find|query" in _TRANSIENT_INPUT_JS
    assert "add|create|new|post|insert|append" in _TRANSIENT_INPUT_JS
    # The exemption is what the loop acts on, not just a field nobody reads.
    assert "if (control.transient)" in _CHECK_SCRIPT
    assert "transient: isTransient(node, label)" in _CHECK_SCRIPT


def test_a_settings_field_behind_a_save_button_is_still_checked():
    # The defect this whole gate exists for -- a Focus Timer settings screen whose duration
    # was component-local state -- sits in a form with a Save button. An exemption wide
    # enough to cover "any form" would delete the gate's reason to exist.
    from order_workflow.state_continuity_check import _TRANSIENT_INPUT_JS

    assert "save" not in _TRANSIENT_INPUT_JS.split("insert|append")[1].lower()


def test_the_fixture_carries_the_rule_that_ships_rather_than_a_copy():
    # A rule verified against a fixture that has drifted from the shipped rule verifies
    # nothing, which is why the fixture interpolates the same constant the check does.
    from order_workflow.state_continuity_check import _TRANSIENT_INPUT_JS, build_transient_input_fixture

    fixture = build_transient_input_fixture()

    assert _TRANSIENT_INPUT_JS in fixture
    assert _TRANSIENT_INPUT_JS in _CHECK_SCRIPT
    # The cases a browser was pointed at on 2026-08-26: four exempt, two still required.
    for label in ("Add book", "Search by title or author", "Save", "Notes"):
        assert label in fixture


def test_what_the_gate_declined_to_ask_about_is_printed():
    # qa_evidence.md is read by the client. A gate that silently drops half the controls it
    # looked at cannot be argued with.
    assert "Not required to survive navigation" in _CHECK_SCRIPT


def test_the_failure_message_names_the_control_and_both_values():
    # The repair loop gets this text verbatim; "state is broken" is not actionable.
    assert 'was set to "${item.was}" but reverted to "${item.now}"' in _CHECK_SCRIPT
    assert "lift it into shared state" in _CHECK_SCRIPT


def test_the_preview_connection_is_retried_before_giving_up():
    # Same lesson as the visual check: a slow-starting preview server otherwise reports as a
    # broken app and burns both repair attempts on a problem that does not exist.
    assert "attempt < 15" in _CHECK_SCRIPT
    assert "never accepted a connection" in _CHECK_SCRIPT


# --- the docker wrapper ----------------------------------------------------------------


def test_the_script_is_cleaned_up_from_the_delivered_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", _Recorder())

    run_state_continuity_check_in_docker((), tmp_path)

    assert list(tmp_path.glob("___freelancerstudio_state_check*")) == []


def test_the_check_runs_in_the_shared_playwright_image(tmp_path, monkeypatch):
    from order_workflow.docker_qa_runner import PLAYWRIGHT_IMAGE

    recorder = _Recorder()
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", recorder)

    run_state_continuity_check_in_docker((), Path(tmp_path))

    assert recorder.calls[0]["image"] == PLAYWRIGHT_IMAGE


def test_the_runner_matches_the_repair_loop_signature(tmp_path, monkeypatch):
    # run_qa_repair_loop calls qa_runner(qa_commands, cwd) positionally.
    monkeypatch.setattr("order_workflow.state_continuity_check.run_qa_commands_in_docker", _Recorder())

    outcome = run_state_continuity_check_in_docker(("label",), Path(tmp_path))

    assert outcome.passed is True


def test_the_gate_rebuilds_before_it_previews():
    """`npm run preview` serves dist/, and dist/ is whatever the phase built before the
    repair loop began. Measured on 2026-08-26: dist built 17:05, the repair edited
    src/styles/global.css at 17:20, and the gate at 17:21 reported its three findings word
    for word off the 17:05 bundle. Two repairs and 865 seconds bought nothing visible."""
    from order_workflow.functional_smoke_check import _SHELL_COMMAND as smoke
    from order_workflow.state_continuity_check import _SHELL_COMMAND as state
    from order_workflow.visual_check import _SHELL_COMMAND as visual

    for command in (visual, state, smoke):
        assert command.index("npm run build") < command.index("npm run preview")
        # A rebuild that fails has to say so: an unexplained non-zero exit sends the repair
        # looking at the browser findings it can no longer even reach.
        assert "BUILD FAILED" in command


# --- screens that are not links --------------------------------------------------------


def test_a_screen_reachable_only_by_clicking_is_still_a_screen():
    """All three delivered reading journals were skipped as having "fewer than two navigable
    screens" while having exactly two routes: `/` from the nav links, and `/book/:id`,
    reachable only by clicking a book card (`onClick={() => navigate(...)}`, not an anchor).
    That is the app whose whole subject is data it must not lose.

    Verified by running the shipped script in a real browser against a fixture with the same
    shape -- nav links that all point at `/`, cards that navigate by script: the previous
    script printed SKIPPED, this one reports the component-local notes field, and passes once
    the same field is persisted."""
    assert "discoverClickScreens" in _CHECK_SCRIPT
    # A screen is what is rendered, not what the address bar says: the journal delivered on
    # 2026-08-28 has no router at all, and swaps its page from component state.
    assert "changedEnough" in _CHECK_SCRIPT
    assert "screenText" in _CHECK_SCRIPT


def test_clicking_for_screens_happens_only_when_the_check_would_otherwise_skip():
    """Its cost and its clicking are confined to the case that is otherwise vacuous: with two
    URLs to visit, the check never clicks anything looking for a third."""
    main = _CHECK_SCRIPT.split("const failures = [];", 1)[1]

    assert main.index("routes.length >= 2") < main.index("discoverClickScreens")


def test_the_discovery_pass_does_not_click_buttons_that_do_things():
    """A discovery pass that presses "Delete" is worse than a skipped gate. Action labels are
    excluded, and the page is reloaded after every click so nothing a click did survives into
    the checks themselves."""
    discovery = _CHECK_SCRIPT.split("async function discoverClickScreens", 1)[1]
    discovery = discovery[: discovery.index("async function checkScreen")]

    assert "ACTION_LABEL.test(label)" in discovery
    for word in ("delete", "remove", "save", "submit", "clear"):
        assert word in _CHECK_SCRIPT.split("const ACTION_LABEL", 1)[1].split("\n", 1)[0]
    assert "await page.goto(TARGET_URL" in discovery
    assert "handles.slice(0, 40)" in discovery  # bounded: this runs inside a 240s gate


def test_a_way_back_is_required_before_a_screen_counts():
    """"Away and back" needs both halves. A click that opens something with no way to return
    is not a navigation the gate can test, and pretending otherwise would report every field
    on the opening screen as lost."""
    discovery = _CHECK_SCRIPT.split("async function discoverClickScreens", 1)[1]
    discovery = discovery[: discovery.index("async function checkScreen")]

    assert "if (back) found.push({ forward: label, back })" in discovery
    assert "BACK_LABEL" in discovery


def test_a_draft_field_without_a_form_around_it_is_still_a_draft():
    """The first time the gate reached the book page of a delivered journal it demanded that
    "Add a favourite quote" survive leaving the page -- the same demand the form rule was
    written to drop, from a draft that simply has no <form> around it."""
    from order_workflow.state_continuity_check import _TRANSIENT_INPUT_JS

    assert "(add|new|write|type|enter)" in _TRANSIENT_INPUT_JS
    assert '[class*="composer"]' in _TRANSIENT_INPUT_JS


def test_a_pass_has_to_mean_something_was_examined():
    """Both web apps of the 2026-08-28 acceptance sequence printed PASSED under "Checked 0
    control(s)": the gate found its screens, exempted every field on the first one as a draft
    and never reached the second. In qa_evidence.md that reads as evidence, and it is not."""
    assert "if (checked.length === 0)" in _CHECK_SCRIPT
    tail = _CHECK_SCRIPT.split("if (checked.length === 0)", 1)[1]
    assert tail.index("SKIPPED") < tail.index("STATE CONTINUITY CHECK PASSED")


def test_a_control_is_found_by_what_it_is_not_by_where_it_sat():
    """Mutating one control can add or remove another -- moving a book to "Reading" grows a
    "Page stopped at" field. Indexed lookups then crossed the wires: the gate reported a value
    typed into one field as lost by a different one, and named a draft it had just exempted."""
    assert "current.find((item) => item.key === control.key)" in _CHECK_SCRIPT
    assert "after.find((item) => item.key === control.key)" in _CHECK_SCRIPT
    # And the list is numbered before it is filtered, so the index still addresses the same
    # node Playwright's own query returns.
    assert ".map(({ node, index }) => ({ node, index }))" in _CHECK_SCRIPT or "({ node, index })" in _CHECK_SCRIPT


def test_a_debounced_autosave_is_given_time_to_land():
    """The delivered journal commits notes 600ms after the last keystroke. A gate that typed
    and left inside 150ms recorded them as lost -- a race worth half a second, not a repair."""
    assert "SAVE_SETTLE_MS = 1000" in _CHECK_SCRIPT
    body = _CHECK_SCRIPT.split("const mutated = await mutate(page, control);", 1)[1]
    assert body.index("SAVE_SETTLE_MS") < body.index("await away()")


def test_a_setting_made_of_buttons_is_still_a_setting():
    """The Focus Timer this gate was written for -- "a settings screen whose duration control
    was useState('25')" -- was delivered on 2026-08-28 with that control as a
    `role="radiogroup"` of buttons. The gate looked only at input/select/textarea: three
    screens, zero controls, nothing examined.

    Verified against that delivered build: 4 controls checked across 3 screens, PASSED, with
    the history range chips exempted as a filter."""
    from order_workflow.state_continuity_check import _ARIA_GROUP_JS

    assert 'role="radiogroup"' in _ARIA_GROUP_JS
    assert "aria-checked" in _ARIA_GROUP_JS and "aria-pressed" in _ARIA_GROUP_JS
    # One control, whose value is the option chosen -- not one control per button.
    assert "options.length < 2" in _ARIA_GROUP_JS
    assert "mutateGroup" in _CHECK_SCRIPT


def test_a_row_of_filter_chips_is_as_transient_as_a_search_box():
    """The same rule that exempts a search field has to exempt "All / Want to read / Reading"
    above a list, or the repair persists a stale filter -- which is what happened the last
    time this gate demanded a search query survive navigation."""
    from order_workflow.state_continuity_check import _ARIA_GROUP_JS

    assert "filterish" in _ARIA_GROUP_JS
    assert '[role="toolbar"], [class*="filter"], [class*="chip"], [class*="tabs"]' in _ARIA_GROUP_JS


def test_one_control_in_a_shared_header_is_one_finding():
    """b03, 2026-08-29: "Colour theme" is in the header of every screen, so the gate sent the
    same sentence five times -- to a repair prompt that shows four findings. One control that
    does not survive navigation is one fix, wherever it is rendered.

    Verified in a real DOM: a fixture whose header theme group resets on every render reports
    once, naming the other screen it also happens on."""
    assert "const grouped = new Map()" in _CHECK_SCRIPT
    assert "The same control does it on" in _CHECK_SCRIPT
    # Grouped by what the control did, not by where it was seen.
    assert "`${failure.control}|${failure.kind}|${failure.was || ''}|${failure.now || ''}`" in _CHECK_SCRIPT
