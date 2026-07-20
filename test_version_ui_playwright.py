import json
from pathlib import Path

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.unit


def _open_info(page, studio_server):
    page.goto(studio_server, wait_until="networkidle")
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    page.get_by_role("button", name="Info", exact=True).click()
    return page.get_by_test_id("app-version")


def test_info_uses_exact_package_version_in_browser_fallback(studio_server, browser_session):
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))

    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        expect(_open_info(page, studio_server)).to_have_text(f"Version {package['version']}")


def test_info_handles_empty_packaged_version(studio_server, browser_session):
    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        page.add_init_script(
            "window.env = Object.freeze({ getAppVersion: async () => '   ' });"
        )
        expect(_open_info(page, studio_server)).to_have_text("Version unavailable")
