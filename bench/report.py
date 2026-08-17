"""Read recorded runs and print the two numbers the week is about.

    python bench/report.py                          # bench/results.csv
    python bench/report.py --transcripts demo/transcripts   # past demo runs
    python bench/report.py --per-run                 # one line per run as well

Accepts archived demo transcripts as well as bench rows, so the baseline can be read off
runs that happened before the benchmark existed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bench.metrics import CSV_COLUMNS, format_report, row_from_transcript, summarise_rows  # noqa: E402
from bench.orders import BENCH_ORDERS  # noqa: E402

DEFAULT_CSV = REPO_ROOT / "bench" / "results.csv"


def _identify(transcript: dict) -> tuple[str, str, str]:
    """Best-effort match of an archived transcript to a benchmark order.

    Matched on the order's own title words rather than on the file name, which carries only
    a timestamp. Anything unrecognised is recorded as adhoc rather than being forced onto a
    benchmark id it was not run against.
    """
    goal = (transcript.get("summary") or {}).get("brief_goal") or ""
    haystack = goal.casefold()
    for order in BENCH_ORDERS:
        if all(word in haystack for word in order.title.casefold().split()):
            return order.id, order.kind, order.product_type
    for order in BENCH_ORDERS:
        # The reading-journal and recipe-box orders are quoted verbatim in the bench set, so
        # their opening clause identifies them even when the title does not survive.
        if order.description[:60].casefold() in haystack:
            return order.id, order.kind, order.product_type
    return "adhoc", "unknown", "unknown"


def rows_from_transcripts(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            transcript = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"skipped unreadable transcript {path.name}", file=sys.stderr)
            continue
        bench_id, kind, product_type = _identify(transcript)
        row = row_from_transcript(
            transcript,
            run_at=path.stem.replace("demo-", "").replace("bench-", ""),
            bench_id=bench_id,
            kind=kind,
            product_type=product_type,
        )
        row["notes"] = path.name
        rows.append(row)
    return rows


def rows_from_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--transcripts", type=Path, help="read transcripts from this directory instead of the CSV")
    parser.add_argument("--per-run", action="store_true", help="also print one line per run")
    args = parser.parse_args()

    rows = rows_from_transcripts(args.transcripts) if args.transcripts else rows_from_csv(args.csv)
    source = args.transcripts or args.csv
    print(f"source: {source}")
    print()
    if args.per_run:
        header = ("run_at", "bench_id", "outcome", "duration_seconds", "repair_attempts", "failure_cause")
        widths = [max(len(str(row.get(column, ""))) for row in rows + [{column: column}]) for column in header]
        print("  ".join(name.ljust(width) for name, width in zip(header, widths)))
        for row in rows:
            print("  ".join(str(row.get(column, "")).ljust(width) for column, width in zip(header, widths)))
        print()
    print(format_report(summarise_rows(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
