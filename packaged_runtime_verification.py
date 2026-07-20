import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parent
APP_PROCESS_NAMES = {"ai freelance studio.exe", "freelancerstudio-backend.exe"}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def process_snapshot():
    return {
        process.pid: {
            "name": process.info.get("name") or "",
            "exe": process.info.get("exe") or "",
            "cmdline": process.info.get("cmdline") or [],
        }
        for process in psutil.process_iter(["name", "exe", "cmdline"])
        if (process.info.get("name") or "").lower() in APP_PROCESS_NAMES
    }


def terminate_process_tree(pid):
    try:
        parent = psutil.Process(pid)
        processes = parent.children(recursive=True) + [parent]
        for process in processes:
            process.kill()
        psutil.wait_procs(processes, timeout=10)
    except psutil.Error:
        pass


def wait_until(predicate, timeout=30, interval=0.1, message="condition was not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(message)


def wait_for_cdp(port):
    def probe():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                return response.status == 200
        except Exception:
            return False

    wait_until(probe, timeout=30, message="Electron CDP endpoint did not start")


def wait_for_backend(user_data):
    port_file = user_data / "studio_port.txt"

    def read_port():
        try:
            value = int(port_file.read_text(encoding="utf-8").strip())
            with urllib.request.urlopen(f"http://127.0.0.1:{value}/health", timeout=0.5) as response:
                return value if response.status == 200 else 0
        except Exception:
            return 0

    return wait_until(read_port, timeout=30, message="Packaged backend did not become healthy")


def backend_is_down(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5)
        return False
    except Exception:
        return True


def sanitized_environment(profile_root):
    roaming = profile_root / "AppData" / "Roaming"
    local = profile_root / "AppData" / "Local"
    temp = profile_root / "Temp"
    for directory in (roaming, local, temp):
        directory.mkdir(parents=True, exist_ok=True)
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment = os.environ.copy()
    environment.update({
        "APPDATA": str(roaming),
        "LOCALAPPDATA": str(local),
        "TEMP": str(temp),
        "TMP": str(temp),
        "PATH": os.pathsep.join((str(system_root / "System32"), str(system_root))),
        "ELECTRON_OPEN_DEVTOOLS": "0",
    })
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHON_PATH", "PYTHON_EXECUTABLE", "VIRTUAL_ENV", "NODE_PATH", "NVM_HOME", "NVM_SYMLINK"):
        environment.pop(name, None)
    return environment, roaming / "ai-freelance-studio"


def seed_blocked_project(user_data):
    user_data.mkdir(parents=True, exist_ok=True)
    project = {
        "project_id": "stage8-blocked-project",
        "id": "stage8-blocked-project",
        "title": "Stage 8 Retry Fixture",
        "description": "Fixed packaged-runtime fixture for Retry Generation UI wiring.",
        "platform": "manual",
        "status": "blocked",
        "_phase": "coding",
        "logs": ["[Fixture]: Generation was blocked before Stage 8 verification."],
        "issues": [{"title": "OpenCode unavailable", "message": "Reconnect OpenCode, then retry generation."}],
        "chat_history": [],
        "cancel_requested": False,
    }
    (user_data / "projects_state.json").write_text(
        json.dumps({"projects": {project["project_id"]: project}, "tasks": {}}, indent=2),
        encoding="utf-8",
    )


def launch_application(executable, environment, cdp_port, user_data):
    return subprocess.Popen(
        [str(executable), f"--remote-debugging-port={cdp_port}", f"--user-data-dir={user_data}"],
        cwd=executable.parent,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def connect_page(playwright, cdp_port):
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")

    def first_page():
        for context in browser.contexts:
            if context.pages:
                return context.pages[0]
        return None

    page = wait_until(first_page, timeout=20, message="Packaged renderer page was not created")
    page.set_default_timeout(20_000)
    expect(page.locator(".fs-shell")).to_be_visible(timeout=30_000)
    return browser, page


def resize_window(page, width=1100, height=650):
    page.set_viewport_size({"width": width, "height": height})
    return page.evaluate("() => ({width: innerWidth, height: innerHeight, scale: devicePixelRatio})")


def close_onboarding(page):
    overlay = page.locator("div.fixed.inset-0").last
    try:
        overlay.wait_for(state="visible", timeout=5_000)
    except Exception:
        return
    close = overlay.get_by_role("button", name="Close this window", exact=True)
    if close.count():
        close.last.click()
    else:
        buttons = overlay.locator("button")
        buttons.nth(buttons.count() - 2).click()
    expect(overlay).to_be_hidden()


def screenshot(page, directory, name):
    path = directory / f"{name}.png"
    page.screenshot(path=str(path), animations="disabled")
    if path.stat().st_size < 10_000:
        raise AssertionError(f"Packaged screenshot is unexpectedly small: {path}")
    return str(path)


def scroll_contract(page, body, last, screenshot_dir, name):
    metrics = body.evaluate(
        "element => ({clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, overflowY: getComputedStyle(element).overflowY})"
    )
    body.evaluate("element => { element.scrollTop = 0 }")
    page.wait_for_function("element => element.scrollTop < 2", arg=body.element_handle())
    body.focus()
    body.hover()
    page.mouse.wheel(0, 450)
    if metrics["scrollHeight"] > metrics["clientHeight"]:
        try:
            page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle(), timeout=3_000)
        except Exception:
            page.keyboard.press("PageDown")
            page.wait_for_function("element => element.scrollTop > 0", arg=body.element_handle())
        wheel_top = body.evaluate("element => element.scrollTop")
        page.keyboard.press("End")
        page.wait_for_function(
            "element => element.scrollHeight - element.clientHeight - element.scrollTop < 3",
            arg=body.element_handle(),
        )
    else:
        wheel_top = 0
    expect(last).to_be_in_viewport()
    image = screenshot(page, screenshot_dir, name)
    page.keyboard.press("Home")
    return {**metrics, "wheelScrollTop": wheel_top, "screenshot": image}


def verify_runtime(page, screenshot_dir):
    report = {"screenshots": [], "install_requests": [], "settings_scroll": {}}
    page.on("request", lambda request: report["install_requests"].append(request.url) if "/api/system/install/" in request.url else None)
    close_onboarding(page)
    expect(page.get_by_text("Stage 8 Retry Fixture", exact=True).first).to_be_visible(timeout=20_000)
    report["screenshots"].append(screenshot(page, screenshot_dir, "packaged-overview"))

    page.get_by_role("button", name="Info", exact=True).click()
    info = page.get_by_role("region", name="Info content")
    expect(info).to_be_focused()
    page.get_by_role("button", name="Show Diagnostics", exact=True).click()
    rows = info.locator("[data-component-id]")
    expect(rows).to_have_count(14, timeout=20_000)
    report["info_scroll"] = scroll_contract(
        page, info, rows.last, screenshot_dir, "packaged-info-bottom"
    )

    check_button = page.get_by_role("button", name="Check All Components", exact=True)
    check_button.click()
    expect(page.get_by_role("button", name="Checking...", exact=True)).to_be_disabled()
    summary = page.get_by_test_id("check-summary")
    expect(summary).to_be_visible(timeout=30_000)
    report["check_all_summary"] = summary.inner_text()
    report["component_ids"] = rows.evaluate_all("elements => elements.map(element => element.dataset.componentId)")
    report["component_registry"] = page.evaluate(
        "async () => (await (await fetch('/api/system/requirements')).json()).components"
    )
    report["screenshots"].append(screenshot(page, screenshot_dir, "packaged-check-all"))

    report["official_unknown_result"] = page.evaluate("() => window.env.openOfficialDownload('unknown')")
    missing_action = page.locator('[data-status="Missing"] button').first
    if missing_action.count():
        report["official_action_label"] = missing_action.inner_text()
        missing_action.click()
        expect(page.get_by_role("status")).to_contain_text("Official component page opened", timeout=10_000)
        report["official_action_result"] = page.get_by_role("status").inner_text()
    else:
        report["official_action_result"] = "No missing component was available for an official action."

    page.locator('.fs-header-actions button[title="Open settings"]').click()
    settings = page.get_by_role("region", name="Settings content")
    expect(settings).to_be_visible()
    for tab in ("general", "appearance", "ai", "studio"):
        page.locator(f'[data-settings-tab="{tab}"]').click()
        expect(settings).to_have_attribute("data-active-tab", tab)
        last = page.get_by_test_id(f"settings-last-{tab}")
        expect(last).to_be_attached(timeout=20_000)
        report["settings_scroll"][tab] = scroll_contract(
            page, settings, last, screenshot_dir, f"packaged-settings-{tab}-bottom"
        )

    page.locator('[data-settings-tab="ai"]').click()
    onboarding_action = page.get_by_role("button", name="Detect Again", exact=True).first
    expect(onboarding_action).to_be_visible(timeout=20_000)
    report["opencode_onboarding_actions"] = page.locator("button").evaluate_all(
        "buttons => buttons.map(button => button.innerText.trim()).filter(text => ['Detect Again', 'Download Node.js', 'Download OpenCode', 'Authenticate Provider', 'Start OpenCode Web', 'Test Connection', 'Save Connection'].includes(text))"
    )
    detect = onboarding_action
    if detect.count() and detect.is_visible():
        detect.click()
        expect(detect).to_be_enabled(timeout=20_000)
    report["screenshots"].append(screenshot(page, screenshot_dir, "packaged-opencode-onboarding"))

    page.locator('[data-settings-tab="appearance"]').click()
    light = page.get_by_role("button", name="Light").first
    light.click()
    page.wait_for_function("document.documentElement.classList.contains('theme-light')")
    report["theme_before_restart"] = page.evaluate("localStorage.getItem('studio_theme')")
    report["screenshots"].append(screenshot(page, screenshot_dir, "packaged-theme-light"))

    page.locator('.fs-nav button[title="Overview"]').click()
    hide_chat = page.get_by_role("button", name="Hide Chat", exact=True)
    if hide_chat.count() and hide_chat.is_visible():
        hide_chat.click()
        expect(page.get_by_role("button", name="Show Chat", exact=True)).to_be_visible()
    retry = page.get_by_role("button", name="Retry Generation", exact=True).first
    expect(retry).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST" and response.url.endswith("/api/projects/stage8-blocked-project/retry")
    ) as retry_response:
        retry.click()
    report["retry_generation_response"] = retry_response.value.json()
    if report["retry_generation_response"].get("status") != "retrying_generation":
        raise AssertionError(f"Retry Generation returned an unexpected response: {report['retry_generation_response']}")
    report["screenshots"].append(screenshot(page, screenshot_dir, "packaged-retry-generation"))

    if report["install_requests"]:
        raise AssertionError(f"Automatic install requests were observed: {report['install_requests']}")
    if len(report["component_ids"]) != 14 or len(set(report["component_ids"])) != 14:
        raise AssertionError("Packaged component registry is incomplete or duplicated")
    if any(component.get("can_auto_install") for component in report["component_registry"]):
        raise AssertionError("Packaged registry exposes automatic installation")
    return report


def close_and_verify(browser, page, process, backend_port, initial_processes):
    page.close()
    browser.close()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)
        raise AssertionError("Packaged Electron process did not close normally")
    wait_until(lambda: backend_is_down(backend_port), timeout=15, message="Backend port remained active after app close")

    def remaining_runtime_processes():
        current = process_snapshot()
        return {pid: details for pid, details in current.items() if pid not in initial_processes}

    deadline = time.monotonic() + 15
    remaining = remaining_runtime_processes()
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = remaining_runtime_processes()
    if remaining:
        raise AssertionError(f"Packaged runtime processes remained after app close: {remaining}")
    return {"electron_exit_code": process.returncode, "backend_port_closed": True, "runtime_processes_closed": True}


def verify_executable(executable, artifact_dir):
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = artifact_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    profile_root = artifact_dir / "profile"
    if profile_root.exists():
        shutil.rmtree(profile_root)
    environment, user_data = sanitized_environment(profile_root)
    seed_blocked_project(user_data)
    initial_processes = process_snapshot()
    cdp_port = free_port()
    process = launch_application(executable, environment, cdp_port, user_data)
    backend_port = 0
    browser = page = None
    try:
        wait_for_cdp(cdp_port)
        backend_port = wait_for_backend(user_data)
        with sync_playwright() as playwright:
            browser, page = connect_page(playwright, cdp_port)
            viewport = resize_window(page)
            runtime = verify_runtime(page, screenshot_dir)
            lifecycle = close_and_verify(browser, page, process, backend_port, initial_processes)
            browser = page = None

        second_cdp = free_port()
        second_process = launch_application(executable, environment, second_cdp, user_data)
        with sync_playwright() as playwright:
            wait_for_cdp(second_cdp)
            second_backend_port = wait_for_backend(user_data)
            second_browser, second_page = connect_page(playwright, second_cdp)
            close_onboarding(second_page)
            second_page.wait_for_function("document.documentElement.classList.contains('theme-light')")
            runtime["theme_after_restart"] = second_page.evaluate("localStorage.getItem('studio_theme')")
            runtime["screenshots"].append(screenshot(second_page, screenshot_dir, "packaged-theme-light-after-restart"))
            second_lifecycle = close_and_verify(second_browser, second_page, second_process, second_backend_port, initial_processes)

        report = {
            "status": "passed",
            "executable": str(executable.resolve()),
            "executable_size": executable.stat().st_size,
            "sanitized_environment": {
                "path": environment["PATH"],
                "pythonhome_present": "PYTHONHOME" in environment,
                "pythonpath_present": "PYTHONPATH" in environment,
                "virtual_env_present": "VIRTUAL_ENV" in environment,
                "node_path_present": "NODE_PATH" in environment,
            },
            "viewport": viewport,
            "backend_port": backend_port,
            "runtime": runtime,
            "lifecycle": {"first_launch": lifecycle, "restart": second_lifecycle},
        }
        (artifact_dir / "packaged-runtime-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        try:
            if page and not page.is_closed():
                page.close()
            if browser:
                browser.close()
        except Exception:
            pass
        if process.poll() is None:
            terminate_process_tree(process.pid)
        for pid in process_snapshot():
            if pid not in initial_processes:
                terminate_process_tree(pid)


def main():
    parser = argparse.ArgumentParser(description="Verify a packaged FreelancerStudio Windows executable.")
    parser.add_argument("executable", type=Path)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    if not args.executable.is_file():
        parser.error(f"Packaged executable not found: {args.executable}")
    report = verify_executable(args.executable.resolve(), args.artifacts.resolve())
    print(json.dumps({"status": report["status"], "report": str((args.artifacts / 'packaged-runtime-report.json').resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
