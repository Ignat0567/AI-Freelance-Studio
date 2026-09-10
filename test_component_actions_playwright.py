from playwright.sync_api import expect

from ui_regression_support import capture_evidence, load_ui_fixture
import pytest


# Needs a real browser: these drive Chromium through the `page`/`browser_session`
# fixtures and start a built Studio server. Marked so the default suite excludes them
# rather than failing on a machine without Playwright's browsers installed -- CI runs
# `-m "not external and not browser and not android"` and, unmarked, these were inside
# that selection while nothing in the workflow ever ran `playwright install`.
pytestmark = pytest.mark.browser


def test_missing_component_actions_pass_only_fixed_ids(studio_server, browser_session):
    components = load_ui_fixture("component_registry")
    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        page.add_init_script("""
            window.__officialActionIds = [];
            window.env = { openOfficialDownload: async id => {
                window.__officialActionIds.push(id);
                return { status: 'opened', downloadId: id };
            }};
        """)
        page.route("**/api/system/requirements", lambda route: route.fulfill(json={"components": components}))
        page.route("**/api/system/check", lambda route: route.fulfill(json={
            "results": [{**component, "status": "Missing", "installed": False, "version": "", "duration_ms": 2} for component in components],
            "summary": {"total": len(components), "installed": 0, "missing": len(components), "error": 0, "restart_required": 0, "duration_ms": 5},
        }))
        install_requests = []
        page.on("request", lambda request: install_requests.append(request.url) if "/api/system/install/" in request.url else None)

        page.goto(studio_server, wait_until="networkidle")
        onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
        if onboarding_close.count():
            onboarding_close.click()
        page.get_by_role("button", name="Info", exact=True).click()
        page.get_by_role("button", name="Show Diagnostics", exact=True).click()
        page.get_by_role("button", name="Check All Components", exact=True).click()
        expect(page.get_by_test_id("check-summary")).to_contain_text(f"Missing: {len(components)}")
        capture_evidence(page, "component_registry_download_actions")

        expected = {component["id"]: component["action_label"] for component in components}
        for component_id, action_label in expected.items():
            row = page.locator(f'[data-component-id="{component_id}"]')
            expect(row).to_contain_text(components[list(expected).index(component_id)]["category"])
            expect(row).to_contain_text("Version: Not detected")
            row.get_by_role("button", name=action_label, exact=True).click()

        assert page.evaluate("window.__officialActionIds") == list(expected)
        assert install_requests == []
