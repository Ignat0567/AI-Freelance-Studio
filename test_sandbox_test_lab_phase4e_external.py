from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import psutil
import pytest
from playwright.sync_api import expect, sync_playwright

from sandbox_test_lab.production_bridge import PRODUCTION_UI_EXTERNAL_OPT_IN


pytestmark = pytest.mark.external

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
ELECTRON = FRONTEND / "node_modules" / "electron" / "dist" / "electron.exe"
SANDBOX_NAMES = {
    "windowssandbox.exe",
    "windowssandboxclient.exe",
    "windowssandboxremotesession.exe",
    "windowssandboxserver.exe",
}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "infrastructure_error"}
STATUS_ORDER = {
    "queued": 0,
    "preparing": 1,
    "launching": 2,
    "running": 3,
    "cancelling": 4,
    "succeeded": 5,
    "failed": 5,
    "cancelled": 5,
    "infrastructure_error": 5,
}


@pytest.fixture(autouse=True)
def _require_exact_external_opt_in(request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if os.environ.get(PRODUCTION_UI_EXTERNAL_OPT_IN) != "1" or mark_expression.strip() != "external":
        pytest.skip(f"Set {PRODUCTION_UI_EXTERNAL_OPT_IN}=1 and select exactly -m external")
    if os.environ.get("PYTEST_XDIST_WORKER") or len(request.session.items) != 1:
        pytest.fail("Phase 4E external trials require one exact node ID and no parallel worker")


def _sandbox_processes() -> list[dict[str, object]]:
    processes = []
    for process in psutil.process_iter(["pid", "ppid", "name", "exe", "create_time"]):
        try:
            name = str(process.info.get("name") or "")
            if name.lower() not in SANDBOX_NAMES:
                continue
            processes.append({
                "name": name,
                "pid": int(process.info["pid"]),
                "ppid": int(process.info.get("ppid") or 0),
                "exe": str(process.info.get("exe") or ""),
                "create_time": float(process.info.get("create_time") or 0),
            })
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return sorted(processes, key=lambda item: (str(item["name"]), int(item["pid"])))


def _owned_studio_processes() -> list[int]:
    frontend = str(FRONTEND).lower()
    owned = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if str(process.info.get("name") or "").lower() != "electron.exe":
                continue
            command = " ".join(process.info.get("cmdline") or []).lower()
            if frontend in command:
                owned.append(process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return owned


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until(predicate, timeout: float, message: str, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(message)


def _terminate_owned_process_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        processes = parent.children(recursive=True) + [parent]
        for process in processes:
            process.kill()
        psutil.wait_procs(processes, timeout=10)
    except psutil.Error:
        pass


class _SandboxMonitor:
    def __init__(self) -> None:
        self.snapshots: list[list[dict[str, object]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="phase4e-sandbox-monitor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def _run(self) -> None:
        previous = None
        while not self._stop.is_set():
            current = _sandbox_processes()
            identity = [(item["name"], item["pid"], item["ppid"]) for item in current]
            if identity != previous:
                self.snapshots.append(current)
                previous = identity
            self._stop.wait(0.05)

    def owned_identity(self) -> dict[str, object] | None:
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        expected_launcher = os.path.normcase(str(system32 / "WindowsSandbox.exe"))
        windows_apps_prefix = os.path.normcase(
            str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WindowsApps")
        )
        for snapshot in self.snapshots:
            launchers = [item for item in snapshot if str(item["name"]).lower() == "windowssandbox.exe"]
            # Legacy model: launcher + client
            if len(launchers) == 1 and os.path.normcase(str(launchers[0]["exe"])) == expected_launcher:
                clients = [item for item in snapshot if str(item["name"]).lower() == "windowssandboxclient.exe"]
                if (
                    len(clients) == 1
                    and clients[0]["ppid"] == launchers[0]["pid"]
                    and float(clients[0]["create_time"]) >= float(launchers[0]["create_time"]) - 2
                ):
                    return {"model": "legacy_client", "launcher": launchers[0], "client": clients[0]}
            # RemoteSession model: RemoteSession (+ optional Server), launcher may have already exited
            remote_sessions = [
                item for item in snapshot
                if str(item["name"]).lower() == "windowssandboxremotesession.exe"
            ]
            if len(remote_sessions) == 1:
                rs_path = os.path.normcase(str(remote_sessions[0]["exe"]))
                if rs_path.startswith(windows_apps_prefix) and "microsoftwindows.windowssandbox_" in rs_path:
                    servers = [
                        item for item in snapshot
                        if str(item["name"]).lower() == "windowssandboxserver.exe"
                    ]
                    result = {
                        "model": "remote_session",
                        "remote_session": remote_sessions[0],
                    }
                    if len(servers) == 1:
                        sv_path = os.path.normcase(str(servers[0]["exe"]))
                        if (
                            sv_path.startswith(windows_apps_prefix)
                            and "microsoftwindows.windowssandbox_" in sv_path
                            and servers[0]["ppid"] == remote_sessions[0]["pid"]
                        ):
                            result["server"] = servers[0]
                    return result
        return None

    def assert_single_model(self) -> None:
        for snapshot in self.snapshots:
            for name in ("windowssandbox.exe", "windowssandboxclient.exe",
                         "windowssandboxremotesession.exe", "windowssandboxserver.exe"):
                assert len([item for item in snapshot if str(item["name"]).lower() == name]) <= 1, (
                    f"duplicate {name} in snapshot"
                )


class _StudioTrial:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.profile_root = tmp_path / "profile"
        self.runtime_root = tmp_path / "local"
        self.stdout_path = tmp_path / "electron.stdout.log"
        self.stderr_path = tmp_path / "electron.stderr.log"
        self.process: subprocess.Popen | None = None
        self.browser = None
        self.page = None
        self.monitor = _SandboxMonitor()
        self.lifecycle: list[str] = []
        self.public_run_id = ""
        self.before_job_ids = self._job_ids()
        self.cancel_clicks = 0

    def _job_ids(self) -> set[str]:
        path = ROOT / "sandbox-test-lab-jobs.json"
        if not path.is_file():
            return set()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(item["run_id"]) for item in payload.get("jobs", [])}

    def start(self) -> None:
        assert os.environ.get(PRODUCTION_UI_EXTERNAL_OPT_IN) == "1"
        assert os.environ.get("FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_PRODUCTION_SELF_TEST") != "1"
        assert os.environ.get("FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_SCREENSHOT_EXTERNAL") != "1"
        assert ELECTRON.is_file()
        assert _sandbox_processes() == []
        assert _owned_studio_processes() == []
        for directory in (self.profile_root, self.runtime_root, self.tmp_path / "temp"):
            directory.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update({
            "APPDATA": str(self.profile_root / "roaming"),
            "LOCALAPPDATA": str(self.runtime_root),
            "TEMP": str(self.tmp_path / "temp"),
            "TMP": str(self.tmp_path / "temp"),
            "ELECTRON_OPEN_DEVTOOLS": "0",
        })
        cdp_port = _free_port()
        stdout = self.stdout_path.open("wb")
        stderr = self.stderr_path.open("wb")
        self.process = subprocess.Popen(
            [str(ELECTRON), str(FRONTEND), f"--remote-debugging-port={cdp_port}", f"--user-data-dir={self.profile_root}"],
            cwd=FRONTEND,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        stdout.close()
        stderr.close()
        self.monitor.start()
        _wait_until(
            lambda: self._cdp_ready(cdp_port),
            30,
            "Electron CDP endpoint did not start",
        )
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        self.page = _wait_until(
            lambda: next((page for context in self.browser.contexts for page in context.pages), None),
            20,
            "Electron renderer was not created",
        )
        self.page.set_default_timeout(30_000)
        expect(self.page.locator(".fs-shell")).to_be_visible(timeout=30_000)
        self._close_onboarding()
        self.page.get_by_role("button", name="Sandbox Test Lab", exact=True).click()
        expect(self.page.get_by_test_id("sandbox-test-lab-page")).to_be_visible()
        expect(self.page.get_by_text("Sandbox Test Lab is ready", exact=True)).to_be_visible(timeout=60_000)
        capability = self.page.evaluate("async () => await window.env.sandboxTestLab.getCapabilities()")
        assert capability["ok"] is True
        assert capability["data"]["backend_mode"] == "loopback"
        assert capability["data"]["operations"] == {
            "launch": True, "cancel": True, "status": True, "evidence": False,
        }
        assert self.page.evaluate("Object.keys(window.env.sandboxTestLab).sort()") == [
            "cancelRun", "getCapabilities", "getRun", "launchRun",
        ]
        self._assert_loopback_backend()

    @staticmethod
    def _cdp_ready(port: int) -> bool:
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.5) as response:
                targets = json.loads(response.read().decode("utf-8"))
                return response.status == 200 and any(item.get("type") == "page" for item in targets)
        except Exception:
            return False

    def _close_onboarding(self) -> None:
        overlay = self.page.locator("div.fixed.inset-0").last
        try:
            overlay.wait_for(state="visible", timeout=5_000)
        except Exception:
            return
        close = overlay.get_by_role("button", name="Close this window", exact=True)
        if close.count():
            close.last.click()
        else:
            buttons = overlay.locator("button")
            assert buttons.count() >= 2
            buttons.nth(buttons.count() - 2).click()
        expect(overlay).to_be_hidden()

    def _assert_loopback_backend(self) -> None:
        port_path = ROOT / "studio_port.txt"
        def read_port() -> int:
            try:
                return int(port_path.read_text(encoding="utf-8").strip()) if port_path.is_file() else 0
            except (OSError, ValueError):
                return 0
        port = _wait_until(
            read_port,
            10,
            "backend port file was not written",
        )
        listeners = [
            connection for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
        ]
        assert listeners
        assert all(connection.laddr.ip in {"127.0.0.1", "::1", "::ffff:127.0.0.1"} for connection in listeners)

    def launch(self, operation_label: str) -> str:
        self.page.get_by_text(operation_label, exact=True).click()
        self.page.get_by_role("button", name="Review and launch", exact=True).click()
        expect(self.page.get_by_role("dialog", name=f"Launch {operation_label}" )).to_be_visible()
        self.page.get_by_role("button", name="Launch controlled run", exact=True).click()
        expect(self.page.get_by_role("heading", name=operation_label, exact=True)).to_be_visible(timeout=30_000)
        self.public_run_id = self.page.locator(".fs-test-lab-facts dd").first.get_attribute("title") or ""
        assert self.public_run_id
        self._record_status()
        return self.public_run_id

    def _get_run(self) -> dict[str, object]:
        response = self.page.evaluate(
            "async runId => await window.env.sandboxTestLab.getRun(runId)",
            self.public_run_id,
        )
        assert response["ok"] is True, response
        return response["data"]

    def _record_status(self) -> dict[str, object]:
        snapshot = self._get_run()
        status = str(snapshot["status"])
        if not self.lifecycle or self.lifecycle[-1] != status:
            self.lifecycle.append(status)
        return snapshot

    def wait_for_owned_identity(self) -> dict[str, object]:
        identity = _wait_until(self.monitor.owned_identity, 45, "exact owned Sandbox launcher/client identity was not observed")
        self.monitor.assert_single_model()
        return identity

    def wait_for_status(self, statuses: set[str], timeout: float = 600) -> dict[str, object]:
        def poll():
            snapshot = self._record_status()
            return snapshot if snapshot["status"] in statuses else None
        return _wait_until(poll, timeout, f"run did not reach one of {sorted(statuses)}", interval=0.25)

    def cancel(self) -> tuple[str, float]:
        before = self._record_status()["status"]
        started = time.monotonic()
        cancel = self.page.get_by_role("button", name="Cancel run", exact=True)
        expect(cancel).to_be_visible()
        cancel.click()
        self.cancel_clicks += 1
        expect(self.page.get_by_text("Cancelling", exact=True)).to_be_visible(timeout=10_000)
        return str(before), time.monotonic() - started

    def verify_terminal(self, expected: str) -> dict[str, object]:
        snapshot = self.wait_for_status(TERMINAL_STATUSES)
        assert snapshot["status"] == expected, snapshot
        label = {
            "succeeded": "Succeeded",
            "cancelled": "Cancelled",
            "failed": "Failed",
            "infrastructure_error": "Infrastructure error",
        }[expected]
        expect(self.page.locator(".fs-test-lab-run-heading .fs-status")).to_have_text(label, timeout=10_000)
        for _ in range(3):
            assert self._get_run() == snapshot
            time.sleep(0.1)
        assert all(
            STATUS_ORDER[later] >= STATUS_ORDER[earlier]
            for earlier, later in zip(self.lifecycle, self.lifecycle[1:])
        ), self.lifecycle
        return snapshot

    def verify_run_boundaries(self) -> dict[str, object]:
        after_ids = self._job_ids()
        assert after_ids - self.before_job_ids == {self.public_run_id}
        run_root = self.runtime_root / "AI Freelance Studio" / "sandbox-test-lab" / "runs"
        workspaces = [path for path in run_root.iterdir() if path.is_dir()]
        assert len(workspaces) == 1
        host_result = json.loads((workspaces[0] / "host-result.json").read_text(encoding="utf-8"))
        assert host_result["status"] in {"passed", "cancelled"}
        return {
            "public_run_id": self.public_run_id,
            "runner_run_id": host_result["run_id"],
            "runner_status": host_result["status"],
            "launcher_return_code": host_result["launcher_return_code"],
            "workspace": str(workspaces[0]),
        }

    def verify_renderer_boundary(self) -> None:
        payload = self.page.evaluate("""() => ({
          url: location.href,
          body: document.body.innerText,
          local: Object.entries(localStorage),
          session: Object.entries(sessionStorage),
          resources: performance.getEntriesByType('resource').map(entry => entry.name),
        })""")
        serialized = json.dumps(payload).lower()
        for forbidden in ("x-freelancerstudio-token", "backend-token", "backendtoken", "token="):
            assert forbidden not in serialized

    def close(self) -> float:
        started = time.monotonic()
        if self.page is not None and not self.page.is_closed():
            self.page.evaluate("window.close()")
        if self.process is not None:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if _sandbox_processes():
                    raise AssertionError("Studio did not close while a Sandbox session remained active")
                _terminate_owned_process_tree(self.process.pid)
        elapsed = time.monotonic() - started
        if self.browser is not None:
            self.browser.close()
        if getattr(self, "playwright", None) is not None:
            self.playwright.stop()
        self.monitor.stop()
        assert _wait_until(lambda: not _sandbox_processes(), 10, "Sandbox resources remained after terminal cleanup") is True
        return elapsed

    def cleanup_after_failure(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        identity_proven = self.monitor.owned_identity() is not None
        if _sandbox_processes() and identity_proven and self.page is not None and self.public_run_id:
            try:
                self.page.evaluate(
                    "async runId => await window.env.sandboxTestLab.cancelRun(runId)",
                    self.public_run_id,
                )
                _wait_until(lambda: not _sandbox_processes(), 80, "cooperative failure cleanup did not finish")
            except Exception:
                pass
        failure_report = {
            "public_run_id": self.public_run_id,
            "lifecycle": self.lifecycle,
            "identity_proven": identity_proven,
            "process_transitions": self.monitor.snapshots,
            "sandbox_processes_remaining": _sandbox_processes(),
            "electron_stdout": str(self.stdout_path),
            "electron_stderr": str(self.stderr_path),
        }
        (self.tmp_path / "phase4e-failure-report.json").write_text(
            json.dumps(failure_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not _sandbox_processes():
            _terminate_owned_process_tree(self.process.pid)
            self.monitor.stop()

    def report(self, trial: str, terminal: dict[str, object], identity: dict[str, object], **extra) -> dict[str, object]:
        self.monitor.assert_single_model()
        report = {
            "trial": trial,
            "lifecycle": self.lifecycle,
            "terminal": terminal,
            "owned_identity": identity,
            "run_boundary": self.verify_run_boundaries(),
            "cancel_clicks": self.cancel_clicks,
            "sandbox_processes_after": _sandbox_processes(),
            **extra,
        }
        (self.tmp_path / "phase4e-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print("PHASE4E_EXTERNAL_REPORT=" + json.dumps(report, sort_keys=True))
        return report


def _normal_trial(tmp_path: Path, operation: str, trial: str) -> None:
    studio = _StudioTrial(tmp_path)
    try:
        studio.start()
        studio.launch(operation)
        identity = studio.wait_for_owned_identity()
        terminal = studio.verify_terminal("succeeded")
        studio.verify_renderer_boundary()
        close_seconds = studio.close()
        report = studio.report(trial, terminal, identity, shutdown_seconds=close_seconds)
        assert report["sandbox_processes_after"] == []
        assert close_seconds < 10
    finally:
        studio.cleanup_after_failure()


@pytest.mark.timeout(720)
def test_phase4e_ui_production_self_test(tmp_path: Path):
    _normal_trial(tmp_path, "Production Self-Test", "production_self_test")


@pytest.mark.timeout(720)
def test_phase4e_ui_production_screenshot(tmp_path: Path):
    _normal_trial(tmp_path, "Production Screenshot", "production_screenshot")


@pytest.mark.timeout(180)
def test_phase4e_ui_cooperative_cancellation(tmp_path: Path):
    studio = _StudioTrial(tmp_path)
    try:
        studio.start()
        studio.launch("Production Self-Test")
        identity = studio.wait_for_owned_identity()
        studio.wait_for_status({"launching", "running"}, timeout=30)
        delivery_phase, accepted_seconds = studio.cancel()
        terminal = studio.verify_terminal("cancelled")
        assert studio.cancel_clicks == 1
        studio.verify_renderer_boundary()
        close_seconds = studio.close()
        report = studio.report(
            "cooperative_cancellation",
            terminal,
            identity,
            cancellation_delivery_phase=delivery_phase,
            cancellation_acceptance_seconds=accepted_seconds,
            shutdown_seconds=close_seconds,
        )
        assert "cancelling" in report["lifecycle"]
        assert report["sandbox_processes_after"] == []
        assert close_seconds < 10
    finally:
        studio.cleanup_after_failure()


@pytest.mark.timeout(180)
def test_phase4e_ui_safe_reconnect(tmp_path: Path):
    studio = _StudioTrial(tmp_path)
    try:
        studio.start()
        run_id = studio.launch("Production Self-Test")
        identity = studio.wait_for_owned_identity()
        studio.wait_for_status({"launching", "running"}, timeout=30)
        jobs_before_navigation = studio._job_ids()
        studio.page.get_by_role("button", name="Settings", exact=True).first.click()
        studio.page.wait_for_timeout(2_000)
        studio.page.get_by_role("button", name="Sandbox Test Lab", exact=True).click()
        expect(studio.page.get_by_role("heading", name="Production Self-Test", exact=True)).to_be_visible()
        observed_run_id = studio.page.locator(".fs-test-lab-facts dd").first.get_attribute("title")
        assert observed_run_id == run_id
        assert studio._job_ids() == jobs_before_navigation
        delivery_phase, accepted_seconds = studio.cancel()
        terminal = studio.verify_terminal("cancelled")
        studio.verify_renderer_boundary()
        close_seconds = studio.close()
        report = studio.report(
            "safe_reconnect",
            terminal,
            identity,
            reconnect_kind="navigate_away_and_back",
            duplicate_launch=False,
            cancellation_delivery_phase=delivery_phase,
            cancellation_acceptance_seconds=accepted_seconds,
            shutdown_seconds=close_seconds,
        )
        assert report["cancel_clicks"] == 1
        assert report["sandbox_processes_after"] == []
    finally:
        studio.cleanup_after_failure()
