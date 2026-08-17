"""Two of the five archived live runs failed: one on an expired provider token after 18.7
seconds, one because the visual gate never closed after 22 minutes. "3 of 5 completed" hides
the only thing that matters about that pair -- the first says nothing about the generated
code and the second says everything.

These tests pin the classification and, more importantly, pin that an unrecognised code
stays `unknown` instead of being forced into the nearest bucket.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from order_workflow.failure_cause import classify_failure_cause
from order_workflow.models import ExecutionResult, TestSummary as WorkflowTestSummary


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("claude_code_auth_expired", "provider"),
        ("opencode_execution_failed", "provider"),
        ("claude_code_process_failed", "provider"),
        ("preflight_coding_cli_auth_expired", "provider"),
        ("coding_cli_timeout", "budget"),
        ("claude_code_execution_timeout", "budget"),
        ("docker_engine_unreachable", "environment"),
        ("preflight_node_too_old", "environment"),
        ("preflight_disk_space_low", "environment"),
        ("qa_failed", "generated_code"),
        ("visual_check_failed", "generated_code"),
        ("state_continuity_failed", "generated_code"),
        ("functional_smoke_check_failed", "generated_code"),
        ("execution_internal_error", "product_bug"),
    ],
)
def test_every_code_the_pipeline_emits_has_a_class(code, expected):
    assert classify_failure_cause((code,)) == expected


def test_a_timeout_is_neither_provider_noise_nor_a_verdict_on_the_code():
    """Its own class on purpose: a run that ran out of clock says nothing about quality, but
    prompts that systematically outgrow their budget is a real problem that would be
    invisible inside a bucket labelled 'the provider had a bad day'."""
    assert classify_failure_cause(("coding_cli_timeout",)) == "budget"
    assert classify_failure_cause(("claude_code_auth_expired",)) == "provider"


def test_an_unrecognised_code_is_unknown_rather_than_the_nearest_bucket():
    assert classify_failure_cause(("some_code_added_next_month",)) == "unknown"


def test_the_first_recognised_code_wins_because_producers_put_the_specific_one_first():
    # A phase failure reports the coding CLI's own error ahead of any generic phase code.
    assert classify_failure_cause(("claude_code_auth_expired", "qa_failed")) == "provider"
    assert classify_failure_cause(("visual_check_failed", "qa_failed")) == "generated_code"


def test_an_unrecognised_code_falls_through_to_a_later_recognised_one():
    assert classify_failure_cause(("mystery", "docker_engine_unreachable")) == "environment"


def test_the_outcome_is_the_fallback_when_no_code_is_recognised():
    assert classify_failure_cause((), outcome="qa_failed") == "generated_code"
    assert classify_failure_cause(("mystery",), outcome="docker_unavailable") == "environment"


def test_no_information_at_all_is_unknown():
    assert classify_failure_cause(()) == "unknown"
    assert classify_failure_cause((), outcome=None) == "unknown"


def _result(**overrides) -> ExecutionResult:
    payload = dict(
        success=False,
        summary="The ui_shell phase did not complete.",
        outcome="failed",
        test_summary=WorkflowTestSummary(failed=1),
        completed_at=NOW,
    )
    payload.update(overrides)
    return ExecutionResult(**payload)


def test_a_failed_result_classifies_itself_without_the_producer_doing_anything():
    """Derived in the model rather than at each producer, so a failure path added later
    cannot forget to carry a class."""
    assert _result(errors=("claude_code_auth_expired",)).failure_cause == "provider"
    assert _result(errors=("visual_check_failed",)).failure_cause == "generated_code"


def test_a_successful_result_carries_no_cause():
    assert _result(success=True, outcome="generated", test_summary=WorkflowTestSummary(skipped=1)).failure_cause is None


def test_an_explicit_cause_is_not_overwritten():
    assert _result(errors=("qa_failed",), failure_cause="product_bug").failure_cause == "product_bug"


def test_the_class_survives_the_persistence_round_trip():
    """Executions are dumped to JSON and re-validated on restart. A computed field would be
    rejected on the way back in (extra="forbid"), so this has to be a real field that
    round-trips."""
    original = _result(errors=("coding_cli_timeout",))

    restored = ExecutionResult.model_validate(original.to_dict())

    assert restored.failure_cause == "budget"
    assert restored.to_dict()["failure_cause"] == "budget"


def test_a_record_written_before_this_field_existed_still_loads():
    payload = _result(errors=("qa_failed",)).to_dict()
    del payload["failure_cause"]

    restored = ExecutionResult.model_validate(payload)

    assert restored.failure_cause == "generated_code"
