"""Replay an archived run's event stream, at a watchable pace, with nothing running.

A demo that depends on a provider being up, Docker being healthy and a login not having
expired is a demo that will fail in front of someone eventually -- and on 2026-08-17 all
three of those failed at least once in a single day. This replays a real recorded run so
the narrative can be shown on a train, on a stranger's laptop, or while the coding CLI is
rate-limited.

It is a recording and says so, twice: a banner before the first line and a footer after the
last one, both naming the transcript and the date the run actually happened. The point is a
demo that cannot be knocked over, not a demo that pretends. Anything that blurred that line
would be worth less than the live run it stands in for -- the entire value of this project's
evidence is that it is real.

    python demo/replay.py                       # newest successful run, 20x
    python demo/replay.py --speed 1             # true real time (~21 minutes)
    python demo/replay.py --transcript path.json
    python demo/replay.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from demo import run_end_to_end_demo as harness  # noqa: E402

TRANSCRIPT_DIRS = (REPO_ROOT / "bench" / "transcripts", REPO_ROOT / "demo" / "transcripts")
# A coding-CLI call is 3 to 11 minutes of one unchanging line. Scaled by --speed alone, the
# fast parts become unreadable before the slow parts become watchable, so long gaps are
# capped separately: the rhythm survives, the dead air does not.
DEFAULT_SPEED = 20.0
MAX_GAP_SECONDS = 6.0


def _display_path(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise -- a transcript handed in with
    --transcript may live anywhere, and formatting a path must never be what ends a demo."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_transcripts() -> list[Path]:
    found: list[Path] = []
    for directory in TRANSCRIPT_DIRS:
        if directory.is_dir():
            found.extend(sorted(directory.glob("*.json")))
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def pick_transcript(candidates: list[Path]) -> Path | None:
    """Newest *successful* run. A replay is shown to someone, and the failures are for
    reading afterwards, not for demonstrating."""
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (data.get("execution") or {}).get("status") == "succeeded":
            return path
    return None


def replay_schedule(events: list[dict], *, speed: float = DEFAULT_SPEED, max_gap: float = MAX_GAP_SECONDS) -> list[tuple[float, dict]]:
    """(delay before this event, event) pairs. Delays are real gaps, scaled then capped."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    schedule: list[tuple[float, dict]] = []
    previous: datetime | None = None
    for event in events:
        stamp = event.get("created_at")
        if not stamp:
            schedule.append((0.0, event))
            continue
        try:
            current = _parse_time(stamp)
        except ValueError:
            schedule.append((0.0, event))
            continue
        if previous is None:
            delay = 0.0
        else:
            # max(0, ...) because a transcript is not guaranteed monotonic: events are
            # emitted from several threads and two can land out of order by milliseconds.
            delay = min(max((current - previous).total_seconds(), 0.0) / speed, max_gap)
        schedule.append((delay, event))
        previous = current
    return schedule


def original_duration(events: list[dict]) -> float:
    stamps = []
    for event in events:
        value = event.get("created_at")
        if value:
            try:
                stamps.append(_parse_time(value))
            except ValueError:
                continue
    return (max(stamps) - min(stamps)).total_seconds() if len(stamps) >= 2 else 0.0


def banner(path: Path, events: list[dict], summary: dict) -> list[str]:
    when = ""
    for event in events:
        if event.get("created_at"):
            when = _parse_time(event["created_at"]).strftime("%Y-%m-%d %H:%M UTC")
            break
    minutes = original_duration(events) / 60
    return [
        "THIS IS A RECORDING, NOT A LIVE RUN.",
        f"Recorded {when}, took {minutes:.0f} minutes, replayed from {path.name}.",
        f"Order: {(summary.get('brief_goal') or '')[:96]}",
        "Nothing is being generated right now -- no provider, no Docker, no cost.",
    ]


def replay(path: Path, *, speed: float = DEFAULT_SPEED, sleeper=time.sleep) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = list(data.get("events") or [])
    summary = data.get("summary") or {}
    if not events:
        print(f"{path.name} carries no events to replay")
        return 1

    harness.section("REPLAY")
    for line in banner(path, events, summary):
        harness.log(line)
    print()

    for delay, event in replay_schedule(events, speed=speed):
        sleeper(delay)
        marker = "*" if event.get("kind") == "milestone" else " "
        harness.log(f"{marker} [{event.get('stage', '?'):<17}] {event.get('message', '')}", prefix="  ")
        for detail in event.get("details") or ():
            for detail_line in str(detail).splitlines()[:6]:
                harness.log(f"      {detail_line}", prefix="  ")

    execution = data.get("execution") or {}
    result = execution.get("result") or {}
    print()
    harness.section("END OF RECORDING")
    harness.log(f"Outcome: {execution.get('status')} in {result.get('duration_seconds', 0):.0f}s")
    if result.get("usage"):
        harness.log(f"Cost: ${result['usage'].get('total_cost_usd', 0):.2f}")
    harness.log(f"Replayed from {_display_path(path)} -- this was a recording.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="1 replays in real time")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    candidates = find_transcripts()
    if args.list:
        for path in candidates:
            print(_display_path(path))
        return 0

    path = args.transcript or pick_transcript(candidates)
    if path is None:
        print("no successful transcript found to replay", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"no such transcript: {path}", file=sys.stderr)
        return 1
    return replay(path, speed=args.speed)


if __name__ == "__main__":
    raise SystemExit(main())
