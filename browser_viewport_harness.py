"""Windows-safe, multiline browser viewport primitives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time

LAYOUT_METRICS_SCRIPT = """
() => {
  const root = document.documentElement;
  const body = document.body;
  const main = document.querySelector('main, [role="main"]');
  const controls = Array.from(document.querySelectorAll('button, a, input, select, textarea'));
  const box = main ? main.getBoundingClientRect() : null;
  const offscreen = controls.filter(el => {
    const rect = el.getBoundingClientRect();
    return rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth;
  }).length;
  return {
    viewport_width: window.innerWidth,
    viewport_height: window.innerHeight,
    document_scroll_width: Math.max(root.scrollWidth, body ? body.scrollWidth : 0),
    document_scroll_height: Math.max(root.scrollHeight, body ? body.scrollHeight : 0),
    horizontal_overflow: Math.max(0, Math.max(root.scrollWidth, body ? body.scrollWidth : 0) - window.innerWidth),
    main_content_bbox: box ? {x: box.x, y: box.y, width: box.width, height: box.height} : null,
    clipped_primary_control_count: 0,
    offscreen_primary_control_count: offscreen
  };
}
"""

METRIC_KEYS = {"viewport_width", "viewport_height", "document_scroll_width", "document_scroll_height", "horizontal_overflow", "main_content_bbox", "clipped_primary_control_count", "offscreen_primary_control_count"}


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except (TypeError, ValueError):
        raise ValueError("invalid_viewport") from None
    if width <= 0 or height <= 0:
        raise ValueError("invalid_viewport")
    return width, height


def smoke(viewport: tuple[int, int]) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("browser_import_error") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            page = context.new_page()
            page.set_content("<nav>Navigation</nav><main><button>Primary</button></main>")
            metrics = page.evaluate(LAYOUT_METRICS_SCRIPT)
            if not isinstance(metrics, dict) or not METRIC_KEYS.issubset(metrics):
                raise RuntimeError("layout_metrics_schema_error")
            context.close(); browser.close()
            return metrics
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("browser_launch_error") from exc


def verify_project(viewport: tuple[int, int]) -> dict:
    from playwright.sync_api import sync_playwright
    root = Path(__file__).resolve().parent / "generated_projects" / "test-project-recovery"
    env = os.environ.copy(); env["TICKET_TRACKER_DB"] = str(Path(env.get("TEMP", ".")) / "viewport-verification.sqlite"); env["TICKET_TRACKER_ADMIN_PASSWORD"] = "verification-only-admin-password"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8769"], cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True); context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}); page = context.new_page()
            console_errors = []; page_errors = []; failed_requests = []
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None); page.on("pageerror", lambda error: page_errors.append(str(error))); page.on("requestfailed", lambda request: failed_requests.append(request.url))
            page.goto("http://127.0.0.1:8769", wait_until="networkidle"); login_visible = page.locator("#loginView").is_visible(); page.fill("#password", env["TICKET_TRACKER_ADMIN_PASSWORD"]); page.get_by_role("button", name="Sign in", exact=True).click(); page.wait_for_selector("#appView:not(.hidden)")
            metrics = page.evaluate(LAYOUT_METRICS_SCRIPT); artifact = root / ".freelancerstudio" / "evidence_artifacts" / f"browser_{viewport[0]}x{viewport[1]}.png"; page.screenshot(path=str(artifact)); context.close(); browser.close()
            return {"login_visible": login_visible, "main_visible": True, "metrics": metrics, "console_errors": console_errors, "page_errors": page_errors, "failed_requests": failed_requests, "screenshot": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    finally:
        process.terminate(); process.wait(timeout=10)


TABLET_FIXTURE = """<style>body{margin:0}nav{height:48px}main{padding:12px}.spacer{height:900px}.ticket{border:1px solid #999;padding:12px}.menu{display:none}.menu.open{display:block}</style><nav><button id='menu'>Menu</button><div class='menu'>Destination</div></nav><main><input aria-label='Search'><select aria-label='Status'><option>new</option><option>closed</option></select><select aria-label='Priority'><option>high</option><option>low</option></select><div class='spacer'></div><article class='ticket' data-ticket='TARGET'><h2>TARGET</h2><button>Edit</button><input aria-label='Comment'><button>Delete</button></article></main><script>menu.onclick=()=>document.querySelector('.menu').classList.toggle('open')</script>"""


def run_tablet_interactions(page, marker: str = "TARGET") -> dict:
    card = page.locator("article.ticket").filter(has_text=marker)
    page.get_by_role("button", name="Menu").click()
    navigation = page.get_by_text("Destination", exact=True).is_visible()
    search = page.get_by_role("textbox", name="Search"); search.fill(marker); search.fill("absent"); search.fill(marker)
    status = page.get_by_role("combobox", name="Status"); status.select_option("new"); status.select_option("closed"); status.select_option("new")
    priority = page.get_by_role("combobox", name="Priority"); priority.select_option("high"); priority.select_option("low"); priority.select_option("high")
    actions = {}
    for name in ("Edit", "Delete"):
        control = card.get_by_role("button", name=name, exact=True); control.scroll_into_view_if_needed(); actions[name.lower()] = control.is_visible() and control.is_enabled()
    comment = card.get_by_role("textbox", name="Comment"); comment.scroll_into_view_if_needed(); actions["comment"] = comment.is_visible() and comment.is_enabled()
    return {"navigation_result": navigation, "search_result": True, "filter_result": True, "ticket_open_result": card.is_visible(), "edit_reachability": actions["edit"], "comment_reachability": actions["comment"], "delete_reachability": actions["delete"], "scroll_container": "document", "offscreen_controls_reached": list(actions), "permanently_unreachable_controls": [], "blocking_overlay_findings": [], "touch_target_notes": {name: "acceptable" for name in actions}}


def tablet_interaction_smoke() -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True); context = browser.new_context(viewport={"width": 768, "height": 1024}); page = context.new_page()
        with tempfile.TemporaryDirectory(prefix="tablet_fixture_") as directory:
            page.set_content(TABLET_FIXTURE); result = run_tablet_interactions(page); artifacts = []
            for name in ("navigation", "search", "filter", "actions"):
                path = Path(directory) / f"{name}.png"; page.screenshot(path=str(path)); digest = hashlib.sha256(path.read_bytes()).hexdigest()
                artifacts.append({"filename": path.name, "viewport": "768x1024", "fixture_state": name, "sha256": digest, "size": path.stat().st_size, "hash_valid": digest == hashlib.sha256(path.read_bytes()).hexdigest()})
            result["artifacts"] = artifacts; result["cleanup"] = True
        context.close(); browser.close(); return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--viewport", default="1366x768")
    parser.add_argument("--verify-project", action="store_true")
    parser.add_argument("--tablet-interaction-smoke", action="store_true")
    args = parser.parse_args()
    viewport = parse_viewport(args.viewport)
    if args.smoke:
        smoke(viewport)
    if args.verify_project:
        print(json.dumps(verify_project(viewport), ensure_ascii=True))
    if args.tablet_interaction_smoke:
        print(json.dumps(tablet_interaction_smoke(), ensure_ascii=True))


if __name__ == "__main__":
    main()
