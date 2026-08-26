"""What the time ceilings are actually doing, read off the runs' own records.

    python bench/budget.py                            # every transcript on disk
    python bench/budget.py --transcripts bench/transcripts
    python bench/budget.py --per-call                 # every call, newest run last

Two ceilings decide whether a call lives: 1500s for a from-scratch build and 450s for a
repair (order_workflow/claude_code_client.py). "Is 450 too low" has been asked all week and
answered by the wrong number -- the distribution of *finished* calls, which is the one sample
that cannot contain a call the ceiling killed. Five finished repairs showed a comfortable p90
of 72% in the same week three others were killed at 100% and left no trace at all.

So this prints the strike count first and the distribution second, and says out loud when the
distribution is censored. A strike rate near zero means the ceiling is a safety net; a strike
rate that is not near zero means the ceiling is part of the pipeline's failure rate, and
raising it is a measurement, not an opinion.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bench.metrics import CallRecord, cli_call_records, percentile  # noqa: E402

DEFAULT_DIRS = (REPO_ROOT / "bench" / "transcripts", REPO_ROOT / "demo" / "transcripts")
# Named so the report can say which ceiling it is talking about. Read from the client rather
# than repeated here, so a change to a budget cannot leave this labelling stale.
try:
    from order_workflow.claude_code_client import CLAUDE_CODE_REPAIR_TIMEOUT, CLAUDE_CODE_TASK_TIMEOUT

    CEILING_NAMES = {CLAUDE_CODE_TASK_TIMEOUT: "build", CLAUDE_CODE_REPAIR_TIMEOUT: "repair"}
except ImportError:  # pragma: no cover - the report is still readable without the labels
    CEILING_NAMES = {}


def records_from(directories: list[Path]) -> list[tuple[str, CallRecord]]:
    """(run name, call) pairs, so a suspicious call can be traced back to its run."""
    found: list[tuple[str, CallRecord]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                transcript = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print(f"skipped unreadable transcript {path.name}", file=sys.stderr)
                continue
            for record in cli_call_records(transcript):
                found.append((path.stem, record))
    return found


def format_budget_report(pairs: list[tuple[str, CallRecord]]) -> str:
    if not pairs:
        return "no coding-CLI calls found -- nothing to say about the ceilings"
    lines: list[str] = []
    for ceiling in sorted({record.budget for _, record in pairs}):
        calls = [record for _, record in pairs if record.budget == ceiling]
        strikes = [record for record in calls if record.stopped_at_ceiling]
        finished = sorted(record.elapsed for record in calls if not record.stopped_at_ceiling)
        label = CEILING_NAMES.get(ceiling, "")
        heading = f"ceiling {ceiling}s" + (f"  ({label})" if label else "")
        rate = len(strikes) / len(calls)
        lines.append(f"{heading}   {len(calls)} call(s), {len(strikes)} stopped at it ({rate:.0%})")
        if finished:
            worst = max(finished)
            lines.append(
                f"    finished     n={len(finished)}  median={percentile(finished, 0.5):.0f}s"
                f"  p90={percentile(finished, 0.9):.0f}s  max={worst:.0f}s ({worst / ceiling:.0%})"
            )
        else:
            lines.append("    finished     none -- every call under this ceiling was killed by it")
        if strikes:
            # Said explicitly because the line above reads like the whole story and is not.
            lines.append(
                f"    ^ the distribution above excludes {len(strikes)} call(s) this ceiling ended;"
                " it cannot be used on its own to judge the ceiling"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcripts", type=Path, action="append", default=[], help="directory of transcripts; repeatable")
    parser.add_argument("--per-call", action="store_true", help="also list every call")
    args = parser.parse_args()

    directories = args.transcripts or list(DEFAULT_DIRS)
    pairs = records_from(directories)
    print("source: " + ", ".join(str(directory) for directory in directories))
    print()
    if args.per_call:
        for name, record in pairs:
            mark = "  <-- stopped at the ceiling" if record.stopped_at_ceiling else ""
            print(f"  {name:<46} {record.elapsed:7.0f}s / {record.budget}s ({record.elapsed / record.budget:.0%}){mark}")
        print()
    print(format_budget_report(pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
