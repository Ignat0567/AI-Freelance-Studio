"""delivery_report.md answers MVP_ACCEPTANCE.md's four questions, or it fails these tests.

The report this replaces read "Phased live execution completed. Meaningful artifacts
detected: 6. Workspace files inspected: 41." -- facts about the pipeline's own bookkeeping,
answering none of what was built, what was found and fixed, whether it runs, or how to
start it. These tests pin the four sections and, separately, that a clean run says so
plainly rather than reading as an omission.
"""

from __future__ import annotations

import pytest

from order_workflow.delivery_report import build_delivery_report
from order_workflow.models import TokenUsage


pytestmark = pytest.mark.unit


def _report(**overrides) -> str:
    payload = dict(
        goal="A pomodoro focus timer that runs entirely offline in the browser.",
        requirements=("Timer screen with start/pause/reset", "Daily streak of dots"),
        note="No backend was required; the UI shell and core feature are the complete deliverable.",
        gate_log=[],
        files_created=41,
        meaningful_artifact_count=6,
    )
    payload.update(overrides)
    return build_delivery_report(**payload)


def test_what_was_built_states_the_goal_and_the_requirements():
    report = _report()

    assert "## What was built" in report
    assert "pomodoro focus timer" in report
    assert "Timer screen with start/pause/reset" in report
    assert "Daily streak of dots" in report


def test_a_clean_run_says_so_plainly_rather_than_reading_as_an_omission():
    """A report that only ever lists problems reads as improvised. Silence about repairs is
    not the same claim as stating that none were needed."""
    report = _report(gate_log=[("ui_shell", 0, True), ("core_feature", 0, True)])

    assert "## What we found and fixed" in report
    assert "first attempt" in report.casefold()
    assert "repair" not in report.split("## What we found and fixed")[1].split("## Proof")[0].casefold()


def test_a_repaired_gate_is_named_by_what_it_is_not_by_its_internal_stage_id():
    report = _report(gate_log=[("ui_shell", 2, True)])

    section = report.split("## What we found and fixed")[1].split("## Proof")[0]
    assert "ui_shell" not in section
    assert "screens and navigation" in section.casefold()
    assert "2 repair attempts" in section
    assert "passed after" in section


def test_a_check_is_not_told_it_did_not_pass_its_checks():
    """Three of the six labels are the name of a check, so the delivered sentence read "The
    design and accessibility check did not pass its checks at first"."""
    section = _report(gate_log=[("ui_shell/visual", 1, True)]).split("## What we found and fixed")[1]

    assert "The design and accessibility check passed after 1 repair attempt." in section
    assert "its checks" not in section


def test_singular_repair_is_not_pluralised():
    report = _report(gate_log=[("core_feature", 1, True)])

    section = report.split("## What we found and fixed")[1]
    assert "1 repair attempt." in section
    assert "1 repair attempts" not in section


def test_a_gate_that_never_recovered_is_flagged_for_a_human_rather_than_hidden():
    """A run can reach _finalize_success only when it succeeded overall, but an individual
    phase's repair loop can still exhaust its attempts and pass on because a *later*
    gate accepted the state it left behind -- that gate's own failure must stay visible."""
    report = _report(gate_log=[("ui_shell", 2, False)])

    section = report.split("## What we found and fixed")[1].split("## Proof")[0]
    assert "still does not pass" in section
    assert "human look" in section


def test_multiple_gates_each_get_their_own_line():
    report = _report(gate_log=[("ui_shell", 2, True), ("core_feature", 1, True)])

    section = report.split("## What we found and fixed")[1].split("## Proof")[0]
    assert section.count("- ") == 2


def test_proof_prefers_the_deployment_outcome_when_one_ran():
    report = _report(deployment_status="Container served HTTP 200 from the production image.", deployment_image="freelancerstudio/exec-1:latest")

    section = report.split("## Proof it runs")[1].split("## How")[0]
    assert "HTTP 200" in section
    assert "freelancerstudio/exec-1:latest" in section


def test_proof_falls_back_to_the_qa_gates_when_no_container_was_built():
    report = _report(deployment_status=None)

    section = report.split("## Proof it runs")[1].split("## How")[0]
    assert "container packaging was not part of this run" in section.casefold()


def test_cost_appears_only_when_something_was_actually_reported():
    """A missing usage figure and a genuine zero are different facts (see failure_cause.py's
    sibling reasoning) -- the report must not print "$0.00" for a run nothing was billed
    for, which would read as free rather than as unmeasured."""
    with_cost = _report(usage=TokenUsage(total_cost_usd=5.14, output_tokens=100208))
    without = _report(usage=None)

    assert "$5.14" in with_cost
    assert "$" not in without


def test_how_to_run_it_prefers_the_containers_own_command():
    report = _report(run_command="docker run -p 8080:80 freelancerstudio/exec-1:latest")

    section = report.split("## How to run it")[1]
    assert "docker run" in section


def test_how_to_run_it_falls_back_to_the_preview_script_every_ui_shell_must_have():
    report = _report(run_command=None)

    section = report.split("## How to run it")[1]
    assert "npm install" in section
    assert "npm run preview" in section


def test_all_four_sections_are_present_and_in_order():
    report = _report()
    headings = ("## What was built", "## What we found and fixed", "## Proof it runs", "## How to run it")

    positions = [report.index(heading) for heading in headings]
    assert positions == sorted(positions)


# --- the evidence file ---------------------------------------------------------------


REAL_VISUAL_OUTPUT = """Palette: 4/4 approved colours painted (100%).
Dark mode: page repaints under prefers-color-scheme: dark (#eef4fb -> #101725).
VISUAL CHECK PASSED"""


def test_evidence_carries_the_gate_output_verbatim():
    """Summarising it into prose would reintroduce the exact gap the file exists to close:
    delivery_report.md already claims the checks passed."""
    from order_workflow.delivery_report import build_qa_evidence

    evidence = build_qa_evidence([("ui_shell", REAL_VISUAL_OUTPUT)])

    assert "Palette: 4/4 approved colours painted (100%)." in evidence
    assert "#eef4fb -> #101725" in evidence


def test_evidence_names_gates_the_way_a_client_would():
    from order_workflow.delivery_report import build_qa_evidence

    evidence = build_qa_evidence([("ui_shell", REAL_VISUAL_OUTPUT)])

    assert "ui_shell" not in evidence
    assert "screens and navigation" in evidence.casefold()


def test_evidence_says_the_checks_are_programs_not_opinions():
    from order_workflow.delivery_report import build_qa_evidence

    evidence = build_qa_evidence([("core_feature", "npm test: 12 passed")]).casefold()

    assert "browser" in evidence and "not opinions" in evidence


def test_each_gate_gets_its_own_fenced_block():
    from order_workflow.delivery_report import build_qa_evidence

    evidence = build_qa_evidence([("ui_shell", "a"), ("core_feature", "b")])

    assert evidence.count("```") == 4


def test_no_measurable_output_produces_no_file_rather_than_an_empty_promise():
    """A file headed "what the checks measured" containing nothing is worse than no file."""
    from order_workflow.delivery_report import build_qa_evidence

    assert build_qa_evidence([]) is None
    assert build_qa_evidence([("ui_shell", "   "), ("core_feature", "")]) is None


def test_the_report_points_at_the_evidence_only_when_it_exists():
    with_file = _report(evidence_file="qa_evidence.md")
    without = _report()

    assert "qa_evidence.md" in with_file
    assert "not a summary of them" in with_file
    assert "qa_evidence" not in without


def test_the_report_points_at_the_screenshot_when_the_gate_captured_one():
    """MVP_ACCEPTANCE's second criterion asks for proof the thing runs, and names a
    screenshot: a stranger reads a picture faster than an HTTP status line."""
    with_shot = _report(screenshot_file="delivery_screenshot.png")
    without = _report()

    assert "delivery_screenshot.png" in with_shot
    assert "as the check saw it" in with_shot
    assert "screenshot" not in without.casefold()


# --- what the first live delivery got wrong -------------------------------------------


def test_the_report_lists_what_was_delivered_instead_of_counting_the_toolchain():
    """The first live static-page delivery reported "477 files generated" for a single HTML
    page: node_modules from the QA step, the isolated .git and the pipeline's own markers all
    counted. A number a client can see is wrong undermines the measurements printed next to
    it."""
    report = _report(delivered_files=("index.html", "README.md", "qa_evidence.md"))

    assert "`index.html`" in report
    assert "477" not in report
    assert "files generated" not in report


def test_a_long_delivery_list_is_trimmed_rather_than_dumped():
    report = _report(delivered_files=tuple(f"file{i}.md" for i in range(20)))

    assert "and 8 more" in report


def test_a_normal_delivery_folder_is_listed_whole():
    """Ten-ish entries is what a folder holds once the toolchain is excluded, and the three
    documents a client reads sort late. At a six-entry cap the b01 report of 2026-08-28 named
    a screenshot and hid the README."""
    report = _report(delivered_files=(
        "dist/ (3 files)", "src/ (23 files)", "ARCHITECTURE.md", "Dockerfile", "README.md",
        "delivery_report.md", "delivery_screenshot.png", "design-tokens.css", "index.html",
        "package.json", "qa_evidence.md",
    ))

    assert "more." not in report
    for name in ("README.md", "delivery_report.md", "ARCHITECTURE.md"):
        assert name in report


def test_nothing_is_claimed_when_there_is_nothing_to_list():
    report = _report(delivered_files=())

    assert "Delivered:" not in report


def test_a_single_file_page_is_not_told_to_run_npm_install():
    """A static page has no package.json. `npm install` is the first thing a client would
    try and the first thing that would fail."""
    report = _report(run_command="Open `index.html` in any browser. There is nothing to install and nothing to start.")

    section = report.split("## How to run it")[1]
    assert "npm install" not in section
    assert "index.html" in section


# --- the gates the report used to omit entirely ---------------------------------------


def test_a_browser_check_is_named_by_what_it_checked():
    """Live run 2026-08-18: the visual gate demanded a repair and the delivered report said
    only "the backend did not pass at first". Three of the four gates recorded their repairs
    into the totals but were never labelled, so they produced no line here and no section in
    qa_evidence.md -- the palette and contrast numbers, which are the most convincing thing
    the pipeline measures, were missing from the evidence file."""
    report = _report(gate_log=[("ui_shell/visual", 2, True)])

    section = report.split("## What we found and fixed")[1].split("## Proof")[0]
    assert "ui_shell/visual" not in section
    assert "design and accessibility check" in section
    assert "2 repair attempts" in section


def test_every_browser_check_has_a_client_facing_name():
    from order_workflow.delivery_report import _gate_label

    for gate, expected in (
        ("ui_shell/visual", "design and accessibility"),
        ("core_feature/smoke", "browser render"),
        ("core_feature/state", "survives a reload"),
    ):
        assert expected in _gate_label(gate)


def test_a_phase_without_a_check_suffix_still_reads_as_the_phase():
    from order_workflow.delivery_report import _gate_label

    assert _gate_label("ui_shell") == "the screens and navigation"
    assert _gate_label("static_page_build") == "the page"


def test_an_unknown_check_degrades_to_something_readable():
    from order_workflow.delivery_report import _gate_label

    assert _gate_label("ui_shell/new_check_added_later") == "new check added later"


def test_the_evidence_file_carries_the_visual_gates_measurements():
    """The whole point of qa_evidence.md. Before the labels were fixed it shipped `vite build`
    output and nothing from the browser."""
    from order_workflow.delivery_report import build_qa_evidence

    evidence = build_qa_evidence([
        ("ui_shell", "vite build\n✓ built in 594ms"),
        ("ui_shell/visual", "Palette: 4/4 approved colours painted (100%).\nContrast: all text clears AA."),
    ])

    assert "Palette: 4/4" in evidence
    assert "design and accessibility check" in evidence


def test_a_bullet_that_only_repeats_the_goal_is_not_printed():
    """An order written as one paragraph produces a single "core feature" that is the goal
    itself, trimmed to its field. The reading-journal delivery of 2026-08-26 printed the
    same sentence twice under "What was built", the second time ending mid-thought."""
    from order_workflow.delivery_report import build_delivery_report

    goal = "A personal reading journal that lives in one browser on my laptop. I add a book by typing its title."

    report = build_delivery_report(
        goal=goal,
        requirements=("A personal reading journal that lives in one browser on my laptop. I add a book by\u2026",),
        note="No backend was required.",
        gate_log=[],
        files_created=3,
        meaningful_artifact_count=3,
    )

    assert report.count("A personal reading journal") == 1


def test_a_real_feature_list_is_still_printed():
    from order_workflow.delivery_report import build_delivery_report

    report = build_delivery_report(
        goal="A reading journal.",
        requirements=("Drag a book between shelves", "Write notes on a book"),
        note="",
        gate_log=[],
        files_created=3,
        meaningful_artifact_count=3,
    )

    assert "- Drag a book between shelves" in report
    assert "- Write notes on a book" in report


def test_a_page_with_no_container_says_it_was_served_not_that_it_was_not_packaged():
    """Criterion 2 asks the folder to prove the thing runs. A single-file page builds no
    container, and until 2026-08-27 its client read "container packaging was not part of this
    run" under "Proof it runs" -- an absence in the place evidence belongs. The page is served
    over HTTP by its own check, and that status is a fact."""
    from order_workflow.delivery_report import build_delivery_report

    report = build_delivery_report(
        goal="Three pricing tiers on one page.",
        requirements=(),
        note="",
        gate_log=[],
        files_created=1,
        meaningful_artifact_count=1,
        served_http_status=200,
        screenshot_file="delivery_screenshot.png",
    )

    proof = report.split("## Proof it runs")[1].split("## ")[0]
    assert "served over HTTP and answered 200" in proof
    assert "container packaging was not part of this run" not in proof
    assert "delivery_screenshot.png" in proof


def test_a_deployed_project_still_leads_with_its_container():
    from order_workflow.delivery_report import build_delivery_report

    report = build_delivery_report(
        goal="An app.",
        requirements=(),
        note="",
        gate_log=[],
        files_created=9,
        meaningful_artifact_count=9,
        deployment_status="Container served HTTP 200 from the production image.",
        served_http_status=200,
    )

    proof = report.split("## Proof it runs")[1].split("## ")[0]
    assert "Container served HTTP 200 from the production image." in proof
    assert "during its checks" not in proof


# --- what the evidence file looks like when a person opens it --------------------------


def test_terminal_colour_codes_do_not_reach_the_client():
    """The delivered evidence of 2026-08-27 carried 52 escape sequences, so the client's copy
    of the test run opened as "[1m[30m[46m RUN [49m[39m[22m [36mv4.1.11". Every character a
    reader can see is kept; only the bytes that are not text are dropped."""
    from order_workflow.delivery_report import build_qa_evidence

    document = build_qa_evidence([("core_feature", "\x1b[1m\x1b[36m RUN \x1b[39m v4.1.11\n\x1b[32m1 passed\x1b[39m")])

    assert "\x1b" not in document
    assert "RUN  v4.1.11" in document
    assert "1 passed" in document


def test_one_check_that_ran_twice_is_one_section():
    """"## the browser render check" appeared twice in the delivered file, with byte-identical
    output and nothing saying which run either belonged to."""
    from order_workflow.delivery_report import build_qa_evidence

    same = "FUNCTIONAL SMOKE CHECK PASSED\nRendered text length: 908"
    document = build_qa_evidence([("ui_shell/smoke", same), ("core_feature/smoke", same)])

    assert document.count("## the browser render check") == 1
    assert document.count("FUNCTIONAL SMOKE CHECK PASSED") == 1
    assert "Ran again after the core feature, with the same output." in document


def test_the_same_check_with_different_output_keeps_both_and_says_which_is_which():
    from order_workflow.delivery_report import build_qa_evidence

    document = build_qa_evidence([
        ("ui_shell/smoke", "FUNCTIONAL SMOKE CHECK PASSED\nVisible interactive elements: 9"),
        ("core_feature/smoke", "FUNCTIONAL SMOKE CHECK PASSED\nVisible interactive elements: 22"),
    ])

    assert "## the browser render check, after the screens and navigation" in document
    assert "## the browser render check, after the core feature" in document
    assert "elements: 9" in document and "elements: 22" in document
