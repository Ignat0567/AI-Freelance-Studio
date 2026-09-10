import json

import pytest
from playwright.sync_api import expect

from ui_regression_support import capture_evidence


# Needs a real browser: these drive Chromium through the `page`/`browser_session`
# fixtures and start a built Studio server. Marked so the default suite excludes them
# rather than failing on a machine without Playwright's browsers installed -- CI runs
# `-m "not external and not browser and not android"` and, unmarked, these were inside
# that selection while nothing in the workflow ever ran `playwright install`.
pytestmark = pytest.mark.browser


VIEWPORTS = [
    ("1366x768@100", 1366, 768, 1),
    ("1280x720@125", 1280, 720, 1.25),
    ("1024x768@150", 1024, 768, 1.5),
]
TABS = ("general", "appearance", "ai", "studio")


def _open_settings(page, studio_server):
    page.goto(studio_server, wait_until="networkidle")
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    page.locator('.fs-header-actions button[title="Open settings"]').click()
    body = page.get_by_role("region", name="Settings content")
    expect(body).to_be_visible()
    return body


def _metrics(locator):
    return locator.evaluate(
        """element => ({
            clientHeight: element.clientHeight,
            scrollHeight: element.scrollHeight,
            scrollTop: element.scrollTop,
            overflowY: getComputedStyle(element).overflowY,
            scrollbarGutter: getComputedStyle(element).scrollbarGutter,
        })"""
    )


def _ensure_runtime_overflow(body, last_element):
    return body.evaluate(
        """(element, last) => {
            element.querySelector('[data-testid="settings-overflow-probe"]')?.remove();
            const natural = {
                clientHeight: element.clientHeight,
                scrollHeight: element.scrollHeight,
            };
            if (element.scrollHeight <= element.clientHeight) {
                const probe = document.createElement('div');
                probe.dataset.testid = 'settings-overflow-probe';
                probe.setAttribute('aria-hidden', 'true');
                probe.style.height = `${element.clientHeight + 160}px`;
                probe.style.minHeight = `${element.clientHeight + 160}px`;
                last.before(probe);
            }
            return natural;
        }""",
        last_element.element_handle(),
    )


@pytest.mark.parametrize(("viewport_name", "width", "height", "device_scale_factor"), VIEWPORTS)
def test_embedded_settings_tabs_share_one_runtime_scroll_contract(
    studio_server, browser_session, viewport_name, width, height, device_scale_factor
):
    with browser_session(
        viewport={"width": width, "height": height}, device_scale_factor=device_scale_factor
    ) as page:
        body = _open_settings(page, studio_server)
        workspace = page.locator(".fs-settings-workspace")
        dialog = page.locator(".settings-inline-container")

        assert page.evaluate("window.devicePixelRatio") == device_scale_factor
        expect(dialog).to_be_visible()
        assert workspace.evaluate("element => getComputedStyle(element).overflowY") == "hidden"
        assert workspace.evaluate("element => element.scrollTop") == 0

        reports = []
        for index, tab in enumerate(TABS):
            page.locator(f'[data-settings-tab="{tab}"]').click()
            expect(body).to_have_attribute("data-active-tab", tab)
            page.wait_for_function(
                "tab => document.querySelector(`[data-testid=settings-last-${tab}]`)",
                arg=tab,
            )
            last_element = page.get_by_test_id(f"settings-last-{tab}")
            natural = _ensure_runtime_overflow(body, last_element)
            exercised = _metrics(body)

            assert exercised["scrollHeight"] > exercised["clientHeight"]
            assert exercised["overflowY"] == "auto"
            assert exercised["scrollbarGutter"] == "stable"
            assert workspace.evaluate("element => element.scrollTop") == 0
            scroll_owners = body.evaluate(
                """element => {
                    const owners = [];
                    for (let node = element; node; node = node.parentElement) {
                        const overflowY = getComputedStyle(node).overflowY;
                        if (['auto', 'scroll'].includes(overflowY) && node.scrollHeight > node.clientHeight) {
                            owners.push(node.className);
                        }
                        if (node.classList.contains('fs-settings-workspace')) break;
                    }
                    return owners;
                }"""
            )
            assert len(scroll_owners) == 1
            assert "settings-modal-body" in scroll_owners[0]

            body.focus()
            body.hover()
            page.mouse.wheel(0, 420)
            page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle())
            wheel_scroll_top = body.evaluate("element => element.scrollTop")

            page.keyboard.press("End")
            page.wait_for_function(
                "element => element.scrollHeight - element.clientHeight - element.scrollTop < 2",
                arg=body.element_handle(),
            )
            end_scroll_top = body.evaluate("element => element.scrollTop")
            expect(last_element).to_be_in_viewport()
            expect(page.locator(".settings-modal-header")).to_be_in_viewport()
            expect(page.locator(".settings-modal-tabs")).to_be_in_viewport()
            expect(page.locator(".settings-modal-footer")).to_be_in_viewport()

            page.keyboard.press("Home")
            page.wait_for_function("element => element.scrollTop < 2", arg=body.element_handle())
            home_scroll_top = body.evaluate("element => element.scrollTop")

            reports.append(
                {
                    "viewport": viewport_name,
                    "tab": tab,
                    "naturalClientHeight": natural["clientHeight"],
                    "naturalScrollHeight": natural["scrollHeight"],
                    "clientHeight": exercised["clientHeight"],
                    "scrollHeight": exercised["scrollHeight"],
                    "wheelScrollTop": wheel_scroll_top,
                    "endScrollTop": end_scroll_top,
                    "homeScrollTop": home_scroll_top,
                    "owner": ".settings-modal-body",
                }
            )

            if index + 1 < len(TABS):
                body.evaluate("element => { element.scrollTop = 120 }")
                next_tab = TABS[index + 1]
                page.locator(f'[data-settings-tab="{next_tab}"]').click()
                expect(body).to_have_attribute("data-active-tab", next_tab)
                assert body.evaluate("element => element.scrollTop") < 2

        assert workspace.evaluate("element => element.scrollTop") == 0
        capture_evidence(page, f"settings_scroll_{viewport_name}")
        print("SETTINGS_SCROLL_METRICS=" + json.dumps(reports, sort_keys=True))


def test_settings_tabs_are_keyboard_navigable(studio_server, browser_session):
    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        body = _open_settings(page, studio_server)
        appearance = page.locator('[data-settings-tab="appearance"]')
        appearance.focus()
        expect(appearance).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator('[data-settings-tab="general"]')).to_be_focused()
        page.keyboard.press("Enter")
        expect(body).to_have_attribute("data-active-tab", "general")
