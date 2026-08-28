"""Run the benchmark set end to end and append one CSV row per run.

    python bench/run_bench.py                      # all eight, in order
    python bench/run_bench.py --only b01-profile-card --only b04-tip-splitter
    python bench/run_bench.py --kinds static_page  # just the cheap ones
    python bench/run_bench.py --dry-run            # print the plan and exit

Drives the real HTTP API against a real backend with live execution enabled -- the same path
the demo runner uses, reusing its transport rather than re-implementing it. One backend
serves the whole set; the runs are sequential because a live execution holds a coding CLI and
a Docker container, and Studio now bounds that to one at a time anyway.

Expect roughly 20 minutes per web_app order. The full set is an overnight job, which is what
it is for: the numbers are only worth anything as a before-and-after pair.

Requires Docker running and the `claude` CLI authenticated. Preflight will refuse the first
run in seconds if either is untrue, rather than discovering it 20 minutes in.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bench.metrics import CSV_COLUMNS, format_report, row_from_transcript, summarise_rows  # noqa: E402
from bench.orders import BENCH_ORDERS, BENCH_ORDERS_BY_ID, BenchOrder, order_payload  # noqa: E402
from demo import run_end_to_end_demo as harness  # noqa: E402
from demo.run_end_to_end_demo import EXECUTION_START_TIMEOUT_SECONDS  # noqa: E402

RESULTS_CSV = REPO_ROOT / "bench" / "results.csv"
POLL_SECONDS = 5
# Long enough for the phases and repairs a real run performs, short enough that a wedged run
# cannot consume the whole night and leave the remaining orders unmeasured.
#
# 3600 -> 5400 when the repair ceiling went 450 -> 900 (2026-08-26), and 5400 -> 7200 when a
# phase gained a third repair attempt (2026-08-27). A realistic_app runs two phases, each a
# build plus up to three repairs, so the theoretical worst case (2 x (1500 + 3 x 900) = 8400s)
# is past this number on purpose: it is set against the longest run actually recorded (2155s)
# with room for the new budgets, not against a worst case no run has approached. A run that
# really is wedged still costs at most this, and is recorded as product_bug rather than lost.
RUN_TIMEOUT_SECONDS = 7200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _transcript_dir(csv_path: Path) -> Path:
    """Transcripts live beside the results file, not beside this script.

    Acceptance runs from a fresh clone with --csv pointing at the main repository, so the
    rows outlived the checkout that produced them: three rows from 2026-08-23 name transcript
    files that were deleted with the clone. A row whose evidence is gone is an assertion, not
    a measurement -- and the missing one was the failed run, the only one worth reading.
    """
    return csv_path.resolve().parent / "transcripts"


_COMMIT_AT_STARTUP: str | None = None


def _run_commit() -> str:
    """HEAD as it was when this process started.

    Evaluated per row before, which meant an edit made while a three-hour set was running
    marked its later rows -dirty even though the backend was still executing the code it
    launched with. The commit a row reports has to be the commit that produced it.
    """
    global _COMMIT_AT_STARTUP
    if _COMMIT_AT_STARTUP is None:
        _COMMIT_AT_STARTUP = _current_commit()
    return _COMMIT_AT_STARTUP


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10, check=False
        )
    except OSError:
        return ""
    head = (completed.stdout or "").strip()
    if completed.returncode != 0 or not head:
        return ""
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15, check=False
    )
    # A row measured against uncommitted edits is not reproducible, and saying so is the
    # difference between a comparable record and a misleading one.
    return f"{head}-dirty" if (dirty.stdout or "").strip() else head


def _migrate_header(csv_path: Path) -> None:
    """Rewrite an existing results file whose header predates a new column.

    Without this, appending rows shaped like the new CSV_COLUMNS to a file carrying the old
    header writes values under the wrong names -- silently, and only in the rows recorded
    after the change. This file is meant to stay comparable for weeks and will gain columns
    more than once.
    """
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames == list(CSV_COLUMNS):
            return
        existing = list(reader)
    backup = csv_path.with_suffix(".csv.bak")
    csv_path.replace(backup)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in existing:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    print(f"migrated {csv_path.name} to the current columns (previous file kept as {backup.name})")


def _append_row(row: dict, csv_path: Path) -> None:
    """Written after every run, not at the end: an interrupted overnight session must leave
    the rows it did finish behind."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if csv_path.is_file():
        _migrate_header(csv_path)
    fresh = not csv_path.is_file()
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if fresh:
            writer.writeheader()
        writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})


def _failed_row(order: BenchOrder, run_at: str, cause: str, note: str) -> dict:
    """A run that never started is still a row. Left out, the set silently shrinks and the
    yield it reports is computed over a denominator nobody chose."""
    return {
        **{column: "" for column in CSV_COLUMNS},
        "run_at": run_at,
        "commit": _run_commit(),
        "bench_id": order.id,
        "kind": order.kind,
        "product_type": order.product_type,
        "outcome": "not_started",
        "completed": 0,
        "clean": 0,
        "failure_cause": cause,
        "duration_seconds": 0,
        "repair_attempts": 0,
        "notes": note,
    }


def run_one(order: BenchOrder, *, csv_path: Path) -> dict:
    run_at = _now()
    harness.section(f"{order.id}  ({order.kind})  --  {order.title}")
    harness.log(f"covers: {order.covers}")

    state = harness.request("POST", "/api/orders", order_payload(order))
    order_id = state["order"]["id"]
    harness.log(f"order {order_id}")

    state = harness.request("POST", f"/api/orders/{order_id}/autopilot")
    brief = state["brief"]
    harness.log(f"brief {brief['id']} rev{brief['revision']} -- {len(brief['core_features'])} features")

    readiness = harness.request("GET", f"/api/orders/{order_id}/readiness?mode=production")
    if not readiness.get("can_run_live"):
        blockers = readiness.get("blockers", [])
        harness.log(f"cannot run live: {blockers}")
        row = _failed_row(order, run_at, "environment", f"readiness blocked: {blockers}")
        _append_row(row, csv_path)
        return row

    harness.request(
        "POST",
        f"/api/orders/{order_id}/execution",
        {"mode": "production", "live": True},
        timeout=EXECUTION_START_TIMEOUT_SECONDS,
    )

    seen: set[str] = set()
    events: list[dict] = []
    execution: dict = {}
    deadline = time.time() + RUN_TIMEOUT_SECONDS
    while True:
        time.sleep(POLL_SECONDS)
        state = harness.request("GET", f"/api/orders/{order_id}/execution")
        execution = state.get("execution") or {}
        events = execution.get("events", [])
        for event in events:
            if event["id"] in seen:
                continue
            seen.add(event["id"])
            harness.log_event(event)
        if execution.get("status") in {"succeeded", "failed", "cancelled"}:
            break
        if execution.get("status") == "awaiting_user":
            # A run that stops to ask a human has finished, as far as an unattended
            # benchmark is concerned. Without this the poll loop waited out the full hour on
            # a run that had already said what it wanted -- three times over for three
            # orders. It cost tonight's run: a transient 401 blocked the first order at
            # 21:35 and the harness sat on it.
            asked = next((event.get("message", "") for event in reversed(events) if event.get("level") in {"warning", "error"}), "")
            harness.log(f"the run is waiting for a human: {asked}")
            row = _failed_row(order, run_at, "environment", f"awaiting_user: {asked}"[:500])
            _append_row(row, csv_path)
            return row
        if time.time() > deadline:
            harness.log(f"giving up after {RUN_TIMEOUT_SECONDS}s -- recording as wedged and moving on")
            row = _failed_row(order, run_at, "product_bug", f"no terminal status within {RUN_TIMEOUT_SECONDS}s")
            _append_row(row, csv_path)
            return row

    transcript = {
        "summary": harness.summarise(events, execution, brief),
        "events": events,
        "execution": execution,
        "bench": {"id": order.id, "kind": order.kind, "covers": order.covers},
    }
    transcript_dir = _transcript_dir(csv_path)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"bench-{order.id}-{run_at}.json"
    path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    row = row_from_transcript(
        transcript,
        run_at=run_at,
        bench_id=order.id,
        kind=order.kind,
        product_type=order.product_type,
        commit=_run_commit(),
    )
    row["notes"] = path.name
    _append_row(row, csv_path)
    harness.log(
        f"-> {row['outcome']} in {row['duration_seconds']}s, {row['repair_attempts']} repair(s)"
        + (f", cause={row['failure_cause']}" if row["failure_cause"] else "")
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=[], help="benchmark id; repeatable")
    parser.add_argument("--kinds", action="append", default=[], help="static_page | small_app | realistic_app")
    parser.add_argument("--csv", type=Path, default=RESULTS_CSV)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit without touching a backend")
    args = parser.parse_args()

    selected = list(BENCH_ORDERS)
    if args.only:
        unknown = [item for item in args.only if item not in BENCH_ORDERS_BY_ID]
        if unknown:
            raise SystemExit(f"unknown benchmark id(s): {unknown}")
        selected = [BENCH_ORDERS_BY_ID[item] for item in args.only]
    if args.kinds:
        selected = [order for order in selected if order.kind in args.kinds]
    if not selected:
        raise SystemExit("nothing selected")

    print(f"plan: {len(selected)} run(s) -> {args.csv}")
    for order in selected:
        print(f"  {order.id:<22} {order.kind:<15} {order.title}")
    if args.dry_run:
        return 0

    started = time.time()
    rows: list[dict] = []
    backend = harness.start_backend()
    try:
        for order in selected:
            try:
                rows.append(run_one(order, csv_path=args.csv))
            except SystemExit as exc:
                # One order failing to even reach execution must not end the night: the
                # remaining rows are the point of running a set rather than a single order.
                harness.log(f"{order.id} aborted: {exc}")
                row = _failed_row(order, _now(), "product_bug", f"aborted: {exc}")
                _append_row(row, args.csv)
                rows.append(row)
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        harness.log("backend stopped")

    harness.section("BENCH SUMMARY")
    print(format_report(summarise_rows(rows)))
    print(f"\nwall clock: {int(time.time() - started)}s")
    print(f"rows appended to {args.csv}")
    return 0 if all(int(row.get("completed") or 0) for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
