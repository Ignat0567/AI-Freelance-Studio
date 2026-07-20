import glob
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time


PLATFORM = sys.platform  # 'win32', 'darwin', 'linux'
IS_WIN = PLATFORM == "win32"
IS_MAC = PLATFORM == "darwin"
IS_LINUX = PLATFORM.startswith("linux")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPONENT_CHECK_TIMEOUT_SECONDS = 12.0
_RUN_STATE = threading.local()
logger = logging.getLogger(__name__)


def _run(cmd, timeout=30, cwd=None):
    deadline = getattr(_RUN_STATE, "deadline", None)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _RUN_STATE.error_code = "subprocess_timeout"
            return False, "", "timed out"
        timeout = min(timeout, remaining)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=IS_WIN, cwd=cwd)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return False, "", "not found"
    except subprocess.TimeoutExpired:
        _RUN_STATE.error_code = "subprocess_timeout"
        return False, "", "timed out"
    except Exception:
        _RUN_STATE.error_code = "subprocess_failed"
        return False, "", "check failed"


def _which(name):
    if IS_WIN:
        ok, out, _ = _run(["where", name])
    else:
        ok, out, _ = _run(["which", name])
    if ok:
        path = out.split("\n")[0].strip()
        return path
    return None


def _executable_path(name):
    return _which(name)


def _parse_version(text):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if m:
        parts = [int(m.group(1)), int(m.group(2))]
        if m.group(3):
            parts.append(int(m.group(3)))
        return tuple(parts)
    return None


def _version_ok(current, minimum):
    if not minimum or not current:
        return True
    return current >= minimum


def _pip_list():
    ok, out, _ = _run([sys.executable, "-m", "pip", "list", "--format=json"])
    if ok and out:
        try:
            return {pkg["name"].lower(): pkg["version"] for pkg in json.loads(out)}
        except json.JSONDecodeError:
            pass
    return {}


def _npm_list():
    pkg_path = os.path.join(BASE_DIR, "frontend", "package.json")
    if not os.path.exists(pkg_path):
        return {}
    frontend_dir = os.path.join(BASE_DIR, "frontend")
    ok, out, _ = _run(["npm", "ls", "--depth=0", "--json"], timeout=60, cwd=frontend_dir)
    if ok and out:
        try:
            data = json.loads(out)
            deps = data.get("dependencies", {})
            result = {}
            for name, info in deps.items():
                ver = info.get("version", "")
                if ver:
                    result[name.lower()] = ver
            return result
        except json.JSONDecodeError:
            pass
    return {}


# ─── Component definitions ─────────────────────────────────────────────

COMPONENTS = []


def _add(comp):
    comp.pop("install", None)
    comp["can_auto_install"] = False
    COMPONENTS.append(comp)


def _check_python():
    path = _executable_path("python3") or _executable_path("python")
    if not path and not getattr(sys, "frozen", False):
        path = sys.executable
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_python():
    if IS_WIN:
        return False, "Download Python from https://python.org"
    elif IS_MAC:
        return False, "Run: brew install python"
    else:
        return False, "Run: sudo apt install python3 python3-pip  (or your distro's equivalent)"


_add({
    "id": "python",
    "name": "Python",
    "description": "Runtime for the AI backend and code generation engine",
    "required": True,
    "min_version": (3, 10, 0),
    "can_auto_install": False,
    "check": _check_python,
    "install": _install_python,
})


def _check_pip():
    path = _executable_path("pip3") or _executable_path("pip") or _executable_path("pip3.12") or _executable_path("pip3.11")
    if path:
        ok, out, _ = _run([path, "--version"])
    elif getattr(sys, "frozen", False):
        return False, "", "", None
    else:
        ok, out, _ = _run([sys.executable, "-m", "pip", "--version"])
    if ok:
        ver = _parse_version(out)
        pip_path = path or sys.executable + " -m pip"
        display_version = f"pip {'.'.join(str(part) for part in ver)}" if ver else "pip detected"
        return True, display_version, pip_path, ver
    return False, "", "", None


def _install_pip():
    if IS_WIN:
        return False, "Run: python -m ensurepip --upgrade"
    elif IS_MAC:
        return False, "Run: python3 -m ensurepip --upgrade"
    else:
        return False, "Run: sudo apt install python3-pip"


_add({
    "id": "pip",
    "name": "Pip",
    "description": "Python package manager for installing dependencies",
    "required": True,
    "min_version": (21, 0),
    "can_auto_install": False,
    "check": _check_pip,
    "install": _install_pip,
})


def _check_node():
    path = _executable_path("node")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_node():
    if IS_WIN:
        return False, "Download Node.js from https://nodejs.org"
    elif IS_MAC:
        return False, "Run: brew install node"
    else:
        return False, "Run: sudo apt install nodejs npm"


_add({
    "id": "nodejs",
    "name": "Node.js",
    "description": "JavaScript runtime for the Electron desktop app and frontend build",
    "required": True,
    "min_version": (18, 0, 0),
    "can_auto_install": False,
    "check": _check_node,
    "install": _install_node,
})


def _check_npm():
    path = _executable_path("npm")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_npm():
    return False, "npm is bundled with Node.js. Install Node.js first."


_add({
    "id": "npm",
    "name": "npm",
    "description": "Node package manager for frontend dependencies",
    "required": True,
    "min_version": (8, 0),
    "can_auto_install": False,
    "check": _check_npm,
    "install": _install_npm,
})


def _check_docker():
    path = _executable_path("docker")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_docker():
    url = "https://docker.com/products/docker-desktop"
    if IS_WIN:
        return False, f"Download Docker Desktop from {url}"
    elif IS_MAC:
        return False, f"Download Docker Desktop from {url} or run: brew install --cask docker"
    else:
        return False, "Run: sudo apt install docker.io  or  sudo yum install docker"


_add({
    "id": "docker",
    "name": "Docker",
    "description": "Container engine for isolated QA testing of generated projects",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_docker,
    "install": _install_docker,
})


def _check_git():
    path = _executable_path("git")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_git():
    if IS_WIN:
        return False, "Download Git from https://git-scm.com"
    elif IS_MAC:
        return False, "Run: brew install git"
    else:
        return False, "Run: sudo apt install git"


_add({
    "id": "git",
    "name": "Git",
    "description": "Version control system for GitHub integration",
    "required": True,
    "min_version": (2, 0),
    "can_auto_install": False,
    "check": _check_git,
    "install": _install_git,
})


def _check_python_packages():
    required = {
        "fastapi": "0.100",
        "uvicorn": "0.20",
        "pydantic": "2.0",
        "aiofiles": "23.0",
        "python-multipart": "0.0.6",
        "docker": "6.0",
        "httpx": "0.24",
    }
    installed = _pip_list()
    results = []
    all_ok = True
    for pkg, min_ver in required.items():
        ver_str = installed.get(pkg.lower(), "")
        installed_ver = _parse_version(ver_str) if ver_str else None
        min_ver_tuple = _parse_version(min_ver)
        ok = installed_ver is not None and _version_ok(installed_ver, min_ver_tuple)
        if not ok:
            all_ok = False
        results.append({
            "package": pkg,
            "installed": ok,
            "version": ver_str or "not installed",
            "min_version": min_ver,
        })
    status = " + ".join([r["package"] for r in results if r["installed"]][:5])
    if all_ok:
        status += " (all ok)"
    else:
        missing = [r["package"] for r in results if not r["installed"]]
        status += f" missing: {', '.join(missing)}"
    return all_ok, status, "", None


def _install_python_packages():
    return False, "Automatic package installation is disabled."


_add({
    "id": "python_packages",
    "name": "Python Packages",
    "description": "Required Python libraries for the backend server",
    "required": True,
    "min_version": None,
    "can_auto_install": True,
    "check": _check_python_packages,
    "install": _install_python_packages,
})


def _check_node_packages():
    frontend_path = os.path.join(BASE_DIR, "frontend")
    pkg_json = os.path.join(frontend_path, "package.json")
    if not os.path.exists(pkg_json):
        return False, "package.json not found", "", None

    with open(pkg_json, "r") as f:
        data = json.load(f)

    all_deps = {}
    all_deps.update(data.get("dependencies", {}))
    all_deps.update(data.get("devDependencies", {}))

    node_modules = os.path.join(frontend_path, "node_modules")
    installed_pkgs = _npm_list()

    all_ok = True
    missing = []
    for pkg, req_ver in all_deps.items():
        pkg_lower = pkg.lower()
        pkg_dir = os.path.join(node_modules, pkg)
        has_dir = os.path.isdir(pkg_dir)
        ver_str = installed_pkgs.get(pkg_lower, "")
        ok = has_dir or bool(ver_str)
        if not ok:
            all_ok = False
            missing.append(pkg)
    status = f"{len(all_deps)} deps"
    if missing:
        status += f", missing: {', '.join(missing[:5])}"
    else:
        status += ", all installed"
    return all_ok, status, "", None


def _install_node_packages():
    return False, "Automatic package installation is disabled."


_add({
    "id": "node_packages",
    "name": "Node Packages",
    "description": "Required JavaScript packages for the Electron + React frontend",
    "required": True,
    "min_version": None,
    "can_auto_install": True,
    "check": _check_node_packages,
    "install": _install_node_packages,
})


def _check_electron():
    frontend_path = os.path.join(BASE_DIR, "frontend")
    electron_path = os.path.join(frontend_path, "node_modules", ".bin", "electron")
    if IS_WIN:
        electron_path += ".cmd"
    if os.path.exists(electron_path):
        ok, out, _ = _run([electron_path, "--version"])
        if ok:
            ver = _parse_version(out)
            return True, out.strip(), electron_path, ver
    return False, "", "", None


def _install_electron():
    return False, "Electron is installed via npm. Run: cd frontend && npm install"


_add({
    "id": "electron",
    "name": "Electron",
    "description": "Desktop application framework for the cross-platform UI",
    "required": True,
    "min_version": (28, 0),
    "can_auto_install": False,
    "check": _check_electron,
    "install": _install_electron,
})


def _check_ollama():
    path = _executable_path("ollama")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"])
    if ok:
        ver = _parse_version(out)
        return True, out, path, ver
    return False, "", "", None


def _install_ollama():
    if IS_WIN:
        return False, "Download Ollama from https://ollama.com"
    elif IS_MAC:
        return False, "Download Ollama from https://ollama.com or run: brew install --cask ollama"
    else:
        return False, "Run: curl -fsSL https://ollama.com | sh"


_add({
    "id": "ollama",
    "name": "Ollama",
    "description": "Local AI model runner for free offline inference (alternative to NVIDIA)",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_ollama,
    "install": _install_ollama,
})


def _check_vscode():
    path = _executable_path("code")
    if path:
        ok, out, _ = _run([path, "--version"])
        if ok:
            ver = _parse_version(out.split("\n")[0] if "\n" in out else out)
            return True, out.split("\n")[0].strip() if "\n" in out else out, path, ver
    # Check common install paths
    if IS_WIN:
        common = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\bin\code.cmd"),
        ]
    elif IS_MAC:
        common = ["/usr/local/bin/code", "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"]
    else:
        common = ["/usr/bin/code", "/snap/bin/code"]
    for p in common:
        if os.path.exists(p):
            return True, "installed", p, None
    return False, "", "", None


def _install_vscode():
    if IS_WIN:
        return False, "Download VS Code from https://code.visualstudio.com"
    elif IS_MAC:
        return False, "Run: brew install --cask visual-studio-code"
    else:
        return False, "Run: sudo snap install code --classic  or  download from https://code.visualstudio.com"


_add({
    "id": "vscode",
    "name": "VS Code",
    "description": "Code editor for viewing and editing generated project files",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_vscode,
    "install": _install_vscode,
})


def _check_expo_cli():
    frontend_path = os.path.join(BASE_DIR, "frontend")
    path = os.path.join(frontend_path, "node_modules", ".bin", "expo")
    if IS_WIN:
        path += ".cmd"
    if not os.path.exists(path):
        return False, "", "", None
    ok, out, _ = _run([path, "--version"], timeout=10, cwd=frontend_path)
    if ok:
        ver = _parse_version(out)
        return True, out.strip(), path, ver
    return False, "", "", None


def _install_expo_cli():
    return False, "Expo runs through npx. Install Node.js/npm, then use: npx create-expo-app or npm install -g expo-cli"


_add({
    "id": "expo",
    "name": "Expo / React Native",
    "description": "Mobile app toolchain for React Native and Expo Android/iOS projects",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_expo_cli,
    "install": _install_expo_cli,
})


def _check_android_sdk():
    adb = _executable_path("adb")
    if adb:
        ok, out, _ = _run([adb, "version"], timeout=10)
        first_line = out.splitlines()[0] if out else "ADB detected"
        return (True, first_line, adb, _parse_version(first_line)) if ok else (False, "", "", None)
    return False, "", "", None


def _install_android_sdk():
    return False, "Install Android Studio and configure ANDROID_HOME/ANDROID_SDK_ROOT: https://developer.android.com/studio"


_add({
    "id": "android-platform-tools",
    "name": "Android Platform Tools / ADB",
    "description": "Optional native Android emulator/runtime for generated mobile apps",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_android_sdk,
    "install": _install_android_sdk,
})


def _check_ios_simulator():
    if not IS_MAC:
        return False, "iOS Simulator requires macOS and Xcode", "", None
    path = _executable_path("xcrun")
    if not path:
        return False, "xcrun not found", "", None
    ok, out, err = _run(["xcrun", "simctl", "list", "devices"], timeout=30)
    if ok:
        return True, "iOS Simulator available", path, None
    return False, err or out, path, None


def _install_ios_simulator():
    return False, "Install Xcode from the Mac App Store, then open Xcode once to install simulator components."


_add({
    "id": "ios_simulator",
    "name": "iOS Simulator",
    "description": "Optional native iPhone/iPad simulator for Expo or React Native projects on macOS",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_ios_simulator,
    "install": _install_ios_simulator,
})


def _check_opencode():
    from opencode_bridge import _discover_opencode

    path = _discover_opencode()
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"], timeout=10)
    return (True, out.strip(), path, _parse_version(out)) if ok else (False, "", "", None)


def _check_github_cli():
    path = _executable_path("gh")
    if not path:
        return False, "", "", None
    ok, out, _ = _run([path, "--version"], timeout=10)
    first_line = out.splitlines()[0] if out else ""
    return (True, first_line, path, _parse_version(first_line)) if ok else (False, "", "", None)


def _check_pycharm():
    for command in ("pycharm", "pycharm64"):
        path = _executable_path(command)
        if path:
            return True, "Detected", path, None
    if IS_WIN:
        patterns = [
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\scripts\pycharm.cmd"),
        ]
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                return True, "Detected", matches[0], None
    return False, "", "", None


_add({
    "id": "opencode",
    "name": "OpenCode",
    "description": "Mandatory coding and repair runtime used by the production pipeline",
    "required": True,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_opencode,
    "install": lambda: (False, "Use the official OpenCode installation documentation."),
})

_add({
    "id": "github-cli",
    "name": "GitHub CLI",
    "description": "Optional command-line tooling for GitHub workflows",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_github_cli,
    "install": lambda: (False, "Use the official GitHub CLI installation page."),
})

_add({
    "id": "pycharm",
    "name": "PyCharm",
    "description": "Integrated editor option for generated Python projects",
    "required": False,
    "min_version": None,
    "can_auto_install": False,
    "check": _check_pycharm,
    "install": lambda: (False, "Use the official PyCharm download page."),
})


# ─── Public API ────────────────────────────────────────────────────────

COMPONENT_PRESENTATION = {
    "python": {"category": "Recommended", "action_label": "Download Python", "description": "Runs and validates generated Python projects."},
    "pip": {"category": "Recommended", "action_label": "Install Instructions", "description": "Installs Python project dependencies; normally bundled with Python."},
    "nodejs": {"category": "Recommended", "action_label": "Download Node.js", "description": "Runs and validates generated JavaScript projects."},
    "npm": {"category": "Recommended", "action_label": "Node.js Instructions", "description": "Installs JavaScript dependencies and normally comes with Node.js."},
    "opencode": {"category": "Required", "action_label": "Download OpenCode", "description": "Mandatory coding and repair runtime for the production pipeline."},
    "git": {"category": "Recommended", "action_label": "Download Git", "description": "Version control for generated projects and collaboration."},
    "github-cli": {"category": "Optional", "action_label": "Download GitHub CLI", "description": "Optional command-line integration for GitHub workflows."},
    "vscode": {"category": "Recommended", "action_label": "Download VS Code", "description": "Editor integration for generated project files."},
    "pycharm": {"category": "Recommended", "action_label": "Download PyCharm", "description": "Editor integration for generated Python projects."},
    "android-platform-tools": {"category": "Optional", "action_label": "Download Platform Tools", "description": "ADB and Android tooling for target-specific mobile verification."},
    "docker": {"category": "Optional", "action_label": "Download Docker", "description": "Optional container runtime for isolated project testing."},
    "ollama": {"category": "Optional", "action_label": "Download Ollama", "description": "Optional local model runtime."},
    "expo": {"category": "Optional", "action_label": "Install Instructions", "description": "Optional Expo and React Native project tooling."},
    "ios_simulator": {"category": "Optional", "action_label": "Install Instructions", "description": "Optional Xcode simulator tooling available on macOS."},
}

COMPONENT_ORDER = tuple(COMPONENT_PRESENTATION)


def _user_components():
    registered = {}
    for component in COMPONENTS:
        registered.setdefault(component["id"], component)
    return [
        {**registered[component_id], **COMPONENT_PRESENTATION[component_id]}
        for component_id in COMPONENT_ORDER
        if component_id in registered
    ]


def get_components():
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "category": c["category"],
            "required": c["category"] == "Required",
            "min_version": c.get("min_version"),
            "can_auto_install": False,
            "action_label": c["action_label"],
        }
        for c in _user_components()
    ]


def _minimum_version(component):
    minimum = component.get("min_version")
    return ".".join(str(part) for part in minimum) if minimum else ""


def _error_result(component, duration_ms, error_code, message, timed_out=False):
    return {
        "id": component["id"],
        "name": component["name"],
        "description": component["description"],
        "category": component["category"],
        "action_label": component["action_label"],
        "status": "Error",
        "installed": False,
        "version": "",
        "path": "",
        "up_to_date": False,
        "required": component["category"] == "Required",
        "min_version": _minimum_version(component),
        "can_auto_install": False,
        "timed_out": timed_out,
        "error_code": error_code,
        "message": message,
        "duration_ms": duration_ms,
    }


def _check_component(component, component_timeout, results, closed, lock):
    started = time.monotonic()
    _RUN_STATE.error_code = None
    _RUN_STATE.deadline = started + component_timeout
    try:
        outcome = component["check"]()
        if not isinstance(outcome, tuple) or len(outcome) != 4:
            raise ValueError("Invalid component check result")
        installed, version, _path, parsed_ver = outcome
        min_ver = component.get("min_version")
        up_to_date = _version_ok(parsed_ver, min_ver) if installed else False
        run_error = getattr(_RUN_STATE, "error_code", None)
        duration_ms = round((time.monotonic() - started) * 1000)
        if run_error and not installed:
            result = _error_result(
                component,
                duration_ms,
                run_error,
                "The component check did not complete successfully.",
                timed_out=run_error == "subprocess_timeout",
            )
        else:
            restart_required = bool(component.get("restart_required")) and installed
            status = "Restart required" if restart_required else "Installed" if installed else "Missing"
            result = {
                "id": component["id"],
                "name": component["name"],
                "description": component["description"],
                "category": component["category"],
                "action_label": component["action_label"],
                "status": status,
                "installed": bool(installed),
                "version": str(version).replace("\r", " ").replace("\n", " ")[:120] if installed else "",
                "path": "",
                "up_to_date": bool(up_to_date),
                "required": component["category"] == "Required",
                "min_version": _minimum_version(component),
                "can_auto_install": False,
                "timed_out": False,
                "error_code": None,
                "message": "Update required." if installed and not up_to_date else "",
                "duration_ms": duration_ms,
            }
    except Exception:
        duration_ms = round((time.monotonic() - started) * 1000)
        result = _error_result(
            component,
            duration_ms,
            "check_failed",
            "The component check failed.",
        )

    with lock:
        if component["id"] not in closed:
            results[component["id"]] = result


def check_all(component_timeout=COMPONENT_CHECK_TIMEOUT_SECONDS):
    started = time.monotonic()
    unique_components = _user_components()

    results = {}
    closed = set()
    lock = threading.Lock()
    workers = []
    for component in unique_components:
        thread = threading.Thread(
            target=_check_component,
            args=(component, component_timeout, results, closed, lock),
            name=f"component-check-{component['id']}",
            daemon=True,
        )
        workers.append((component, thread, time.monotonic()))
        thread.start()

    for component, thread, component_started in workers:
        remaining = max(0.0, component_timeout - (time.monotonic() - component_started))
        thread.join(remaining)
        if thread.is_alive():
            with lock:
                closed.add(component["id"])
                results[component["id"]] = _error_result(
                    component,
                    round(component_timeout * 1000),
                    "timeout",
                    "The component check timed out.",
                    timed_out=True,
                )

    ordered_results = [results[component["id"]] for component in unique_components]
    summary = {
        "total": len(ordered_results),
        "installed": sum(result["status"] == "Installed" for result in ordered_results),
        "missing": sum(result["status"] == "Missing" for result in ordered_results),
        "error": sum(result["status"] == "Error" for result in ordered_results),
        "restart_required": sum(result["status"] == "Restart required" for result in ordered_results),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    for result in ordered_results:
        logger.info(
            "system_component_check id=%s status=%s duration_ms=%d",
            result["id"],
            result["status"],
            result["duration_ms"],
        )
    logger.info(
        "system_component_check_summary total=%d installed=%d missing=%d error=%d restart_required=%d duration_ms=%d",
        summary["total"],
        summary["installed"],
        summary["missing"],
        summary["error"],
        summary["restart_required"],
        summary["duration_ms"],
    )
    return {"results": ordered_results, "summary": summary}


def install_component(component_id):
    return {
        "success": False,
        "status": "disabled",
        "message": f"Automatic installation is disabled for {component_id}. Use the official component action.",
    }
