"""Which build is this process actually running?

On 2026-08-21 a palette fix was committed at 09:44. The desktop app's backend had started at
21:46 the evening before and still held the pre-fix modules in memory, so a live run
reproduced the old behaviour exactly. The only way that became visible was reading a
generated CSS file out of the workspace mid-run; without that it would have cost around $12
and twenty minutes to discover, and the conclusion drawn would have been "the fix does not
work" rather than "this window is old".

Nothing in the product could answer the question. The bench runner is immune by
construction -- it starts its own backend per invocation and stamps the commit into every
CSV row -- but the application a client actually uses had no equivalent.

So the commit is captured once, at import, and compared against the working tree on demand:

* `RUNNING_BUILD` is what this process loaded. Read at startup precisely because a later
  reading would answer a different question -- the whole failure being that the files on
  disk had moved on while the process had not.
* `build_status()` compares it with HEAD now. Only meaningful inside a checkout; a packaged
  install has no working tree to compare against, and saying "unknown" there is honest
  where "up to date" would be a guess.

Deliberately not a hard failure. A stale process still runs, and refusing to serve because
someone committed during a session would be worse than the problem.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
# Long enough for a cold git on Windows, short enough that a hung git cannot delay startup.
_GIT_TIMEOUT_SECONDS = 10


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""


def read_build_commit() -> str:
    """The short HEAD, with -dirty when the tree carried uncommitted edits."""
    head = _git("rev-parse", "--short", "HEAD")
    if not head:
        return ""
    return f"{head}-dirty" if _git("status", "--porcelain") else head


# Captured at import: this is the commit whose code this process is executing, whatever the
# files on disk say later.
RUNNING_BUILD = read_build_commit()


@dataclass(frozen=True, slots=True)
class BuildStatus:
    running: str
    current: str
    stale: bool
    message: str


def build_status() -> BuildStatus:
    """What this process runs, what the checkout holds, and whether they have diverged."""
    current = read_build_commit()
    if not RUNNING_BUILD or not current:
        return BuildStatus(
            running=RUNNING_BUILD,
            current=current,
            stale=False,
            message="Not running from a git checkout, so the build cannot be identified.",
        )
    if RUNNING_BUILD == current:
        return BuildStatus(running=RUNNING_BUILD, current=current, stale=False, message=f"Running {RUNNING_BUILD}.")
    return BuildStatus(
        running=RUNNING_BUILD,
        current=current,
        stale=True,
        message=(
            f"This window is running {RUNNING_BUILD}, but the checkout is now at {current}. "
            "Restart Studio to pick up the newer code -- until then, changes made since will "
            "not take effect."
        ),
    )
