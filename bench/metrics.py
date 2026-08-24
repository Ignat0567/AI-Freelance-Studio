"""One CSV row per run, and the aggregate over rows -- both as pure functions.

Kept free of HTTP and of the backend on purpose. The transcripts of past runs are already
on disk, so the same code that will record tonight's baseline can be pointed at the five
runs archived on 2026-08-16/17 and produce their numbers now. A measurement tool that can
only measure the future cannot tell you whether it is measuring correctly.

The other reason these are functions over a transcript rather than counters inside the
pipeline: the pipeline's own structured record was wrong for months (`repair_attempts: 0`
in five transcripts whose event streams showed one and two). Deriving from the event stream
as well as from the result means a disagreement between them is visible instead of silent.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Iterable, Sequence

from order_workflow.failure_cause import classify_failure_cause


CSV_COLUMNS: tuple[str, ...] = (
    "run_at",
    # Which code produced this row. A benchmark whose rows do not say what they measured
    # cannot answer "did Wednesday's change help", which is the only question it exists for.
    "commit",
    "bench_id",
    "kind",
    "product_type",
    "outcome",
    "completed",
    "clean",
    "failure_cause",
    "final_stage",
    "duration_seconds",
    "repair_attempts",
    "repairs_by_gate",
    "gates_passed",
    "gates_failed",
    "cli_calls",
    "cli_seconds",
    # How many coding-CLI calls were stopped at their ceiling rather than finishing. The
    # direct answer to "is the budget too low", and the one the timing line alone cannot
    # give: before 2026-08-24 a killed call emitted nothing, so the sample contained only
    # the calls that fit and every ceiling-strike was invisible.
    "cli_timeouts",
    # What the run actually spent. The pipeline reported this only for failures until
    # 2026-08-17, so every successful run in the archive looks free.
    "cost_usd",
    "output_tokens",
    "cli_max_budget_ratio",
    "models",
    "artifacts",
    "delivery_report",
    "container_http_200",
    "notes",
)

_BUDGET_LINE = re.compile(r"returned after (\d+(?:\.\d+)?)s of its (\d+)s budget")
_CEILING_MARKER = "stopped at the ceiling"
_REPAIR_LINE = re.compile(r"QA failed; asking .* to fix \(attempt (\d+) of (\d+)\)")
_MODEL_LINE = re.compile(r"Sending (\w+) prompt to the coding CLI \(model: ([^)]*)\)")

# Which gate a repair loop belongs to, by the agent the event was emitted under. The visual
# gate runs as Elena and the build/test/browser gates as BugCatcher, which is the only thing
# separating them in the shared repair-loop event shape.
_GATE_BY_AGENT = {"Elena": "visual", "BugCatcher": "qa"}


def _events(transcript: dict) -> list[dict]:
    return list(transcript.get("events") or [])


def _result(transcript: dict) -> dict:
    execution = transcript.get("execution") or {}
    return dict(execution.get("result") or {})


def cli_call_timings(transcript: dict) -> list[tuple[float, int]]:
    """(elapsed, budget) for every coding-CLI call the run made."""
    timings: list[tuple[float, int]] = []
    for event in _events(transcript):
        found = _BUDGET_LINE.search(event.get("message") or "")
        if found:
            timings.append((float(found.group(1)), int(found.group(2))))
    return timings


def repairs_by_gate(transcript: dict) -> dict[str, int]:
    """How many repair attempts each gate demanded, counted from the events.

    Which gate is doing the rejecting is the actionable part: across the archived runs the
    visual gate accounts for nearly every repair, and that fact is invisible in a single
    total.
    """
    counts: dict[str, int] = {}
    for event in _events(transcript):
        if not _REPAIR_LINE.search(event.get("message") or ""):
            continue
        gate = _GATE_BY_AGENT.get(event.get("agent") or "", "other")
        stage = event.get("stage") or "?"
        counts[f"{stage}/{gate}"] = counts.get(f"{stage}/{gate}", 0) + 1
    return counts


def models_used(transcript: dict) -> list[str]:
    used: list[str] = []
    for event in _events(transcript):
        found = _MODEL_LINE.search(event.get("message") or "")
        if found:
            used.append(f"{found.group(1)}={found.group(2).split('--')[0].strip()}")
    return used


def row_from_transcript(
    transcript: dict, *, run_at: str, bench_id: str, kind: str, product_type: str, commit: str = ""
) -> dict[str, Any]:
    execution = transcript.get("execution") or {}
    result = _result(transcript)
    test_summary = dict(result.get("test_summary") or {})
    timings = cli_call_timings(transcript)
    gates = repairs_by_gate(transcript)
    events = _events(transcript)

    # Prefer the record's own class; fall back to classifying its errors, so transcripts
    # written before failure_cause existed are still comparable with tonight's.
    outcome = result.get("outcome") or execution.get("status") or "unknown"
    completed = execution.get("status") == "succeeded"
    cause = result.get("failure_cause")
    if not completed and not cause:
        cause = classify_failure_cause(tuple(result.get("errors") or ()), outcome=outcome)

    # Counted from the events rather than read from test_summary: the two disagreed in every
    # archived transcript, and the event stream was the one telling the truth.
    repair_total = sum(gates.values())

    return {
        "run_at": run_at,
        "commit": commit,
        "bench_id": bench_id,
        "kind": kind,
        "product_type": product_type,
        "outcome": outcome,
        "completed": int(completed),
        # A run nobody had to repair. This, not "completed", is the number that says the
        # pipeline is getting better: every archived success needed one or two repairs.
        "clean": int(completed and repair_total == 0),
        "failure_cause": "" if completed else (cause or "unknown"),
        "final_stage": result.get("final_stage") or execution.get("stage") or "",
        "duration_seconds": round(float(result.get("duration_seconds") or 0.0), 1),
        "repair_attempts": repair_total,
        "repairs_by_gate": ";".join(f"{key}={value}" for key, value in sorted(gates.items())),
        "gates_passed": int(test_summary.get("passed") or 0),
        "gates_failed": int(test_summary.get("failed") or 0),
        "cli_calls": len(timings),
        "cli_seconds": round(sum(elapsed for elapsed, _ in timings), 1),
        "cli_timeouts": sum(1 for event in events if _CEILING_MARKER in (event.get("message") or "")),
        "cost_usd": round(float((result.get("usage") or {}).get("total_cost_usd") or 0.0), 3),
        "output_tokens": int((result.get("usage") or {}).get("output_tokens") or 0),
        # The ratio that says whether the ceiling is manufacturing failures. Near 1.0 means
        # the budget, not the work, decided the outcome.
        "cli_max_budget_ratio": round(max((elapsed / budget for elapsed, budget in timings), default=0.0), 3),
        "models": ";".join(models_used(transcript)),
        "artifacts": len(execution.get("artifacts") or ()),
        "delivery_report": int(any((artifact.get("name") or "") == "delivery_report.md" for artifact in execution.get("artifacts") or ())),
        "container_http_200": int(any("HTTP 200" in (event.get("message") or "") for event in events)),
        "notes": "",
    }


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. Deliberately not interpolating: with a handful of runs an
    interpolated p90 invents a duration no run had."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


def summarise_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in rows]
    if not rows:
        return {"runs": 0}
    completed = [row for row in rows if int(row.get("completed") or 0)]
    durations = [float(row["duration_seconds"]) for row in completed if float(row.get("duration_seconds") or 0)]

    causes: dict[str, int] = {}
    for row in rows:
        if int(row.get("completed") or 0):
            continue
        causes[str(row.get("failure_cause") or "unknown")] = causes.get(str(row.get("failure_cause") or "unknown"), 0) + 1

    gates: dict[str, int] = {}
    for row in rows:
        for entry in str(row.get("repairs_by_gate") or "").split(";"):
            if not entry:
                continue
            key, _, value = entry.partition("=")
            gates[key] = gates.get(key, 0) + int(value or 0)

    ratios = [float(row.get("cli_max_budget_ratio") or 0.0) for row in rows]
    timeouts = sum(int(row.get("cli_timeouts") or 0) for row in rows)
    costs = [float(row.get("cost_usd") or 0.0) for row in rows]
    return {
        "cli_timeouts": timeouts,
        "cost_usd_total": round(sum(costs), 2),
        "cost_usd_median": round(median([c for c in costs if c]), 2) if any(costs) else 0.0,
        "runs": len(rows),
        "completed": len(completed),
        # The two headline numbers. Completion yield is what a client experiences; clean
        # yield is what says the generated code is getting better rather than the repair
        # loop getting more patient.
        "completion_yield": round(len(completed) / len(rows), 3),
        "clean_yield": round(sum(int(row.get("clean") or 0) for row in rows) / len(rows), 3),
        "median_duration_seconds": round(median(durations), 1) if durations else 0.0,
        "p90_duration_seconds": round(_percentile(durations, 0.9), 1),
        "repairs_total": sum(int(row.get("repair_attempts") or 0) for row in rows),
        "repairs_by_gate": dict(sorted(gates.items(), key=lambda item: -item[1])),
        "failures_by_cause": dict(sorted(causes.items(), key=lambda item: -item[1])),
        "max_budget_ratio": round(max(ratios, default=0.0), 3),
    }


def format_report(summary: dict[str, Any]) -> str:
    if not summary.get("runs"):
        return "no runs recorded yet"
    lines = [
        f"runs                {summary['runs']}",
        f"completion yield    {summary['completion_yield']:.0%}  ({summary['completed']}/{summary['runs']})",
        f"clean yield         {summary['clean_yield']:.0%}  (completed with zero repairs)",
        f"duration median     {summary['median_duration_seconds']:.0f}s",
        f"duration p90        {summary['p90_duration_seconds']:.0f}s",
        f"repair attempts     {summary['repairs_total']}",
    ]
    if summary.get("cost_usd_total"):
        lines.append(f"cost                ${summary['cost_usd_total']:.2f} total, ${summary['cost_usd_median']:.2f} median per run")
    if summary.get("cli_timeouts"):
        lines.append(f"calls hit the ceiling {summary['cli_timeouts']}   <-- these are the ones the budget question is about")
    if summary.get("repairs_by_gate"):
        lines.append("repairs by gate     " + ", ".join(f"{key} {value}" for key, value in summary["repairs_by_gate"].items()))
    if summary.get("failures_by_cause"):
        lines.append("failures by cause   " + ", ".join(f"{key} {value}" for key, value in summary["failures_by_cause"].items()))
    ratio = summary.get("max_budget_ratio") or 0.0
    if ratio:
        warning = "   <-- the budget, not the work, is deciding outcomes" if ratio >= 0.9 else ""
        lines.append(f"worst budget ratio  {ratio:.0%}{warning}")
    return "\n".join(lines)
