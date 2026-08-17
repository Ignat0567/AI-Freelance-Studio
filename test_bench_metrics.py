"""The benchmark's arithmetic, tested -- including against the transcripts already on disk.

A measurement tool nobody checks is how `repair_attempts: 0` survived five live runs whose
own event streams showed one and two. So the derivation is pure, it is tested on synthetic
transcripts for the edge cases, and it is tested against the real archived runs for the
property that matters most: it must not report zero repairs for a run that had some.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.metrics import (
    CSV_COLUMNS,
    cli_call_timings,
    format_report,
    models_used,
    repairs_by_gate,
    row_from_transcript,
    summarise_rows,
)
from bench.orders import BENCH_ORDERS, BENCH_ORDERS_BY_ID


pytestmark = pytest.mark.unit
ARCHIVE = Path(__file__).resolve().parent / "demo" / "transcripts"


def _event(message: str, *, stage: str = "ui_shell", agent: str = "BugCatcher") -> dict:
    return {"id": f"event-{abs(hash(message)) % 10**8}", "message": message, "stage": stage, "agent": agent}


def _transcript(events: list[dict], *, status: str = "succeeded", result: dict | None = None) -> dict:
    return {
        "summary": {"brief_goal": "a goal"},
        "events": events,
        "execution": {
            "status": status,
            "artifacts": [{"name": "delivery_report.md"}, {"name": "README.md"}],
            "result": {
                "outcome": "succeeded" if status == "succeeded" else "failed",
                "duration_seconds": 1200.0,
                "test_summary": {"passed": 2, "failed": 0, "repair_attempts": 0},
                **(result or {}),
            },
        },
    }


def _row(transcript: dict) -> dict:
    return row_from_transcript(transcript, run_at="20260817T120000Z", bench_id="b03-focus-timer", kind="small_app", product_type="web_app")


# --- the bench set itself -------------------------------------------------------------


def test_the_bench_set_has_eight_orders_with_unique_ids():
    assert len(BENCH_ORDERS) == 8
    assert len(BENCH_ORDERS_BY_ID) == 8


def test_the_split_is_five_small_and_three_realistic():
    kinds = [order.kind for order in BENCH_ORDERS]
    assert kinds.count("realistic_app") == 3
    assert kinds.count("static_page") + kinds.count("small_app") == 5


def test_both_branches_of_the_complexity_router_are_covered():
    """A benchmark where every order routes to the same model measures one code path."""
    from order_workflow.complexity import _COMPLEXITY_KEYWORDS

    trips = [
        order.id
        for order in BENCH_ORDERS
        if any(keyword in order.description.casefold() for keyword in _COMPLEXITY_KEYWORDS)
    ]
    assert trips, "no order trips a complexity keyword -- the 'complex' route is untested"
    assert len(trips) < len(BENCH_ORDERS), "every order trips one -- the 'routine' route is untested"


# --- derivation from events ----------------------------------------------------------


def test_repairs_are_counted_per_gate_not_just_totalled():
    """Which gate rejects the work is the actionable part; a single total hides it."""
    transcript = _transcript([
        _event("QA failed; asking Codex to fix (attempt 1 of 2)", agent="Elena"),
        _event("QA failed; asking Codex to fix (attempt 2 of 2)", agent="Elena"),
        _event("QA failed; asking Codex to fix (attempt 1 of 2)", stage="core_feature", agent="BugCatcher"),
    ])

    assert repairs_by_gate(transcript) == {"ui_shell/visual": 2, "core_feature/qa": 1}
    assert _row(transcript)["repair_attempts"] == 3


def test_a_run_with_repairs_is_never_reported_as_clean():
    """The exact failure that made five archived transcripts useless."""
    with_repairs = _transcript([_event("QA failed; asking Codex to fix (attempt 1 of 2)", agent="Elena")])
    without = _transcript([_event("QA passed.")])

    assert _row(with_repairs)["clean"] == 0
    assert _row(with_repairs)["completed"] == 1
    assert _row(without)["clean"] == 1


def test_repairs_come_from_the_events_not_from_test_summary():
    """test_summary and the event stream disagreed in every archived run, and the events
    were the ones telling the truth. If they disagree again, the events win."""
    transcript = _transcript(
        [_event("QA failed; asking Codex to fix (attempt 1 of 2)", agent="Elena")],
        result={"test_summary": {"passed": 1, "failed": 0, "repair_attempts": 0}},
    )

    assert _row(transcript)["repair_attempts"] == 1


def test_cli_calls_are_timed_against_their_budget():
    transcript = _transcript([
        _event("Claude Code CLI returned after 400s of its 1500s budget (27%)"),
        _event("Claude Code CLI returned after 430s of its 450s budget (96%)"),
    ])

    assert cli_call_timings(transcript) == [(400.0, 1500), (430.0, 450)]
    row = _row(transcript)
    assert row["cli_calls"] == 2
    assert row["cli_seconds"] == 830.0
    # The near-miss, not the average: this is the number that says the ceiling is deciding
    # outcomes rather than the work.
    assert row["cli_max_budget_ratio"] == pytest.approx(0.956, abs=0.001)


def test_model_routing_is_recorded_per_phase():
    transcript = _transcript([
        _event("Sending ui_shell prompt to the coding CLI (model: opus -- complex: matched 'offline')"),
        _event("Sending core_feature prompt to the coding CLI (model: sonnet -- routine: no complexity keywords)"),
    ])

    assert models_used(transcript) == ["ui_shell=opus", "core_feature=sonnet"]


def test_a_failure_carries_its_cause_and_a_success_does_not():
    failed = _transcript([], status="failed", result={"errors": ["claude_code_auth_expired"], "outcome": "failed"})
    succeeded = _transcript([_event("QA passed.")])

    assert _row(failed)["failure_cause"] == "provider"
    assert _row(succeeded)["failure_cause"] == ""


def test_a_transcript_written_before_failure_cause_existed_is_still_classified():
    """The archived runs predate the field; they still have to be comparable with tonight's."""
    old = _transcript([], status="failed", result={"errors": ["coding_cli_timeout"], "outcome": "failed"})

    assert _row(old)["failure_cause"] == "budget"


def test_the_records_own_class_is_preferred_over_reclassifying():
    transcript = _transcript([], status="failed", result={"errors": ["qa_failed"], "failure_cause": "product_bug"})

    assert _row(transcript)["failure_cause"] == "product_bug"


# --- aggregation ---------------------------------------------------------------------


def test_yields_separate_completion_from_never_needing_a_repair():
    rows = [
        {"completed": 1, "clean": 0, "duration_seconds": 1000, "repair_attempts": 2, "repairs_by_gate": "ui_shell/visual=2"},
        {"completed": 1, "clean": 1, "duration_seconds": 1200, "repair_attempts": 0, "repairs_by_gate": ""},
        {"completed": 0, "clean": 0, "duration_seconds": 0, "repair_attempts": 0, "failure_cause": "provider", "repairs_by_gate": ""},
        {"completed": 0, "clean": 0, "duration_seconds": 0, "repair_attempts": 1, "failure_cause": "generated_code", "repairs_by_gate": "ui_shell/visual=1"},
    ]

    summary = summarise_rows(rows)

    assert summary["completion_yield"] == 0.5
    assert summary["clean_yield"] == 0.25
    assert summary["failures_by_cause"] == {"provider": 1, "generated_code": 1}
    assert summary["repairs_by_gate"] == {"ui_shell/visual": 3}


def test_duration_statistics_ignore_runs_that_never_produced_one():
    """A failure at 18 seconds is not a fast build, and averaging it in makes the pipeline
    look quicker the more often it breaks."""
    rows = [
        {"completed": 1, "clean": 1, "duration_seconds": 1200, "repairs_by_gate": ""},
        {"completed": 1, "clean": 1, "duration_seconds": 1400, "repairs_by_gate": ""},
        {"completed": 0, "clean": 0, "duration_seconds": 18, "failure_cause": "provider", "repairs_by_gate": ""},
    ]

    summary = summarise_rows(rows)

    assert summary["median_duration_seconds"] == 1300.0
    assert summary["p90_duration_seconds"] == 1400.0


def test_the_percentile_never_invents_a_duration_no_run_had():
    durations = [100, 200, 300, 400, 500]
    rows = [{"completed": 1, "clean": 1, "duration_seconds": value, "repairs_by_gate": ""} for value in durations]

    assert summarise_rows(rows)["p90_duration_seconds"] in durations


def test_an_empty_set_reports_nothing_rather_than_dividing_by_zero():
    assert summarise_rows([]) == {"runs": 0}
    assert "no runs" in format_report(summarise_rows([]))


def test_a_budget_ratio_at_the_ceiling_is_called_out_in_the_report():
    rows = [{"completed": 1, "clean": 1, "duration_seconds": 1200, "cli_max_budget_ratio": 0.97, "repairs_by_gate": ""}]

    assert "budget, not the work" in format_report(summarise_rows(rows))


# --- against the real archive --------------------------------------------------------


@pytest.mark.skipif(not ARCHIVE.is_dir(), reason="no archived transcripts on this machine")
def test_the_archived_runs_parse_and_their_repairs_are_not_all_zero():
    """The regression this whole file exists to prevent: the archived successes each needed
    one or two repairs, and the structured record said zero."""
    from bench.report import rows_from_transcripts

    rows = rows_from_transcripts(ARCHIVE)
    if not rows:
        pytest.skip("archive is empty")

    assert all(row["outcome"] for row in rows)
    assert sum(row["repair_attempts"] for row in rows) > 0
    # Every failure must carry a class -- an unclassified failure is a hole in the yield.
    assert all(row["failure_cause"] for row in rows if not row["completed"])


def test_a_row_records_which_commit_produced_it():
    """Before-and-after is the only comparison the benchmark exists to support, and it is
    impossible over rows that do not say what code they measured."""
    row = row_from_transcript(
        _transcript([_event("QA passed.")]),
        run_at="20260817T120000Z",
        bench_id="b01-profile-card",
        kind="static_page",
        product_type="static_page",
        commit="40a8878",
    )

    assert row["commit"] == "40a8878"


def test_results_csv_gains_a_column_without_corrupting_earlier_rows(tmp_path):
    """Appending new-shaped rows to a file carrying an older header writes values under the
    wrong names -- silently, and only in the rows recorded after the change."""
    import csv as csv_module

    from bench.run_bench import _migrate_header

    path = tmp_path / "results.csv"
    old_columns = [column for column in CSV_COLUMNS if column != "commit"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv_module.DictWriter(handle, fieldnames=old_columns)
        writer.writeheader()
        writer.writerow({**{column: "" for column in old_columns}, "bench_id": "b03-focus-timer", "completed": "1", "duration_seconds": "1200"})

    _migrate_header(path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv_module.DictReader(handle))
    assert list(rows[0].keys()) == list(CSV_COLUMNS)
    assert rows[0]["bench_id"] == "b03-focus-timer"
    assert rows[0]["duration_seconds"] == "1200"
    assert rows[0]["commit"] == ""
    assert path.with_suffix(".csv.bak").is_file()
