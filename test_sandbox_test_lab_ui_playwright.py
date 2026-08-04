import json
import re

import pytest
from playwright.sync_api import expect

from ui_regression_support import capture_evidence


RUN_ONE = "11111111-1111-4111-8111-111111111111"
RUN_TWO = "22222222-2222-4222-8222-222222222222"


def _install_test_lab_mock(page, *, capability="available", deferred=False):
    configuration = json.dumps(
        {
            "capabilityMode": capability,
            "deferredCapabilities": deferred,
            "runOne": RUN_ONE,
            "runTwo": RUN_TWO,
        }
    )
    script = """(() => {
          const { capabilityMode, deferredCapabilities, runOne, runTwo } = __TEST_LAB_CONFIGURATION__;
          const state = {
            capabilityMode,
            deferredCapabilities,
            capabilityCalls: 0,
            launchCalls: [],
            statusCalls: 0,
            cancelCalls: 0,
            launchFailures: [],
            launchDeferred: false,
            statusFailures: [],
            statusDeferred: false,
            cancelDeferred: false,
            cancelAccepted: true,
            cancelStatus: 'cancelling',
            activeRunId: runOne,
            activeOperation: 'production_self_test',
            currentStatus: 'queued',
            terminal: false,
          };
          const capabilityResult = () => {
            if (state.capabilityMode === 'network') return { ok: false, status: 403, error: { code: 'network_bind_disallowed' } };
            if (state.capabilityMode === 'sandbox-unavailable') return { ok: true, status: 200, data: {
              available: false,
              reasons: [{ code: 'sandbox_capability_unavailable' }],
              backend_mode: 'loopback',
              operations: { launch: false, cancel: false, status: false, evidence: false },
            }};
            if (state.capabilityMode === 'raw-error') throw new Error('C:\\private\\backend-token raw failure');
            return { ok: true, status: 200, data: {
              available: true,
              reasons: [],
              backend_mode: 'loopback',
              operations: { launch: true, cancel: true, status: true, evidence: false },
            }};
          };
          const runData = runId => ({
            run_id: runId,
            operation: state.activeOperation,
            status: state.currentStatus,
            terminal: state.terminal,
            progress: {
              phase: state.terminal ? 'complete' : state.currentStatus === 'running' ? 'running_checks' : state.currentStatus === 'cancelling' ? 'cancelling' : 'not_started',
              message: state.terminal ? 'The run is complete.' : state.currentStatus === 'running' ? 'Sandbox checks are in progress.' : state.currentStatus === 'cancelling' ? 'Cancellation is in progress.' : 'The run is queued.',
            },
            result: state.terminal ? { outcome: state.currentStatus, manual_close_required: false } : null,
            errors: state.currentStatus === 'failed' ? ['run_failed'] : [],
          });
          const api = {
            getCapabilities: () => {
              state.capabilityCalls += 1;
              if (!state.deferredCapabilities) return Promise.resolve().then(capabilityResult);
              return new Promise((resolve, reject) => { state.resolveCapabilities = () => {
                state.deferredCapabilities = false;
                try { resolve(capabilityResult()); } catch (error) { reject(error); }
              }; });
            },
            launchRun: (operation, idempotencyKey) => {
              state.launchCalls.push({ operation, idempotencyKey });
              state.activeOperation = operation;
              const failure = state.launchFailures.shift();
              if (failure) return Promise.resolve({ ok: false, status: 0, error: { code: failure } });
              const result = () => {
                state.activeRunId = state.launchCalls.length > 2 ? runTwo : runOne;
                state.currentStatus = 'queued';
                state.terminal = false;
                return { ok: true, status: 202, data: { run_id: state.activeRunId, status: 'queued' } };
              };
              if (state.launchDeferred) return new Promise(resolve => { state.resolveLaunch = () => { state.launchDeferred = false; resolve(result()); }; });
              return Promise.resolve(result());
            },
            getRun: runId => {
              state.statusCalls += 1;
              const failure = state.statusFailures.shift();
              if (failure) return Promise.resolve({ ok: false, status: failure === 'run_not_found' ? 404 : 0, error: { code: failure } });
              if (state.statusDeferred) return new Promise(resolve => { state.resolveStatus = () => { state.statusDeferred = false; resolve({ ok: true, status: 200, data: runData(runId) }); }; });
              return Promise.resolve({ ok: true, status: 200, data: runData(runId) });
            },
            cancelRun: runId => {
              state.cancelCalls += 1;
              const result = () => {
                state.currentStatus = state.cancelStatus;
                state.terminal = state.cancelStatus === 'cancelled';
                return { ok: true, status: 200, data: { run_id: runId, status: state.cancelStatus, accepted: state.cancelAccepted } };
              };
              if (state.cancelDeferred) return new Promise(resolve => { state.resolveCancel = () => { state.cancelDeferred = false; resolve(result()); }; });
              return Promise.resolve(result());
            },
          };
          Object.defineProperty(window, 'env', { configurable: true, value: Object.freeze({ sandboxTestLab: Object.freeze(api) }) });
          window.__testLab = state;
        })()"""
    page.add_init_script(script.replace("__TEST_LAB_CONFIGURATION__", configuration))


def _open_test_lab(page, studio_server):
    page.goto(studio_server, wait_until="networkidle")
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    page.get_by_role("button", name="Sandbox Test Lab", exact=True).click()
    expect(page.get_by_test_id("sandbox-test-lab-page")).to_be_visible()


def _launch_selected(page, operation="Production Self-Test"):
    page.get_by_text(operation, exact=True).click()
    expect(page.get_by_role("radio", name=re.compile(operation))).to_be_checked()
    page.get_by_role("button", name="Review and launch", exact=True).click()
    dialog = page.get_by_role("dialog", name=re.compile(f"Launch {operation}"))
    expect(dialog).to_be_visible()
    expect(page.get_by_role("button", name="Go back", exact=True)).to_be_focused()
    page.get_by_role("button", name="Launch controlled run", exact=True).click()


def test_test_lab_available_launch_poll_cancel_and_terminal_race(studio_server, browser_session):
    with browser_session(viewport={"width": 1366, "height": 768}) as page:
        _install_test_lab_mock(page, deferred=True)
        _open_test_lab(page, studio_server)

        expect(page.get_by_text("Checking protected local availability...", exact=True)).to_be_visible()
        page.evaluate("window.__testLab.resolveCapabilities()")
        expect(page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible()
        expect(page.get_by_role("radio")).to_have_count(3)
        expect(page.get_by_role("radio", name=re.compile("Production Self-Test"))).to_be_enabled()
        expect(page.get_by_role("button", name="Review and launch", exact=True)).to_be_disabled()

        page.evaluate("window.__testLab.launchDeferred = true")
        _launch_selected(page)
        expect(page.get_by_role("button", name="Launching...", exact=True)).to_be_disabled()
        expect(page.get_by_role("dialog")).to_be_focused()
        page.get_by_role("button", name="Launching...", exact=True).evaluate("button => button.click()")
        assert page.evaluate("window.__testLab.launchCalls.length") == 1
        page.evaluate("window.__testLab.resolveLaunch()")
        expect(page.get_by_role("heading", name="Production Self-Test", exact=True)).to_be_visible()
        expect(page.get_by_text("Queued", exact=True)).to_be_visible()
        assert page.evaluate("window.__testLab.launchCalls.length") == 1
        launch = page.evaluate("window.__testLab.launchCalls[0]")
        assert launch["operation"] == "production_self_test"
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", launch["idempotencyKey"])

        page.evaluate("window.__testLab.currentStatus = 'running'")
        expect(page.get_by_text("Running", exact=True)).to_be_visible(timeout=4_000)
        page.evaluate("window.__testLab.cancelDeferred = true")
        page.get_by_role("button", name="Cancel run", exact=True).click()
        expect(page.get_by_role("button", name="Requesting cancellation...", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Requesting cancellation...", exact=True).evaluate("button => button.click()")
        assert page.evaluate("window.__testLab.cancelCalls") == 1
        page.evaluate("window.__testLab.resolveCancel()")
        expect(page.get_by_text(re.compile("Cancellation was requested"))).to_be_visible()
        expect(page.get_by_text("Cancelling", exact=True)).to_be_visible()

        page.evaluate("window.__testLab.currentStatus = 'succeeded'; window.__testLab.terminal = true")
        expect(page.get_by_text("Succeeded", exact=True)).to_be_visible(timeout=4_000)
        expect(page.get_by_role("button", name="Cancel run", exact=True)).to_have_count(0)
        expect(page.get_by_text("Cancelled", exact=True)).to_have_count(0)
        status_calls = page.evaluate("window.__testLab.statusCalls")
        page.wait_for_timeout(1700)
        assert page.evaluate("window.__testLab.statusCalls") == status_calls
        expect(page.locator('[aria-live="polite"]')).not_to_have_count(0)
        capture_evidence(page, "sandbox_test_lab_terminal_success")


def test_launch_retry_reuses_key_and_new_intent_gets_fresh_key(studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        expect(page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible()
        page.evaluate("window.__testLab.launchFailures = ['transport_error']")

        _launch_selected(page, "Production Screenshot")
        expect(page.get_by_role("alert")).to_contain_text("trusted Studio transport")
        page.get_by_role("button", name="Retry launch request", exact=True).click()
        expect(page.get_by_role("heading", name="Production Screenshot", exact=True)).to_be_visible()
        calls = page.evaluate("window.__testLab.launchCalls")
        assert len(calls) == 2
        assert calls[0]["operation"] == calls[1]["operation"] == "production_screenshot"
        assert calls[0]["idempotencyKey"] == calls[1]["idempotencyKey"]

        page.evaluate("window.__testLab.currentStatus = 'succeeded'; window.__testLab.terminal = true")
        expect(page.get_by_text("Succeeded", exact=True)).to_be_visible(timeout=3_000)
        page.get_by_role("button", name="Run again", exact=True).click()
        page.get_by_role("button", name="Launch controlled run", exact=True).click()
        page.wait_for_function("window.__testLab.launchCalls.length === 3")
        third = page.evaluate("window.__testLab.launchCalls[2]")
        assert third["idempotencyKey"] != calls[0]["idempotencyKey"]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("network", "disabled while the Studio backend is accessible over the network"),
        ("sandbox-unavailable", "Windows Sandbox is not available"),
        ("raw-error", "trusted Studio transport could not reach the local backend"),
    ],
)
def test_capability_unavailable_and_transport_errors_are_sanitized(mode, message, studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page, capability=mode)
        _open_test_lab(page, studio_server)
        expect(page.get_by_role("alert")).to_contain_text(message)
        expect(page.get_by_role("button", name="Review and launch", exact=True)).to_be_disabled()
        assert "private" not in page.locator("body").inner_text().lower()
        assert "backend-token" not in page.locator("body").inner_text().lower()


def test_polling_prevents_overlap_and_backend_restart_never_relaunches(studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        expect(page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible()
        page.evaluate("window.__testLab.statusFailures = ['backend_restarting']")
        _launch_selected(page)
        page.evaluate("window.__testLab.currentStatus = 'running'")

        expect(page.get_by_text(re.compile("Reconnecting to the local backend"))).to_be_visible()
        expect(page.get_by_text("Running", exact=True)).to_be_visible(timeout=4_000)
        assert page.evaluate("window.__testLab.capabilityCalls") >= 2
        page.evaluate("window.__testLab.statusFailures = ['run_not_found']")
        expect(page.get_by_role("heading", name="The backend no longer recognizes this run", exact=True)).to_be_visible(timeout=4_000)
        assert page.evaluate("window.__testLab.launchCalls.length") == 1
        assert page.evaluate("window.__testLab.capabilityCalls") >= 3
        expect(page.get_by_text(re.compile("was not relaunched"))).to_be_visible()

        page.get_by_role("button", name="Return to operations", exact=True).click()
        _launch_selected(page)
        page.evaluate("window.__testLab.currentStatus = 'running'")
        expect(page.get_by_text("Running", exact=True)).to_be_visible(timeout=4_000)
        calls_before_navigation = page.evaluate("window.__testLab.statusCalls")
        page.get_by_role("button", name="Settings", exact=True).first.click()
        page.wait_for_timeout(1700)
        assert page.evaluate("window.__testLab.statusCalls") == calls_before_navigation
        page.get_by_role("button", name="Sandbox Test Lab", exact=True).click()
        page.wait_for_function(f"window.__testLab.statusCalls > {calls_before_navigation}")

        deferred_baseline = page.evaluate("window.__testLab.statusCalls")
        page.evaluate("window.__testLab.statusDeferred = true")
        page.wait_for_function(f"window.__testLab.statusCalls > {deferred_baseline}")
        calls = page.evaluate("window.__testLab.statusCalls")
        page.wait_for_timeout(1700)
        assert page.evaluate("window.__testLab.statusCalls") == calls
        page.evaluate("window.__testLab.currentStatus = 'succeeded'; window.__testLab.terminal = true; window.__testLab.resolveStatus()")
        expect(page.get_by_text("Succeeded", exact=True)).to_be_visible()


@pytest.mark.parametrize(
    ("terminal_status", "label"),
    [("failed", "Failed"), ("infrastructure_error", "Infrastructure error"), ("cancelled", "Cancelled")],
)
def test_terminal_failure_infrastructure_and_cancelled_states(terminal_status, label, studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        expect(page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible()
        _launch_selected(page)
        page.evaluate(
            """status => {
              window.__testLab.currentStatus = status;
              window.__testLab.terminal = true;
            }""",
            terminal_status,
        )
        expect(page.get_by_text(label, exact=True)).to_be_visible(timeout=4_000)
        expect(page.get_by_role("button", name="Cancel run", exact=True)).to_have_count(0)
        if terminal_status == "failed":
            expect(page.get_by_role("alert")).to_contain_text("did not complete successfully")


def test_stale_cancel_response_cannot_overwrite_terminal_success(studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        _launch_selected(page)
        page.evaluate("window.__testLab.currentStatus = 'running'; window.__testLab.cancelDeferred = true")
        expect(page.get_by_text("Running", exact=True)).to_be_visible(timeout=4_000)
        page.get_by_role("button", name="Cancel run", exact=True).click()
        page.evaluate("window.__testLab.currentStatus = 'succeeded'; window.__testLab.terminal = true")
        expect(page.get_by_text("Succeeded", exact=True)).to_be_visible(timeout=4_000)
        page.evaluate("window.__testLab.resolveCancel()")
        page.wait_for_timeout(100)
        expect(page.get_by_text("Succeeded", exact=True)).to_be_visible()
        expect(page.get_by_text(re.compile("Cancellation was requested"))).to_have_count(0)


def test_already_cancelled_acknowledgement_is_terminal_and_authoritative(studio_server, browser_session):
    with browser_session() as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        _launch_selected(page)
        page.evaluate("window.__testLab.currentStatus = 'running'; window.__testLab.cancelAccepted = false; window.__testLab.cancelStatus = 'cancelled'")
        expect(page.get_by_text("Running", exact=True)).to_be_visible(timeout=4_000)
        page.evaluate("window.__testLab.statusDeferred = true")
        page.get_by_role("button", name="Cancel run", exact=True).click()
        expect(page.get_by_role("button", name="Requesting cancellation...", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Requesting cancellation...", exact=True).evaluate("button => button.click()")
        assert page.evaluate("window.__testLab.cancelCalls") == 1
        page.evaluate("window.__testLab.resolveStatus()")
        expect(page.get_by_text("Cancelled", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Return to operations", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Cancel run", exact=True)).to_have_count(0)


def test_test_lab_layout_themes_navigation_and_keyboard_at_supported_viewports(studio_server, browser_session):
    with browser_session(viewport={"width": 1366, "height": 768}) as page:
        _install_test_lab_mock(page)
        _open_test_lab(page, studio_server)
        expect(page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible()

        for width, height in ((1366, 768), (1920, 1080), (2560, 1440), (768, 1024)):
            page.set_viewport_size({"width": width, "height": height})
            overflow = page.evaluate("""() => ({
              document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              page: document.querySelector('[data-testid="sandbox-test-lab-page"]').scrollWidth - document.querySelector('[data-testid="sandbox-test-lab-page"]').clientWidth,
            })""")
            assert overflow["document"] <= 1
            assert overflow["page"] <= 1

        page.get_by_role("radio", name=re.compile("Production Self-Test")).focus()
        page.keyboard.press("Space")
        expect(page.get_by_role("radio", name=re.compile("Production Self-Test"))).to_be_checked()
        page.get_by_role("button", name="Review and launch", exact=True).press("Enter")
        expect(page.get_by_role("dialog")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)

        page.evaluate("document.documentElement.classList.add('theme-light')")
        light_theme = page.evaluate("""() => ({
          panel: getComputedStyle(document.documentElement).getPropertyValue('--fs-panel'),
          intro: getComputedStyle(document.querySelector('.fs-test-lab-intro')).backgroundImage,
        })""")
        assert "255" in light_theme["panel"]
        assert light_theme["intro"] != "none"
        page.get_by_role("button", name="Settings", exact=True).first.click()
        expect(page.get_by_text("Appearance", exact=True)).to_be_visible()
        page.get_by_role("button", name="Sandbox Test Lab", exact=True).click()
        expect(page.get_by_test_id("sandbox-test-lab-page")).to_be_visible()
