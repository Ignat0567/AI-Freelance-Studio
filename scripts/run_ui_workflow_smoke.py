from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from completion_semantics import compute_completion_status
from execution_config import EffectiveExecutionConfig
from safe_command_runner import run_command
from snapshot_manager import SnapshotManager
from workflow_artifacts import RunArtifactStore, utc_now
from workflow_contracts import DevelopmentTask, ExecutionBrief


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def wait_for_url(url: str, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.25)
    return False


async def main() -> int:
    run_id = f"ui-smoke-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    project = Path(os.environ.get("TEMP", str(ROOT / "artifacts"))) / "opencode" / run_id
    if project.exists():
        shutil.rmtree(project)
    project.mkdir(parents=True)

    store = RunArtifactStore(run_id, ROOT / "artifacts" / "runs")
    run_dir = store.initialize({"smoke": "ui_todo", "project_root": str(project)})
    snapshots = SnapshotManager(str(project), str(run_dir))

    config = EffectiveExecutionConfig(
        connection_id="ui-smoke-opencode",
        provider_id="openai",
        model_id="openai/gpt-5.5",
        execution_mode="production_smoke",
        live_execution_enabled=True,
        project_root=str(project),
        backend_type="opencode",
    )
    store.write_json("effective_execution_config.json", config.to_dict())
    store.write_json("readiness.json", {"ready": True, "checks": {"opencode": {"status": "not_mocked", "command": "opencode.cmd run"}}})

    brief = ExecutionBrief(
        project_id=run_id,
        task_id=f"{run_id}-todo-ui",
        title="Minimal Todo UI",
        objective="Build a small browser Todo application with local persistence and tested add/complete/delete behavior.",
        project_root=str(project),
        requirements=["Add a task", "Mark a task completed", "Delete a task", "Persist tasks in localStorage"],
        constraints=["Use plain HTML/CSS/JavaScript", "No network dependencies", "Do not fake tests or screenshots"],
        acceptance_criteria=["Unit tests pass", "Browser can add, complete, delete, and persist a task", "Screenshot evidence exists"],
        allowed_paths=[str(project)],
        forbidden_paths=[str(Path.home())],
        implementation_steps=["Create app.js", "Create index.html", "Create styles.css", "Create server.mjs", "Run node tests"],
        test_commands=["node --test tests/todo.test.mjs", "node --check app.js"],
        validation_commands=["Playwright browser smoke"],
        requires_browser_validation=True,
        requires_security_review=False,
        approval_policy="smoke_auto_approved",
        sandbox_policy="project_root_containment",
        metadata={"run_id": run_id},
    )
    store.write_json("execution_brief.json", brief.to_dict())
    plan = [DevelopmentTask(id="todo-ui", title="Build Todo UI", description="Implement plain JS Todo app", dependencies=[], acceptance_criteria=brief.acceptance_criteria, expected_files=["index.html", "app.js", "styles.css", "server.mjs"], test_commands=brief.test_commands).to_dict()]
    store.write_json("plan.json", {"revision": 1, "immutable_for_run": True, "tasks": plan})

    write(project / "package.json", json.dumps({"type": "module", "scripts": {"test": "node --test tests/todo.test.mjs", "start": "node server.mjs"}}, indent=2))
    write(project / "tests" / "todo.test.mjs", """
import test from 'node:test';
import assert from 'node:assert/strict';
import { createTodoStore } from '../app.js';

test('todo store add complete delete and persistence', () => {
  const saved = {};
  const storage = { getItem: key => saved[key] ?? null, setItem: (key, value) => { saved[key] = value; } };
  const store = createTodoStore(storage);
  const first = store.add('Write evidence');
  assert.equal(store.list().length, 1);
  store.toggle(first.id);
  assert.equal(store.list()[0].completed, true);
  const restored = createTodoStore(storage);
  assert.equal(restored.list()[0].text, 'Write evidence');
  restored.remove(first.id);
  assert.equal(restored.list().length, 0);
});
""".strip() + "\n")

    before = snapshots.create_snapshot("before")
    prompt = (
        "Implement the Todo UI project in the current directory. Create index.html, app.js, styles.css, and server.mjs. "
        "app.js must export createTodoStore(storage) for the existing node:test tests and also wire the browser UI. "
        "The browser UI must have input#new-task, button#add-task, ul#todo-list, use localStorage, allow completing by checkbox, and deleting by button.delete-task. "
        "server.mjs must serve the directory on process.env.PORT or 4173. Run node --test tests/todo.test.mjs and node --check app.js."
    )
    oc = await run_command(["opencode.cmd", "run", prompt, "--model", config.model_id, "--dangerously-skip-permissions"], str(project), timeout_seconds=240)
    store.record_command({"name": "opencode_run", **oc.to_dict()})
    store.write_text("stdout/opencode.txt", oc.stdout)
    store.write_text("stderr/opencode.txt", oc.stderr)

    after = snapshots.create_snapshot("after")
    diff = snapshots.diff_snapshots(before, after)

    test_plan = {"steps": [{"id": "unit", "command": ["node", "--test", "tests/todo.test.mjs"], "required": True}, {"id": "syntax", "command": ["node", "--check", "app.js"], "required": True}]}
    store.write_json("test_results/test_plan.json", test_plan)
    unit = await run_command(["node", "--test", "tests/todo.test.mjs"], str(project), timeout_seconds=60)
    syntax = await run_command(["node", "--check", "app.js"], str(project), timeout_seconds=30)
    store.record_command({"name": "unit_tests", **unit.to_dict()})
    store.record_command({"name": "syntax_check", **syntax.to_dict()})
    store.write_text("stdout/unit_tests.txt", unit.stdout)
    store.write_text("stderr/unit_tests.txt", unit.stderr)
    tests_passed = unit.exit_code == 0 and syntax.exit_code == 0
    store.write_json("test_results/results.json", {"passed": tests_passed, "unit": unit.to_dict(), "syntax": syntax.to_dict()})

    browser_result = {"status": "not_executed", "passed": False, "console_errors": [], "failed_requests": [], "screenshots": []}
    server = None
    if tests_passed:
        port = 4173
        server_out = open(run_dir / "stdout" / "server.txt", "w", encoding="utf-8")
        server_err = open(run_dir / "stderr" / "server.txt", "w", encoding="utf-8")
        env = {**os.environ, "PORT": str(port)}
        server = subprocess.Popen(["node", "server.mjs"], cwd=str(project), stdout=server_out, stderr=server_err, text=True, env=env)
        ready = wait_for_url(f"http://127.0.0.1:{port}/", 20)
        if ready:
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={"width": 1280, "height": 800})
                    console_errors = []
                    failed_requests = []
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                    page.on("requestfailed", lambda req: failed_requests.append(req.url))
                    await page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                    await page.fill("#new-task", "Ship evidence")
                    await page.click("#add-task")
                    await page.check("#todo-list input[type=checkbox]")
                    await page.reload(wait_until="networkidle")
                    completed = await page.locator("#todo-list li.completed").count() >= 1
                    await page.click("#todo-list .delete-task")
                    deleted = await page.locator("#todo-list li").count() == 0
                    shot = run_dir / "browser_evidence" / "todo_1280x800.png"
                    await page.screenshot(path=str(shot), full_page=True)
                    await browser.close()
                browser_result = {"status": "passed" if completed and deleted and not console_errors and not failed_requests else "failed", "passed": completed and deleted and not console_errors and not failed_requests, "console_errors": console_errors, "failed_requests": failed_requests, "screenshots": [str(shot)], "readiness_url": f"http://127.0.0.1:{port}/"}
            except Exception as exc:
                browser_result = {"status": "blocked", "passed": False, "reason": str(exc), "console_errors": [], "failed_requests": [], "screenshots": []}
        else:
            browser_result = {"status": "blocked", "passed": False, "reason": "server_readiness_timeout", "console_errors": [], "failed_requests": [], "screenshots": []}
        if server:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        server_out.close(); server_err.close()
    store.write_json("browser_evidence/result.json", browser_result)

    judge_input = {"execution_brief": brief.to_dict(), "plan": plan, "diff": diff, "tests": {"passed": tests_passed}, "browser": browser_result, "workflow_state": "product_review"}
    missing = []
    if not diff["changed_files"]: missing.append("diff")
    if not tests_passed: missing.append("test_results")
    if not browser_result.get("passed"): missing.append("browser_evidence")
    judge_result = {"verdict": "approved" if not missing else "insufficient_evidence", "score": 1.0 if not missing else 0.0, "passed_criteria": brief.acceptance_criteria if not missing else [], "failed_criteria": [] if not missing else brief.acceptance_criteria, "missing_evidence": missing, "blockers": missing, "recommendations": []}
    store.write_json("product_judge/input.json", judge_input)
    store.write_json("product_judge/result.json", judge_result)

    final = compute_completion_status({"execution_completed": oc.exit_code == 0, "changes_confirmed": bool(diff["changed_files"]), "required_tests_passed": tests_passed, "browser_required": True, "browser_passed": browser_result.get("passed"), "product_judge_required": True, "product_judge_verdict": judge_result["verdict"], "evidence_available": True, "blockers": judge_result["blockers"]})
    store.finalize(final["status"], {"project_root": str(project), "changed_files": diff["changed_files"], "tests_passed": tests_passed, "browser": browser_result, "product_judge": judge_result})
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "status": final["status"], "project_root": str(project)}, indent=2))
    return 0 if final["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
