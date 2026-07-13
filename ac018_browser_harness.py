"""Reusable, multiline Playwright primitives for the isolated AC-018 workflow."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable
import uuid


def submit_comment_and_wait(page: Any, submit_button: Any, response_predicate: Callable[[Any], bool]) -> dict[str, Any]:
    """Submit a ticket-scoped comment and wait for its actual network response."""
    started = time.monotonic()
    with page.expect_response(response_predicate) as response_info:
        submit_button.click()
    response = response_info.value
    return {
        "method": response.request.method,
        "status": response.status,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def browser_launch_smoke() -> None:
    """Verify Playwright can start a clean desktop browser context without app interaction."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()
        page.set_content("<main>browser harness ready</main>")
        assert page.get_by_text("browser harness ready", exact=True).is_visible()
        context.close()
        browser.close()


def run_ac018() -> dict[str, Any]:
    """Run the isolated, ticket-scoped desktop control flow and persist AC-018."""
    from playwright.sync_api import sync_playwright
    import project_state

    workspace = Path(__file__).resolve().parent
    project_root = workspace / "generated_projects" / "test-project-recovery"
    state, error = project_state.load_project_state(str(project_root))
    if error:
        raise RuntimeError(error)
    expected_snapshot = "9b71fb4ce69b78c4396d3b6f34c3079d22e512049049cfa19d1d0bc1a5060e5e"
    if project_state.project_snapshot_fingerprint(str(project_root)) != expected_snapshot:
        raise RuntimeError("unexpected_source_snapshot")
    if state.get("contract_migration", {}).get("contract_fingerprint") != "7d7c5b8dc3120596d56fd94ce3858930c86bb2bc61d29bb6e29528d7d1a1e735":
        raise RuntimeError("unexpected_contract_fingerprint")

    marker = f"AC018-{uuid.uuid4().hex}"
    comment = f"AC018-COMMENT-{uuid.uuid4().hex}"
    artifact_root = project_root / ".freelancerstudio" / "evidence_artifacts" / f"ac018-{uuid.uuid4().hex}"
    artifact_root.mkdir(parents=True, exist_ok=True)
    db_path = Path(os.environ.get("TEMP", ".")) / f"ac018-{uuid.uuid4().hex}.sqlite"
    environment = os.environ.copy()
    environment["TICKET_TRACKER_DB"] = str(db_path)
    environment["TICKET_TRACKER_ADMIN_PASSWORD"] = f"verification-{uuid.uuid4().hex}"
    port = "8767"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", port], cwd=str(project_root), env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    artifacts: list[dict[str, str]] = []
    actions: dict[str, bool] = {}
    comment_network: dict[str, Any] = {}
    fallback_used = False
    try:
        time.sleep(2)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()
            def snapshot(name: str) -> None:
                path = artifact_root / name
                page.screenshot(path=str(path))
                artifacts.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            def card():
                return page.locator("article.ticket").filter(has_text=marker)
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
            page.fill("#password", environment["TICKET_TRACKER_ADMIN_PASSWORD"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_selector("#appView:not(.hidden)")
            actions["login"] = True; snapshot("01-login.png")
            page.fill("#clientName", marker); page.fill("#contact", "ac018@example.test"); page.fill("#problem", f"Primary {marker}")
            page.select_option("#priority", "high"); page.select_option("#status", "new")
            page.get_by_role("button", name="Save request", exact=True).click(); card().wait_for()
            actions["create"] = True; snapshot("02-created.png")
            card().get_by_role("button", name="View comments", exact=True).click()
            actions["view"] = True; snapshot("03-viewed.png")
            card().get_by_role("button", name="Edit", exact=True).click(); page.fill("#problem", f"Updated {marker}")
            page.get_by_role("button", name="Save request", exact=True).click(); card().wait_for()
            actions["edit"] = True; snapshot("04-edited.png")
            responses: list[Any] = []
            page.on("response", lambda response: responses.append(response) if "/comments" in response.url and response.request.method == "POST" else None)
            card().locator("input[name=body]").fill(comment)
            card().get_by_role("button", name="Comment", exact=True).click()
            page.wait_for_timeout(250)
            card().get_by_role("button", name="View comments", exact=True).click()
            try:
                card().locator(".comment").filter(has_text=comment).wait_for(timeout=2000)
            except Exception:
                fallback_used = True
                page.reload(wait_until="networkidle")
                card().wait_for()
                card().get_by_role("button", name="View comments", exact=True).click()
                card().locator(".comment").filter(has_text=comment).wait_for(timeout=3000)
            response = responses[-1] if responses else None
            comment_network = {"method": "POST", "status": response.status if response else None, "response_detected": bool(response)}
            actions["comment"] = bool(response and response.status in (200, 201)); snapshot("05-comment.png")
            page.fill("#search", marker); card().wait_for(); page.fill("#search", f"absent-{marker}"); page.wait_for_timeout(150)
            actions["search"] = not card().is_visible(); page.fill("#search", marker); card().wait_for(); snapshot("06-search.png")
            page.select_option("#filterStatus", "new"); card().wait_for(); page.select_option("#filterStatus", "closed"); page.wait_for_timeout(150)
            actions["status_filter"] = not card().is_visible(); page.select_option("#filterStatus", "new"); card().wait_for(); snapshot("07-status.png")
            page.select_option("#filterPriority", "high"); card().wait_for(); page.select_option("#filterPriority", "low"); page.wait_for_timeout(150)
            actions["priority_filter"] = not card().is_visible(); page.select_option("#filterPriority", "high"); card().wait_for(); snapshot("08-priority.png")
            page.once("dialog", lambda dialog: dialog.dismiss()); card().get_by_role("button", name="Delete", exact=True).click(); card().wait_for(); snapshot("09-delete-confirm.png")
            page.once("dialog", lambda dialog: dialog.accept()); card().get_by_role("button", name="Delete", exact=True).click(); page.wait_for_timeout(200)
            actions["delete"] = not card().is_visible(); snapshot("10-removed.png")
            context.close(); browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
    passed = all(actions.get(name) for name in ("login", "create", "view", "edit", "comment", "search", "status_filter", "priority_filter", "delete"))
    criterion = next(item for item in state["acceptance_criteria"] if item["id"] == "AC-018")
    evidence = {"status": "passed" if passed else "not_verified", "verdict": "passed" if passed else "not_verified", "verifier_type": "playwright_ticket_scoped_primary_controls", "assertions": list(actions), "collected_evidence": {"contract_fingerprint": state["contract_migration"]["contract_fingerprint"], "viewport": "1366x768", "marker_hash": hashlib.sha256(marker.encode()).hexdigest(), "comment_marker_hash": hashlib.sha256(comment.encode()).hexdigest(), "comment_network": comment_network, "comment_fallback_used": fallback_used, "actions": actions, "artifacts": artifacts}, "failure_reason": "" if passed else "browser_control_flow_incomplete"}
    state["target_path"] = str(project_root); state["title"] = state.get("project_name", ""); state["status"] = "failed_qa"
    project_state.append_evidence_record(state, criterion, evidence, str(project_root))
    project_state.persist_project_state(state, str(project_root))
    return {"passed": passed, "actions": actions, "artifacts": artifacts, "fallback_used": fallback_used, "comment_network": comment_network}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--run-ac018", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        browser_launch_smoke()
    if args.run_ac018:
        print(json.dumps(run_ac018(), ensure_ascii=True))
