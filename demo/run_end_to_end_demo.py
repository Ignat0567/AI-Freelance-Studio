"""One reproducible end-to-end run: order -> planner -> coder -> reviewer -> self-healing.

Drives the real HTTP API against a real backend with live execution enabled, exactly the
way the product runs -- no in-process shortcuts, no fakes. Prints every stage transition
with a timestamp, then a summary of the two mechanisms worth watching:

  * role-based model routing -- which model each phase was routed to, and why
  * the self-healing loop     -- QA failures and the repair attempts that followed

Usage:
    python demo/run_end_to_end_demo.py
    python demo/run_end_to_end_demo.py --title "Recipe Box" --description "..."

Requires Docker running (QA runs in containers) and the `claude` CLI authenticated.
Writes a full JSON transcript to demo/transcripts/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Overridable so a leftover server on the default port does not block a run: the port has
# to be passed to demo_backend.py *and* used for this script's own BASE_URL and Origin
# header, so it is read once here rather than hardcoded.
PORT = int(os.environ.get("FREELANCERSTUDIO_DEMO_PORT", "8099"))
BASE_URL = f"http://127.0.0.1:{PORT}"
TOKEN = "test-only-local-token-32-bytes-long"
TRANSCRIPT_DIR = Path(__file__).resolve().parent / "transcripts"

# Fixed on purpose: the demo has to produce the same shape of run every time. "offline"
# and "notifications" are complexity keywords (see order_workflow/complexity.py), so the
# build phases route to the stronger model; none of them are backend-need signals (see
# phase_prompts.decide_backend_need), so the run stays a clean two-phase frontend build.
DEMO_ORDER = {
    "title": "Focus Timer",
    "description": (
        "A pomodoro focus timer that runs entirely offline in the browser. The user can "
        "start, pause and reset a 25-minute focus session, sees a large animated countdown "
        "ring, and gets a desktop notification when the session ends. Completed sessions "
        "for the day are shown as a simple streak of dots."
    ),
    "product_type": "web_app",
    "preferred_language": "en",
    "constraints": [],
}


def _stamp() -> str:
    # Local time on purpose: this output is read live while the demo runs, next to a
    # wall clock. The saved transcript keeps UTC timestamps for the record.
    return datetime.now().strftime("%H:%M:%S")


def _use_utf8_output() -> None:
    """Best effort: make this process's own output able to carry any character.

    Redirected to a file on a Russian Windows, stdout defaults to cp1251, and a generated
    page's text is not ASCII.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_use_utf8_output()


def _printable(text: str) -> str:
    """Whatever the stream can actually encode.

    A log line must never be able to kill the run it describes. On 2026-08-27 a three-order
    acceptance run died mid-build with UnicodeEncodeError: the visual gate had found an
    element labelled with "▲", the log prints the gate's findings, and stdout was cp1251
    because the run was redirected to a file. The pipeline's own messages are ASCII; the
    gate's findings are the *generated page's* text, which is arbitrary Unicode -- so this
    became reachable the moment those findings started being printed.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except (LookupError, UnicodeError):
        return text.encode("ascii", errors="replace").decode("ascii")


def log(message: str, *, prefix: str = "  ") -> None:
    print(_printable(f"[{_stamp()}]{prefix}{message}"), flush=True)


# Enough to carry a gate's verdict and its findings without turning the log into the
# transcript. A visual check fails with up to five findings plus its own header.
_DETAIL_LINES = 12


def log_event(event: dict, *, prefix: str = "  ") -> None:
    """One event as the operator sees it, its details included.

    A gate says what it found in `details`; the message only says that it failed. Until
    2026-08-26 just replay.py printed them, so a *recording* of a run was more informative
    than watching the run happen: live, a failing visual gate printed "QA failed; asking
    Codex to fix (attempt 1 of 2)" and nothing else, while the six layout findings behind it
    sat in the event stream and could only be read back out of the API afterwards.
    """
    marker = "*" if event.get("kind") == "milestone" else " "
    log(f"{marker} [{event.get('stage', '?'):<17}] {event.get('message', '')}", prefix=prefix)
    for detail in event.get("details") or ():
        # The tail, not the head. A gate's detail opens with the shell command it ran, its
        # exit code and an empty stderr, and closes with the verdict -- so the first six
        # lines of a failing visual check are the container invocation and the findings are
        # exactly what falls off. Watched live on 2026-08-26, this printed "Palette: 4/4
        # approved colours painted" and stopped.
        lines = str(detail).splitlines()
        if len(lines) > _DETAIL_LINES:
            log(f"      ... {len(lines) - _DETAIL_LINES} earlier line(s)", prefix=prefix)
            lines = lines[-_DETAIL_LINES:]
        for line in lines:
            log(f"      {line}", prefix=prefix)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def request(method: str, path: str, payload: dict | None = None, *, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "X-FreelancerStudio-Token": TOKEN,
            # Must match the bound port or LocalSecurityContext rejects it as invalid_origin.
            "Origin": BASE_URL,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {detail}") from None


def start_backend() -> subprocess.Popen:
    launcher = REPO_ROOT / "demo" / "demo_backend.py"
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    # A server already on this port is never the one this demo just launched -- most often
    # it is a leftover from an earlier run. Without this check the loop below sees its
    # /health answer, reports "backend is up", and the whole demo drives a stranger's
    # process (running whatever code it started with) while our own uvicorn dies unnoticed
    # on a bind error, several lines up in the output.
    # `request` raises SystemExit on an HTTP error, and SystemExit is not an Exception, so
    # `except Exception` let it through: a squatter that answers 404 on /health killed the
    # runner outright, printing its 404 page instead of this message. Which is exactly what
    # happened on 2026-08-27 -- three orphaned `python -m http.server 8099` processes, left
    # behind by repair calls that started them inside the workspace and never stopped them.
    occupied = False
    try:
        request("GET", "/health", timeout=2)
    except SystemExit:
        occupied = True
    except Exception:
        pass
    else:
        occupied = True
    if occupied:
        raise SystemExit(
            f"something is already serving {BASE_URL} -- stop it, or set "
            f"FREELANCERSTUDIO_DEMO_PORT to a free port, and run again"
        )

    log(f"starting backend on {BASE_URL}")
    # Kept on stderr rather than DEVNULL: a backend that dies during startup has to be
    # diagnosable from the demo's own output, not silently swallowed.
    process = subprocess.Popen(
        [str(python), str(launcher)],
        cwd=str(REPO_ROOT),
        env={**os.environ, "FREELANCERSTUDIO_DEMO_PORT": str(PORT)},
    )
    for _ in range(60):
        try:
            request("GET", "/health", timeout=2)
            log("backend is up")
            return process
        except Exception:
            if process.poll() is not None:
                raise SystemExit("backend exited during startup (see traceback above)")
            time.sleep(1)
    process.terminate()
    raise SystemExit("backend did not become healthy in 60s")


def workspace_delivery_report(order_id: str) -> str:
    """Read the delivery report out of the generated workspace.

    Located by globbing rather than reconstructing the path: the workspace directory name
    is `{title-slug}-{order_id}-{execution_id}` truncated to a filesystem-safe length, so
    rebuilding it here would duplicate (and eventually drift from) workspace.py's rules.
    """
    root = REPO_ROOT / "generated_projects"
    matches = sorted(root.glob(f"*{order_id}*"), key=lambda path: path.stat().st_mtime, reverse=True)
    for match in matches:
        report = match / "delivery_report.md"
        if report.is_file():
            return report.read_text(encoding="utf-8")
    return ""


def summarise(events: list[dict], execution: dict, brief: dict) -> dict:
    """Pull the two mechanisms the demo exists to show out of the raw event stream."""
    routing = []
    repairs = []
    qa_results = []
    for event in events:
        message = event.get("message", "")
        if "prompt to the coding CLI" in message:
            stage = event.get("stage", "?")
            model = message.split("model:")[-1].strip(" )") if "model:" in message else "(default)"
            routing.append({"stage": stage, "model": model})
        # "The repair call ..." lines belong in the self-healing section too: a repair that
        # never finished is the difference between "the gate's finding stands" and "nobody
        # re-checked it", and reading only the QA lines cannot tell those apart.
        if "QA failed; asking" in message or message.startswith("The repair call"):
            repairs.append({"stage": event.get("stage", "?"), "agent": event.get("agent", "?"), "message": message})
        if message.startswith("QA passed") or message.startswith("QA failed after"):
            qa_results.append({"stage": event.get("stage", "?"), "agent": event.get("agent", "?"), "message": message})

    result = execution.get("result") or {}
    return {
        "order_title": brief.get("goal", "")[:80],
        "brief_goal": brief.get("goal", ""),
        "target_users": brief.get("target_users", []),
        "backend_decision": next(
            (e.get("message") for e in events if e.get("stage") == "backend_decision"), None
        ),
        "model_routing": routing,
        "repair_attempts": repairs,
        "qa_results": qa_results,
        "deployment": [
            e.get("message") for e in events if e.get("stage") == "packaging"
        ],
        # The visual gate runs under Elena rather than BugCatcher, which is what separates
        # its QA lines from the build/test ones in the shared repair-loop event shape.
        "visual_check": [
            e.get("message") for e in events if e.get("agent") == "Elena"
        ],
        "outcome": result.get("outcome"),
        "duration_seconds": result.get("duration_seconds"),
        "final_stage": result.get("final_stage"),
        "test_summary": result.get("test_summary"),
        "warnings": result.get("warnings", []),
        "artifacts": [a.get("name") for a in execution.get("artifacts", [])],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-workspace", action="store_true", help="do not print the cleanup hint")
    parser.add_argument("--title", help="order title; defaults to the built-in Focus Timer demo")
    parser.add_argument("--description", help="what the app should do, in the client's own words")
    parser.add_argument("--product-type", default=DEMO_ORDER["product_type"], choices=["web_app", "bot", "static_page"])
    args = parser.parse_args()

    # The built-in order stays the default so the demo is still one command with no
    # arguments, but any order can be run without editing this file -- which is what
    # "try a different format" needs.
    order = dict(DEMO_ORDER)
    if args.title:
        order["title"] = args.title
    if args.description:
        order["description"] = args.description
    order["product_type"] = args.product_type

    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    backend = start_backend()

    try:
        section("1/5  ORDER  -- the client's request enters the system")
        state = request("POST", "/api/orders", order)
        order_id = state["order"]["id"]
        log(f"order {order_id}")
        log(f"status: {state['order']['status']}")
        for question in state.get("questions", []):
            log(f"planner asks: {question['text']}", prefix="    ")

        section("2/5  PLANNER  -- clarification, brief, approval, handoff")
        state = request("POST", f"/api/orders/{order_id}/autopilot")
        brief = state["brief"]
        log(f"brief {brief['id']} rev{brief['revision']}")
        log(f"goal: {brief['goal']}")
        log(f"audience: {', '.join(brief['target_users'])}")
        log(f"features: {len(brief['core_features'])}, constraints: {len(brief['technical_constraints'])}")
        log(f"design: {brief['elena_design_choice']}")
        log(f"handoff ready: {state['handoff_ready']}")

        readiness = request("GET", f"/api/orders/{order_id}/readiness?mode=production")
        blockers = readiness.get("blockers", [])
        log(f"live execution ready: {readiness['can_run_live']}" + (f" blockers={blockers}" if blockers else ""))
        if not readiness["can_run_live"]:
            raise SystemExit(f"cannot run live: {blockers}")

        section("3/5  CODER + REVIEWER + SELF-HEALING  -- live execution")
        log("phases: UI_SHELL -> CORE_FEATURE -> BACKEND_DECISION")
        log("each phase: coding CLI -> QA in Docker -> repair loop on failure")
        request("POST", f"/api/orders/{order_id}/execution", {"mode": "production", "live": True})

        seen: set[str] = set()
        events: list[dict] = []
        execution: dict = {}
        while True:
            time.sleep(5)
            state = request("GET", f"/api/orders/{order_id}/execution")
            execution = state.get("execution") or {}
            events = execution.get("events", [])
            for event in events:
                if event["id"] in seen:
                    continue
                seen.add(event["id"])
                log_event(event)
            if execution.get("status") in {"succeeded", "failed", "cancelled"}:
                break

        section("4/5  RESULT")
        summary = summarise(events, execution, brief)
        log(f"outcome: {execution.get('status')} in {summary['duration_seconds']}s")
        log("")
        log("role-based model routing:")
        for entry in summary["model_routing"]:
            log(f"  {entry['stage']:<17} -> {entry['model']}", prefix="    ")
        log("")
        log("self-healing loop:")
        if summary["repair_attempts"]:
            for entry in summary["repair_attempts"]:
                log(f"  {entry['stage']}: {entry['message']}", prefix="    ")
        else:
            log("  no QA failures -- nothing to repair this run", prefix="    ")
        for entry in summary["qa_results"]:
            log(f"  {entry['stage']:<15} [{entry['agent']}] {entry['message']}", prefix="    ")
        log("")
        log("visual check (palette / WCAG contrast / mobile layout):")
        for message in summary["visual_check"] or ["  (gate did not run)"]:
            log(f"  {message}", prefix="    ")
        log("")
        log(f"backend decision: {summary['backend_decision']}")
        log("")
        log("container deploy:")
        for message in summary["deployment"] or ["  (stage did not run)"]:
            log(f"  {message}", prefix="    ")
        report = workspace_delivery_report(order_id).strip()
        for line in report.splitlines():
            if line.startswith(("Image:", "Run locally:")):
                log(f"  {line}", prefix="    ")
        log("")
        log(f"artifacts: {', '.join(summary['artifacts'])}")
        if summary["warnings"]:
            log(f"warnings: {summary['warnings']}")

        section("5/5  TRANSCRIPT")
        name = f"demo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path = TRANSCRIPT_DIR / name
        path.write_text(
            json.dumps({"summary": summary, "events": events, "execution": execution}, indent=2),
            encoding="utf-8",
        )
        log(f"saved {path.relative_to(REPO_ROOT)}")
        log(f"total wall clock: {int(time.time() - started)}s")
        return 0 if execution.get("status") == "succeeded" else 1
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        log("backend stopped")


if __name__ == "__main__":
    raise SystemExit(main())
