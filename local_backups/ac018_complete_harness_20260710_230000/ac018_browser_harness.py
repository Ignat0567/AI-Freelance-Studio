"""Reusable, multiline Playwright primitives for the isolated AC-018 workflow."""
from __future__ import annotations

import argparse
import time
from typing import Any, Callable


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        browser_launch_smoke()
