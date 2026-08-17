"""What kind of thing went wrong, as a class rather than as a code.

The five archived live runs from 2026-08-16/17 include two failures: one died after 18.7
seconds on an expired provider token, the other after 22 minutes because the visual gate
never closed. Averaging those into "3 of 5 completed" measures nothing -- the first says
nothing whatsoever about the quality of the generated code, and the second says everything.
Yield is only interpretable once each failure carries which kind it was.

Deliberately a lookup over codes the pipeline already emits, not a model call and not a
scan of prose: the producers have already worked out *why* they failed and said so in
`ExecutionResult.errors`. Anything not in the table classifies as "unknown" rather than
being forced into the nearest bucket -- a wrong class is worse than a missing one, because
it silently moves a real quality signal into the noise column.
"""

from __future__ import annotations

from typing import Literal


FailureCause = Literal["provider", "budget", "environment", "generated_code", "product_bug", "unknown"]


# `budget` is deliberately its own class rather than folded into `provider`. A timeout is not
# evidence about the code (the doc that describes the repair loop makes this point about the
# loop itself), but it is also not provider noise: if prompts systematically outgrow their
# wall clock, that is a real, fixable problem which would be invisible inside a bucket
# labelled "the provider had a bad day".
_CAUSE_BY_CODE: dict[str, FailureCause] = {
    # The provider's side of the contract: nothing about the run or the machine changes these.
    "claude_code_auth_expired": "provider",
    "claude_code_unavailable": "provider",
    "claude_code_process_failed": "provider",
    "opencode_unavailable": "provider",
    "opencode_execution_failed": "provider",
    "provider_rate_limited": "provider",
    "preflight_coding_cli_auth_expired": "provider",
    # Out of clock, not out of ability.
    "claude_code_execution_timeout": "budget",
    "coding_cli_timeout": "budget",
    "timed_out_without_artifacts": "budget",
    # The machine the pipeline runs on, not the code it produced.
    "docker_engine_unreachable": "environment",
    "docker_unavailable": "environment",
    "preflight_docker_unavailable": "environment",
    "preflight_node_missing": "environment",
    "preflight_node_too_old": "environment",
    "preflight_disk_space_low": "environment",
    "workspace_unavailable": "environment",
    "workspace_not_writable": "environment",
    "workspace_root_unavailable": "environment",
    "unsafe_workspace_path": "environment",
    "qa_stack_undetected": "environment",
    # An oracle rejected what the coding CLI wrote. These are the only failures that carry a
    # quality signal, and the only ones whose rate should move when the pipeline improves.
    "qa_failed": "generated_code",
    "visual_check_failed": "generated_code",
    "functional_smoke_check_failed": "generated_code",
    "state_continuity_failed": "generated_code",
    "static_page_check_failed": "generated_code",
    "simulated_verification_failure": "generated_code",
    # Studio's own fault.
    "execution_internal_error": "product_bug",
    "execution_contract_violation": "product_bug",
}

# Outcomes are coarser than error codes and only consulted when no code matched.
_CAUSE_BY_OUTCOME: dict[str, FailureCause] = {
    "qa_failed": "generated_code",
    "docker_unavailable": "environment",
    "timed_out_without_artifacts": "budget",
}


def classify_failure_cause(errors: tuple[str, ...] | list[str], *, outcome: str | None = None) -> FailureCause:
    """First recognised error code wins; the outcome is the fallback.

    Order matters: producers put the most specific code first (a phase failure reports the
    coding CLI's own error rather than a generic phase code), so scanning in order keeps the
    class as specific as the record allows.
    """
    for code in errors or ():
        cause = _CAUSE_BY_CODE.get(str(code).strip())
        if cause is not None:
            return cause
    if outcome:
        cause = _CAUSE_BY_OUTCOME.get(outcome.strip())
        if cause is not None:
            return cause
    return "unknown"
