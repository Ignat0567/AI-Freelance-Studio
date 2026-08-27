"""Stop what a coding-CLI call left running in a workspace.

Acceptance run 8 on 2026-08-27 finished at 17:29. Three processes did not:

    16:18:52  python -m http.server 8099
    16:25:10  python -m http.server 8099
    16:25:48  python -m http.server 8099
    16:26:52  python -m http.server 8100

Repair calls raised them inside the generated project to look at their own work and never
stopped them. `python -m http.server` binds every interface by default, so a client's project
directory was being served to the local network by accident; and 8099 is the port the demo
backend and the benchmark use, so the next run refused to start at all -- correctly, but
after an hour of nobody understanding why.

Killing the CLI's process tree does not cover this: the servers outlive the call that started
them, and an orphan on Windows is reparented and no longer reachable from the parent's pid.
What does identify them reliably is where they are running: a process whose working directory
is inside the generated workspace belongs to that build and to nothing else. Anything the
operator is running lives elsewhere.

Deliberately narrow:

* Only processes whose cwd is inside the workspace, never a cmdline match -- a path can
  appear in an unrelated command's arguments (an editor, a grep, this very module's tests).
* Never this process or its ancestors: the backend itself can have the workspace as its cwd
  in a test, and killing the thing doing the killing is a poor way to clean up.
* A refusal to answer (AccessDenied, a process that exited mid-scan) is skipped, not retried
  and not raised: cleanup must never be able to fail the run it is cleaning up after.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

# Given to callers to log; the shape is deliberately human-readable rather than structured,
# because its only consumer is a line in the event stream that a person reads.
StoppedProcess = str

# How long a process gets to exit after being asked politely, before it is killed.
TERMINATE_GRACE_SECONDS = 3.0


class _ProcessLike(Protocol):  # pragma: no cover - a description of psutil.Process
    pid: int

    def cwd(self) -> str: ...

    def cmdline(self) -> list[str]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...


def _default_processes() -> Iterable[Any]:
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is declared in requirements.txt
        return ()
    return psutil.process_iter()


def _is_inside(path: str, workspace: Path) -> bool:
    try:
        return Path(path).resolve() == workspace or workspace in Path(path).resolve().parents
    except (OSError, ValueError):
        return False


def stop_processes_left_in_workspace(
    workspace_path: Path | str,
    *,
    processes: Callable[[], Iterable[Any]] | None = None,
    self_pid: int | None = None,
) -> tuple[StoppedProcess, ...]:
    """Terminate anything still running inside `workspace_path`. Returns what it stopped.

    Called after every coding-CLI invocation, including the ones that timed out or crashed --
    those are exactly the calls most likely to leave something behind.
    """
    try:
        workspace = Path(workspace_path).resolve()
    except (OSError, ValueError):
        return ()
    if not workspace.is_dir():
        return ()

    mine = self_pid if self_pid is not None else os.getpid()
    stopped: list[StoppedProcess] = []
    for process in (processes or _default_processes)():
        try:
            if process.pid == mine:
                continue
            if not _is_inside(process.cwd(), workspace):
                continue
            argv = process.cmdline()
            # The executable's basename, not its path: a Windows Store python lives 100
            # characters deep, which pushed "-m http.server 8099" -- the only part that
            # identifies what was killed -- past the end of the line.
            argv = ([Path(argv[0]).name] + argv[1:]) if argv else []
            command = " ".join(argv)[:160] or f"pid {process.pid}"
        except Exception:
            # psutil raises several distinct exceptions here (NoSuchProcess, AccessDenied,
            # ZombieProcess), and a process that vanishes between the scan and the question
            # is the normal case rather than an error worth naming.
            continue
        try:
            process.terminate()
            try:
                process.wait(timeout=TERMINATE_GRACE_SECONDS)
            except Exception:
                process.kill()
        except Exception:
            continue
        stopped.append(f"{command} (pid {process.pid})")
    return tuple(stopped)
