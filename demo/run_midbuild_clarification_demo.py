"""Prove the mid-build clarification checkpoint against a real running backend.

The pipeline pauses after the UI shell to ask the client about assumptions it had to make,
then resumes from its own checkpoints with the answers folded into the next phase's prompt.

This script plays the client's part programmatically, so the loop can be demonstrated --
and regression-tested -- without a human sitting in front of it. It reuses the same backend
launcher as the main demo, with the checkpoint switched on.

Usage:
    python demo/run_midbuild_clarification_demo.py

Requires Docker running and the `claude` CLI authenticated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PORT = 8100
BASE_URL = f"http://127.0.0.1:{PORT}"
TOKEN = "test-only-local-token-32-bytes-long"

# The Focus Timer order, reused verbatim from the main demo because it is the one order
# proven to build and pass every gate on this machine. This script exists to prove the
# pause -> answer -> resume loop, so the build ahead of the checkpoint should be the least
# risky one available, not a fresh variable.
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

# What the "client" answers when the pipeline stops to ask. Free text on the open question
# so the correction is visible in the next phase's prompt.
CLIENT_CORRECTION = "Show today's completed-session count at the top, above the countdown ring."


def log(message: str, *, prefix: str = "  ") -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}]{prefix}{message}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def request(method: str, path: str, payload: dict | None = None, *, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"X-FreelancerStudio-Token": TOKEN, "Origin": BASE_URL, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} on {method} {path}: {exc.read().decode('utf-8', errors='replace')}") from None


def start_backend() -> subprocess.Popen:
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    log(f"starting backend on {BASE_URL} with the mid-build checkpoint enabled")
    process = subprocess.Popen(
        [str(python), str(REPO_ROOT / "demo" / "demo_backend.py")],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "FREELANCERSTUDIO_DEMO_PORT": str(PORT),
            "FREELANCERSTUDIO_ENABLE_MIDBUILD_CLARIFICATION": "1",
        },
    )
    for _ in range(60):
        try:
            request("GET", "/health", timeout=2)
            log("backend is up")
            return process
        except Exception:
            if process.poll() is not None:
                raise SystemExit("backend exited during startup")
            time.sleep(1)
    process.terminate()
    raise SystemExit("backend did not become healthy in 60s")


def poll_until(order_id: str, wanted: set[str], *, seen: set[str]) -> dict:
    while True:
        time.sleep(5)
        execution = (request("GET", f"/api/orders/{order_id}/execution").get("execution") or {})
        for event in execution.get("events", []):
            if event["id"] in seen:
                continue
            seen.add(event["id"])
            log(f"  [{event.get('stage','?'):<15}] {event.get('message','')}", prefix="  ")
        if execution.get("status") in wanted:
            return execution


def main() -> int:
    backend = start_backend()
    seen: set[str] = set()
    try:
        section("1/4  ORDER AND PLANNER")
        order_id = request("POST", "/api/orders", DEMO_ORDER)["order"]["id"]
        state = request("POST", f"/api/orders/{order_id}/autopilot")
        log(f"brief approved, assumptions recorded: {len(state['brief']['assumptions'])}")
        for assumption in state["brief"]["assumptions"]:
            log(f"  - {assumption}", prefix="    ")

        readiness = request("GET", f"/api/orders/{order_id}/readiness?mode=production")
        if not readiness["can_run_live"]:
            raise SystemExit(f"cannot run live: {readiness.get('blockers')}")

        section("2/4  BUILD STARTS, THEN PAUSES TO ASK")
        request("POST", f"/api/orders/{order_id}/execution", {"mode": "production", "live": True})
        execution = poll_until(order_id, {"awaiting_user", "succeeded", "failed", "cancelled"}, seen=seen)
        if execution.get("status") != "awaiting_user":
            log(f"run ended as {execution.get('status')} without pausing")
            return 1

        questions = execution.get("pending_questions", [])
        log("")
        log(f"PAUSED. The pipeline is asking {len(questions)} question(s):")
        for question in questions:
            log(f"  - {question['text']}", prefix="    ")
            log(f"    why: {question['reason']}", prefix="    ")

        section("3/4  THE CLIENT ANSWERS")
        open_question = next((q for q in questions if q["id"].endswith("shell-corrections")), questions[-1])
        answers = [{"question_id": open_question["id"], "value": CLIENT_CORRECTION}]
        log(f'answering "{open_question["id"]}": {CLIENT_CORRECTION}')
        request("POST", f"/api/orders/{order_id}/execution/answers", {"answers": answers})

        section("4/4  BUILD RESUMES FROM ITS CHECKPOINTS")
        execution = poll_until(order_id, {"succeeded", "failed", "cancelled"}, seen=seen)
        result = execution.get("result") or {}
        log("")
        log(f"outcome: {execution.get('status')} in {result.get('duration_seconds')}s")

        workspace = next(
            (p for p in sorted((REPO_ROOT / "generated_projects").glob(f"*{order_id}*"), key=lambda x: x.stat().st_mtime, reverse=True)),
            None,
        )
        if workspace is not None:
            prompt_file = workspace / "execution_prompt_core_feature.md"
            if prompt_file.is_file():
                carried = CLIENT_CORRECTION in prompt_file.read_text(encoding="utf-8")
                log(f"correction reached the core-feature prompt: {carried}")
                if not carried:
                    return 1
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
