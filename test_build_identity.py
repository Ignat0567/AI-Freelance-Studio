"""Which build is running, and saying so before it costs a run to find out.

On 2026-08-21 a palette fix committed at 09:44 was tested by a backend that had started at
21:46 the previous evening. It reproduced the old behaviour exactly, because it was still
holding the old modules. That became visible only by reading a generated CSS file out of the
workspace mid-run; the natural conclusion from the outside would have been "the fix does not
work", which was false.
"""

from __future__ import annotations

import pytest

import build_identity
from build_identity import BuildStatus, build_status


pytestmark = pytest.mark.unit


def test_a_process_running_the_current_checkout_is_not_stale(monkeypatch):
    monkeypatch.setattr(build_identity, "RUNNING_BUILD", "abc1234")
    monkeypatch.setattr(build_identity, "read_build_commit", lambda: "abc1234")

    status = build_status()

    assert status.stale is False
    assert "abc1234" in status.message


def test_a_process_older_than_the_checkout_says_so_and_says_what_to_do(monkeypatch):
    """The message has to name both commits and the remedy. "Stale" alone leaves the reader
    wondering whether it matters, which is how a twelve-hour-old build went unnoticed."""
    monkeypatch.setattr(build_identity, "RUNNING_BUILD", "old0000")
    monkeypatch.setattr(build_identity, "read_build_commit", lambda: "new1111")

    status = build_status()

    assert status.stale is True
    assert "old0000" in status.message and "new1111" in status.message
    assert "Restart" in status.message


def test_outside_a_checkout_the_question_is_answered_with_unknown(monkeypatch):
    """A packaged install has no working tree to compare against. Reporting "up to date"
    there would be a guess dressed as a fact."""
    monkeypatch.setattr(build_identity, "RUNNING_BUILD", "")
    monkeypatch.setattr(build_identity, "read_build_commit", lambda: "")

    status = build_status()

    assert status.stale is False
    assert "cannot be identified" in status.message


def test_a_dirty_tree_is_marked_as_such(monkeypatch):
    """A build made from uncommitted edits is not reproducible, and a row or a delivery that
    claims a bare commit hash for it would be misleading."""
    calls = {"status": " M order_workflow/models.py"}
    monkeypatch.setattr(build_identity, "_git", lambda *args: "abc1234" if args[0] == "rev-parse" else calls["status"])

    assert build_identity.read_build_commit() == "abc1234-dirty"


def test_a_clean_tree_reports_a_bare_commit(monkeypatch):
    monkeypatch.setattr(build_identity, "_git", lambda *args: "abc1234" if args[0] == "rev-parse" else "")

    assert build_identity.read_build_commit() == "abc1234"


def test_git_being_unavailable_is_not_an_error(monkeypatch):
    """Studio has to start on a machine without git, or in a packaged install with no
    repository. Failing to identify the build must never stop the product working."""
    monkeypatch.setattr(build_identity, "_git", lambda *args: "")

    assert build_identity.read_build_commit() == ""
    monkeypatch.setattr(build_identity, "RUNNING_BUILD", "")
    assert build_status().stale is False


def test_the_running_build_is_captured_at_import_not_on_demand():
    """The whole failure being that the files on disk moved on while the process did not, a
    value re-read on demand would answer a different question than the one asked."""
    from pathlib import Path

    source = Path("build_identity.py").read_text(encoding="utf-8")
    assert "RUNNING_BUILD = read_build_commit()" in source
    assert isinstance(build_identity.RUNNING_BUILD, str)


def test_the_api_exposes_it_where_a_person_can_see_it():
    from pathlib import Path

    source = Path("api/system.py").read_text(encoding="utf-8")

    assert "/api/system/build" in source
    assert "running_build" in source and "stale" in source


def test_the_delivery_report_records_the_build_that_produced_it():
    from order_workflow.delivery_report import build_delivery_report

    report = build_delivery_report(
        goal="A page.",
        requirements=(),
        note="Delivered.",
        gate_log=[],
        files_created=1,
        meaningful_artifact_count=1,
        built_by="abc1234",
    )

    assert "abc1234" in report


def test_a_delivery_with_no_identifiable_build_claims_nothing():
    from order_workflow.delivery_report import build_delivery_report

    report = build_delivery_report(
        goal="A page.",
        requirements=(),
        note="Delivered.",
        gate_log=[],
        files_created=1,
        meaningful_artifact_count=1,
    )

    assert "Built by" not in report
