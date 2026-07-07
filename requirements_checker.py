import os
import re
import subprocess
import sys
import json


PLATFORM = sys.platform  # 'win32', 'darwin', 'linux'
IS_WIN = PLATFORM == "win32"
IS_MAC = PLATFORM == "darwin"
IS_LINUX = PLATFORM.startswith("linux")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _run(cmd, timeout=30, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=IS_WIN, cwd=cwd)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return False, "", "not found"
    except subprocess.TimeoutExpired:
        return False, "", "timed out"
    except Exception as e:
        return False, "", str(e)


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
    COMPONENTS.append(comp)


def _check_python():
    path = _executable_path("python3") or _executable_path("python") or sys.executable
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
    else:
        ok, out, _ = _run([sys.executable, "-m", "pip", "--version"])
    if ok:
        ver = _parse_version(out)
        pip_path = path or sys.executable + " -m pip"
        return True, out.split(",")[0] if out else "", pip_path, ver
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
    "id": "node",
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
    req_path = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req_path):
        # Create one
        pkgs = ["fastapi", "uvicorn", "pydantic", "aiofiles", "python-multipart", "docker", "httpx"]
        return True, f"Run: pip install {' '.join(pkgs)}"
    ok, out, err = _run([sys.executable, "-m", "pip", "install", "-r", req_path], timeout=120)
    if ok:
        return True, "All Python packages installed successfully."
    return False, f"pip install failed: {err[:200]}"


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
    frontend_path = os.path.join(BASE_DIR, "frontend")
    if not os.path.exists(os.path.join(frontend_path, "package.json")):
        return False, "package.json not found"
    ok, out, err = _run(["npm", "install"], timeout=120, cwd=frontend_path)
    if ok:
        return True, "All Node packages installed successfully."
    return False, f"npm install failed: {err[:200]}"


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


# ─── Public API ────────────────────────────────────────────────────────


def get_components():
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "required": c["required"],
            "min_version": c.get("min_version"),
            "can_auto_install": c.get("can_auto_install", False),
        }
        for c in COMPONENTS
    ]


def check_all():
    results = []
    for c in COMPONENTS:
        try:
            installed, version, path, parsed_ver = c["check"]()
        except Exception as e:
            installed, version, path, parsed_ver = False, "", "", None

        min_ver = c.get("min_version")
        up_to_date = _version_ok(parsed_ver, min_ver) if installed else False

        results.append({
            "id": c["id"],
            "name": c["name"],
            "installed": installed,
            "version": version,
            "path": path,
            "up_to_date": up_to_date,
            "required": c["required"],
            "min_version": str(min_ver) if min_ver else "",
            "can_auto_install": c.get("can_auto_install", False),
        })
    return results


def install_component(component_id):
    for c in COMPONENTS:
        if c["id"] == component_id:
            try:
                success, message = c["install"]()
                return {"success": success, "message": message}
            except Exception as e:
                return {"success": False, "message": str(e)}
    return {"success": False, "message": f"Unknown component: {component_id}"}
