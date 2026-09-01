"""Drive a small static-page order through the Studio UI in Chromium."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "artifacts" / "ui_small_order"
TOKEN = "test-only-local-token-32-bytes-long"
BASE = "http://127.0.0.1:8099"


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(
            extra_http_headers={
                "X-FreelancerStudio-Token": TOKEN,
            },
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(60_000)

        def rewrite_localhost(route):
            url = route.request.url
            if "localhost:8099" in url:
                route.continue_(url=url.replace("localhost:8099", "127.0.0.1:8099"))
            else:
                route.continue_()

        page.route("**/*", rewrite_localhost)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        shot(page, "01_loaded")

        closer = page.get_by_role("button", name="Close this window")
        if closer.count() and closer.first.is_visible():
            closer.first.click()
            page.wait_for_timeout(400)

        page.get_by_role("button", name="Create Project").first.click()
        page.wait_for_timeout(500)
        shot(page, "02_create_project")

        page.locator("input").first.fill("Mini Card")
        plate = ROOT / "local_assets" / "elena_shore.webp"
        page.locator("textarea").first.fill(
            "A single HTML page showing the name Mini Card, one sentence "
            "'Local Studio test', and a button labelled Email. "
            "Elena must prepare a design concept and animate the client plate "
            f"as a living full-viewport background: {plate}. "
            "Slow camera drift, breathing sunlight, water glints. "
            "Keep the card on a frosted glass panel."
        )
        page.locator("select").first.select_option("static_page")
        shot(page, "03_form_filled")

        page.get_by_role("button", name="Create & run automatically").click()
        page.wait_for_timeout(1000)
        shot(page, "04_autopilot_clicked")
        page.get_by_text("Mini Card", exact=False).first.wait_for(timeout=60_000)

        # Autopilot talks to Grok CLI; allow a few minutes.
        page.get_by_role("button", name="Start live build").wait_for(timeout=300_000)
        shot(page, "05_execution_ready")

        page.locator("label", has_text="write files").locator("input[type=checkbox]").check()
        page.get_by_role("button", name="Start live build").click()
        shot(page, "06_live_started")

        deadline = time.time() + 20 * 60
        last = ""
        while time.time() < deadline:
            body = page.inner_text("body")
            if body != last:
                last = body
            status = page.locator("#ow-result-title")
            if status.count():
                label = status.inner_text().strip().lower()
                if label in {"succeeded", "failed", "cancelled"}:
                    shot(page, "07_result")
                    print(f"RESULT:{label}")
                    print(page.inner_text(".ow-page")[:4000])
                    browser.close()
                    return 0 if label == "succeeded" else 2
            page.wait_for_timeout(4000)

        shot(page, "07_timeout")
        print("RESULT:timeout")
        print(page.inner_text("body")[:4000])
        browser.close()
        return 3


if __name__ == "__main__":
    sys.exit(main())
