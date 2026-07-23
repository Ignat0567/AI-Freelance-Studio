import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from test_security_support import TEST_LOCAL_TOKEN


ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = ROOT / "ui_regression_fixtures.json"


def load_ui_fixture(name):
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload[name]


def _wait_for_server(port, process):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Studio test server exited with code {process.returncode}")
        with socket.socket() as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError("Studio test server did not start")


@pytest.fixture(scope="session")
def studio_server(tmp_path_factory):
    if os.environ.get("UI_REGRESSION_SKIP_BUILD") != "1":
        subprocess.run(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "build:vite"],
            cwd=ROOT / "frontend",
            check=True,
        )
    runtime_dir = tmp_path_factory.mktemp("ui-regression-runtime")
    environment = os.environ.copy()
    environment["FREELANCERSTUDIO_USER_DATA"] = str(runtime_dir)
    environment["FREELANCERSTUDIO_RUNTIME_DIR"] = str(runtime_dir)
    port = 8080
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "ui_regression_server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(port, process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _artifact_name(value):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_")


def _artifact_dir(test_name):
    root = Path(os.environ.get("UI_REGRESSION_ARTIFACT_DIR", ROOT / "artifacts" / "ui_regression" / "standalone"))
    path = root / "browser" / _artifact_name(test_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _route_offline(route):
    parsed = urlparse(route.request.url)
    if parsed.scheme in {"data", "blob"} or parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        route.continue_()
    else:
        route.abort("blockedbyclient")


@pytest.fixture
def browser_session(request):
    counter = 0

    @contextmanager
    def open_browser(viewport=None, device_scale_factor=1):
        nonlocal counter
        counter += 1
        test_name = f"{request.node.nodeid}-{counter}"
        evidence = os.environ.get("UI_REGRESSION_EVIDENCE") == "1"
        artifacts = _artifact_dir(test_name)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=viewport or {"width": 1280, "height": 720},
                device_scale_factor=device_scale_factor,
                extra_http_headers={"X-FreelancerStudio-Token": TEST_LOCAL_TOKEN},
            )
            context.route("**/*", _route_offline)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.set_default_timeout(10_000)
            try:
                yield page
            except BaseException:
                page.screenshot(path=str(artifacts / "failure.png"), animations="disabled", full_page=True)
                context.tracing.stop(path=str(artifacts / "trace.zip"))
                raise
            else:
                if evidence:
                    context.tracing.stop(path=str(artifacts / "trace.zip"))
                else:
                    context.tracing.stop()
            finally:
                context.close()
                browser.close()

    return open_browser


def capture_evidence(page, name):
    if os.environ.get("UI_REGRESSION_EVIDENCE") != "1":
        return None
    artifacts = _artifact_dir(os.environ.get("PYTEST_CURRENT_TEST", "ui-regression")) / "screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / f"{_artifact_name(name)}.png"
    page.screenshot(path=str(path), animations="disabled")
    assert path.stat().st_size > 10_000
    return path
