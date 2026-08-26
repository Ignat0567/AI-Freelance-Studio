"""The ceiling report, tested on the case that made it necessary.

A killed call and a call that finished at 99% look identical in an elapsed/budget ratio, and
the week that ended 2026-08-24 drew the wrong conclusion from exactly that. So: a strike must
be counted, it must be kept out of the distribution of finished calls, and the report must
say the distribution is censored rather than leaving that to be noticed.
"""

from __future__ import annotations

import pytest

from bench.budget import format_budget_report, records_from
from bench.metrics import CallRecord, cli_call_records


pytestmark = pytest.mark.unit


def _event(message: str) -> dict:
    return {"id": f"event-{abs(hash(message)) % 10**8}", "message": message, "stage": "ui_shell", "agent": "Claude Code"}


def _finished(elapsed: int, budget: int) -> dict:
    return _event(f"Claude Code CLI returned after {elapsed}s of its {budget}s budget ({elapsed / budget:.0%})")


def _killed(elapsed: int, budget: int) -> dict:
    return _event(f"Claude Code CLI returned after {elapsed}s of its {budget}s budget (100% -- stopped at the ceiling)")


def test_a_killed_call_is_a_call(  ):
    transcript = {"events": [_finished(155, 450), _killed(450, 450)]}

    records = cli_call_records(transcript)

    assert [record.stopped_at_ceiling for record in records] == [False, True]
    assert [record.budget for record in records] == [450, 450]


def test_the_strike_rate_is_reported_before_the_distribution():
    pairs = [("run", CallRecord(155.0, 450, False)), ("run", CallRecord(450.0, 450, True)), ("run", CallRecord(450.0, 450, True))]

    report = format_budget_report(pairs)

    assert "3 call(s), 2 stopped at it (67%)" in report


def test_the_distribution_excludes_the_calls_the_ceiling_ended():
    # The whole defect in one assertion: with the strikes folded in, the median would be 450.
    pairs = [("run", CallRecord(100.0, 450, False)), ("run", CallRecord(450.0, 450, True)), ("run", CallRecord(450.0, 450, True))]

    report = format_budget_report(pairs)

    assert "n=1  median=100s" in report
    assert "excludes 2 call(s) this ceiling ended" in report


def test_a_ceiling_that_killed_everything_says_so_instead_of_printing_nothing():
    pairs = [("run", CallRecord(450.0, 450, True))]

    report = format_budget_report(pairs)

    assert "every call under this ceiling was killed by it" in report


def test_a_clean_ceiling_carries_no_censoring_warning():
    pairs = [("run", CallRecord(120.0, 450, False)), ("run", CallRecord(300.0, 450, False))]

    report = format_budget_report(pairs)

    assert "0 stopped at it (0%)" in report
    assert "excludes" not in report


def test_missing_transcript_directories_are_not_an_error(tmp_path):
    assert records_from([tmp_path / "nope"]) == []
    assert "nothing to say about the ceilings" in format_budget_report([])
