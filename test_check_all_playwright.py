from playwright.sync_api import expect

from ui_regression_support import capture_evidence, load_ui_fixture
import pytest


# Needs a real browser: these drive Chromium through the `page`/`browser_session`
# fixtures and start a built Studio server. Marked so the default suite excludes them
# rather than failing on a machine without Playwright's browsers installed -- CI runs
# `-m "not external and not browser and not android"` and, unmarked, these were inside
# that selection while nothing in the workflow ever ran `playwright install`.
pytestmark = pytest.mark.browser


def _install_check_mock(page):
    page.evaluate(
        """({ components, checkResults }) => {
            window.__checkMode = 'pending-success';
            window.__checkRequestCount = 0;
            window.__installRequestCount = 0;
            const originalFetch = window.fetch.bind(window);
            const originalSetTimeout = window.setTimeout.bind(window);
            const originalClearTimeout = window.clearTimeout.bind(window);
            const response = (body, status = 200) => new Response(JSON.stringify(body), {
                status,
                headers: { 'Content-Type': 'application/json' },
            });
            window.setTimeout = (callback, delay, ...args) => {
                if (delay === 80) {
                    window.__fireCheckTimeout = () => callback(...args);
                    return 987654321;
                }
                return originalSetTimeout(callback, delay, ...args);
            };
            window.clearTimeout = timer => timer === 987654321 ? undefined : originalClearTimeout(timer);
            window.fetch = (input, options = {}) => {
                const url = String(input);
                if (url.includes('/api/system/install/')) window.__installRequestCount += 1;
                if (url.includes('/api/system/requirements')) {
                    return new Promise(resolve => {
                        window.__resolveRequirements = () => resolve(response({ components }));
                    });
                }
                if (!url.includes('/api/system/check')) return originalFetch(input, options);
                window.__checkRequestCount += 1;
                const mode = window.__checkMode;
                return new Promise((resolve, reject) => {
                    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
                    if (options.signal?.aborted) return abort();
                    options.signal?.addEventListener('abort', abort, { once: true });
                    if (mode === 'hang') return;
                    if (mode === 'backend-error') return resolve(response({ detail: 'Component service is unavailable.' }, 503));
                    window.__resolveCheck = () => resolve(response(checkResults));
                });
            };
        }""",
        {
            "components": load_ui_fixture("check_components"),
            "checkResults": load_ui_fixture("check_results"),
        },
    )


def test_check_all_loading_statuses_summary_errors_and_timeout(studio_server, browser_session):
    with browser_session(viewport={"width": 1280, "height": 720}) as page:
        page.goto(studio_server, wait_until="networkidle")
        onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
        if onboarding_close.count():
            onboarding_close.click()
        _install_check_mock(page)

        page.get_by_role("button", name="Info", exact=True).click()
        page.get_by_role("button", name="Show Diagnostics", exact=True).click()
        expect(page.get_by_text("Loading components...", exact=True)).to_be_visible()
        capture_evidence(page, "check_all_loading")
        page.evaluate("window.__resolveRequirements()")
        button = page.get_by_role("button", name="Check All Components", exact=True)
        button.click()

        checking_button = page.get_by_role("button", name="Checking...", exact=True)
        expect(checking_button).to_be_disabled()
        expect(page.locator('[data-status="Checking"]')).to_have_count(4)
        checking_button.evaluate("element => element.click()")
        assert page.evaluate("window.__checkRequestCount") == 1
        page.evaluate("window.__resolveCheck()")

        summary = page.get_by_test_id("check-summary")
        expect(summary).to_contain_text("Installed: 1")
        expect(summary).to_contain_text("Missing: 1")
        expect(summary).to_contain_text("Errors: 1")
        expect(summary).to_contain_text("Restart required: 1")
        expect(page.locator('[data-component-id="python"]')).to_have_count(1)
        expect(page.locator('[data-status="Installed"]')).to_have_count(1)
        expect(page.locator('[data-status="Missing"]')).to_have_count(1)
        expect(page.locator('[data-status="Error"]')).to_have_count(1)
        expect(page.locator('[data-status="Restart required"]')).to_have_count(1)
        expect(page.locator('[data-component-id="python"]')).to_contain_text("Detected")
        expect(page.locator('[data-component-id="python"]').get_by_role("button", name="Recheck", exact=True)).to_be_visible()
        expect(page.locator('[data-component-id="git"]').get_by_role("button", name="Detect Again", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Check All Components", exact=True)).to_be_enabled()
        assert page.evaluate("window.__installRequestCount") == 0
        capture_evidence(page, "check_all_summary")

        page.evaluate("window.__checkMode = 'backend-error'")
        page.get_by_role("button", name="Check All Components", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("Component service is unavailable.")
        expect(page.get_by_role("button", name="Check All Components", exact=True)).to_be_enabled()
        capture_evidence(page, "check_all_backend_error")

        page.evaluate("window.__checkMode = 'hang'; window.__CHECK_ALL_TIMEOUT_MS = 80")
        page.get_by_role("button", name="Check All Components", exact=True).click()
        expect(page.get_by_role("button", name="Checking...", exact=True)).to_be_disabled()
        page.evaluate("window.__fireCheckTimeout()")
        expect(page.get_by_role("alert")).to_contain_text("timed out")
        expect(page.get_by_role("button", name="Check All Components", exact=True)).to_be_enabled()
        assert page.evaluate("window.__installRequestCount") == 0
        capture_evidence(page, "check_all_timeout")
