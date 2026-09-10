import pytest
from playwright.sync_api import expect

from ui_regression_support import capture_evidence


# Needs a real browser: these drive Chromium through the `page`/`browser_session`
# fixtures and start a built Studio server. Marked so the default suite excludes them
# rather than failing on a machine without Playwright's browsers installed -- CI runs
# `-m "not external and not browser and not android"` and, unmarked, these were inside
# that selection while nothing in the workflow ever ran `playwright install`.
pytestmark = pytest.mark.browser


VIEWPORTS = [(1366, 768, 1), (1280, 720, 1.25), (1024, 768, 1.5)]


def _open_diagnostics(page, studio_server):
    page.goto(studio_server, wait_until="networkidle")
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    page.get_by_role("button", name="Info", exact=True).click()
    body = page.get_by_role("region", name="Info content")
    expect(body).to_be_focused()
    page.get_by_role("button", name="Show Diagnostics", exact=True).click()
    expect(body.locator(".space-y-2 > div").first).to_be_visible()
    return body


@pytest.mark.parametrize(("width", "height", "device_scale_factor"), VIEWPORTS)
def test_embedded_info_scrolls_without_clipping_at_supported_viewports(
    studio_server, browser_session, width, height, device_scale_factor
):
    with browser_session(
        viewport={"width": width, "height": height}, device_scale_factor=device_scale_factor
    ) as page:
        body = _open_diagnostics(page, studio_server)

        assert page.evaluate("window.devicePixelRatio") == device_scale_factor
        metrics = body.evaluate(
            "element => ({clientHeight: element.clientHeight, scrollHeight: element.scrollHeight})"
        )
        assert metrics["scrollHeight"] > metrics["clientHeight"]
        assert page.locator(".fs-info-page").evaluate("element => element.scrollTop") == 0

        body.hover()
        page.mouse.wheel(0, 500)
        page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle())

        body.evaluate("element => { element.scrollTop = element.scrollHeight }")
        last_card = body.locator(".space-y-2 > div").last
        expect(last_card).to_be_in_viewport()
        expect(page.locator(".info-modal-header")).to_be_in_viewport()
        expect(page.locator(".info-modal-footer")).to_be_in_viewport()
        capture_evidence(page, f"info_scroll_{width}x{height}_{device_scale_factor}")


def test_embedded_info_supports_keyboard_and_tab_navigation(studio_server, browser_session):
    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        body = _open_diagnostics(page, studio_server)

        body.focus()
        page.keyboard.press("PageDown")
        page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle())
        page.keyboard.press("End")
        page.wait_for_function(
            "element => element.scrollHeight - element.clientHeight - element.scrollTop < 2",
            arg=body.element_handle(),
        )
        page.keyboard.press("Home")
        page.wait_for_function("element => element.scrollTop < 2", arg=body.element_handle())
        page.keyboard.press("ArrowDown")
        page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle())
        page.keyboard.press("Tab")
        expect(page.get_by_role("button", name="Hide Diagnostics", exact=True)).to_be_focused()
