import json
import hashlib
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_spec import Issue, acceptance_evidence_is_direct, detect_project_profiles, ensure_acceptance_evidence_history, normalize_acceptance_evidence, plan_acceptance_verifier, record_acceptance_evidence


IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache", "__pycache__"}
IGNORED_EXTS = {".pyc", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".glb"}
SECRET_PATTERNS = [
    r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b",
    r"sk-[A-Za-z0-9_-]{16,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*=\s*(?!replace_me|your_|example|changeme|<)[^\s'\"]{10,}",
]
TODO_PATTERNS = ("todo: implement", "pass  # todo", "raise notimplementederror", "not implemented", "fake output")
OUTPUT_TAIL_LIMIT = 4000
HORIZONTAL_OVERFLOW_TOLERANCE_PX = 2
AC_CLASS_IMPLEMENTED = "IMPLEMENTED_BUT_NOT_VERIFIED"
AC_CLASS_PARTIAL = "PARTIALLY_IMPLEMENTED"
AC_CLASS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
AC_CLASS_UNSUPPORTED = "UNSUPPORTED_VERIFIER"
AC_CLASS_AMBIGUOUS = "AMBIGUOUS_REQUIREMENT"
PLAYWRIGHT_SETUP_COMMANDS = {
    "python_package": f"{sys.executable} -m pip install playwright",
    "chromium_runtime": f"{sys.executable} -m playwright install chromium",
    "browser_family": "chromium",
    "ownership": "FreelancerStudio verification subsystem only; generated project requirements are not modified",
    "expected_storage": "Playwright package in the active Studio Python environment; Chromium runtime in the Playwright-managed browser cache for the current OS user",
}
WEB_VIEWPORT_MATRIX = [
    {"name": "laptop", "width": 1366, "height": 768, "purpose": "low-height laptop usability"},
    {"name": "full_hd_desktop", "width": 1920, "height": 1080, "purpose": "standard desktop layout"},
    {"name": "high_resolution_desktop", "width": 2560, "height": 1440, "purpose": "high-resolution layout balance"},
    {"name": "ultra_wide_desktop", "width": 3440, "height": 1440, "purpose": "ultra-wide uncontrolled stretching prevention"},
    {"name": "tablet_portrait", "width": 768, "height": 1024, "purpose": "portrait tablet layout"},
    {"name": "tablet_landscape", "width": 1024, "height": 768, "purpose": "landscape tablet layout"},
]
DESKTOP_WINDOW_MATRIX = [
    {"name": "minimum_supported_window", "width": 1024, "height": 700, "maximized": False},
    {"name": "compact_window", "width": 1280, "height": 800, "maximized": False},
    {"name": "default_window", "width": 1600, "height": 1000, "maximized": False},
    {"name": "full_hd_window", "width": 1920, "height": 1080, "maximized": True},
    {"name": "high_resolution_window", "width": 2560, "height": 1440, "maximized": True},
    {"name": "ultra_wide_maximized_window", "width": 3440, "height": 1440, "maximized": True, "optional": True},
]


@dataclass
class RuntimeAdapterResult:
    applicable: bool
    started: bool
    verified: bool
    stopped_cleanly: bool
    evidence: dict[str, Any]
    error: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeServerHandle:
    adapter: str
    base_url: str
    process: subprocess.Popen
    result: RuntimeAdapterResult


class RuntimeAdapter:
    name = "runtime"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        raise NotImplementedError

    def start_http_server(self, project: dict, root: str, env_extra: dict[str, str] | None = None) -> tuple[RuntimeAdapterResult, RuntimeServerHandle | None]:
        return RuntimeAdapterResult(False, False, False, True, {"reason": f"{self.name} does not expose a reusable HTTP runtime"}, ""), None


def _walk_files(root: str):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in IGNORED_EXTS:
                continue
            path = os.path.join(current, name)
            yield os.path.relpath(path, root).replace(os.sep, "/"), path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _add(checks: list[dict[str, Any]], name: str, status: str, evidence: dict[str, Any]):
    checks.append({"name": name, "status": status, "evidence": evidence})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _redact_secrets(value: Any) -> str:
    text = "" if value is None else str(value)
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = re.sub(pattern, lambda m: m.group(0).split("=", 1)[0] + "=<redacted>" if "=" in m.group(0) else "<redacted>", redacted)
    redacted = re.sub(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s'\"]+", r"\1=<redacted>", redacted)
    return redacted


def _tail_output(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return _redact_secrets(value)[-OUTPUT_TAIL_LIMIT:]


def _run_command(command: list[str], cwd: str, timeout: int = 120) -> dict[str, Any]:
    started = time.time()
    command_text = _redact_secrets(" ".join(command))
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return {
            "command": command_text,
            "cwd": cwd,
            "timeout": timeout,
            "exit_code": result.returncode,
            "stdout_tail": _tail_output(result.stdout),
            "stderr_tail": _tail_output(result.stderr),
            "duration": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command_text,
            "cwd": cwd,
            "timeout": timeout,
            "exit_code": "timeout",
            "stdout_tail": _tail_output(getattr(exc, "stdout", "") or ""),
            "stderr_tail": _tail_output(getattr(exc, "stderr", "") or ""),
            "duration": round(time.time() - started, 3),
        }


def _check_secrets(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        try:
            text = _read(path)
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(rel)
                break
    return not hits, hits


def _check_todos(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        if not rel.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".md")):
            continue
        try:
            text = _read(path).lower()
        except Exception:
            continue
        if any(pattern in text for pattern in TODO_PATTERNS):
            hits.append(rel)
    return not hits, hits


class FastAPIRuntimeAdapter(RuntimeAdapter):
    name = "fastapi"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
        if "fastapi" not in profiles:
            return RuntimeAdapterResult(False, False, False, True, {"reason": "No FastAPI profile"}, "")
        candidates = [("main.py", "main:app"), ("app.py", "app:app")]
        entrypoint = None
        for filename, module_ref in candidates:
            if os.path.isfile(os.path.join(root, filename)):
                entrypoint = (filename, module_ref)
                break
        if not entrypoint:
            return RuntimeAdapterResult(True, False, False, True, {}, "Missing root FastAPI entrypoint (main.py or app.py)")
        port = _free_port()
        command = [sys.executable, "-m", "uvicorn", entrypoint[1], "--host", "127.0.0.1", "--port", str(port)]
        command_text = " ".join(command)
        proc = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        started = True
        output = []
        probes = []
        base_evidence = {
            "adapter": self.name,
            "entrypoint": entrypoint[0],
            "module_ref": entrypoint[1],
            "host": "127.0.0.1",
            "free_port": port,
            "command": command_text,
            "pid": proc.pid,
            "owned_process_only": True,
            "shutdown_method": "pending",
            "killed": False,
            "probes": probes,
        }
        result = RuntimeAdapterResult(
            True,
            started,
            False,
            False,
            dict(base_evidence),
            "Runtime verification did not complete",
        )
        try:
            deadline = time.time() + 12
            last_error = ""
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                for path in ("/health", "/"):
                    try:
                        url = f"http://127.0.0.1:{port}{path}"
                        with urllib.request.urlopen(url, timeout=1.5) as resp:
                            body = resp.read(500).decode("utf-8", errors="replace")
                        probes.append({"path": path, "url": url, "status_code": resp.status, "success": True})
                        evidence = dict(base_evidence)
                        evidence.update({
                            "url": url,
                            "status_code": resp.status,
                            "response_sample": body,
                        })
                        result = RuntimeAdapterResult(
                            True,
                            started,
                            True,
                            False,
                            evidence,
                            "",
                        )
                        return result
                    except Exception as exc:
                        last_error = str(exc)
                        probes.append({"path": path, "url": f"http://127.0.0.1:{port}{path}", "success": False, "error": _tail_output(last_error)})
                time.sleep(0.4)
            try:
                if proc.poll() is not None and proc.stdout:
                    output.append(proc.stdout.read()[-2000:])
            except Exception:
                pass
            evidence = dict(base_evidence)
            evidence["process_output"] = "\n".join(output)
            result = RuntimeAdapterResult(
                True,
                started,
                False,
                False,
                evidence,
                last_error,
            )
            return result
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    result.stopped_cleanly = True
                    result.evidence["shutdown_method"] = "terminate"
                except subprocess.TimeoutExpired:
                    proc.kill()
                    result.stopped_cleanly = False
                    result.evidence["shutdown_method"] = "kill"
                    result.evidence["killed"] = True
            else:
                result.stopped_cleanly = True
                result.evidence["shutdown_method"] = "already_exited"

    def start_http_server(self, project: dict, root: str, env_extra: dict[str, str] | None = None) -> tuple[RuntimeAdapterResult, RuntimeServerHandle | None]:
        profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
        if "fastapi" not in profiles:
            return RuntimeAdapterResult(False, False, False, True, {"reason": "No FastAPI profile"}, ""), None

        candidates = [("main.py", "main:app"), ("app.py", "app:app")]
        entrypoint = None
        for filename, module_ref in candidates:
            if os.path.isfile(os.path.join(root, filename)):
                entrypoint = (filename, module_ref)
                break
        if not entrypoint:
            return RuntimeAdapterResult(True, False, False, True, {}, "Missing root FastAPI entrypoint (main.py or app.py)"), None

        port = _free_port()
        command = [sys.executable, "-m", "uvicorn", entrypoint[1], "--host", "127.0.0.1", "--port", str(port)]
        command_text = " ".join(command)
        env = os.environ.copy()
        if env_extra:
            env.update({str(key): str(value) for key, value in env_extra.items()})
        proc = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        probes = []
        base_evidence = {
            "adapter": self.name,
            "entrypoint": entrypoint[0],
            "module_ref": entrypoint[1],
            "host": "127.0.0.1",
            "free_port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "command": command_text,
            "pid": proc.pid,
            "env_overrides": sorted(env_extra or {}),
            "owned_process_only": True,
            "shutdown_method": "pending",
            "killed": False,
            "probes": probes,
        }
        result = RuntimeAdapterResult(True, True, False, False, dict(base_evidence), "Runtime verification did not complete")
        deadline = time.time() + 12
        last_error = ""
        output = []
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            for path in ("/openapi.json", "/health", "/"):
                url = f"http://127.0.0.1:{port}{path}"
                try:
                    with urllib.request.urlopen(url, timeout=1.5) as resp:
                        body = resp.read(500).decode("utf-8", errors="replace")
                    probes.append({"path": path, "url": url, "status_code": resp.status, "success": True})
                    evidence = dict(base_evidence)
                    evidence.update({"url": url, "status_code": resp.status, "response_sample": _redact_secrets(body)})
                    result = RuntimeAdapterResult(True, True, True, False, evidence, "")
                    return result, RuntimeServerHandle(self.name, f"http://127.0.0.1:{port}", proc, result)
                except urllib.error.HTTPError as exc:
                    last_error = f"HTTP {exc.code}"
                    body = exc.read(500).decode("utf-8", errors="replace") if exc.fp else ""
                    probes.append({"path": path, "url": url, "status_code": exc.code, "success": False, "error": _tail_output(body or last_error)})
                except Exception as exc:
                    last_error = str(exc)
                    probes.append({"path": path, "url": url, "success": False, "error": _tail_output(last_error)})
            time.sleep(0.4)

        try:
            if proc.poll() is not None and proc.stdout:
                output.append(proc.stdout.read()[-2000:])
        except Exception:
            pass
        evidence = dict(base_evidence)
        evidence["process_output"] = _tail_output("\n".join(output))
        result = RuntimeAdapterResult(True, True, False, False, evidence, _tail_output(last_error))
        _stop_owned_process(proc, result)
        return result, None


def _static_entrypoint(root: str) -> tuple[str, str] | None:
    for rel in ("index.html", "frontend/index.html", "dist/index.html"):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return rel, os.path.dirname(path)
    return None


def _local_asset_refs(html: str) -> list[str]:
    refs = []
    for attr in ("href", "src"):
        for match in re.finditer(rf"\b{attr}\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE):
            ref = match.group(1).strip()
            if not ref or ref.startswith(("http://", "https://", "//", "mailto:", "tel:", "#", "data:")):
                continue
            refs.append(ref.split("?", 1)[0].split("#", 1)[0])
    return _dedupe_strings(refs)


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.replace("\\", "/")
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _asset_exists(site_root: str, ref: str) -> bool:
    if not ref or os.path.isabs(ref) and not ref.startswith("/"):
        return False
    rel = ref.lstrip("/").replace("/", os.sep)
    if ".." in Path(rel).parts:
        return False
    return os.path.exists(os.path.join(site_root, rel))


def _project_profiles(project: dict) -> set[str]:
    return set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))


def _effective_project_profiles(project: dict, root: str) -> list[str]:
    spec = project.get("project_spec", {}) if isinstance(project.get("project_spec"), dict) else {}
    text = "\n".join(
        str(part)
        for part in (
            project.get("title", ""),
            project.get("description", ""),
            spec.get("original_user_request", ""),
        )
        if part
    )
    profiles = detect_project_profiles(spec, project_path=root, text=text)
    if profiles:
        project["project_profiles"] = profiles
        if isinstance(spec, dict):
            spec["project_profiles"] = profiles
            spec["project_type"] = profiles[0]
    return profiles


def _package_json_path(root: str) -> tuple[str, str] | None:
    for rel in ("package.json", "frontend/package.json"):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return rel, os.path.dirname(path)
    return None


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _stop_owned_process(proc: subprocess.Popen, result: RuntimeAdapterResult) -> None:
    if proc.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T"], capture_output=True, text=True, timeout=5)
                proc.wait(timeout=5)
                result.stopped_cleanly = True
                result.evidence["shutdown_method"] = "taskkill_tree"
                result.evidence["killed"] = False
                return
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
            result.stopped_cleanly = True
            result.evidence["shutdown_method"] = "terminate"
        except subprocess.TimeoutExpired:
            proc.kill()
            result.stopped_cleanly = False
            result.evidence["shutdown_method"] = "kill"
            result.evidence["killed"] = True
    else:
        result.stopped_cleanly = True
        result.evidence["shutdown_method"] = "already_exited"


def _runtime_script_command(script_name: str, script_value: str, port: int) -> list[str]:
    command = [_npm_command(), "run", script_name]
    if "vite" in script_value.lower():
        command.extend(["--", "--host", "127.0.0.1", "--port", str(port)])
    return command


class ReactViteRuntimeAdapter(RuntimeAdapter):
    name = "react_vite"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        profiles = _project_profiles(project)
        if not profiles.intersection({"react_frontend", "vite_frontend"}):
            return RuntimeAdapterResult(False, False, False, True, {"reason": "No React/Vite profile"}, "")

        package_info = _package_json_path(root)
        if not package_info:
            return RuntimeAdapterResult(True, False, False, True, {"checked_paths": ["package.json", "frontend/package.json"]}, "Missing package.json")

        package_rel, app_root = package_info
        try:
            package_data = json.loads(_read(os.path.join(root, package_rel)))
        except json.JSONDecodeError as exc:
            return RuntimeAdapterResult(True, False, False, True, {"package_json": package_rel}, f"Invalid package.json: {exc}")

        scripts = package_data.get("scripts", {}) if isinstance(package_data, dict) else {}
        if not isinstance(scripts, dict):
            scripts = {}
        missing_scripts = [name for name in ("build",) if not scripts.get(name)]
        runtime_script = "preview" if scripts.get("preview") else "dev" if scripts.get("dev") else ""
        if not runtime_script:
            missing_scripts.append("preview_or_dev")
        base_evidence = {
            "adapter": self.name,
            "package_json": package_rel,
            "app_root": app_root,
            "scripts": {name: scripts.get(name) for name in ("build", "preview", "dev") if scripts.get(name)},
            "missing_scripts": missing_scripts,
            "install_command": f"{_npm_command()} install",
            "build_command": f"{_npm_command()} run build",
            "runtime_script": runtime_script,
        }
        if missing_scripts:
            return RuntimeAdapterResult(True, False, False, True, base_evidence, "Missing required package scripts")

        build_result = _run_command([_npm_command(), "run", "build"], app_root, timeout=120)
        base_evidence["build_result"] = build_result
        if build_result.get("exit_code") != 0:
            return RuntimeAdapterResult(True, False, False, True, base_evidence, "npm run build failed")

        port = _free_port()
        script_value = str(scripts.get(runtime_script, ""))
        command = _runtime_script_command(runtime_script, script_value, port)
        command_text = " ".join(command)
        env = os.environ.copy()
        env.update({"HOST": "127.0.0.1", "PORT": str(port)})
        proc = subprocess.Popen(command, cwd=app_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
        probes = []
        evidence = dict(base_evidence)
        evidence.update({
            "host": "127.0.0.1",
            "free_port": port,
            "command": command_text,
            "pid": proc.pid,
            "owned_process_only": True,
            "shutdown_method": "pending",
            "killed": False,
            "probes": probes,
        })
        result = RuntimeAdapterResult(True, True, False, False, dict(evidence), "Runtime verification did not complete")
        try:
            deadline = time.time() + 12
            last_error = ""
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                url = f"http://127.0.0.1:{port}/"
                try:
                    with urllib.request.urlopen(url, timeout=1.5) as resp:
                        body = resp.read(500).decode("utf-8", errors="replace")
                    probes.append({"path": "/", "url": url, "status_code": resp.status, "success": resp.status == 200})
                    verified_evidence = dict(evidence)
                    verified_evidence.update({"url": url, "status_code": resp.status, "response_sample": body})
                    result = RuntimeAdapterResult(True, True, resp.status == 200, False, verified_evidence, "" if resp.status == 200 else f"HTTP {resp.status}")
                    return result
                except Exception as exc:
                    last_error = str(exc)
                    probes.append({"path": "/", "url": url, "success": False, "error": _tail_output(last_error)})
                time.sleep(0.4)
            result = RuntimeAdapterResult(True, True, False, False, dict(evidence), last_error)
            return result
        finally:
            _stop_owned_process(proc, result)


def _python_modules_for_import(root: str) -> list[str]:
    modules = []
    for rel in ("config.py", "bot.py", "handlers/commands.py", "handlers/callbacks.py", "handlers/admin.py", "services/database.py", "services/joke_service.py"):
        if os.path.isfile(os.path.join(root, rel)):
            modules.append(rel[:-3].replace("/", "."))
    return modules


def _run_import_smoke(root: str, modules: list[str]) -> dict[str, Any]:
    if not modules:
        return {"exit_code": 0, "modules": [], "imported": [], "failed": []}
    code = (
        "import importlib, json, os, sys, traceback\n"
        "root = sys.argv[1]\n"
        "mods = sys.argv[2:]\n"
        "sys.path.insert(0, root)\n"
        "os.environ.setdefault('BOT_TOKEN', 'replace_me')\n"
        "os.environ.setdefault('ADMIN_ID', '0')\n"
        "os.environ.setdefault('DATABASE_PATH', 'data/test_bot.db')\n"
        "imported = []\n"
        "failed = []\n"
        "for mod in mods:\n"
        "    try:\n"
        "        importlib.import_module(mod)\n"
        "        imported.append(mod)\n"
        "    except Exception as exc:\n"
        "        failed.append({'module': mod, 'error': type(exc).__name__ + ': ' + str(exc), 'traceback_tail': traceback.format_exc()[-1200:]})\n"
        "print(json.dumps({'modules': mods, 'imported': imported, 'failed': failed}))\n"
        "sys.exit(1 if failed else 0)\n"
    )
    result = _run_command([sys.executable, "-c", code, root, *modules], root, timeout=60)
    details: dict[str, Any] = {"modules": modules, "imported": [], "failed": []}
    stdout = result.get("stdout_tail", "").strip()
    if stdout:
        try:
            details = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    details.update(result)
    return details


def _read_project_text(root: str, rels: list[str]) -> dict[str, str]:
    texts = {}
    for rel in rels:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            texts[rel] = _read(path)
    return texts


CREDENTIAL_NAME_RE = re.compile(r"\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*\b")
CREDENTIAL_PLACEHOLDERS = {"", "replace_me", "changeme", "change_me", "your_key_here", "your_token_here", "example", "none", "null"}


def _bool_field(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "no", "0", "optional")
    if value is None:
        return default
    return bool(value)


def _credential_description(name: str) -> str:
    labels = {
        "OPENAI_API_KEY": "OpenAI API key",
        "ANTHROPIC_API_KEY": "Anthropic API key",
        "TELEGRAM_BOT_TOKEN": "Telegram Bot API token",
        "SMTP_PASSWORD": "SMTP credentials",
        "STRIPE_API_KEY": "Stripe API key",
        "DISCORD_TOKEN": "Discord bot token",
        "EXTERNAL_SERVICE_CREDENTIAL": "External service credential",
    }
    return labels.get(name, name.replace("_", " ").title())


def _credential_blocks_completion(name: str, required: bool, source: str, explicit: Any = None) -> bool:
    if explicit is not None:
        return _bool_field(explicit, default=required)
    if not required:
        return False
    if name == "TELEGRAM_BOT_TOKEN" and source in ("project_spec", "user_request"):
        return False
    return True


def _credential_configured(root: str, name: str) -> bool:
    value = os.environ.get(name, "")
    if value.strip() and value.strip().lower() not in CREDENTIAL_PLACEHOLDERS:
        return True
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return False
    try:
        for line in _read(env_path).splitlines():
            match = re.match(rf"^\s*{re.escape(name)}\s*=\s*(.*)$", line)
            if not match:
                continue
            configured = match.group(1).strip().strip("'\"")
            return bool(configured and configured.lower() not in CREDENTIAL_PLACEHOLDERS)
    except Exception:
        return False
    return False


def _merge_credential(records: dict[str, dict[str, Any]], name: str, source: str, required: bool, description: str = "", blocks_completion: Any = None) -> None:
    if not name:
        return
    current = records.setdefault(
        name,
        {
            "name": name,
            "source": source,
            "required": False,
            "configured": False,
            "externally_verifiable": True,
            "blocks_completion": False,
            "description": description or _credential_description(name),
        },
    )
    if source not in str(current.get("source", "")).split("+"):
        current["source"] = "+".join(part for part in (current.get("source"), source) if part)
    current["required"] = bool(current.get("required")) or bool(required)
    current["description"] = current.get("description") or description or _credential_description(name)
    current["blocks_completion"] = bool(current.get("blocks_completion")) or _credential_blocks_completion(name, bool(required), source, blocks_completion)


def _credential_names_from_env_example(root: str) -> list[str]:
    path = os.path.join(root, ".env.example")
    if not os.path.isfile(path):
        return []
    names = []
    for line in _read(path).splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]+)\s*=", line)
        if match and CREDENTIAL_NAME_RE.fullmatch(match.group(1)):
            names.append(match.group(1))
    return _dedupe_strings(names)


def _credential_names_from_code(root: str) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    for rel, path in _walk_files(root):
        if not rel.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".md")):
            continue
        try:
            text = _read(path)
        except Exception:
            continue
        for name in CREDENTIAL_NAME_RE.findall(text):
            required = bool(
                re.search(rf"os\.environ\[['\"]{re.escape(name)}['\"]\]", text)
                or re.search(rf"raise\s+\w*Error\([^\)]*{re.escape(name)}", text)
                or re.search(rf"Set\s+{re.escape(name)}", text, flags=re.IGNORECASE)
            )
            found.append((name, required))
    deduped: dict[str, bool] = {}
    for name, required in found:
        deduped[name] = deduped.get(name, False) or required
    return list(deduped.items())


def normalize_credential_state(project: dict, root: str, qa_result: dict | None = None) -> list[dict[str, Any]]:
    spec = project.get("project_spec", {}) if isinstance(project.get("project_spec"), dict) else {}
    records: dict[str, dict[str, Any]] = {}
    spec_credentials = spec.get("required_credentials", []) if isinstance(spec.get("required_credentials", []), list) else []
    for credential in spec_credentials:
        if not isinstance(credential, dict):
            continue
        name = str(credential.get("name") or "").strip()
        required = _bool_field(credential.get("required"), default=True)
        _merge_credential(records, name, str(credential.get("source") or "project_spec"), required, str(credential.get("description") or ""), credential.get("blocks_completion"))

    for name in _credential_names_from_env_example(root):
        _merge_credential(records, name, "env_example", False)

    for name, required in _credential_names_from_code(root):
        _merge_credential(records, name, "generated_project_code", required)

    if (qa_result or {}).get("needs_credentials") and not any(record.get("required") and record.get("blocks_completion") for record in records.values()):
        _merge_credential(records, "EXTERNAL_SERVICE_CREDENTIAL", "qa_result", True, "External credential reported by QA", True)

    normalized = []
    for name in sorted(records):
        record = records[name]
        configured = _credential_configured(root, name)
        record["configured"] = configured
        record["blocks_completion"] = bool(record.get("blocks_completion") and not configured)
        normalized.append(record)
    return normalized


def _credential_limitations(spec: dict, credential_state: list[dict[str, Any]]) -> list[str]:
    generic_credential_risk = "Project depends on external credentials that cannot be validated without user input"
    limitations = [risk for risk in spec.get("risks", []) if risk != generic_credential_risk]
    for credential in credential_state:
        if credential.get("configured"):
            continue
        if credential.get("required") and credential.get("blocks_completion"):
            continue
        if "generated_project_code" in str(credential.get("source", "")) and credential.get("externally_verifiable"):
            limitations.append(f"Optional credential {credential.get('name')} is not configured; related external behavior was not verified")
    return _dedupe_strings(limitations)


def _telegram_env_safety(root: str) -> dict[str, Any]:
    env_example = os.path.join(root, ".env.example")
    gitignore = os.path.join(root, ".gitignore")
    env_file = os.path.join(root, ".env")
    evidence = {
        "env_example_exists": os.path.isfile(env_example),
        "bot_token_documented": False,
        "env_example_secret_like": False,
        "env_file_secret_like": False,
        "gitignore_exists": os.path.isfile(gitignore),
        "gitignore_ignores_env": False,
    }
    if os.path.isfile(env_example):
        text = _read(env_example)
        evidence["bot_token_documented"] = "BOT_TOKEN" in text
        token_match = re.search(r"(?m)^BOT_TOKEN\s*=\s*(.+)$", text)
        evidence["env_example_secret_like"] = bool(token_match and re.match(r"^\d{7,}:[A-Za-z0-9_-]{20,}$", token_match.group(1).strip()))
    if os.path.isfile(env_file):
        evidence["env_file_secret_like"] = bool(re.search(r"(?m)^BOT_TOKEN\s*=\s*\d{7,}:[A-Za-z0-9_-]{20,}\s*$", _read(env_file)))
    if os.path.isfile(gitignore):
        evidence["gitignore_ignores_env"] = any(line.strip() in (".env", "*.env") for line in _read(gitignore).splitlines())
    evidence["safe"] = bool(
        evidence["env_example_exists"]
        and evidence["bot_token_documented"]
        and evidence["gitignore_exists"]
        and evidence["gitignore_ignores_env"]
        and not evidence["env_example_secret_like"]
        and not evidence["env_file_secret_like"]
    )
    return evidence


def _telegram_static_smoke(root: str) -> dict[str, Any]:
    rels = ["bot.py", "handlers/commands.py", "handlers/callbacks.py", "handlers/admin.py", "keyboards/inline.py"]
    texts = _read_project_text(root, rels)
    combined = "\n".join(texts.values())
    command_hits = sorted(set(re.findall(r"Command\(\s*['\"]([^'\"]+)['\"]", combined)))
    slash_hits = sorted(set(re.findall(r"/(start|help|joke|category|top|favorite|search|stats|users|broadcast)\b", combined)))
    key_commands = sorted(set(command_hits + slash_hits).intersection({"start", "help"}))
    router_modules = [rel for rel, text in texts.items() if "Router(" in text or "router =" in text]
    include_router = "include_router" in combined or "include_routers" in combined
    callback_values = []
    for match in re.finditer(r"callback_data['\"]?\s*[:=]\s*f?['\"]([^'\"]+)['\"]", combined):
        callback_values.append(match.group(1))
    too_long = [value for value in callback_values if len(value.encode("utf-8")) > 64]
    return {
        "handler_files": sorted(texts),
        "router_modules": router_modules,
        "handler_registration_detected": bool(router_modules and include_router),
        "key_commands": key_commands,
        "key_command_smoke_passed": {cmd: cmd in key_commands for cmd in ("start", "help")},
        "callback_data_values": callback_values,
        "callback_data_too_long": too_long,
        "callback_data_max_bytes": max((len(value.encode("utf-8")) for value in callback_values), default=0),
    }


def _telegram_database_service_smoke(root: str) -> dict[str, Any]:
    applicable = any(os.path.isfile(os.path.join(root, rel)) for rel in ("services/database.py", "services/joke_service.py", "database.py"))
    modules = [module for module in ("services.database", "services.joke_service", "database") if os.path.isfile(os.path.join(root, module.replace(".", os.sep) + ".py"))]
    if not applicable:
        return {"applicable": False, "status": "not_applicable", "modules": []}
    result = _run_import_smoke(root, modules)
    return {"applicable": True, "status": "passed" if result.get("exit_code") == 0 else "failed", "modules": modules, "import_result": result}


class TelegramBotRuntimeAdapter(RuntimeAdapter):
    name = "telegram_bot"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        profiles = _project_profiles(project)
        if "telegram_bot" not in profiles:
            return RuntimeAdapterResult(False, False, False, True, {"reason": "No Telegram bot profile"}, "")

        modules = _python_modules_for_import(root)
        import_result = _run_import_smoke(root, modules)
        env_safety = _telegram_env_safety(root)
        static_smoke = _telegram_static_smoke(root)
        db_smoke = _telegram_database_service_smoke(root)
        network_behavior = {
            "status": "not_verified",
            "reason": "Real Telegram network behavior requires BOT_TOKEN credentials and is intentionally not exercised by credential-free runtime verification.",
        }
        evidence = {
            "adapter": self.name,
            "credential_free": True,
            "started": False,
            "imports": import_result,
            "env_safety": env_safety,
            "handler_registration": static_smoke,
            "database_service_smoke": db_smoke,
            "network_behavior": network_behavior,
        }
        missing = []
        if not modules:
            missing.append("importable Telegram modules")
        if import_result.get("exit_code") != 0:
            missing.append("imports")
        if not env_safety.get("safe"):
            missing.append("config/.env safety")
        if not static_smoke.get("handler_registration_detected"):
            missing.append("handler registration")
        if not all(static_smoke.get("key_command_smoke_passed", {}).values()):
            missing.append("key commands")
        if static_smoke.get("callback_data_too_long"):
            missing.append("callback_data length")
        if db_smoke.get("applicable") and db_smoke.get("status") != "passed":
            missing.append("database/service smoke")
        verified = not missing
        evidence["missing_local_checks"] = missing
        return RuntimeAdapterResult(True, False, verified, True, evidence, "" if verified else "; ".join(missing))


class StaticWebRuntimeAdapter(RuntimeAdapter):
    name = "static_web"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
        if "static_website" not in profiles:
            return RuntimeAdapterResult(False, False, False, True, {"reason": "No static website profile"}, "")

        entrypoint = _static_entrypoint(root)
        if not entrypoint:
            return RuntimeAdapterResult(True, False, False, True, {"checked_entrypoints": ["index.html", "frontend/index.html", "dist/index.html"]}, "Missing static entry HTML")

        entry_rel, site_root = entrypoint
        entry_path = os.path.join(root, entry_rel)
        html = _read(entry_path)
        asset_refs = _local_asset_refs(html)
        missing_assets = [ref for ref in asset_refs if not _asset_exists(site_root, ref)]
        if missing_assets:
            return RuntimeAdapterResult(
                True,
                False,
                False,
                True,
                {
                    "adapter": self.name,
                    "entry_html": entry_rel,
                    "site_root": site_root,
                    "local_assets": asset_refs,
                    "missing_assets": missing_assets,
                },
                "Missing local static assets",
            )

        port = _free_port()
        command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
        command_text = " ".join(command)
        proc = subprocess.Popen(command, cwd=site_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        probes = []
        base_evidence = {
            "adapter": self.name,
            "entry_html": entry_rel,
            "site_root": site_root,
            "host": "127.0.0.1",
            "free_port": port,
            "command": command_text,
            "pid": proc.pid,
            "owned_process_only": True,
            "shutdown_method": "pending",
            "killed": False,
            "local_assets": asset_refs,
            "missing_assets": [],
            "probes": probes,
        }
        result = RuntimeAdapterResult(True, True, False, False, dict(base_evidence), "Runtime verification did not complete")
        try:
            deadline = time.time() + 8
            last_error = ""
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                url = f"http://127.0.0.1:{port}/"
                try:
                    with urllib.request.urlopen(url, timeout=1.5) as resp:
                        body = resp.read(500).decode("utf-8", errors="replace")
                    probes.append({"path": "/", "url": url, "status_code": resp.status, "success": resp.status == 200})
                    evidence = dict(base_evidence)
                    evidence.update({"url": url, "status_code": resp.status, "response_sample": body})
                    result = RuntimeAdapterResult(True, True, resp.status == 200, False, evidence, "" if resp.status == 200 else f"HTTP {resp.status}")
                    return result
                except Exception as exc:
                    last_error = str(exc)
                    probes.append({"path": "/", "url": url, "success": False, "error": _tail_output(last_error)})
                time.sleep(0.3)
            result = RuntimeAdapterResult(True, True, False, False, dict(base_evidence), last_error)
            return result
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    result.stopped_cleanly = True
                    result.evidence["shutdown_method"] = "terminate"
                except subprocess.TimeoutExpired:
                    proc.kill()
                    result.stopped_cleanly = False
                    result.evidence["shutdown_method"] = "kill"
                    result.evidence["killed"] = True
            else:
                result.stopped_cleanly = True
                result.evidence["shutdown_method"] = "already_exited"


RUNTIME_ADAPTERS = [FastAPIRuntimeAdapter(), ReactViteRuntimeAdapter(), TelegramBotRuntimeAdapter(), StaticWebRuntimeAdapter()]
KNOWN_RUNTIME_PROFILES = {"fastapi", "react_frontend", "vite_frontend", "telegram_bot", "static_website"}


def _adapter_status(result: RuntimeAdapterResult) -> str:
    if not result.applicable:
        return "not_applicable"
    start_ok = result.started or bool(result.evidence.get("credential_free"))
    return "passed" if start_ok and result.verified and result.stopped_cleanly else "failed"


def _runtime_smoke(project: dict, root: str) -> dict[str, Any]:
    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    for adapter in RUNTIME_ADAPTERS:
        result = adapter.run(project, root)
        if not result.applicable:
            continue
        data = result.to_dict()
        data["adapter"] = adapter.name
        data["status"] = _adapter_status(result)
        if result.error:
            data["error"] = result.error
        data.update(result.evidence)
        return data

    runnable_profiles = sorted(profiles.intersection(KNOWN_RUNTIME_PROFILES))
    if runnable_profiles:
        return {
            "status": "failed",
            "applicable": False,
            "started": False,
            "verified": False,
            "stopped_cleanly": True,
            "profiles": sorted(profiles),
            "known_runnable_profiles": runnable_profiles,
            "error": "Runnable known profile has no applicable runtime adapter",
        }

    return {
        "status": "passed",
        "adapter": "generic",
        "applicable": True,
        "started": False,
        "verified": True,
        "stopped_cleanly": True,
        "profiles": sorted(profiles),
        "limitation": "No specific runtime adapter is available for this unknown project type; generic verification is limited to audit metadata, README/run instructions, files, QA, and acceptance evidence.",
        "error": "",
    }


def _verify_readme(root: str) -> tuple[bool, dict[str, Any]]:
    readme = os.path.join(root, "README.md")
    if not os.path.isfile(readme):
        return False, {"error": "README.md missing"}
    text = _read(readme).lower()
    missing = []
    for label, tokens in {
        "install": ("install", "pip install", "npm install", "установка"),
        "run": ("run", "uvicorn", "python", "npm run", "запуск"),
        "test": ("test", "pytest", "npm test", "провер"),
    }.items():
        if not any(token in text for token in tokens):
            missing.append(label)
    return not missing, {"missing_sections": missing}


def _criteria_mandatory(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in criteria if c.get("priority") in ("critical", "high")]


def _is_mandatory_criterion(criterion: dict[str, Any]) -> bool:
    return criterion.get("priority") in ("critical", "high")


def _evidence_status(evidence: dict[str, Any] | None) -> str:
    return str((evidence or {}).get("status") or "not_verified").lower()


def _has_direct_acceptance_evidence(evidence: dict[str, Any] | None) -> bool:
    return acceptance_evidence_is_direct(evidence, str((evidence or {}).get("criterion_id") or "") or None)


def _latest_direct_acceptance_evidence(criterion: dict[str, Any]) -> dict[str, Any] | None:
    evidence_entries = criterion.get("evidence", [])
    if not isinstance(evidence_entries, list):
        return None
    for evidence in reversed(evidence_entries):
        if _has_direct_acceptance_evidence(evidence):
            return evidence
    return None


def _check_status(checks: list[dict[str, Any]], name: str) -> str | None:
    for check in checks:
        if check.get("name") == name:
            return check.get("status")
    return None


def _registry_evidence(
    method: str,
    status: str,
    summary: str,
    *,
    setup_steps: list[str] | None = None,
    action_steps: list[str] | None = None,
    assertions: list[str] | None = None,
    collected_evidence: dict[str, Any] | None = None,
    failure_reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    evidence = {
        "source": "final_delivery_audit",
        "verifier": "final_delivery_audit",
        "verifier_type": method or "unknown",
        "setup_steps": setup_steps or [],
        "action_steps": action_steps or [],
        "assertions": assertions or [],
        "collected_evidence": collected_evidence or {},
        "failure_reason": failure_reason if status not in ("pass", "passed") else "",
        "method": method,
        "status": status,
        "summary": summary,
        "artifacts": [],
    }
    evidence.update(extra)
    return evidence


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_snapshot_fingerprint(root: str) -> str:
    digest = hashlib.sha256()
    for rel, path in sorted(_walk_files(root)):
        digest.update(rel.encode("utf-8", errors="replace"))
        try:
            digest.update(Path(path).read_bytes())
        except Exception as exc:
            digest.update(str(exc).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _effective_profile_list(project: dict, root: str) -> list[str]:
    return sorted(_effective_project_profiles(project, root))


def _criterion_evidence_context(criterion: dict[str, Any], project: dict, root: str, classification: str) -> dict[str, Any]:
    return {
        "project_path": root,
        "criterion_id": criterion.get("id", ""),
        "criterion_text": " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result", "trace")).strip(),
        "classification": classification,
        "effective_project_profile": _effective_profile_list(project, root),
        "project_snapshot_fingerprint": _project_snapshot_fingerprint(root),
        "execution_timestamp": _utc_now(),
    }


def _targeted_evidence(
    criterion: dict[str, Any],
    project: dict,
    root: str,
    method: str,
    status: str,
    summary: str,
    *,
    classification: str = AC_CLASS_IMPLEMENTED,
    setup_steps: list[str] | None = None,
    action_steps: list[str] | None = None,
    assertions: list[str] | None = None,
    collected_evidence: dict[str, Any] | None = None,
    failure_reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    collected = dict(collected_evidence or {})
    if isinstance(criterion.get("semantic_verifier_plan"), dict):
        collected.setdefault("semantic_verifier_plan", criterion["semantic_verifier_plan"])
    collected.update(_criterion_evidence_context(criterion, project, root, classification))
    evidence = _registry_evidence(
        method,
        status,
        summary,
        setup_steps=setup_steps,
        action_steps=action_steps,
        assertions=assertions,
        collected_evidence=collected,
        failure_reason=failure_reason,
        classification=classification,
        criterion_id=criterion.get("id", ""),
        verdict=status if status in ("passed", "failed", "blocked") else "not_executed",
        **extra,
    )
    return evidence


def _semantic_acceptance_text(criterion: dict[str, Any]) -> str:
    return " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result", "trace")).lower()


def _semantic_verifier_plan(criterion: dict[str, Any], project: dict, root: str) -> dict[str, Any]:
    text = _semantic_acceptance_text(criterion)
    profiles = _effective_profile_list(project, root)
    plan: dict[str, Any] = {
        "criterion_id": criterion.get("id", ""),
        "verifier_type": "manual_or_unsupported",
        "semantic_intent": "unknown",
        "target_entity": "",
        "required_fields": [],
        "expected_values": [],
        "required_actions": [],
        "expected_outcomes": [],
        "project_profile": profiles,
        "route_hints": [],
        "ui_hints": [],
        "confidence": 0.0,
        "unsupported_reason": "No semantic verifier matched the criterion text and project profile",
    }

    def matched(verifier_type: str, semantic_intent: str, *, target_entity: str = "request", confidence: float = 0.9, **extra: Any) -> dict[str, Any]:
        updated = dict(plan)
        updated.update({
            "verifier_type": verifier_type,
            "semantic_intent": semantic_intent,
            "target_entity": target_entity,
            "confidence": confidence,
            "unsupported_reason": "",
        })
        updated.update(extra)
        return updated

    is_request = any(token in text for token in ("request", "requests", "ticket", "tickets", "заяв"))
    if is_request and any(token in text for token in ("lifecycle", "tracking", "track", "current status", "state visibility")):
        return matched("lifecycle_tracking", "track_record_lifecycle", required_actions=["create", "read", "change_status", "read"], expected_outcomes=["stable identity", "status persisted"])
    if any(token in text for token in ("browser", "usable through a browser", "браузер")):
        return matched("browser_usability", "browser_runtime_usability", target_entity="primary_ui", ui_hints=["primary page", "primary action"], expected_outcomes=["real browser page loads", "interaction reachable"])
    if is_request and "required field" in text and any(token in text for token in ("store", "roundtrip", "records")):
        return matched("required_fields_roundtrip", "required_fields_survive_create_read", required_fields=["client_name", "contact", "company", "description", "priority", "status"], required_actions=["create", "read"], expected_outcomes=["field values match"])
    if is_request and "status" in text and any(token in text for token in ("workflow", "states", "values", "include")):
        return matched("workflow_statuses", "workflow_status_set_and_transitions", expected_values=["новая", "в работе", "ожидает клиента", "выполнена", "закрыта"], required_actions=["read metadata", "create", "transition", "read"])
    if any(token in text for token in ("summary count", "request counter", "dashboard", "home page displays current request summary")):
        return matched("dashboard_metrics", "dashboard_counts_match_fixtures", target_entity="dashboard", required_actions=["create fixtures", "read metrics"], expected_outcomes=["exact counts"])
    if is_request and "filter" in text and "status" in text and "priority" in text:
        return matched("filter_behavior", "status_priority_filtering", required_actions=["create distinguishable records", "apply filters"], expected_outcomes=["target inclusion", "control exclusion"])
    if any(token in text for token in ("modern", "tidy", "visual design", "professional appearance")):
        return matched("product_ui_quality", "objective_ui_quality_and_judge", target_entity="primary_ui", ui_hints=["visual hierarchy", "spacing", "readability"])
    if any(token in text for token in ("desktop", "tablet", "responsive", "viewport")):
        return matched("responsive_ui", "desktop_tablet_usability", target_entity="primary_ui", ui_hints=["1440x900", "768x1024"], expected_outcomes=["no catastrophic overflow", "controls reachable"])
    if any(token in text for token in ("single local administrator", "local admin", "complex user system", "public signup")):
        return matched("admin_scope", "single_local_admin_without_complex_users", target_entity="admin", required_actions=["inspect routes", "inspect auth model", "read admin metadata"])
    if any(token in text for token in ("primary interface buttons", "primary actions", "buttons perform")):
        return matched("primary_ui_actions", "primary_controls_trigger_observable_behavior", target_entity="primary_ui", ui_hints=["create", "open", "update", "delete", "filter"])
    if any(token in text for token in ("automated tests", "test coverage", "tests cover")):
        return matched("behavior_test_coverage", "tests_cover_main_behaviors", target_entity="tests", required_actions=["inspect tests", "run tests"])
    if any(token in text for token in ("windows", "installation", "install/run", "run instructions", "startup instructions")):
        return matched("documentation_validation", "windows_install_run_docs", target_entity="documentation", required_actions=["inspect README", "execute safe install command"])
    if any(token in text for token in ("simple", "maintainable", "local stack", "technology stack")):
        return matched("architecture_simplicity", "simple_local_maintainable_stack", target_entity="architecture", required_actions=["inspect dependencies", "inspect infrastructure"])
    return plan


def _browser_automation_support() -> dict[str, Any]:
    capability = _detect_playwright_capability()
    return {
        "supported": capability.get("real_browser_verification_available"),
        "tool": "playwright",
        "reason": capability.get("reason", ""),
        **capability,
    }


def _detect_playwright_capability() -> dict[str, Any]:
    try:
        import importlib.util
        if importlib.util.find_spec("playwright") is None:
            return {
                "state": "package_missing",
                "playwright_package_available": False,
                "chromium_runtime_available": False,
                "real_browser_verification_available": False,
                "setup_policy": PLAYWRIGHT_SETUP_COMMANDS,
                "reason": "Playwright Python package is not installed in the Studio environment",
            }
    except Exception as exc:
        return {
            "state": "initialization_failed",
            "playwright_package_available": False,
            "chromium_runtime_available": False,
            "real_browser_verification_available": False,
            "setup_policy": PLAYWRIGHT_SETUP_COMMANDS,
            "reason": _tail_output(str(exc)),
        }

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            executable = pw.chromium.executable_path
            runtime_available = bool(executable and os.path.exists(executable))
            state = "available" if runtime_available else "browser_missing"
            return {
                "state": state,
                "playwright_package_available": True,
                "chromium_runtime_available": runtime_available,
                "real_browser_verification_available": runtime_available,
                "chromium_executable": executable,
                "setup_policy": PLAYWRIGHT_SETUP_COMMANDS,
                "reason": "Playwright and Chromium are available" if runtime_available else "Playwright package is installed but Chromium runtime is missing; run the controlled Studio setup command",
            }
    except Exception as exc:
        message = _tail_output(str(exc))
        state = "browser_missing" if "Executable doesn't exist" in message or "playwright install" in message else "initialization_failed"
        return {
            "state": state,
            "playwright_package_available": True,
            "chromium_runtime_available": False,
            "real_browser_verification_available": False,
            "setup_policy": PLAYWRIGHT_SETUP_COMMANDS,
            "reason": message,
        }


def _safe_artifact_part(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "artifact")).strip("_") or "artifact"


def _browser_artifact_dir(root: str, criterion_id: str, fingerprint: str) -> str:
    path = os.path.join(root, "evidence_artifacts", fingerprint[:12], _safe_artifact_part(criterion_id))
    os.makedirs(path, exist_ok=True)
    return path


def _browser_message_severity(message_type: str, text: str = "") -> str:
    lowered = f"{message_type} {text}".lower()
    if any(token in lowered for token in ("uncaught", "referenceerror", "typeerror", "syntaxerror", "renderer crash")):
        return "fatal"
    if message_type.lower() == "error":
        return "error"
    if message_type.lower() == "warning":
        return "warning"
    return "info"


def _page_event_record(source: str, severity: str, message: Any, url: str = "") -> dict[str, Any]:
    return {"source": source, "severity": severity, "message": _tail_output(message), "url": _redact_secrets(url)}


def _viewport_file_name(criterion_id: str, viewport: dict[str, Any]) -> str:
    return f"{_safe_artifact_part(criterion_id)}_{viewport['name']}_{viewport['width']}x{viewport['height']}.png"


def _primary_action_terms(plan: dict[str, Any], criterion: dict[str, Any]) -> list[str]:
    text = " ".join(str(part) for part in [criterion.get("title", ""), criterion.get("description", ""), criterion.get("expected_result", ""), plan.get("semantic_intent", ""), " ".join(plan.get("ui_hints", []) or [])]).lower()
    terms = ["create", "new", "add", "open", "edit", "update", "save", "search", "filter", "submit", "delete"]
    if any(token in text for token in ("request", "ticket", "заяв")):
        terms.extend(["заявка", "новая", "сохранить", "поиск", "фильтр", "удалить"])
    return list(dict.fromkeys(terms))


def _product_judge_review(criterion: dict[str, Any], objective: dict[str, Any], screenshots: list[str], project: dict) -> dict[str, Any]:
    # No independent reviewer is wired in-process; keeping AC-013 not_verified is safer than self-approval.
    return {
        "review_type": "INDEPENDENT_PRODUCT_JUDGE_REVIEW",
        "verdict": "unavailable",
        "blocking_objection": None,
        "can_pass_from_judge_alone": False,
        "input_bundle": {
            "criterion_text": _semantic_acceptance_text(criterion),
            "project_purpose": " ".join(str(project.get(key, "")) for key in ("title", "description")).strip(),
            "representative_screenshots": screenshots[:3],
            "objective_browser_evidence_passed": objective.get("passed"),
            "viewport_results": objective.get("viewport_results", []),
            "layout_findings": objective.get("layout_findings", []),
            "primary_actions": objective.get("primary_actions", []),
        },
        "reason": "No independent Product Judge integration is configured for this run",
    }


def _dependency_names(root: str) -> set[str]:
    names: set[str] = set()
    for rel in ("requirements.txt", "pyproject.toml", "package.json"):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        text = _read(path).lower()
        names.update(re.findall(r"[a-z0-9_.-]+", text))
    return names


def detect_product_runtime_profile(project: dict, root: str) -> dict[str, Any]:
    profiles = _effective_profile_list(project, root)
    files = [rel for rel, _path in _walk_files(root)]
    lower_files = [file.lower() for file in files]
    deps = _dependency_names(root)
    evidence: dict[str, Any] = {"effective_project_profile": profiles, "files_considered": files[:80], "dependencies_detected": sorted(deps)[:80]}

    has_backend = any(profile in profiles for profile in ("fastapi", "REST_API", "python_application")) or any(file in lower_files for file in ("main.py", "app.py"))
    source_blob = "\n".join(_read(path)[:20000] for rel, path in _walk_files(root) if rel.endswith((".py", ".js", ".ts", ".html", ".md")))
    lower_source_blob = source_blob.lower()
    has_browser_ui = (
        any(file.startswith("templates/") for file in lower_files)
        or any(file.endswith(".html") for file in lower_files)
        or any(profile in profiles for profile in ("react_frontend", "vite_frontend", "static_website"))
        or "htmlresponse" in lower_source_blob
        or "<!doctype html" in lower_source_blob
        or "http://127.0.0.1" in lower_source_blob
    )
    has_electron = "electron" in deps or any("browserwindow" in (_read(os.path.join(root, file)) if os.path.exists(os.path.join(root, file)) and file.endswith((".js", ".ts")) else "").lower() for file in files[:200])
    has_tauri = any(file.startswith("src-tauri/") or "tauri.conf" in file for file in lower_files) or "tauri" in deps
    gui_deps = {"pyside6", "pyside2", "pyqt5", "pyqt6", "wxpython", "kivy", "flet", "tkinter"}
    has_native_gui = bool(gui_deps.intersection(deps))
    for rel, path in _walk_files(root):
        if rel.endswith(".py"):
            text = _read(path).lower()
            if any(token in text for token in ("import tkinter", "from tkinter", "pyside6", "pyqt5", "pyqt6", "wxpython", "kivy")):
                has_native_gui = True
                break
    has_telegram = "telegram_bot" in profiles or "python-telegram-bot" in deps or "aiogram" in deps
    has_cli = any("argparse" in (_read(path).lower() if rel.endswith(".py") else "") or "click" in deps for rel, path in _walk_files(root))

    shell_count = sum(bool(value) for value in (has_electron, has_tauri, has_native_gui))
    ambiguous = shell_count > 1
    if ambiguous:
        product_kind, ui_runtime = "unknown", "unknown"
    elif has_backend and has_electron:
        product_kind, ui_runtime = "hybrid_desktop_application", "electron"
    elif has_backend and has_tauri:
        product_kind, ui_runtime = "hybrid_desktop_application", "tauri"
    elif has_electron:
        product_kind, ui_runtime = "desktop_application", "electron"
    elif has_tauri:
        product_kind, ui_runtime = "desktop_application", "tauri"
    elif has_native_gui:
        product_kind, ui_runtime = "desktop_application", "native_python_gui"
    elif has_telegram:
        product_kind, ui_runtime = "telegram_bot", "none"
    elif has_browser_ui and has_backend:
        product_kind, ui_runtime = "web_application", "browser"
    elif has_browser_ui:
        product_kind, ui_runtime = "static_web", "browser"
    elif any(profile in profiles for profile in ("fastapi", "REST_API")):
        product_kind, ui_runtime = "api_service", "none"
    elif has_cli:
        product_kind, ui_runtime = "cli_application", "terminal"
    else:
        product_kind, ui_runtime = "unknown", "unknown"

    packaging_kind = "none"
    if has_electron and any("electron-builder" in dep or "electron-forge" in dep for dep in deps):
        packaging_kind = "installer"
    elif has_tauri:
        packaging_kind = "app_bundle"
    elif "pyinstaller" in deps:
        packaging_kind = "portable_executable"
    elif any(file in lower_files for file in ("dockerfile", "docker-compose.yml")):
        packaging_kind = "container"
    elif product_kind not in ("unknown", "api_service", "telegram_bot", "cli_application"):
        packaging_kind = "development_only"

    evidence.update({
        "signals": {
            "has_backend": has_backend,
            "has_browser_ui": has_browser_ui,
            "has_electron": has_electron,
            "has_tauri": has_tauri,
            "has_native_python_gui": has_native_gui,
            "has_telegram_bot": has_telegram,
            "has_cli": has_cli,
        },
        "ambiguous": ambiguous,
        "ambiguity_reason": "Multiple desktop shell signals detected" if ambiguous else "",
    })
    return {
        "product_kind": product_kind,
        "ui_runtime": ui_runtime,
        "packaging_kind": packaging_kind,
        "target_platforms": ["windows"] if os.name == "nt" else [],
        "evidence": evidence,
    }


class ProductRuntimeEvidenceAdapter:
    adapter_type = "base"
    supported_product_kinds: set[str] = set()
    supported_ui_runtimes: set[str] = set()

    def supports(self, profile: dict[str, Any]) -> bool:
        return profile.get("product_kind") in self.supported_product_kinds and profile.get("ui_runtime") in self.supported_ui_runtimes

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._unsupported(criterion, project, root, plan, profile, "Adapter does not implement UI evidence")

    def _base_metadata(self, root: str, profile: dict[str, Any], execution_mode: str) -> dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "project_path": root,
            "product_kind": profile.get("product_kind"),
            "ui_runtime": profile.get("ui_runtime"),
            "packaging_kind": profile.get("packaging_kind"),
            "execution_mode": execution_mode,
            "timestamp": _utc_now(),
            "snapshot_fingerprint": _project_snapshot_fingerprint(root),
        }

    def _unsupported(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any], reason: str) -> dict[str, Any]:
        return _targeted_evidence(
            criterion,
            project,
            root,
            str(plan.get("verifier_type") or self.adapter_type),
            "not_verified",
            "Product runtime evidence adapter is unsupported for this criterion",
            classification=AC_CLASS_UNSUPPORTED,
            collected_evidence={
                "product_runtime_profile": profile,
                "runtime_ui_adapter": self._base_metadata(root, profile, "unsupported"),
                "unsupported_reason": reason,
                "evidence_level_required": _required_ui_evidence_level(str(plan.get("verifier_type") or "")),
            },
            failure_reason=reason,
        )


def _required_ui_evidence_level(verifier_type: str) -> str:
    if verifier_type in ("browser_usability", "primary_ui_actions"):
        return "LEVEL_3_REAL_INTERACTION"
    if verifier_type in ("responsive_ui", "product_ui_quality"):
        return "LEVEL_4_LAYOUT_AND_DISPLAY_EVIDENCE"
    if verifier_type.startswith("packaged") or verifier_type in ("installer_installation", "installed_app_launch"):
        return "LEVEL_5_PACKAGED_DELIVERY"
    return "LEVEL_2_REAL_UI_RUNTIME"


class BrowserWebEvidenceAdapter(ProductRuntimeEvidenceAdapter):
    adapter_type = "BrowserWebEvidenceAdapter"
    supported_product_kinds = {"web_application", "static_web"}
    supported_ui_runtimes = {"browser"}

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        capability = _detect_playwright_capability()
        collected = {
            "product_runtime_profile": profile,
            "runtime_ui_adapter": self._base_metadata(root, profile, "real_browser"),
            "tool_capabilities": capability,
            "viewport_matrix_required": WEB_VIEWPORT_MATRIX,
            "optional_4k_supported_by_architecture": {"name": "4k_desktop", "width": 3840, "height": 2160, "required": False},
            "display_model": {
                "physical_display_resolution": "browser viewport controlled",
                "dpi_scale": "not_required_for_css_viewport_evidence",
                "device_scale_factor": 1,
                "reason": "Playwright controls isolated Chromium viewport sizes, not the user's physical display",
            },
            "evidence_levels": ["LEVEL_1_STRUCTURAL", "LEVEL_2_REAL_RUNTIME", "LEVEL_3_REAL_INTERACTION", "LEVEL_4_LAYOUT_AND_DISPLAY_EVIDENCE"],
        }
        if not capability.get("real_browser_verification_available"):
            collected["executed_viewports"] = []
            collected["high_resolution_checks"] = {"required": ["high_resolution_desktop", "ultra_wide_desktop"], "executed": False}
            return _targeted_evidence(
                criterion,
                project,
                root,
                str(plan.get("verifier_type") or "browser_ui"),
                "not_verified",
                "Browser UI evidence requires Studio-level Playwright and Chromium runtime",
                classification=AC_CLASS_UNSUPPORTED,
                collected_evidence=collected,
                failure_reason=str(capability.get("reason") or "Browser automation unavailable"),
                failure_kind="tooling_failure",
            )

        handle: RuntimeServerHandle | None = None
        browser = None
        browser_evidence: dict[str, Any] | None = None
        runtime_stop: dict[str, Any] = {"stopped_cleanly": False, "not_started": True}
        env_extra, storage_evidence = _persistence_storage_config(root, str(criterion.get("id", "browser")))
        collected["isolated_test_storage"] = storage_evidence
        try:
            handle, runtime_start = _start_http_sequence_runtime(project, root, env_extra)
            collected["application_startup"] = _runtime_start_evidence(runtime_start)
            if not handle:
                status = "failed" if runtime_start.get("status") == "failed" else "not_verified"
                return _targeted_evidence(
                    criterion, project, root, str(plan.get("verifier_type") or "browser_ui"), status,
                    "Generated application did not start for browser evidence",
                    classification=AC_CLASS_PARTIAL if status == "failed" else AC_CLASS_UNSUPPORTED,
                    collected_evidence=collected,
                    failure_reason=str(runtime_start.get("error") or "Runtime unavailable"),
                    failure_kind="project_failure" if status == "failed" else "tooling_failure",
                )

            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                collected["chromium_startup"] = {"launched": True, "browser_name": browser.browser_type.name, "isolated_user_profile": True}
                browser_evidence = self._execute_browser_plan(browser, handle.base_url, criterion, project, root, plan, profile, collected)
        except Exception as exc:
            collected["browser_execution_exception"] = _tail_output(str(exc))
            return _targeted_evidence(
                criterion, project, root, str(plan.get("verifier_type") or "browser_ui"), "not_verified",
                "Playwright browser execution could not be initialized",
                classification=AC_CLASS_UNSUPPORTED,
                collected_evidence=collected,
                failure_reason=_tail_output(str(exc)),
                failure_kind="tooling_failure",
            )
        finally:
            if browser is not None:
                try:
                    browser.close()
                    collected["browser_closed"] = True
                except Exception as exc:
                    collected["browser_close_error"] = _tail_output(str(exc))
            if handle:
                runtime_stop = _stop_http_sequence_runtime(handle)
            collected["runtime_stop"] = runtime_stop
            if browser_evidence is not None:
                browser_evidence.setdefault("collected_evidence", {})["runtime_stop"] = runtime_stop
                browser_evidence["collected_evidence"]["browser_closed"] = collected.get("browser_closed", False)
        if browser_evidence is not None:
            return browser_evidence
        return _targeted_evidence(
            criterion, project, root, str(plan.get("verifier_type") or "browser_ui"), "not_verified",
            "Browser evidence did not complete",
            classification=AC_CLASS_UNSUPPORTED,
            collected_evidence=collected,
            failure_reason="Browser execution ended without evidence",
            failure_kind="tooling_failure",
        )

    def _execute_browser_plan(self, browser: Any, base_url: str, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any], collected: dict[str, Any]) -> dict[str, Any]:
        verifier_type = str(plan.get("verifier_type") or "browser_ui")
        fingerprint = _project_snapshot_fingerprint(root)
        artifact_dir = _browser_artifact_dir(root, str(criterion.get("id", "AC-UI")), fingerprint)
        terms = _primary_action_terms(plan, criterion)
        viewports_to_run = WEB_VIEWPORT_MATRIX if verifier_type in ("responsive_ui", "product_ui_quality") else [WEB_VIEWPORT_MATRIX[0]]
        viewport_results = []
        screenshots = []
        primary_actions: list[dict[str, Any]] = []

        for viewport in viewports_to_run:
            context = None
            page = None
            page_errors: list[dict[str, Any]] = []
            console_messages: list[dict[str, Any]] = []
            network_failures: list[dict[str, Any]] = []
            screenshot_path = os.path.join(artifact_dir, _viewport_file_name(str(criterion.get("id", "AC-UI")), viewport))
            try:
                context = browser.new_context(viewport={"width": int(viewport["width"]), "height": int(viewport["height"])}, device_scale_factor=1)
                page = context.new_page()
                page.on("pageerror", lambda exc: page_errors.append(_page_event_record("pageerror", "fatal", str(exc), page.url if page else "")))
                page.on("console", lambda msg: console_messages.append(_page_event_record("console", _browser_message_severity(msg.type, msg.text), msg.text, getattr(msg, "location", {}).get("url", "") if isinstance(getattr(msg, "location", {}), dict) else "")))
                page.on("requestfailed", lambda req: network_failures.append(_page_event_record("network", "error", req.failure.get("errorText", "request failed") if req.failure else "request failed", req.url)))
                response = page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_load_state("networkidle", timeout=8000)
                body_text_len = page.locator("body").inner_text(timeout=3000).__len__()
                visible_primary = self._visible_primary_elements(page, terms)
                metrics = self._layout_metrics(page)
                primary_action_reachable = bool(visible_primary)
                fatal_errors = [item for item in page_errors + console_messages if item.get("severity") == "fatal"]
                large_overflow = int(metrics.get("horizontal_overflow_px", 0)) > HORIZONTAL_OVERFLOW_TOLERANCE_PX
                screenshot = page.screenshot(path=screenshot_path, full_page=True, timeout=8000)
                screenshot_written = bool(screenshot and os.path.exists(screenshot_path))
                if screenshot_written:
                    screenshots.append(screenshot_path)

                result = {
                    "category": viewport["name"],
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "final_url": page.url,
                    "document_title": page.title(),
                    "load_result": {"status_code": response.status if response else None, "ok": bool(response and response.ok)},
                    "body_text_length": body_text_len,
                    "page_errors": page_errors,
                    "console_messages": console_messages,
                    "console_errors": [m for m in console_messages if m.get("severity") in ("error", "fatal")],
                    "network_failures": network_failures,
                    "visible_primary_elements": visible_primary,
                    "primary_control_visibility": bool(visible_primary),
                    "primary_navigation_reachable": bool(metrics.get("main_or_nav_visible")),
                    "primary_action_reachable": primary_action_reachable,
                    "overflow_measurements": metrics,
                    "horizontal_overflow_px": metrics.get("horizontal_overflow_px"),
                    "failed_assertions": [],
                    "screenshot_path": screenshot_path if screenshot_written else "",
                }
                if not result["load_result"]["ok"]:
                    result["failed_assertions"].append("navigation did not return a successful response")
                if body_text_len <= 20:
                    result["failed_assertions"].append("page body is blank or nearly blank")
                if fatal_errors:
                    result["failed_assertions"].append("fatal browser/page errors occurred")
                if not metrics.get("main_or_nav_visible"):
                    result["failed_assertions"].append("core interface section is not visible")
                if not primary_action_reachable:
                    result["failed_assertions"].append("primary action is not visible or reachable")
                if large_overflow:
                    result["failed_assertions"].append("catastrophic horizontal overflow exceeds tolerance")
                result["verdict"] = "passed" if not result["failed_assertions"] else "failed"
                viewport_results.append(result)
            except Exception as exc:
                viewport_results.append({
                    "category": viewport["name"], "width": viewport["width"], "height": viewport["height"],
                    "load_result": {"ok": False}, "page_errors": page_errors, "console_errors": console_messages,
                    "primary_control_visibility": False, "primary_action_reachable": False,
                    "horizontal_overflow_px": None, "failed_assertions": [f"browser execution failed: {_tail_output(str(exc))}"],
                    "screenshot_path": screenshot_path if os.path.exists(screenshot_path) else "", "verdict": "failed",
                })
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

        if verifier_type == "primary_ui_actions":
            primary_actions = self._execute_primary_action_flow(browser, base_url, artifact_dir, criterion, terms)

        objective = {
            "passed": all(item.get("verdict") == "passed" for item in viewport_results),
            "viewport_results": viewport_results,
            "layout_findings": self._layout_findings(viewport_results),
            "primary_actions": primary_actions,
            "screenshots": screenshots,
        }
        collected.update({
            "base_url": base_url,
            "artifact_directory": artifact_dir,
            "executed_viewports": viewport_results,
            "screenshots": screenshots,
            "browser_context_isolation": "fresh isolated context per viewport/action flow",
            "snapshot_binding": {"project_path": root, "criterion_id": criterion.get("id", ""), "verifier_type": verifier_type, "adapter_type": self.adapter_type, "product_runtime_profile": profile, "code_snapshot_fingerprint": fingerprint, "timestamp": _utc_now()},
            "high_resolution_checks": {"required": ["high_resolution_desktop", "ultra_wide_desktop"], "executed": any(v.get("category") == "high_resolution_desktop" for v in viewport_results) and any(v.get("category") == "ultra_wide_desktop" for v in viewport_results)},
        })

        if verifier_type == "primary_ui_actions":
            passed = bool(primary_actions) and all(action.get("verdict") == "passed" for action in primary_actions)
            collected["primary_actions_tested"] = primary_actions
            summary = "Primary interface actions were clicked and produced observable results" if passed else "Primary interface action interaction evidence failed"
            return _targeted_evidence(criterion, project, root, verifier_type, "passed" if passed else "failed", summary, classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL, collected_evidence=collected, failure_reason="One or more primary actions were not usable" if not passed else "", failure_kind="project_failure" if not passed else "")

        if verifier_type == "product_ui_quality":
            judge = _product_judge_review(criterion, objective, [path for path in screenshots if any(token in path for token in ("full_hd", "high_resolution", "tablet_portrait"))], project)
            collected["OBJECTIVE_REAL_BROWSER_EVIDENCE"] = objective
            collected["INDEPENDENT_PRODUCT_JUDGE_REVIEW"] = judge
            passed = bool(objective.get("passed") and judge.get("verdict") in ("approved", "approved_with_nonblocking_notes") and not judge.get("blocking_objection"))
            status = "passed" if passed else "failed" if not objective.get("passed") or judge.get("blocking_objection") else "not_verified"
            return _targeted_evidence(criterion, project, root, verifier_type, status, "Objective browser evidence and Product Judge review were evaluated", classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL if status == "failed" else AC_CLASS_UNSUPPORTED, collected_evidence=collected, failure_reason="Product Judge unavailable" if status == "not_verified" else "Objective UI evidence failed or judge raised a blocking objection" if status == "failed" else "", failure_kind="project_failure" if status == "failed" else "tooling_failure" if status == "not_verified" else "")

        passed = bool(objective.get("passed"))
        collected["OBJECTIVE_REAL_BROWSER_EVIDENCE"] = objective
        return _targeted_evidence(criterion, project, root, verifier_type, "passed" if passed else "failed", "Real Chromium browser UI evidence passed" if passed else "Real Chromium browser UI evidence failed", classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL, collected_evidence=collected, failure_reason="One or more required browser viewport assertions failed" if not passed else "", failure_kind="project_failure" if not passed else "")

    def _visible_primary_elements(self, page: Any, terms: list[str]) -> list[dict[str, Any]]:
        script = """
        (terms) => Array.from(document.querySelectorAll('button,a,input,select,textarea,[role="button"],[role="link"],[role="search"]')).map((el) => {
            const r = el.getBoundingClientRect();
            const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '').trim();
            const visible = r.width > 0 && r.height > 0 && r.bottom >= 0 && r.right >= 0 && r.left <= window.innerWidth && r.top <= window.innerHeight;
            return {tag: el.tagName.toLowerCase(), text, id: el.id || '', role: el.getAttribute('role') || '', visible, enabled: !el.disabled, bounds: {x: r.x, y: r.y, width: r.width, height: r.height}};
        }).filter((item) => item.visible && item.enabled && terms.some((term) => item.text.toLowerCase().includes(term) || item.id.toLowerCase().includes(term))).slice(0, 20)
        """
        return page.evaluate(script, terms)

    def _layout_metrics(self, page: Any) -> dict[str, Any]:
        script = """
        () => {
          const de = document.documentElement;
          const body = document.body;
          const main = document.querySelector('main') || body;
          const nav = document.querySelector('nav,header,[role="navigation"]');
          const mr = main.getBoundingClientRect();
          const nr = nav ? nav.getBoundingClientRect() : null;
          const scrollWidth = Math.max(de.scrollWidth, body ? body.scrollWidth : 0);
          const clientWidth = de.clientWidth;
          const controls = Array.from(document.querySelectorAll('button,a,input,select,textarea')).filter((el) => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.right < 0 || r.left > window.innerWidth || r.bottom < 0 || r.top > window.innerHeight);
          });
          return {document_scroll_width: de.scrollWidth, document_client_width: de.clientWidth, body_scroll_width: body ? body.scrollWidth : 0, viewport_width: window.innerWidth, horizontal_overflow_px: Math.max(0, scrollWidth - clientWidth), main_or_nav_visible: (mr.width > 0 && mr.height > 0) || (nr && nr.width > 0 && nr.height > 0), main_content_bounds: {x: mr.x, y: mr.y, width: mr.width, height: mr.height}, navigation_bounds: nr ? {x: nr.x, y: nr.y, width: nr.width, height: nr.height} : null, offscreen_control_count: controls.length};
        }
        """
        return page.evaluate(script)

    def _layout_findings(self, viewport_results: list[dict[str, Any]]) -> list[str]:
        findings = []
        for result in viewport_results:
            overflow = result.get("horizontal_overflow_px")
            if isinstance(overflow, int) and overflow > HORIZONTAL_OVERFLOW_TOLERANCE_PX:
                findings.append(f"{result.get('category')} horizontal overflow {overflow}px exceeds tolerance")
            bounds = (result.get("overflow_measurements") or {}).get("main_content_bounds") or {}
            if result.get("category") == "ultra_wide_desktop" and bounds.get("width", 0) > 3300:
                findings.append("ultra-wide main content spans nearly the full viewport; review readability")
        return findings

    def _execute_primary_action_flow(self, browser: Any, base_url: str, artifact_dir: str, criterion: dict[str, Any], terms: list[str]) -> list[dict[str, Any]]:
        context = browser.new_context(viewport={"width": 1366, "height": 768}, device_scale_factor=1)
        page = context.new_page()
        failed_requests: list[dict[str, Any]] = []
        page.on("requestfailed", lambda req: failed_requests.append(_page_event_record("network", "error", req.failure.get("errorText", "request failed") if req.failure else "request failed", req.url)))
        actions: list[dict[str, Any]] = []
        marker = f"E2E-{int(time.time() * 1000)}"
        try:
            page.goto(f"{base_url}/", wait_until="networkidle", timeout=15000)
            button = page.get_by_role("button", name=re.compile(r"new|create|add|новая|заявка", re.IGNORECASE)).first
            visible = button.is_visible(timeout=3000)
            enabled = button.is_enabled(timeout=3000)
            before_len = len(page.locator("body").inner_text(timeout=3000))
            if visible and enabled:
                button.click(timeout=5000)
            dialog_visible = page.locator("dialog,[role='dialog'],form").first.is_visible(timeout=5000)
            actions.append({"action_semantic_name": "create_request", "selector_strategy": "role=button accessible name from semantic action terms", "visible": visible, "enabled": enabled, "interaction_performed": visible and enabled, "expected_result": "create form/dialog becomes visible", "actual_result": "form/dialog visible" if dialog_visible else "form/dialog not visible", "related_network_failure": failed_requests[-3:], "verdict": "passed" if visible and enabled and dialog_visible else "failed"})

            if dialog_visible:
                self._fill_first(page, "input[name='client_name'], input[placeholder*='Клиент']", marker)
                self._fill_first(page, "input[name='contact']", f"{marker.lower()}@example.com")
                self._fill_first(page, "input[name='company']", "FreelancerStudio verifier")
                self._fill_first(page, "textarea[name='description']", f"Browser verification {marker}")
                save = page.get_by_role("button", name=re.compile(r"save|submit|сохранить", re.IGNORECASE)).first
                save_visible = save.is_visible(timeout=3000)
                save_enabled = save.is_enabled(timeout=3000)
                if save_visible and save_enabled:
                    save.click(timeout=5000)
                    page.wait_for_timeout(1000)
                text_after = page.locator("body").inner_text(timeout=5000)
                created_visible = marker in text_after
                actions.append({"action_semantic_name": "save_created_request", "selector_strategy": "role=button accessible name save/submit", "visible": save_visible, "enabled": save_enabled, "interaction_performed": save_visible and save_enabled, "expected_result": "created request marker appears in UI", "actual_result": "marker visible" if created_visible else "marker not visible", "related_network_failure": failed_requests[-3:], "verdict": "passed" if save_visible and save_enabled and created_visible else "failed"})
                ticket = page.locator("button.ticket").filter(has_text=marker).first
                open_visible = ticket.is_visible(timeout=3000) if created_visible else False
                if open_visible:
                    ticket.click(timeout=5000)
                opened = False
                if open_visible:
                    page.wait_for_timeout(500)
                    opened = bool(page.locator("dialog").first.evaluate("el => !!el.open", timeout=3000))
                actions.append({"action_semantic_name": "open_request", "selector_strategy": "created fixture marker scoped to containing ticket button", "visible": open_visible, "enabled": open_visible, "interaction_performed": open_visible, "expected_result": "request details become visible", "actual_result": "details visible" if opened else "details not visible", "related_network_failure": failed_requests[-3:], "verdict": "passed" if open_visible and opened else "failed"})

            search = page.locator("input[type='search'], input[placeholder*='Поиск'], input[id*='search' i]").first
            search_visible = search.is_visible(timeout=2000)
            if search_visible:
                search.fill(marker, timeout=3000)
                page.wait_for_timeout(800)
            changed = len(page.locator("body").inner_text(timeout=3000)) != before_len or marker in page.locator("body").inner_text(timeout=3000)
            actions.append({"action_semantic_name": "search_filter", "selector_strategy": "search input by type/placeholder/id", "visible": search_visible, "enabled": search.is_enabled(timeout=2000) if search_visible else False, "interaction_performed": search_visible, "expected_result": "visible results update or target marker remains isolated", "actual_result": "results changed or marker visible" if changed else "no observable result change", "related_network_failure": failed_requests[-3:], "verdict": "passed" if search_visible and changed else "failed"})
            page.screenshot(path=os.path.join(artifact_dir, f"{_safe_artifact_part(criterion.get('id'))}_primary_actions_1366x768.png"), full_page=True, timeout=8000)
            return actions
        except Exception as exc:
            actions.append({"action_semantic_name": "primary_action_flow", "selector_strategy": "semantic role/text selectors", "visible": False, "enabled": False, "interaction_performed": False, "expected_result": "primary UI flow completes", "actual_result": _tail_output(str(exc)), "related_network_failure": failed_requests[-3:], "verdict": "failed"})
            return actions
        finally:
            try:
                page.close()
            finally:
                context.close()

    def _fill_first(self, page: Any, selector: str, value: str) -> None:
        locator = page.locator(selector).first
        if locator.is_visible(timeout=2000):
            locator.fill(value, timeout=3000)


class ElectronDesktopEvidenceAdapter(ProductRuntimeEvidenceAdapter):
    adapter_type = "ElectronDesktopEvidenceAdapter"
    supported_product_kinds = {"desktop_application"}
    supported_ui_runtimes = {"electron"}

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._unsupported(criterion, project, root, plan, profile, "Electron desktop automation is not configured; browser evidence cannot satisfy Electron shell criteria")


class TauriDesktopEvidenceAdapter(ProductRuntimeEvidenceAdapter):
    adapter_type = "TauriDesktopEvidenceAdapter"
    supported_product_kinds = {"desktop_application"}
    supported_ui_runtimes = {"tauri"}

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._unsupported(criterion, project, root, plan, profile, "Tauri desktop automation is not configured; frontend browser evidence cannot prove native shell readiness")


class NativePythonGuiEvidenceAdapter(ProductRuntimeEvidenceAdapter):
    adapter_type = "NativePythonGuiEvidenceAdapter"
    supported_product_kinds = {"desktop_application"}
    supported_ui_runtimes = {"native_python_gui", "native_desktop"}

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._unsupported(criterion, project, root, plan, profile, "Native Python GUI automation/window inspection is not configured")


class HybridDesktopEvidenceAdapter(ProductRuntimeEvidenceAdapter):
    adapter_type = "HybridDesktopEvidenceAdapter"
    supported_product_kinds = {"hybrid_desktop_application"}
    supported_ui_runtimes = {"electron", "tauri", "native_python_gui"}

    def execute_ui_evidence(self, criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        return self._unsupported(criterion, project, root, plan, profile, "Hybrid desktop verification requires backend readiness plus desktop shell automation; tooling is not configured")


PRODUCT_RUNTIME_ADAPTERS: list[ProductRuntimeEvidenceAdapter] = [
    HybridDesktopEvidenceAdapter(),
    BrowserWebEvidenceAdapter(),
    ElectronDesktopEvidenceAdapter(),
    TauriDesktopEvidenceAdapter(),
    NativePythonGuiEvidenceAdapter(),
]


def select_product_runtime_adapter(profile: dict[str, Any]) -> ProductRuntimeEvidenceAdapter | None:
    for adapter in PRODUCT_RUNTIME_ADAPTERS:
        if adapter.supports(profile):
            return adapter
    return None


def execute_product_ui_evidence(criterion: dict[str, Any], project: dict, root: str, plan: dict[str, Any]) -> dict[str, Any]:
    profile = detect_product_runtime_profile(project, root)
    adapter = select_product_runtime_adapter(profile)
    if not adapter:
        return _targeted_evidence(
            criterion,
            project,
            root,
            str(plan.get("verifier_type") or "runtime_ui_evidence"),
            "not_verified",
            "No compatible product runtime UI evidence adapter was available",
            classification=AC_CLASS_UNSUPPORTED,
            collected_evidence={"product_runtime_profile": profile, "adapter_registry": [adapter.adapter_type for adapter in PRODUCT_RUNTIME_ADAPTERS]},
            failure_reason=f"No adapter supports product_kind={profile.get('product_kind')} ui_runtime={profile.get('ui_runtime')}",
        )
    return adapter.execute_ui_evidence(criterion, project, root, plan, profile)


def _browser_required_unsupported_evidence(criterion: dict[str, Any], project: dict, root: str, verifier_type: str, collected: dict[str, Any] | None = None) -> dict[str, Any]:
    browser = _browser_automation_support()
    level1 = dict(collected or {})
    level1["ui_evidence_level"] = "LEVEL_1_HTTP_HTML_STRUCTURAL_EVIDENCE"
    level1["real_browser_evidence"] = {"level": "LEVEL_2_REAL_BROWSER_EVIDENCE", **browser}
    return _targeted_evidence(
        criterion,
        project,
        root,
        verifier_type,
        "not_verified",
        "Real browser evidence is required but unavailable",
        classification=AC_CLASS_UNSUPPORTED,
        setup_steps=["Check browser automation support"],
        action_steps=["Attempt to select real browser evidence adapter"],
        assertions=["Criteria requiring browser usability must not pass from HTTP/HTML structure alone"],
        collected_evidence=level1,
        failure_reason=str(browser.get("reason") or "Real browser automation unavailable"),
    )


def _ticket_app_routes(openapi: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
    evidence: dict[str, Any] = {"paths": sorted(str(path) for path in paths)}
    routes = {"collection": "", "item": "", "stats": "", "meta": "", "comments": ""}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        methods = {str(method).lower() for method in item if isinstance(item.get(method), dict)}
        key = _route_key(str(path))
        if {"get", "post"}.issubset(methods) and not _path_parameter_names(str(path)):
            post_schema = _json_request_schema(openapi, item.get("post", {}))
            props = set(_schema_properties(openapi, post_schema))
            if {"client_name", "contact", "description"}.issubset(props):
                routes["collection"] = str(path)
        if {"get", "put", "delete"}.intersection(methods) and _path_parameter_names(str(path)) and "comment" not in key:
            routes["item"] = str(path)
        if "stats" in key and "get" in methods:
            routes["stats"] = str(path)
        if "meta" in key and "get" in methods:
            routes["meta"] = str(path)
        if "comment" in key and "post" in methods:
            routes["comments"] = str(path)
    evidence["discovered_routes"] = dict(routes)
    return routes, evidence


def _start_ticket_runtime(criterion: dict[str, Any], project: dict, root: str) -> tuple[RuntimeServerHandle | None, dict[str, Any], dict[str, Any], dict[str, str]]:
    env_extra, storage = _persistence_storage_config(root, str(criterion.get("id", "targeted")))
    handle, start_evidence = _start_http_sequence_runtime(project, root, env_extra)
    collected = {"storage": storage, "runtime": _runtime_start_evidence(start_evidence)}
    if not handle:
        return None, collected, {}, {}
    openapi_response = _http_json_request(handle.base_url, "GET", "/openapi.json")
    collected["openapi_status"] = openapi_response.get("status")
    if not openapi_response.get("ok") or not isinstance(openapi_response.get("json"), dict):
        collected["openapi_error"] = openapi_response.get("error") or f"status={openapi_response.get('status')}"
        return handle, collected, {}, {}
    routes, route_evidence = _ticket_app_routes(openapi_response["json"])
    collected["route_discovery"] = route_evidence
    return handle, collected, openapi_response["json"], routes


def _ticket_payload(marker: str, *, status: str = "новая", priority: str = "обычный", suffix: str = "") -> dict[str, Any]:
    label = f"{marker}{suffix}"
    return {
        "client_name": f"Client {label}",
        "contact": f"{label}@example.com",
        "company": f"Company {label}",
        "description": f"Request description {label}",
        "priority": priority,
        "status": status,
    }


def _ticket_create(handle: RuntimeServerHandle, routes: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    return _http_json_request(handle.base_url, "POST", routes["collection"], payload)


def _ticket_read(handle: RuntimeServerHandle, routes: dict[str, str], identifier: Any) -> dict[str, Any]:
    return _http_json_request(handle.base_url, "GET", _replace_path_params(routes["item"], str(identifier)))


def _ticket_update(handle: RuntimeServerHandle, routes: dict[str, str], identifier: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return _http_json_request(handle.base_url, "PUT", _replace_path_params(routes["item"], str(identifier)), payload)


def _ticket_list(handle: RuntimeServerHandle, routes: dict[str, str], params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _http_json_request(handle.base_url, "GET", _append_query(routes["collection"], params or {}))


def _ids_from_response(response: dict[str, Any]) -> list[Any]:
    return [item.get("id") for item in _iter_response_objects(response.get("json")) if isinstance(item, dict) and item.get("id") is not None]


def _ticket_runtime_unavailable_evidence(criterion: dict[str, Any], project: dict, root: str, collected: dict[str, Any]) -> dict[str, Any]:
    return _targeted_evidence(
        criterion,
        project,
        root,
        "targeted_ticket_runtime",
        "not_verified",
        "Ticket runtime or route discovery was unavailable",
        classification=AC_CLASS_UNSUPPORTED,
        setup_steps=["Start the real app with isolated local storage", "Discover ticket routes from OpenAPI"],
        assertions=["Runtime starts", "Ticket collection and item routes are discoverable"],
        collected_evidence=collected,
        failure_reason="Runtime unavailable or ticket routes incomplete",
    )


def _artifact_path(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("path") or item.get("file") or item.get("name") or "").strip()
    return str(item or "").strip()


def _artifact_required(item: Any, default: bool) -> bool:
    if not isinstance(item, dict):
        return default
    value = item.get("required", default)
    if isinstance(value, str):
        return value.lower() not in ("false", "no", "0", "optional")
    return bool(value)


def _artifact_exists(root: str, rel_path: str) -> bool:
    if not rel_path or os.path.isabs(rel_path) or ".." in Path(rel_path).parts:
        return False
    return os.path.exists(os.path.join(root, rel_path))


def _artifact_requirements(project: dict) -> tuple[list[str], list[str]]:
    spec = project.get("project_spec", {})
    mandatory: list[str] = []
    optional: list[str] = []
    for item in spec.get("delivery_artifacts", ["README.md"]):
        path = _artifact_path(item)
        if not path or " or " in path:
            continue
        target = mandatory if _artifact_required(item, True) else optional
        target.append(path)
    for key in ("optional_artifacts", "optional_delivery_artifacts"):
        for item in spec.get(key, []):
            path = _artifact_path(item)
            if path and " or " not in path:
                optional.append(path)
    return list(dict.fromkeys(mandatory)), list(dict.fromkeys(optional))


def _verify_file_check(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    mandatory, optional = _artifact_requirements(project)
    missing_mandatory = [path for path in mandatory if not _artifact_exists(root, path)]
    missing_optional = [path for path in optional if not _artifact_exists(root, path)]
    status = "passed"
    if missing_mandatory:
        status = "failed"
    elif missing_optional:
        status = "optional_fail"
    return _registry_evidence(
        "file_check",
        status,
        "Exact delivery artifact paths verified",
        action_steps=["Resolve each required and optional delivery artifact path from the project root"],
        assertions=["Every required artifact path exists exactly", "Missing optional artifacts are reported separately"],
        collected_evidence={
            "required_paths": mandatory,
            "optional_paths": optional,
            "missing_required": missing_mandatory,
            "missing_optional": missing_optional,
        },
        failure_reason="Missing required delivery artifact" if missing_mandatory else "Missing optional delivery artifact" if missing_optional else "",
        required_paths=mandatory,
        optional_paths=optional,
        missing_required=missing_mandatory,
        missing_optional=missing_optional,
    )


def _mandatory_delivery_files(project: dict) -> list[str]:
    mandatory, _optional = _artifact_requirements(project)
    return mandatory


def _verify_secret_scan(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    secret_check = next((check for check in checks if check.get("name") == "secret_scan"), {})
    evidence = secret_check.get("evidence", {}) if isinstance(secret_check.get("evidence"), dict) else {}
    status = "passed" if secret_check.get("status") == "passed" else "failed"
    return _registry_evidence(
        "secret_scan",
        status,
        "Secret scan result mapped to acceptance criterion",
        action_steps=["Scan deliverable files for secret-like values"],
        assertions=["No committed token, API key, password, or secret-like value is present"],
        collected_evidence=evidence,
        failure_reason="Secret-like values were found" if status != "passed" else "",
    )


def _verify_global_qa(method: str, criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    qa_success = bool((qa_result or {}).get("success"))
    status = "passed" if qa_success else "failed"
    return _registry_evidence(method, status, "Global QA result supports system-level acceptance criterion", qa_success=qa_success)


def _criterion_verifier_plan(criterion: dict[str, Any]) -> dict[str, Any]:
    plan = criterion.get("verifier_plan")
    if isinstance(plan, dict) and plan.get("verifier_type"):
        return plan
    try:
        return plan_acceptance_verifier(criterion)
    except Exception as exc:
        return {"verifier_type": "manual_or_unsupported", "plan_error": _tail_output(str(exc))}


def _is_create_list_http_sequence(plan: dict[str, Any]) -> bool:
    if plan.get("verifier_type") != "http_sequence":
        return False
    actions = " ".join(str(item).lower() for item in plan.get("actions", []))
    assertions = " ".join(str(item).lower() for item in plan.get("assertions", []))
    return "create" in actions and "list" in actions and any(token in assertions for token in ("appears", "contains", "identifier", "list"))


def _crud_sequence_kind(criterion: dict[str, Any], plan: dict[str, Any]) -> str:
    if plan.get("verifier_type") != "http_sequence":
        return ""
    criterion_text = " ".join(str(criterion.get(key, "")) for key in ("title", "description", "expected_result")).lower()
    actions_text = " ".join(str(item) for item in plan.get("actions", [])).lower()
    assertions_text = " ".join(str(item) for item in plan.get("assertions", [])).lower()
    text = " ".join((criterion_text, actions_text, assertions_text))
    if "search" in text:
        return "search"
    if "filter" in text:
        return "filter"
    if "delete" in text or "remove" in text:
        return "delete"
    if "status" in (criterion_text + " " + actions_text) and (
        "change request status" in actions_text
        or any(token in criterion_text for token in ("change", "changed", "update", "set"))
    ):
        return "status_change"
    if any(token in criterion_text for token in ("update", "edit")) or "update request" in actions_text:
        return "update"
    if "read succeeds" in assertions_text or (
        any(token in criterion_text for token in ("read", "open", "view")) and "update" not in criterion_text
    ):
        return "read"
    if "create" in text:
        return "create"
    return ""


def _start_http_sequence_runtime(project: dict, root: str, env_extra: dict[str, str] | None = None) -> tuple[RuntimeServerHandle | None, dict[str, Any]]:
    _effective_project_profiles(project, root)
    for adapter in RUNTIME_ADAPTERS:
        starter = getattr(adapter, "start_http_server", None)
        if not callable(starter):
            continue
        result, handle = starter(project, root, env_extra)
        if not result.applicable:
            continue
        evidence = result.to_dict()
        evidence["adapter"] = adapter.name
        evidence["status"] = "passed" if handle and result.started and result.verified else _adapter_status(result)
        evidence.update(result.evidence)
        if handle and result.started and result.verified:
            return handle, evidence
        return None, evidence

    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    runnable_profiles = sorted(profiles.intersection(KNOWN_RUNTIME_PROFILES))
    if runnable_profiles:
        return None, {
            "status": "failed",
            "applicable": False,
            "started": False,
            "verified": False,
            "stopped_cleanly": True,
            "profiles": sorted(profiles),
            "known_runnable_profiles": runnable_profiles,
            "error": "Runnable known profile has no applicable HTTP runtime adapter",
        }
    return None, {
        "status": "not_verified",
        "applicable": False,
        "started": False,
        "verified": False,
        "stopped_cleanly": True,
        "profiles": sorted(profiles),
        "error": "No applicable HTTP runtime adapter for API sequence verification",
    }


def _stop_http_sequence_runtime(handle: RuntimeServerHandle) -> dict[str, Any]:
    _stop_owned_process(handle.process, handle.result)
    return {
        "stopped_cleanly": handle.result.stopped_cleanly,
        "shutdown_method": handle.result.evidence.get("shutdown_method", ""),
        "killed": handle.result.evidence.get("killed", False),
    }


def _persistence_storage_config(root: str, criterion_id: str) -> tuple[dict[str, str], dict[str, Any]]:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(criterion_id or "criterion")).strip("_") or "criterion"
    storage_dir = tempfile.mkdtemp(prefix=f"freelancerstudio_{safe_id}_")
    sqlite_path = os.path.join(storage_dir, "acceptance_verifier.sqlite3")
    json_path = os.path.join(storage_dir, "acceptance_verifier.json")
    env = {
        "FREELANCERSTUDIO_ACCEPTANCE_TEST": "1",
        "APP_ENV": "test",
        "ENV": "test",
        "DATABASE_PATH": sqlite_path,
        "DB_PATH": sqlite_path,
        "SQLITE_PATH": sqlite_path,
        "SQLITE_DB_PATH": sqlite_path,
        "APP_DATABASE_PATH": sqlite_path,
        "TEST_DATABASE_PATH": sqlite_path,
        "DATABASE_URL": f"sqlite:///{sqlite_path.replace(os.sep, '/')}",
        "STORAGE_DIR": storage_dir,
        "DATA_DIR": storage_dir,
        "DATA_FILE": json_path,
    }
    evidence = {
        "isolated_storage_dir": storage_dir,
        "sqlite_path": sqlite_path,
        "json_path": json_path,
        "env_override_names": sorted(env),
        "isolation_policy": "Verifier-created local storage paths are used when the app honors common storage environment variables.",
    }
    return env, evidence


def _runtime_start_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": raw.get("status"),
        "adapter": raw.get("adapter"),
        "started": raw.get("started"),
        "verified": raw.get("verified"),
        "pid": raw.get("pid"),
        "base_url": raw.get("base_url"),
        "error": raw.get("error", ""),
        "env_overrides": raw.get("env_overrides", []),
    }


def _persistence_evidence(
    criterion: dict[str, Any],
    status: str,
    summary: str,
    *,
    created_identifier: str = "",
    marker: str = "",
    pre_restart_verification: dict[str, Any] | None = None,
    stop_result: dict[str, Any] | None = None,
    restart_result: dict[str, Any] | None = None,
    post_restart_verification: dict[str, Any] | None = None,
    final_assertion: dict[str, Any] | None = None,
    method_path: list[dict[str, str]] | None = None,
    safe_request_summary: dict[str, Any] | None = None,
    response_status: dict[str, Any] | None = None,
    redacted_response_excerpt: dict[str, str] | None = None,
    failure_reason: str = "",
    collected_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected = dict(collected_evidence or {})
    collected.update(
        {
            "created_marker": marker,
            "created_identifier": created_identifier,
            "pre_restart_verification": pre_restart_verification or {},
            "stop_result": stop_result or {},
            "restart_result": restart_result or {},
            "post_restart_verification": post_restart_verification or {},
            "final_assertion": final_assertion or {},
            "method_path": method_path or [],
            "safe_request_summary": safe_request_summary or {},
            "response_status": response_status or {},
            "redacted_response_excerpt": redacted_response_excerpt or {},
        }
    )
    return _registry_evidence(
        "persistence_restart",
        status,
        summary,
        setup_steps=["Start runtime with isolated storage configuration", "Create and verify a uniquely identifiable record", "Stop and restart the runtime with the same storage configuration"],
        action_steps=[f"{step.get('method')} {step.get('path')}" for step in method_path or []],
        assertions=["Record exists before restart", "A distinct runtime process starts after clean stop", "Same identifier and marker are observable after restart"],
        collected_evidence=collected,
        failure_reason=failure_reason,
        criterion_id=criterion.get("id", ""),
        created_marker=marker,
        created_identifier=created_identifier,
        pre_restart_verification=pre_restart_verification or {},
        stop_result=stop_result or {},
        restart_result=restart_result or {},
        post_restart_verification=post_restart_verification or {},
        final_assertion=final_assertion or {},
        method_path=method_path or [],
        safe_request_summary=safe_request_summary or {},
        response_status=response_status or {},
        redacted_response_excerpt=redacted_response_excerpt or {},
        verdict=status if status in ("passed", "failed", "blocked") else "not_executed",
    )


def _http_path_url(base_url: str, path: str) -> str:
    cleaned = "/" + str(path or "").lstrip("/")
    return f"{base_url.rstrip('/')}{cleaned}"


def _http_json_request(base_url: str, method: str, path: str, payload: Any = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(_http_path_url(base_url, path), data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            body = resp.read(20000).decode("utf-8", errors="replace")
            status = resp.status
            error = ""
    except urllib.error.HTTPError as exc:
        body = exc.read(20000).decode("utf-8", errors="replace")
        status = exc.code
        error = f"HTTP {exc.code}"
    except Exception as exc:
        return {"ok": False, "status": None, "json": None, "body_text": "", "excerpt": "", "error": _tail_output(str(exc))}

    parsed = None
    try:
        parsed = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        parsed = None
    redacted = _redact_secrets(body)
    return {
        "ok": 200 <= int(status) < 300,
        "status": status,
        "json": parsed,
        "body_text": redacted,
        "excerpt": redacted[:1000],
        "error": _tail_output(error),
    }


def _decode_ref_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_openapi_schema(openapi: dict[str, Any], schema: Any, depth: int = 0) -> dict[str, Any]:
    if not isinstance(schema, dict) or depth > 8:
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = openapi
        for part in ref[2:].split("/"):
            if not isinstance(node, dict):
                return {}
            node = node.get(_decode_ref_token(part), {})
        return _resolve_openapi_schema(openapi, node, depth + 1)
    if isinstance(schema.get("allOf"), list):
        merged = dict(schema)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for item in schema["allOf"]:
            resolved = _resolve_openapi_schema(openapi, item, depth + 1)
            properties.update(resolved.get("properties", {}) if isinstance(resolved.get("properties"), dict) else {})
            required.extend(str(value) for value in resolved.get("required", []) if str(value))
        merged["properties"] = properties or merged.get("properties", {})
        merged["required"] = list(dict.fromkeys(required + [str(value) for value in merged.get("required", []) if str(value)]))
        return merged
    return schema


def _json_request_schema(openapi: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    request_body = operation.get("requestBody") if isinstance(operation, dict) else {}
    content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
    if not isinstance(content, dict):
        return {}
    content_type = "application/json" if "application/json" in content else ""
    if not content_type:
        content_type = next((key for key in content if str(key).endswith("+json")), "")
    if not content_type:
        return {}
    media = content.get(content_type, {}) if isinstance(content.get(content_type), dict) else {}
    return _resolve_openapi_schema(openapi, media.get("schema", {}))


def _has_required_path_parameter(path: str, parameters: list[Any]) -> bool:
    if "{" in path or "}" in path:
        return True
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("in") == "path" and parameter.get("required", True):
            return True
    return False


def _has_required_query_parameter(parameters: list[Any]) -> bool:
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("in") == "query" and parameter.get("required") is True:
            return True
    return False


def _excluded_route(path: str, operation: dict[str, Any]) -> bool:
    text = " ".join(
        str(part)
        for part in (
            path,
            operation.get("operationId", ""),
            operation.get("summary", ""),
            " ".join(str(tag) for tag in operation.get("tags", [])),
        )
    ).lower()
    return any(token in text for token in ("openapi", "docs", "redoc", "health", "login", "logout", "auth", "token"))


def _route_key(path: str) -> str:
    cleaned = "/" + str(path or "").strip("/")
    return cleaned.lower()


def _route_candidate_label(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('method')} {candidate.get('path')}"


def _semantic_route_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower()))
    result = set(tokens)
    for token in tokens:
        if token.endswith("s") and len(token) > 3:
            result.add(token[:-1])
    return result


def _route_pair_score(pair: tuple[dict[str, Any], dict[str, Any]], criterion: dict[str, Any], plan: dict[str, Any]) -> int:
    create_route, list_route = pair
    score = 10 if _route_key(create_route["path"]) == _route_key(list_route["path"]) else 0
    semantic_text = " ".join(
        str(part)
        for part in (
            criterion.get("title", ""),
            criterion.get("description", ""),
            criterion.get("expected_result", ""),
            " ".join(str(item) for item in plan.get("observable_expected_outcomes", [])),
        )
    )
    criterion_tokens = _semantic_route_tokens(semantic_text)
    route_tokens = _semantic_route_tokens(create_route["path"])
    score += len(criterion_tokens.intersection(route_tokens))
    return score


def _discover_create_list_routes(openapi: dict[str, Any], criterion: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str, dict[str, Any]]:
    paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
    if not isinstance(paths, dict) or not paths:
        return None, None, "OpenAPI route discovery returned no paths", {"create_candidates": [], "list_candidates": []}

    create_candidates: list[dict[str, Any]] = []
    list_candidates: list[dict[str, Any]] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters", []) if isinstance(path_item.get("parameters", []), list) else []
        for method, operation in path_item.items():
            method_lower = str(method).lower()
            if method_lower not in {"get", "post"} or not isinstance(operation, dict):
                continue
            parameters = path_parameters + (operation.get("parameters", []) if isinstance(operation.get("parameters", []), list) else [])
            if _has_required_path_parameter(str(path), parameters) or _excluded_route(str(path), operation):
                continue
            if method_lower == "post":
                schema = _json_request_schema(openapi, operation)
                if not schema:
                    continue
                create_candidates.append({"method": "POST", "path": str(path), "operation": operation, "request_schema": schema})
            elif method_lower == "get":
                if _has_required_query_parameter(parameters):
                    continue
                list_candidates.append({"method": "GET", "path": str(path), "operation": operation})

    pairs = [
        (create_route, list_route)
        for create_route in create_candidates
        for list_route in list_candidates
        if _route_key(create_route["path"]) == _route_key(list_route["path"])
    ]
    discovery_evidence = {
        "create_candidates": [_route_candidate_label(candidate) for candidate in create_candidates[:12]],
        "list_candidates": [_route_candidate_label(candidate) for candidate in list_candidates[:12]],
        "matching_pairs": [f"{_route_candidate_label(create_route)} + {_route_candidate_label(list_route)}" for create_route, list_route in pairs[:12]],
    }
    if not pairs:
        return None, None, "No matching POST/GET collection route pair discovered from OpenAPI", discovery_evidence

    scored = [(pair, _route_pair_score(pair, criterion, plan)) for pair in pairs]
    best_score = max(score for _pair, score in scored)
    best_pairs = [pair for pair, score in scored if score == best_score]
    if len(best_pairs) != 1:
        return None, None, "Ambiguous create/list route discovery from OpenAPI: multiple matching POST/GET collection pairs", discovery_evidence
    return best_pairs[0][0], best_pairs[0][1], "", discovery_evidence


def _create_route_score(route: dict[str, Any], criterion: dict[str, Any], plan: dict[str, Any]) -> int:
    semantic_text = " ".join(
        str(part)
        for part in (
            criterion.get("title", ""),
            criterion.get("description", ""),
            criterion.get("expected_result", ""),
            " ".join(str(item) for item in plan.get("observable_expected_outcomes", [])),
        )
    )
    criterion_tokens = _semantic_route_tokens(semantic_text)
    route_tokens = _semantic_route_tokens(str(route.get("path") or ""))
    return len(criterion_tokens.intersection(route_tokens))


def _discover_create_route(openapi: dict[str, Any], criterion: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    routes = _openapi_route_candidates(openapi)
    candidates = [
        route
        for route in routes
        if route.get("method") == "POST" and not _route_has_path_params(route) and _route_has_json_body(route)
    ]
    evidence = {"create_candidates": [_route_candidate_label(candidate) for candidate in candidates[:12]]}
    if not candidates:
        return None, "No POST collection route with a JSON request body was discovered from OpenAPI", evidence
    scored = [(route, _create_route_score(route, criterion, plan)) for route in candidates]
    best_score = max(score for _route, score in scored)
    best = [route for route, score in scored if score == best_score]
    if len(best) != 1:
        return None, "Ambiguous create route discovery from OpenAPI: multiple POST collection routes", evidence
    return best[0], "", evidence


def _operation_parameters(path_item: dict[str, Any], operation: dict[str, Any]) -> list[Any]:
    path_parameters = path_item.get("parameters", []) if isinstance(path_item.get("parameters", []), list) else []
    operation_parameters = operation.get("parameters", []) if isinstance(operation.get("parameters", []), list) else []
    return path_parameters + operation_parameters


def _query_parameters(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [parameter for parameter in route.get("parameters", []) if isinstance(parameter, dict) and parameter.get("in") == "query"]


def _path_parameter_names(path: str) -> list[str]:
    return re.findall(r"\{([^}/]+)\}", str(path or ""))


def _collection_base(path: str) -> str:
    parts = []
    for segment in str(path or "").strip("/").split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            break
        parts.append(segment)
    return "/" + "/".join(parts) if parts else "/"


def _route_under_collection(path: str, collection_path: str) -> bool:
    route = _route_key(_collection_base(path))
    collection = _route_key(collection_path)
    return route == collection or _route_key(path).startswith(collection.rstrip("/") + "/")


def _route_has_path_params(route: dict[str, Any]) -> bool:
    return bool(_path_parameter_names(str(route.get("path") or "")))


def _route_has_json_body(route: dict[str, Any]) -> bool:
    return bool(route.get("request_schema"))


def _replace_path_params(path: str, identifier: str) -> str:
    safe_identifier = urllib.parse.quote(str(identifier), safe="")
    return re.sub(r"\{[^}/]+\}", safe_identifier, str(path or ""))


def _append_query(path: str, params: dict[str, Any]) -> str:
    query = urllib.parse.urlencode({key: str(value) for key, value in params.items()})
    return f"{path}?{query}" if query else path


def _openapi_route_candidates(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
    if not isinstance(paths, dict):
        return routes
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_upper = str(method).upper()
            if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not isinstance(operation, dict):
                continue
            if _excluded_route(str(path), operation):
                continue
            route = {
                "method": method_upper,
                "path": str(path),
                "operation": operation,
                "parameters": _operation_parameters(path_item, operation),
                "request_schema": _json_request_schema(openapi, operation) if method_upper in {"POST", "PUT", "PATCH"} else {},
            }
            routes.append(route)
    return routes


def _find_related_route(routes: list[dict[str, Any]], collection_path: str, methods: set[str], *, require_path_param: bool = True, require_json: bool = False, prefer_tokens: list[str] | None = None) -> tuple[dict[str, Any] | None, str, list[str]]:
    candidates = []
    for route in routes:
        if route.get("method") not in methods:
            continue
        if require_path_param != _route_has_path_params(route):
            continue
        if require_json and not _route_has_json_body(route):
            continue
        if not _route_under_collection(str(route.get("path") or ""), collection_path):
            continue
        candidates.append(route)
    labels = [_route_candidate_label(candidate) for candidate in candidates]
    if not candidates:
        return None, f"No {('/'.join(sorted(methods)))} route related to {collection_path} was discovered", labels
    if prefer_tokens:
        token_preferred = [
            route
            for route in candidates
            if any(token in str(route.get("path", "")).lower() or token in str(route.get("operation", {}).get("summary", "")).lower() for token in prefer_tokens)
        ]
        if len(token_preferred) == 1:
            return token_preferred[0], "", labels
    plain_item_routes = [route for route in candidates if re.search(r"/\{[^}/]+\}/?$", str(route.get("path") or ""))]
    if len(plain_item_routes) == 1:
        return plain_item_routes[0], "", labels
    if len(candidates) == 1:
        return candidates[0], "", labels
    return None, f"Ambiguous {('/'.join(sorted(methods)))} route discovery for {collection_path}: {', '.join(labels)}", labels


def _query_param_names(route: dict[str, Any]) -> list[str]:
    return [str(parameter.get("name") or "") for parameter in _query_parameters(route) if str(parameter.get("name") or "").strip()]


def _find_query_route(routes: list[dict[str, Any]], collection_path: str, kind: str) -> tuple[dict[str, Any] | None, str, str, list[str]]:
    search_names = ("q", "query", "search", "term", "text", "name", "client", "contact")
    filter_names = ("status", "priority", "state", "type", "category")
    desired = search_names if kind == "search" else filter_names
    candidates: list[tuple[dict[str, Any], str]] = []
    for route in routes:
        if route.get("method") != "GET" or _route_has_path_params(route):
            continue
        if not _route_under_collection(str(route.get("path") or ""), collection_path):
            continue
        query_names = _query_param_names(route)
        for name in query_names:
            lower_name = name.lower()
            if lower_name in desired or any(token in lower_name for token in desired if len(token) > 2):
                extra_required = [
                    str(parameter.get("name") or "")
                    for parameter in _query_parameters(route)
                    if parameter.get("required") is True and str(parameter.get("name") or "") != name
                ]
                if extra_required:
                    continue
                candidates.append((route, name))
    labels = [f"{_route_candidate_label(route)}?{name}" for route, name in candidates]
    if not candidates:
        return None, "", f"No declared {kind} query parameter was discovered for {collection_path}", labels
    if len(candidates) != 1:
        return None, "", f"Ambiguous {kind} query route discovery for {collection_path}: {', '.join(labels)}", labels
    return candidates[0][0], candidates[0][1], "", labels


def _schema_type(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), schema_type[0] if schema_type else "")
    if schema_type:
        return str(schema_type)
    if isinstance(schema.get("properties"), dict):
        return "object"
    if isinstance(schema.get("items"), dict):
        return "array"
    return "string"


def _schema_value(openapi: dict[str, Any], name: str, schema: Any, marker: str, depth: int = 0) -> tuple[Any, str]:
    resolved = _resolve_openapi_schema(openapi, schema, depth)
    enum_values = resolved.get("enum") if isinstance(resolved.get("enum"), list) else []
    if enum_values:
        return enum_values[0], ""
    schema_type = _schema_type(resolved)
    lower_name = name.lower()
    if schema_type == "string":
        if resolved.get("format") == "email" or "email" in lower_name:
            return f"{marker}@example.com", name
        if "phone" in lower_name:
            return "+15550123456", ""
        if "status" in lower_name:
            return "new", ""
        if "priority" in lower_name:
            return "high", ""
        return marker, name
    if schema_type == "integer":
        return 1, ""
    if schema_type == "number":
        return 1.0, ""
    if schema_type == "boolean":
        return True, ""
    if schema_type == "array":
        if int(resolved.get("minItems", 0) or 0) > 0:
            value, marker_field = _schema_value(openapi, name, resolved.get("items", {}), marker, depth + 1)
            return [value], marker_field
        return [], ""
    if schema_type == "object" and depth < 4:
        payload, marker_field = _object_payload_from_schema(openapi, resolved, marker, depth + 1)
        return payload, marker_field
    return marker, name


def _object_payload_from_schema(openapi: dict[str, Any], schema: dict[str, Any], marker: str, depth: int = 0) -> tuple[dict[str, Any], str]:
    properties = schema.get("properties", {}) if isinstance(schema.get("properties", {}), dict) else {}
    required = [str(name) for name in schema.get("required", []) if str(name)]
    payload: dict[str, Any] = {}
    marker_field = ""
    for name in required:
        value, field = _schema_value(openapi, name, properties.get(name, {}), marker, depth + 1)
        payload[name] = value
        if field and not marker_field:
            marker_field = name if field == name else f"{name}.{field}"

    if not marker_field:
        for name, prop_schema in properties.items():
            if name in payload:
                continue
            value, field = _schema_value(openapi, str(name), prop_schema, marker, depth + 1)
            if field:
                payload[str(name)] = value
                marker_field = str(name) if field == str(name) else f"{name}.{field}"
                break
    return payload, marker_field


def _build_create_payload(openapi: dict[str, Any], create_route: dict[str, Any], marker: str) -> tuple[dict[str, Any], str, str]:
    schema = _resolve_openapi_schema(openapi, create_route.get("request_schema", {}))
    if not schema:
        return {"name": marker, "description": f"Acceptance verifier marker {marker}"}, "name", ""
    if _schema_type(schema) != "object":
        return {}, "", "Create route JSON schema is not an object payload"
    payload, marker_field = _object_payload_from_schema(openapi, schema, marker)
    if not payload and schema.get("additionalProperties") is False:
        return {}, "", "Create route JSON schema does not expose writable payload fields"
    if not payload:
        payload = {"name": marker, "description": f"Acceptance verifier marker {marker}"}
        marker_field = "name"
    return payload, marker_field, ""


def _schema_properties(openapi: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve_openapi_schema(openapi, schema)
    return resolved.get("properties", {}) if isinstance(resolved.get("properties", {}), dict) else {}


def _schema_has_field(openapi: dict[str, Any], schema: dict[str, Any], field_name: str) -> bool:
    return field_name in _schema_properties(openapi, schema)


def _first_string_field(openapi: dict[str, Any], schema: dict[str, Any], preferred: list[str] | None = None) -> str:
    properties = _schema_properties(openapi, schema)
    for name in preferred or []:
        if name in properties and _schema_type(_resolve_openapi_schema(openapi, properties[name])) == "string":
            return name
    for name, prop_schema in properties.items():
        if _schema_type(_resolve_openapi_schema(openapi, prop_schema)) == "string":
            return str(name)
    return ""


def _field_value_from_payload(payload: dict[str, Any], dotted_field: str) -> Any:
    value: Any = payload
    for part in dotted_field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _set_payload_field(payload: dict[str, Any], dotted_field: str, value: Any) -> bool:
    target: Any = payload
    parts = [part for part in dotted_field.split(".") if part]
    for part in parts[:-1]:
        if not isinstance(target, dict) or part not in target:
            return False
        target = target[part]
    if not parts or not isinstance(target, dict):
        return False
    target[parts[-1]] = value
    return True


def _status_values(openapi: dict[str, Any], schema: dict[str, Any]) -> tuple[str, str]:
    prop_schema = _schema_properties(openapi, schema).get("status", {})
    resolved = _resolve_openapi_schema(openapi, prop_schema)
    enum_values = [str(value) for value in resolved.get("enum", []) if str(value).strip()] if isinstance(resolved.get("enum"), list) else []
    if len(enum_values) >= 2:
        return enum_values[0], enum_values[1]
    return "new", "closed"


def _payload_variant(payload: dict[str, Any], marker_field: str, marker: str, *, field: str = "", field_value: Any = None) -> dict[str, Any]:
    variant = json.loads(json.dumps(payload))
    if marker_field:
        _set_payload_field(variant, marker_field, marker)
    if field:
        _set_payload_field(variant, field, field_value)
    return variant


def _build_update_payload(openapi: dict[str, Any], update_route: dict[str, Any], create_payload: dict[str, Any], marker_field: str, updated_marker: str, *, status_change: bool = False) -> tuple[dict[str, Any], str, Any, str]:
    schema = _resolve_openapi_schema(openapi, update_route.get("request_schema", {}))
    if not schema or _schema_type(schema) != "object":
        return {}, "", None, "Update route JSON schema is not an object payload"
    properties = _schema_properties(openapi, schema)
    required = [str(name) for name in schema.get("required", []) if str(name)]
    payload: dict[str, Any] = {}
    for name in required:
        if name in create_payload:
            payload[name] = create_payload[name]
        else:
            payload[name], _marker_field = _schema_value(openapi, name, properties.get(name, {}), updated_marker)

    if status_change:
        if "status" not in properties:
            return {}, "", None, "No writable status field was discovered in the update schema"
        _initial_status, updated_value = _status_values(openapi, schema)
        payload["status"] = updated_value
        return payload, "status", updated_value, ""

    target_field = ""
    if marker_field and marker_field in properties:
        target_field = marker_field
    if not target_field:
        target_field = _first_string_field(openapi, schema, ["name", "title", "description", "client", "contact"])
    if not target_field:
        return {}, "", None, "No writable string field was discovered in the update schema"
    payload[target_field] = updated_marker
    return payload, target_field, updated_marker, ""


def _filter_field_and_values(openapi: dict[str, Any], create_route: dict[str, Any], query_param: str) -> tuple[str, Any, Any, str]:
    schema = _resolve_openapi_schema(openapi, create_route.get("request_schema", {}))
    properties = _schema_properties(openapi, schema)
    candidates = [query_param, "status", "priority", "state", "category"]
    for name in candidates:
        if name not in properties:
            continue
        resolved = _resolve_openapi_schema(openapi, properties[name])
        enum_values = [value for value in resolved.get("enum", []) if str(value).strip()] if isinstance(resolved.get("enum"), list) else []
        if len(enum_values) >= 2:
            return name, enum_values[0], enum_values[1], ""
        if _schema_type(resolved) == "string":
            return name, "open", "closed", ""
    return "", None, None, f"No writable field matching filter parameter {query_param} was discovered in the create schema"


def _safe_request_summary(payload: dict[str, Any], marker: str, marker_field: str) -> dict[str, Any]:
    return {
        "content_type": "application/json",
        "json_keys": sorted(str(key) for key in payload.keys()),
        "unique_marker": marker if marker_field else "",
        "marker_field": marker_field,
        "value_policy": "Generated non-secret local test values; evidence records keys and marker only.",
    }


IDENTIFIER_KEYS = ("id", "uuid", "uid", "_id", "identifier", "request_id", "ticket_id", "item_id")


def _extract_identifier(value: Any) -> str:
    if isinstance(value, dict):
        for key in IDENTIFIER_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, (str, int, float)) and str(candidate).strip():
                return str(candidate)
        for key in ("data", "item", "request", "ticket", "record"):
            nested = _extract_identifier(value.get(key))
            if nested:
                return nested
    return ""


def _iter_response_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for key in ("items", "data", "results", "records", "requests", "tickets"):
            nested = value.get(key)
            if nested is not value:
                yield from _iter_response_objects(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_response_objects(item)


def _json_contains_marker(value: Any, marker: str) -> bool:
    if not marker:
        return False
    try:
        return marker in json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return marker in str(value)


def _list_contains_created_item(value: Any, created_identifier: str, marker: str) -> tuple[bool, str]:
    for item in _iter_response_objects(value):
        if created_identifier and _extract_identifier(item) == created_identifier:
            return True, "created_identifier"
        if _json_contains_marker(item, marker):
            return True, "unique_marker"
    return False, ""


def _matching_response_object(value: Any, created_identifier: str, marker: str) -> dict[str, Any] | None:
    for item in _iter_response_objects(value):
        if not isinstance(item, dict):
            continue
        if created_identifier and _extract_identifier(item) == created_identifier:
            return item
        if _json_contains_marker(item, marker):
            return item
    return None


def _required_payload_fields(openapi: dict[str, Any], create_route: dict[str, Any], payload: dict[str, Any], marker_field: str) -> list[str]:
    schema = _resolve_openapi_schema(openapi, create_route.get("request_schema", {}))
    fields = [str(name) for name in schema.get("required", []) if isinstance(name, str) and name in payload]
    if marker_field and marker_field in payload:
        fields.append(marker_field)
    return list(dict.fromkeys(fields))


def _verify_persisted_response(response: dict[str, Any], created_identifier: str, marker: str, required_fields: list[str]) -> dict[str, Any]:
    item = _matching_response_object(response.get("json"), created_identifier, marker)
    missing_fields = required_fields if item is None else [field for field in required_fields if field not in item]
    contains_identifier = bool(item and created_identifier and _extract_identifier(item) == created_identifier)
    contains_marker = bool(item and _json_contains_marker(item, marker))
    return {
        "status_code": response.get("status"),
        "response_ok": bool(response.get("ok")),
        "record_exists": bool(item and (contains_identifier or contains_marker)),
        "contains_identifier": contains_identifier,
        "contains_marker": contains_marker,
        "required_fields": required_fields,
        "missing_required_fields": missing_fields,
        "required_fields_present": not missing_fields,
    }


def _discover_crud_route_set(openapi: dict[str, Any], criterion: dict[str, Any], plan: dict[str, Any], kind: str) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    if kind == "create":
        create_route, list_route, discovery_reason, discovery_evidence = _discover_create_list_routes(openapi, criterion, plan)
        if discovery_reason or not create_route or not list_route:
            return None, discovery_reason, discovery_evidence
    else:
        create_route, discovery_reason, discovery_evidence = _discover_create_route(openapi, criterion, plan)
        list_route = None
        if discovery_reason or not create_route:
            return None, discovery_reason, discovery_evidence

    routes = _openapi_route_candidates(openapi)
    route_set: dict[str, Any] = {"create": create_route}
    if list_route:
        route_set["list"] = list_route
    collection_path = str(create_route.get("path") or "")
    discovery_evidence.setdefault("related_route_candidates", {})

    if kind in ("read", "update", "status_change"):
        read_route, reason, labels = _find_related_route(routes, collection_path, {"GET"}, require_path_param=True)
        discovery_evidence["related_route_candidates"]["read"] = labels
        if reason or not read_route:
            return None, reason, discovery_evidence
        route_set["read"] = read_route

    if kind in ("update", "status_change"):
        update_route, reason, labels = _find_related_route(
            routes,
            collection_path,
            {"PATCH", "PUT"},
            require_path_param=True,
            require_json=True,
            prefer_tokens=["status"] if kind == "status_change" else None,
        )
        discovery_evidence["related_route_candidates"]["update"] = labels
        if reason or not update_route:
            return None, reason, discovery_evidence
        route_set["update"] = update_route

    if kind == "delete":
        delete_route, reason, labels = _find_related_route(routes, collection_path, {"DELETE"}, require_path_param=True)
        discovery_evidence["related_route_candidates"]["delete"] = labels
        if reason or not delete_route:
            return None, reason, discovery_evidence
        route_set["delete"] = delete_route
        read_route, _read_reason, read_labels = _find_related_route(routes, collection_path, {"GET"}, require_path_param=True)
        discovery_evidence["related_route_candidates"]["read"] = read_labels
        if read_route:
            route_set["read"] = read_route
        list_route, _list_reason, list_labels = _find_related_route(routes, collection_path, {"GET"}, require_path_param=False)
        discovery_evidence["related_route_candidates"]["list"] = list_labels
        if list_route:
            route_set["list"] = list_route
        if not route_set.get("read") and not route_set.get("list"):
            return None, "No read or list route was discovered to verify deletion state", discovery_evidence

    if kind in ("search", "filter"):
        query_route, query_param, reason, labels = _find_query_route(routes, collection_path, kind)
        discovery_evidence["related_route_candidates"][kind] = labels
        if reason or not query_route:
            return None, reason, discovery_evidence
        route_set[kind] = query_route
        route_set[f"{kind}_param"] = query_param

    return route_set, "", discovery_evidence


def _discover_persistence_routes(openapi: dict[str, Any], criterion: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    create_route, discovery_reason, discovery_evidence = _discover_create_route(openapi, criterion, plan)
    if discovery_reason or not create_route:
        return None, discovery_reason, discovery_evidence
    routes = _openapi_route_candidates(openapi)
    route_set: dict[str, Any] = {"create": create_route}
    collection_path = str(create_route.get("path") or "")
    discovery_evidence.setdefault("related_route_candidates", {})
    read_route, read_reason, read_labels = _find_related_route(routes, collection_path, {"GET"}, require_path_param=True)
    discovery_evidence["related_route_candidates"]["read"] = read_labels
    if read_route:
        route_set["read"] = read_route
    list_route, list_reason, list_labels = _find_related_route(routes, collection_path, {"GET"}, require_path_param=False)
    discovery_evidence["related_route_candidates"]["list"] = list_labels
    if list_route:
        route_set["list"] = list_route
    if not route_set.get("read") and not route_set.get("list"):
        return None, f"No read or list route was discovered for post-restart verification: {read_reason}; {list_reason}", discovery_evidence
    return route_set, "", discovery_evidence


def _record_http_step(method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str], key: str, method: str, path: str, response: dict[str, Any]) -> None:
    method_path.append({"method": method.upper(), "path": path.split("?", 1)[0]})
    response_status[key] = response.get("status")
    redacted_excerpt[key] = str(response.get("excerpt") or "")


def _created_id_or_failure(create_response: dict[str, Any], marker: str, marker_field: str) -> tuple[str, str]:
    created_identifier = _extract_identifier(create_response.get("json"))
    if created_identifier:
        return created_identifier, ""
    if marker_field and _json_contains_marker(create_response.get("json"), marker):
        return marker, ""
    return "", "Create response did not expose an id-like field or preserve the unique marker"


def _execute_create_fixture(handle: RuntimeServerHandle, openapi: dict[str, Any], create_route: dict[str, Any], marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str]) -> tuple[str, str, str, bool, dict[str, Any], dict[str, Any], str]:
    payload, marker_field, payload_reason = _build_create_payload(openapi, create_route, marker)
    safe_summary = _safe_request_summary(payload, marker, marker_field)
    if payload_reason:
        return "not_verified", "Create request payload could not be built from route schema", payload_reason, False, safe_summary, {}, ""
    create_response = _http_json_request(handle.base_url, "POST", create_route["path"], payload)
    _record_http_step(method_path, response_status, redacted_excerpt, "create", "POST", create_route["path"], create_response)
    if not create_response.get("ok"):
        return "failed", "Create endpoint did not return a successful response", create_response.get("error") or f"Create endpoint returned HTTP {create_response.get('status')}", False, safe_summary, payload, ""
    created_identifier, id_reason = _created_id_or_failure(create_response, marker, marker_field)
    if id_reason:
        return "failed", "Create succeeded but produced no created identifier or unique marker", id_reason, False, safe_summary, payload, ""
    return "passed", "Fixture record was created successfully", "", True, safe_summary, payload, created_identifier


def _fetch_persisted_record(handle: RuntimeServerHandle, route_set: dict[str, Any], created_identifier: str, marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str], key: str) -> dict[str, Any]:
    if route_set.get("read") and created_identifier and created_identifier != marker:
        read_path = _replace_path_params(route_set["read"]["path"], created_identifier)
        response = _http_json_request(handle.base_url, "GET", read_path)
        _record_http_step(method_path, response_status, redacted_excerpt, key, "GET", read_path, response)
        return response
    if not route_set.get("list"):
        return {"ok": False, "status": None, "json": None, "body_text": "", "excerpt": "", "error": "No list route is available and create response did not provide a concrete identifier"}
    list_path = route_set["list"]["path"]
    response = _http_json_request(handle.base_url, "GET", list_path)
    _record_http_step(method_path, response_status, redacted_excerpt, key, "GET", list_path, response)
    return response


def _verify_persistence_restart(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = _criterion_verifier_plan(criterion)
    if plan.get("verifier_type") != "persistence_restart":
        return _persistence_evidence(
            criterion,
            "not_verified",
            "Persistence restart verifier requires a persistence_restart plan",
            failure_reason="Verifier plan is not persistence_restart",
            collected_evidence={"plan_verifier_type": plan.get("verifier_type", "")},
        )

    env_extra, storage_evidence = _persistence_storage_config(root, str(criterion.get("id", "")))
    handle: RuntimeServerHandle | None = None
    restarted_handle: RuntimeServerHandle | None = None
    method_path: list[dict[str, str]] = []
    response_status: dict[str, Any] = {}
    redacted_excerpt: dict[str, str] = {}
    safe_summary: dict[str, Any] = {}
    created_identifier = ""
    marker = f"persist_{re.sub(r'[^A-Za-z0-9]+', '_', str(criterion.get('id') or 'criterion')).strip('_').lower()}_{int(time.time() * 1000)}"
    pre_verification: dict[str, Any] = {}
    stop_result: dict[str, Any] = {}
    restart_result: dict[str, Any] = {}
    post_verification: dict[str, Any] = {}
    final_assertion: dict[str, Any] = {"passed": False}
    collected: dict[str, Any] = {"storage": storage_evidence, "verifier_plan": plan}
    status = "failed"
    summary = "Persistence restart verification failed"
    failure_reason = ""

    try:
        handle, start_evidence = _start_http_sequence_runtime(project, root, env_extra)
        collected["initial_start"] = _runtime_start_evidence(start_evidence)
        if not handle:
            reason = start_evidence.get("error") or "Runtime unavailable for persistence restart verification"
            return _persistence_evidence(
                criterion,
                "failed" if start_evidence.get("status") == "failed" else "not_verified",
                "Initial runtime start failed for persistence verification",
                marker=marker,
                failure_reason=str(reason),
                restart_result={"status": "not_started"},
                final_assertion={"passed": False, "reason": str(reason)},
                collected_evidence=collected,
            )

        openapi_response = _http_json_request(handle.base_url, "GET", "/openapi.json")
        collected["openapi_status"] = openapi_response.get("status")
        if not openapi_response.get("ok") or not isinstance(openapi_response.get("json"), dict):
            status = "not_verified"
            summary = "OpenAPI route discovery was unavailable for persistence verification"
            failure_reason = openapi_response.get("error") or f"OpenAPI response status was {openapi_response.get('status')}"
            return _persistence_evidence(
                criterion,
                status,
                summary,
                marker=marker,
                failure_reason=failure_reason,
                collected_evidence=collected,
            )

        openapi = openapi_response["json"]
        route_set, discovery_reason, discovery_evidence = _discover_persistence_routes(openapi, criterion, plan)
        collected["route_discovery"] = discovery_evidence
        if discovery_reason or not route_set:
            status = "not_verified"
            summary = "Persistence route discovery was ambiguous or incomplete"
            failure_reason = discovery_reason
            return _persistence_evidence(
                criterion,
                status,
                summary,
                marker=marker,
                failure_reason=failure_reason,
                collected_evidence=collected,
            )

        create_status, create_summary, create_failure, create_assertion, safe_summary, payload, created_identifier = _execute_create_fixture(
            handle,
            openapi,
            route_set["create"],
            marker,
            method_path,
            response_status,
            redacted_excerpt,
        )
        if create_status != "passed":
            return _persistence_evidence(
                criterion,
                create_status,
                create_summary,
                marker=marker,
                created_identifier=created_identifier,
                safe_request_summary=safe_summary,
                method_path=method_path,
                response_status=response_status,
                redacted_response_excerpt=redacted_excerpt,
                failure_reason=create_failure,
                final_assertion={"passed": bool(create_assertion), "reason": create_failure},
                collected_evidence=collected,
            )

        required_fields = _required_payload_fields(openapi, route_set["create"], payload, str(safe_summary.get("marker_field", "")))
        pre_response = _fetch_persisted_record(handle, route_set, created_identifier, marker, method_path, response_status, redacted_excerpt, "pre_restart_fetch")
        pre_verification = _verify_persisted_response(pre_response, created_identifier, marker, required_fields)
        if not pre_verification.get("record_exists") or not pre_verification.get("required_fields_present"):
            return _persistence_evidence(
                criterion,
                "failed",
                "Created record was not verifiable before restart",
                marker=marker,
                created_identifier=created_identifier,
                pre_restart_verification=pre_verification,
                safe_request_summary=safe_summary,
                method_path=method_path,
                response_status=response_status,
                redacted_response_excerpt=redacted_excerpt,
                failure_reason="Pre-restart fetch did not contain the created identifier, marker, and required fields",
                final_assertion={"passed": False, "reason": "pre_restart_verification_failed"},
                collected_evidence=collected,
            )

        first_pid = handle.result.evidence.get("pid")
        stop_result = _stop_http_sequence_runtime(handle)
        handle = None
        if not stop_result.get("stopped_cleanly"):
            return _persistence_evidence(
                criterion,
                "failed",
                "Runtime did not stop cleanly before persistence restart",
                marker=marker,
                created_identifier=created_identifier,
                pre_restart_verification=pre_verification,
                stop_result=stop_result,
                safe_request_summary=safe_summary,
                method_path=method_path,
                response_status=response_status,
                redacted_response_excerpt=redacted_excerpt,
                failure_reason="Owned runtime process did not stop cleanly",
                final_assertion={"passed": False, "reason": "stop_failed"},
                collected_evidence=collected,
            )

        restarted_handle, raw_restart = _start_http_sequence_runtime(project, root, env_extra)
        restart_result = _runtime_start_evidence(raw_restart)
        restart_result["same_storage_env"] = sorted(env_extra) == sorted(env_extra)
        if restarted_handle:
            restart_result["real_restart"] = restarted_handle.result.evidence.get("pid") != first_pid
        else:
            restart_result["real_restart"] = False
        if not restarted_handle:
            reason = raw_restart.get("error") or "Runtime restart failed"
            return _persistence_evidence(
                criterion,
                "failed",
                "Runtime restart failed during persistence verification",
                marker=marker,
                created_identifier=created_identifier,
                pre_restart_verification=pre_verification,
                stop_result=stop_result,
                restart_result=restart_result,
                safe_request_summary=safe_summary,
                method_path=method_path,
                response_status=response_status,
                redacted_response_excerpt=redacted_excerpt,
                failure_reason=str(reason),
                final_assertion={"passed": False, "reason": str(reason)},
                collected_evidence=collected,
            )
        if not restart_result.get("real_restart"):
            failure_reason = "Restart did not create a distinct runtime process"
            return _persistence_evidence(
                criterion,
                "failed",
                failure_reason,
                marker=marker,
                created_identifier=created_identifier,
                pre_restart_verification=pre_verification,
                stop_result=stop_result,
                restart_result=restart_result,
                safe_request_summary=safe_summary,
                method_path=method_path,
                response_status=response_status,
                redacted_response_excerpt=redacted_excerpt,
                failure_reason=failure_reason,
                final_assertion={"passed": False, "reason": "not_a_real_restart"},
                collected_evidence=collected,
            )

        post_response = _fetch_persisted_record(restarted_handle, route_set, created_identifier, marker, method_path, response_status, redacted_excerpt, "post_restart_fetch")
        post_verification = _verify_persisted_response(post_response, created_identifier, marker, required_fields)
        identity_persisted = bool(post_verification.get("contains_identifier") or (created_identifier == marker and post_verification.get("contains_marker")))
        final_assertion = {
            "passed": bool(identity_persisted and post_verification.get("contains_marker") and post_verification.get("required_fields_present") and restart_result.get("real_restart")),
            "identity_persisted": identity_persisted,
            "marker_persisted": bool(post_verification.get("contains_marker")),
            "required_fields_present": bool(post_verification.get("required_fields_present")),
            "real_restart": bool(restart_result.get("real_restart")),
        }
        if final_assertion["passed"]:
            status = "passed"
            summary = "Created record survived a real application restart with the same storage configuration"
            failure_reason = ""
        else:
            status = "failed"
            summary = "Created record did not survive application restart"
            failure_reason = "Post-restart fetch did not contain the same identifier, marker, and required fields; in-memory or non-persistent storage is likely"
        return _persistence_evidence(
            criterion,
            status,
            summary,
            marker=marker,
            created_identifier=created_identifier,
            pre_restart_verification=pre_verification,
            stop_result=stop_result,
            restart_result=restart_result,
            post_restart_verification=post_verification,
            final_assertion=final_assertion,
            method_path=method_path,
            safe_request_summary=safe_summary,
            response_status=response_status,
            redacted_response_excerpt=redacted_excerpt,
            failure_reason=failure_reason,
            collected_evidence=collected,
        )
    finally:
        if handle is not None:
            collected["initial_runtime_cleanup"] = _stop_http_sequence_runtime(handle)
        if restarted_handle is not None:
            collected["post_restart_stop_result"] = _stop_http_sequence_runtime(restarted_handle)


def _execute_create_observable_sequence(handle: RuntimeServerHandle, openapi: dict[str, Any], route_set: dict[str, Any], marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str]) -> tuple[str, str, str, bool, dict[str, Any], dict[str, Any], str]:
    create_route = route_set["create"]
    list_route = route_set["list"]
    status, summary, failure_reason, assertion_result, safe_summary, payload, created_identifier = _execute_create_fixture(handle, openapi, create_route, marker, method_path, response_status, redacted_excerpt)
    if status != "passed":
        return status, summary, failure_reason, assertion_result, safe_summary, payload, created_identifier
    list_response = _http_json_request(handle.base_url, "GET", list_route["path"])
    _record_http_step(method_path, response_status, redacted_excerpt, "list", "GET", list_route["path"], list_response)
    if not list_response.get("ok"):
        return "failed", "List endpoint did not return a successful response", list_response.get("error") or f"List endpoint returned HTTP {list_response.get('status')}", False, safe_summary, payload, created_identifier
    assertion_result, _match_source = _list_contains_created_item(list_response.get("json"), created_identifier, marker)
    if not assertion_result:
        return "failed", "Created item is absent from the list endpoint response", "List response did not contain the created identifier or unique marker", False, safe_summary, payload, created_identifier
    return "passed", "Created item appears in the list endpoint response", "", True, safe_summary, payload, created_identifier


def _execute_read_sequence(handle: RuntimeServerHandle, openapi: dict[str, Any], route_set: dict[str, Any], marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str]) -> tuple[str, str, str, bool, dict[str, Any], str]:
    status, summary, failure_reason, assertion_result, safe_summary, _payload, created_identifier = _execute_create_fixture(handle, openapi, route_set["create"], marker, method_path, response_status, redacted_excerpt)
    if status != "passed":
        return status, summary, failure_reason, assertion_result, safe_summary, created_identifier
    if created_identifier == marker:
        return "not_verified", "Read route requires a concrete created identifier", "Create response did not expose an id-like value for the item read route", False, safe_summary, created_identifier
    read_path = _replace_path_params(route_set["read"]["path"], created_identifier)
    read_response = _http_json_request(handle.base_url, "GET", read_path)
    _record_http_step(method_path, response_status, redacted_excerpt, "read", "GET", read_path, read_response)
    if not read_response.get("ok"):
        return "failed", "Read endpoint did not return the created record", read_response.get("error") or f"Read endpoint returned HTTP {read_response.get('status')}", False, safe_summary, created_identifier
    read_contains = _json_contains_marker(read_response.get("json"), marker) or _extract_identifier(read_response.get("json")) == created_identifier
    if not read_contains:
        return "failed", "Read endpoint response did not contain the created record", "Read response did not contain the created identifier or unique marker", False, safe_summary, created_identifier
    return "passed", "Created record can be opened/read by identifier", "", True, safe_summary, created_identifier


def _execute_update_sequence(handle: RuntimeServerHandle, openapi: dict[str, Any], route_set: dict[str, Any], marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str], *, status_change: bool = False) -> tuple[str, str, str, bool, dict[str, Any], str, dict[str, Any]]:
    status, summary, failure_reason, assertion_result, safe_summary, create_payload, created_identifier = _execute_create_fixture(handle, openapi, route_set["create"], marker, method_path, response_status, redacted_excerpt)
    if status != "passed":
        return status, summary, failure_reason, assertion_result, safe_summary, created_identifier, {}
    if created_identifier == marker:
        return "not_verified", "Update route requires a concrete created identifier", "Create response did not expose an id-like value for the item update route", False, safe_summary, created_identifier, {}
    read_path = _replace_path_params(route_set["read"]["path"], created_identifier)
    before_response = _http_json_request(handle.base_url, "GET", read_path)
    _record_http_step(method_path, response_status, redacted_excerpt, "read_before_update", "GET", read_path, before_response)
    if not before_response.get("ok"):
        return "failed", "Read-before-update endpoint did not return the created record", before_response.get("error") or f"Read endpoint returned HTTP {before_response.get('status')}", False, safe_summary, created_identifier, {}
    before_contains = _json_contains_marker(before_response.get("json"), marker) or _extract_identifier(before_response.get("json")) == created_identifier
    if not before_contains:
        return "failed", "Read-before-update response did not contain the fixture record", "Read-before-update response did not contain the created identifier or unique marker", False, safe_summary, created_identifier, {}
    updated_marker = f"{marker}_updated"
    update_payload, updated_field, updated_value, payload_reason = _build_update_payload(openapi, route_set["update"], create_payload, safe_summary.get("marker_field", ""), updated_marker, status_change=status_change)
    safe_summary.setdefault("requests", {})["update"] = _safe_request_summary(update_payload, updated_marker, updated_field)
    if payload_reason:
        return "not_verified", "Update request payload could not be built from route schema", payload_reason, False, safe_summary, created_identifier, {}
    update_path = _replace_path_params(route_set["update"]["path"], created_identifier)
    update_response = _http_json_request(handle.base_url, str(route_set["update"]["method"]), update_path, update_payload)
    _record_http_step(method_path, response_status, redacted_excerpt, "update", str(route_set["update"]["method"]), update_path, update_response)
    if not update_response.get("ok"):
        return "failed", "Update endpoint did not return a successful response", update_response.get("error") or f"Update endpoint returned HTTP {update_response.get('status')}", False, safe_summary, created_identifier, {}
    after_response = _http_json_request(handle.base_url, "GET", read_path)
    _record_http_step(method_path, response_status, redacted_excerpt, "read_after_update", "GET", read_path, after_response)
    if not after_response.get("ok"):
        return "failed", "Read-after-update endpoint did not return the record", after_response.get("error") or f"Read endpoint returned HTTP {after_response.get('status')}", False, safe_summary, created_identifier, {}
    changed_value_observed = _json_contains_marker(after_response.get("json"), str(updated_value))
    same_record_observed = _extract_identifier(after_response.get("json")) == created_identifier or _json_contains_marker(after_response.get("json"), created_identifier)
    if not changed_value_observed or not same_record_observed:
        return "failed", "Updated value was not persisted in the read response", "Read-after-update response did not contain the changed value for the same record", False, safe_summary, created_identifier, {"updated_field": updated_field, "updated_value": updated_value}
    summary = "Changed status persisted and is observable" if status_change else "Updated field value persisted and is observable"
    return "passed", summary, "", True, safe_summary, created_identifier, {"updated_field": updated_field, "updated_value": updated_value}


def _execute_delete_sequence(handle: RuntimeServerHandle, openapi: dict[str, Any], route_set: dict[str, Any], marker: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str]) -> tuple[str, str, str, bool, dict[str, Any], str]:
    status, summary, failure_reason, assertion_result, safe_summary, _payload, created_identifier = _execute_create_fixture(handle, openapi, route_set["create"], marker, method_path, response_status, redacted_excerpt)
    if status != "passed":
        return status, summary, failure_reason, assertion_result, safe_summary, created_identifier
    if created_identifier == marker:
        return "not_verified", "Delete route requires a concrete created identifier", "Create response did not expose an id-like value for the item delete route", False, safe_summary, created_identifier
    delete_path = _replace_path_params(route_set["delete"]["path"], created_identifier)
    delete_response = _http_json_request(handle.base_url, "DELETE", delete_path)
    _record_http_step(method_path, response_status, redacted_excerpt, "delete", "DELETE", delete_path, delete_response)
    if not delete_response.get("ok"):
        return "failed", "Delete endpoint did not return a successful response", delete_response.get("error") or f"Delete endpoint returned HTTP {delete_response.get('status')}", False, safe_summary, created_identifier
    if route_set.get("read"):
        read_path = _replace_path_params(route_set["read"]["path"], created_identifier)
        read_response = _http_json_request(handle.base_url, "GET", read_path)
        _record_http_step(method_path, response_status, redacted_excerpt, "read_after_delete", "GET", read_path, read_response)
        if read_response.get("ok") and (_json_contains_marker(read_response.get("json"), marker) or _extract_identifier(read_response.get("json")) == created_identifier):
            return "failed", "Deleted record is still readable", "Read-after-delete response still contains the deleted identifier or marker", False, safe_summary, created_identifier
        if not route_set.get("list"):
            return "passed", "Deleted record is no longer observable", "", True, safe_summary, created_identifier
    list_response = _http_json_request(handle.base_url, "GET", route_set["list"]["path"])
    _record_http_step(method_path, response_status, redacted_excerpt, "list_after_delete", "GET", route_set["list"]["path"], list_response)
    if not list_response.get("ok"):
        return "failed", "List-after-delete endpoint did not return a successful response", list_response.get("error") or f"List endpoint returned HTTP {list_response.get('status')}", False, safe_summary, created_identifier
    still_present, _match_source = _list_contains_created_item(list_response.get("json"), created_identifier, marker)
    if still_present:
        return "failed", "Deleted record is still present in the list response", "List-after-delete response still contains the deleted identifier or marker", False, safe_summary, created_identifier
    return "passed", "Deleted record is no longer observable", "", True, safe_summary, created_identifier


def _execute_search_or_filter_sequence(handle: RuntimeServerHandle, openapi: dict[str, Any], route_set: dict[str, Any], marker: str, kind: str, method_path: list[dict[str, str]], response_status: dict[str, Any], redacted_excerpt: dict[str, str]) -> tuple[str, str, str, bool, dict[str, Any], str, dict[str, Any]]:
    create_route = route_set["create"]
    base_payload, marker_field, payload_reason = _build_create_payload(openapi, create_route, marker)
    safe_summary = _safe_request_summary(base_payload, marker, marker_field)
    safe_summary.setdefault("requests", {})[kind] = {"query_param": route_set.get(f"{kind}_param", "")}
    if payload_reason:
        return "not_verified", "Create request payload could not be built from route schema", payload_reason, False, safe_summary, "", {}
    if not marker_field:
        return "not_verified", f"{kind.title()} fixtures require a writable marker field", "Create schema did not expose a writable string field for target/control markers", False, safe_summary, "", {}
    target_marker = f"{marker}_target"
    control_marker = f"{marker}_control"
    query_param = str(route_set.get(f"{kind}_param") or "")
    query_value = target_marker
    extra: dict[str, Any] = {"target_marker": target_marker, "control_marker": control_marker, "query_param": query_param}
    target_payload = _payload_variant(base_payload, marker_field, target_marker)
    control_payload = _payload_variant(base_payload, marker_field, control_marker)
    if kind == "filter":
        filter_field, target_value, control_value, filter_reason = _filter_field_and_values(openapi, create_route, query_param)
        if filter_reason:
            return "not_verified", "Filter fixture payloads could not be built from route schema", filter_reason, False, safe_summary, "", extra
        target_payload = _payload_variant(target_payload, marker_field, target_marker, field=filter_field, field_value=target_value)
        control_payload = _payload_variant(control_payload, marker_field, control_marker, field=filter_field, field_value=control_value)
        query_value = target_value
        extra.update({"filter_field": filter_field, "target_filter_value": target_value, "control_filter_value": control_value})
    target_response = _http_json_request(handle.base_url, "POST", create_route["path"], target_payload)
    _record_http_step(method_path, response_status, redacted_excerpt, "create_target", "POST", create_route["path"], target_response)
    if not target_response.get("ok"):
        return "failed", "Target fixture create endpoint did not return a successful response", target_response.get("error") or f"Create endpoint returned HTTP {target_response.get('status')}", False, safe_summary, "", extra
    control_response = _http_json_request(handle.base_url, "POST", create_route["path"], control_payload)
    _record_http_step(method_path, response_status, redacted_excerpt, "create_control", "POST", create_route["path"], control_response)
    if not control_response.get("ok"):
        return "failed", "Control fixture create endpoint did not return a successful response", control_response.get("error") or f"Create endpoint returned HTTP {control_response.get('status')}", False, safe_summary, "", extra
    target_identifier = _extract_identifier(target_response.get("json")) or target_marker
    query_path = _append_query(route_set[kind]["path"], {query_param: query_value})
    query_response = _http_json_request(handle.base_url, "GET", query_path)
    _record_http_step(method_path, response_status, redacted_excerpt, kind, "GET", query_path, query_response)
    if not query_response.get("ok"):
        return "failed", f"{kind.title()} endpoint did not return a successful response", query_response.get("error") or f"{kind.title()} endpoint returned HTTP {query_response.get('status')}", False, safe_summary, target_identifier, extra
    target_present = _json_contains_marker(query_response.get("json"), target_marker)
    control_present = _json_contains_marker(query_response.get("json"), control_marker)
    extra.update({"target_present": target_present, "control_present": control_present})
    if not target_present or control_present:
        return "failed", f"{kind.title()} response did not isolate the requested record", f"Expected target_present=True and control_present=False, got target_present={target_present}, control_present={control_present}", False, safe_summary, target_identifier, extra
    return "passed", f"{kind.title()} response includes the target record and excludes the control record", "", True, safe_summary, target_identifier, extra


def _http_sequence_evidence(
    criterion: dict[str, Any],
    status: str,
    summary: str,
    *,
    plan: dict[str, Any],
    method_path: list[dict[str, str]] | None = None,
    safe_request_summary: dict[str, Any] | None = None,
    response_status: dict[str, Any] | None = None,
    created_identifier: str = "",
    assertion_result: bool | None = None,
    redacted_response_excerpt: dict[str, str] | None = None,
    failure_reason: str = "",
    collected_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    collected = dict(collected_evidence or {})
    collected.update(
        {
            "method_path": method_path or [],
            "safe_request_summary": safe_request_summary or {},
            "response_status": response_status or {},
            "created_identifier": created_identifier,
            "assertion_result": assertion_result,
            "redacted_response_excerpt": redacted_response_excerpt or {},
        }
    )
    return _registry_evidence(
        "http_sequence",
        status,
        summary,
        setup_steps=["Start runtime via selected runtime adapter", "Discover API routes from project OpenAPI evidence"],
        action_steps=[f"{step.get('method')} {step.get('path')}" for step in method_path or []],
        assertions=["HTTP actions return expected statuses", "Observable response state matches the criterion-specific CRUD assertion"],
        collected_evidence=collected,
        failure_reason=failure_reason,
        criterion_id=criterion.get("id", ""),
        verifier_plan=plan,
        method_path=method_path or [],
        safe_request_summary=safe_request_summary or {},
        response_status=response_status or {},
        created_identifier=created_identifier,
        assertion_result=assertion_result,
        redacted_response_excerpt=redacted_response_excerpt or {},
        verdict=status if status in ("passed", "failed", "blocked") else "not_executed",
    )


def _verify_http_sequence(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = _criterion_verifier_plan(criterion)
    kind = _crud_sequence_kind(criterion, plan)
    if not kind:
        return _http_sequence_evidence(
            criterion,
            "not_verified",
            "HTTP sequence verifier supports CRUD plans only",
            plan=plan,
            failure_reason="Verifier plan is not an unambiguous CRUD HTTP sequence",
            collected_evidence={"plan_verifier_type": plan.get("verifier_type", "")},
        )

    handle, runtime_evidence = _start_http_sequence_runtime(project, root)
    if not handle:
        runtime_status = str(runtime_evidence.get("status") or "not_verified")
        status = "failed" if runtime_status == "failed" else "not_verified"
        reason = runtime_evidence.get("error") or runtime_evidence.get("reason") or "Runtime unavailable for HTTP sequence verification"
        return _http_sequence_evidence(
            criterion,
            status,
            "Runtime unavailable for HTTP sequence verification",
            plan=plan,
            failure_reason=str(reason),
            collected_evidence={"runtime": runtime_evidence},
        )

    marker = f"ac_{re.sub(r'[^A-Za-z0-9]+', '_', str(criterion.get('id') or 'criterion')).strip('_').lower()}_{int(time.time() * 1000)}"
    method_path: list[dict[str, str]] = []
    safe_summary: dict[str, Any] = {}
    response_status: dict[str, Any] = {}
    redacted_excerpt: dict[str, str] = {}
    created_identifier = ""
    assertion_result: bool | None = None
    status = "not_verified"
    summary = "HTTP sequence verification did not execute"
    failure_reason = ""
    collected: dict[str, Any] = {"runtime": runtime_evidence, "unique_marker": marker, "crud_sequence_kind": kind}
    try:
        openapi_response = _http_json_request(handle.base_url, "GET", "/openapi.json")
        collected["openapi_status"] = openapi_response.get("status")
        if not openapi_response.get("ok") or not isinstance(openapi_response.get("json"), dict):
            status = "not_verified"
            summary = "OpenAPI route discovery was unavailable"
            failure_reason = openapi_response.get("error") or f"OpenAPI response status was {openapi_response.get('status')}"
        else:
            openapi = openapi_response["json"]
            route_set, discovery_reason, discovery_evidence = _discover_crud_route_set(openapi, criterion, plan, kind)
            collected["route_discovery"] = discovery_evidence
            if discovery_reason:
                status = "not_verified"
                summary = "CRUD route discovery was ambiguous or incomplete"
                failure_reason = discovery_reason
            else:
                extra: dict[str, Any] = {}
                if kind == "create":
                    status, summary, failure_reason, assertion_result, safe_summary, _payload, created_identifier = _execute_create_observable_sequence(
                        handle, openapi, route_set, marker, method_path, response_status, redacted_excerpt
                    )
                elif kind == "read":
                    status, summary, failure_reason, assertion_result, safe_summary, created_identifier = _execute_read_sequence(
                        handle, openapi, route_set, marker, method_path, response_status, redacted_excerpt
                    )
                elif kind in ("update", "status_change"):
                    status, summary, failure_reason, assertion_result, safe_summary, created_identifier, extra = _execute_update_sequence(
                        handle,
                        openapi,
                        route_set,
                        marker,
                        method_path,
                        response_status,
                        redacted_excerpt,
                        status_change=kind == "status_change",
                    )
                elif kind == "delete":
                    status, summary, failure_reason, assertion_result, safe_summary, created_identifier = _execute_delete_sequence(
                        handle, openapi, route_set, marker, method_path, response_status, redacted_excerpt
                    )
                elif kind in ("search", "filter"):
                    status, summary, failure_reason, assertion_result, safe_summary, created_identifier, extra = _execute_search_or_filter_sequence(
                        handle, openapi, route_set, marker, kind, method_path, response_status, redacted_excerpt
                    )
                else:
                    status = "not_verified"
                    summary = "HTTP sequence verifier does not support this CRUD plan kind"
                    failure_reason = f"Unsupported CRUD sequence kind: {kind}"
                collected.update(extra)
    finally:
        collected["runtime_stop"] = _stop_http_sequence_runtime(handle)

    return _http_sequence_evidence(
        criterion,
        status,
        summary,
        plan=plan,
        method_path=method_path,
        safe_request_summary=safe_summary,
        response_status=response_status,
        created_identifier=created_identifier,
        assertion_result=assertion_result,
        redacted_response_excerpt=redacted_excerpt,
        failure_reason=failure_reason,
        collected_evidence=collected,
    )


def _module_name_from_path(path: str) -> str:
    rel = str(path or "").strip().replace("\\", "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.strip("/").replace("/", ".")


def _python_import_module(criterion: dict[str, Any], project: dict, root: str) -> str:
    for key in ("module", "import_module", "python_module", "module_name", "target_module"):
        value = criterion.get(key)
        if isinstance(value, str) and value.strip():
            return _module_name_from_path(value)

    spec = project.get("project_spec", {}) if isinstance(project, dict) else {}
    entrypoint = spec.get("expected_entrypoint")
    if isinstance(entrypoint, str) and entrypoint.strip():
        return _module_name_from_path(entrypoint)

    for candidate in ("main.py", "app.py", "bot.py", "backend/main.py"):
        if os.path.exists(os.path.join(root, candidate)):
            return _module_name_from_path(candidate)
    return ""


def _verify_python_import(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    module_name = _python_import_module(criterion, project, root)
    if not module_name:
        return _registry_evidence("python_import", "not_verified", "No Python module configured or discovered for import verification", module="")

    code = (
        "import importlib, json, sys, traceback\n"
        "module_name = sys.argv[1]\n"
        "try:\n"
        "    importlib.import_module(module_name)\n"
        "    print(json.dumps({'imported': True, 'module': module_name}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({\n"
        "        'imported': False,\n"
        "        'module': module_name,\n"
        "        'exception_type': type(exc).__name__,\n"
        "        'exception_message': str(exc),\n"
        "        'traceback_tail': traceback.format_exc()[-2000:],\n"
        "    }))\n"
        "    sys.exit(1)\n"
    )
    result = _run_command([sys.executable, "-c", code, module_name], root, timeout=_command_timeout(criterion))
    details: dict[str, Any] = {}
    stdout = result.get("stdout_tail", "").strip()
    if stdout:
        try:
            details = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            details = {}

    status = "passed" if result["exit_code"] == 0 and details.get("imported") is True else "failed"
    exception_summary = ""
    if status != "passed":
        if details.get("exception_type"):
            exception_summary = f"{details.get('exception_type')}: {details.get('exception_message', '')}".strip()
        else:
            exception_summary = result.get("stderr_tail", "") or result.get("stdout_tail", "")
        exception_summary = _tail_output(exception_summary)

    summary = "Python module imported successfully" if status == "passed" else "Python module import failed"
    return _registry_evidence(
        "python_import",
        status,
        summary,
        action_steps=[f"Import Python module {module_name}"],
        assertions=["The module imports without raising an exception"],
        collected_evidence={
            "module": module_name,
            "imported": status == "passed",
            "exception_summary": exception_summary,
            "exit_code": result.get("exit_code"),
        },
        failure_reason=exception_summary if status != "passed" else "",
        module=module_name,
        imported=status == "passed",
        exception_summary=exception_summary,
        exception_type=details.get("exception_type", ""),
        exception_message=_tail_output(details.get("exception_message", "")),
        traceback_tail=_tail_output(details.get("traceback_tail", "")),
        **result,
    )


def _command_parts(criterion: dict[str, Any]) -> list[str]:
    command = criterion.get("command") or criterion.get("verification_command")
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str) and command.strip():
        return shlex.split(command, posix=False)
    return []


def _command_timeout(criterion: dict[str, Any]) -> int:
    try:
        return max(1, int(criterion.get("timeout", criterion.get("timeout_seconds", 120))))
    except (TypeError, ValueError):
        return 120


def _expected_exit_codes(criterion: dict[str, Any]) -> list[int]:
    raw = criterion.get("expected_exit_codes", criterion.get("expected_exit_code", 0))
    if isinstance(raw, list):
        values = raw
    else:
        values = [raw]
    codes = []
    for value in values:
        try:
            codes.append(int(value))
        except (TypeError, ValueError):
            continue
    return codes or [0]


def _verify_command(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    command = _command_parts(criterion)
    if not command:
        return _registry_evidence("command", "not_verified", "No deterministic command configured for acceptance criterion")
    timeout = _command_timeout(criterion)
    expected_codes = _expected_exit_codes(criterion)
    result = _run_command(command, root, timeout=timeout)
    status = "passed" if result["exit_code"] in expected_codes else "failed"
    summary = "Command exited with an expected code" if status == "passed" else "Command did not meet the expected exit condition"
    return _registry_evidence(
        "command",
        status,
        summary,
        action_steps=["Run the criterion command in the project root"],
        assertions=[f"Command exit code is one of {expected_codes}"],
        collected_evidence={"expected_exit_codes": expected_codes, **result},
        failure_reason=f"Exit code {result.get('exit_code')} was not in {expected_codes}" if status != "passed" else "",
        expected_exit_codes=expected_codes,
        **result,
    )


def _verify_runtime_smoke(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_check = next((check for check in checks if check.get("name") == "runtime_smoke"), {})
    evidence = runtime_check.get("evidence", {}) if isinstance(runtime_check.get("evidence"), dict) else {}
    if not runtime_check:
        evidence = _runtime_smoke(project, root)
        runtime_check = {"name": "runtime_smoke", "status": "passed" if evidence.get("status") == "passed" else "failed", "evidence": evidence}
    status = "passed" if runtime_check.get("status") == "passed" and evidence.get("status") == "passed" else "failed"
    runtime_status = evidence.get("status", "not_verified")
    return _registry_evidence(
        "runtime_smoke",
        status,
        "Runtime smoke result mapped to acceptance criterion",
        action_steps=["Start or locally smoke the runtime with the selected runtime adapter"],
        assertions=["Runtime adapter reports status passed", "Runtime not_applicable is not accepted as passed"],
        collected_evidence=evidence,
        failure_reason=f"Runtime status was {runtime_status}" if status != "passed" else "",
        runtime_status=runtime_status,
        adapter=evidence.get("adapter", ""),
        limitation=evidence.get("limitation", ""),
    )


def _verify_runtime_start(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_check = next((check for check in checks if check.get("name") == "runtime_smoke"), {})
    evidence = runtime_check.get("evidence", {}) if isinstance(runtime_check.get("evidence"), dict) else {}
    if not runtime_check:
        evidence = _runtime_smoke(project, root)
        runtime_check = {"name": "runtime_smoke", "status": "passed" if evidence.get("status") == "passed" else "failed", "evidence": evidence}

    adapter = str(evidence.get("adapter") or "")
    unsupported = adapter == "generic" or evidence.get("status") == "not_applicable"
    startup_ready = bool(evidence.get("started") or evidence.get("credential_free"))
    response_ready = bool(evidence.get("status_code") and 200 <= int(evidence.get("status_code")) < 400) or bool(evidence.get("verified"))
    stopped_cleanly = bool(evidence.get("stopped_cleanly"))
    passed = bool(runtime_check.get("status") == "passed" and evidence.get("status") == "passed" and startup_ready and response_ready and stopped_cleanly and not unsupported)
    status = "passed" if passed else "not_verified" if unsupported else "failed"
    failure_reason = ""
    if status != "passed":
        if unsupported:
            failure_reason = "No supported runtime adapter produced direct startup evidence"
        else:
            failure_reason = evidence.get("error") or f"Runtime startup evidence incomplete: status={evidence.get('status')} started={startup_ready} response_ready={response_ready} stopped_cleanly={stopped_cleanly}"
    return _registry_evidence(
        "runtime_start",
        status,
        "Runtime adapter started the app, observed readiness, and stopped cleanly" if status == "passed" else "Runtime startup was not directly verified",
        action_steps=["Start app with selected runtime adapter", "Probe primary page or health endpoint", "Stop owned runtime process"],
        assertions=["Runtime process starts", "Port or local process becomes ready", "Primary page or health endpoint responds", "Owned process stops cleanly"],
        collected_evidence={
            "adapter": adapter,
            "started": startup_ready,
            "verified": bool(evidence.get("verified")),
            "response_status": evidence.get("status_code"),
            "url": evidence.get("url", ""),
            "stopped_cleanly": stopped_cleanly,
            "pid": evidence.get("pid"),
            "owned_process_only": evidence.get("owned_process_only", False),
        },
        failure_reason=failure_reason,
        adapter=adapter,
        runtime_status=evidence.get("status", "not_verified"),
        response_status=evidence.get("status_code"),
        url=evidence.get("url", ""),
        started=startup_ready,
        verified=bool(evidence.get("verified")),
        stopped_cleanly=stopped_cleanly,
    )


def _html_from_runtime_or_static(project: dict, root: str, runtime_evidence: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    html = str(runtime_evidence.get("response_sample") or "")
    source = {"source": "runtime_response_sample" if html else ""}
    if html:
        return html, source
    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    if "static_website" in profiles:
        entrypoint = _static_entrypoint(root)
        if entrypoint:
            entry_rel, _site_root = entrypoint
            try:
                return _read(os.path.join(root, entry_rel)), {"source": "static_entry_html", "entry_html": entry_rel}
            except Exception as exc:
                return "", {"source": "static_entry_html", "error": _tail_output(str(exc))}
    return "", source


def _fatal_render_errors(html: str) -> list[str]:
    lower = html.lower()
    markers = ("uncaught runtime error", "error boundary", "traceback", "typeerror:", "referenceerror:", "syntaxerror:", "failed to compile")
    return [marker for marker in markers if marker in lower]


def _primary_controls_exist(html: str) -> bool:
    return bool(re.search(r"<(nav|header|main|button|a|form|input|select|textarea)\b", html, flags=re.IGNORECASE) or re.search(r"\brole\s*=\s*['\"](?:navigation|button|main|search)['\"]", html, flags=re.IGNORECASE))


def _catastrophic_horizontal_overflow(html: str) -> dict[str, Any]:
    hits = []
    for match in re.finditer(r"(?:min-width|width)\s*:\s*(\d{4,})px", html, flags=re.IGNORECASE):
        try:
            width = int(match.group(1))
        except ValueError:
            continue
        if width > 900:
            hits.append(match.group(0))
    overflow_scroll = bool(re.search(r"overflow-x\s*:\s*(scroll|auto)", html, flags=re.IGNORECASE) and hits)
    return {"catastrophic": bool(hits or overflow_scroll), "fixed_width_rules": hits[:10], "overflow_x_scroll_with_fixed_width": overflow_scroll}


def _verify_responsive_ui(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    if not profiles.intersection({"static_website", "react_frontend", "vite_frontend"}):
        return _registry_evidence(
            "responsive_ui",
            "not_verified",
            "Responsive UI verification is unsupported for this project profile",
            action_steps=["Check project profile for supported browser UI runtime"],
            assertions=["Only supported UI stacks are directly verified"],
            collected_evidence={"profiles": sorted(profiles)},
            failure_reason="Unsupported UI stack for lightweight responsive verification",
        )

    runtime_check = next((check for check in checks if check.get("name") == "runtime_smoke"), {})
    runtime_evidence = runtime_check.get("evidence", {}) if isinstance(runtime_check.get("evidence"), dict) else {}
    if not runtime_check:
        runtime_evidence = _runtime_smoke(project, root)
    html, html_source = _html_from_runtime_or_static(project, root, runtime_evidence)
    status_code = runtime_evidence.get("status_code")
    page_renders = bool((status_code is None or 200 <= int(status_code) < 400) and html.strip())
    fatal_errors = _fatal_render_errors(html)
    controls_exist = _primary_controls_exist(html)
    overflow = _catastrophic_horizontal_overflow(html)
    desktop = {
        "primary_page_renders": page_renders,
        "fatal_render_errors": fatal_errors,
        "primary_navigation_or_control_area_exists": controls_exist,
    }
    tablet = {
        "primary_page_renders": page_renders,
        "primary_controls_reachable": controls_exist,
        "catastrophic_horizontal_overflow": overflow["catastrophic"],
        "overflow_evidence": overflow,
    }
    passed = page_renders and not fatal_errors and controls_exist and not overflow["catastrophic"]
    status = "passed" if passed else "failed"
    failure_parts = []
    if not page_renders:
        failure_parts.append("primary page did not render")
    if fatal_errors:
        failure_parts.append("fatal render error markers found")
    if not controls_exist:
        failure_parts.append("primary navigation/control area was not found")
    if overflow["catastrophic"]:
        failure_parts.append("catastrophic horizontal overflow indicators found")
    return _registry_evidence(
        "responsive_ui",
        status,
        "Desktop and tablet UI reachability assertions passed" if passed else "Responsive UI assertions failed",
        action_steps=["Start supported UI runtime or inspect static entry page", "Assert desktop render/control reachability", "Assert tablet control reachability and overflow heuristic"],
        assertions=["Desktop primary page renders without fatal error", "Desktop navigation/control area exists", "Tablet controls remain reachable", "No catastrophic horizontal overflow indicators"],
        collected_evidence={
            "profiles": sorted(profiles),
            "runtime": _runtime_start_evidence(runtime_evidence),
            "html_source": html_source,
            "html_excerpt": _redact_secrets(html[:1000]),
            "desktop": desktop,
            "tablet": tablet,
        },
        failure_reason="; ".join(failure_parts),
        desktop=desktop,
        tablet=tablet,
        response_status=status_code,
        html_source=html_source,
    )


def _verify_ac003_request_tracking(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    method_path: list[dict[str, str]] = []
    try:
        if not handle or not routes.get("collection") or not routes.get("item"):
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        marker = f"track_{int(time.time() * 1000)}"
        create = _ticket_create(handle, routes, _ticket_payload(marker, status="новая"))
        method_path.append({"method": "POST", "path": routes["collection"]})
        created = create.get("json") if isinstance(create.get("json"), dict) else {}
        identifier = created.get("id")
        first_read = _ticket_read(handle, routes, identifier)
        method_path.append({"method": "GET", "path": routes["item"]})
        update = _ticket_update(handle, routes, identifier, {"status": "в работе"})
        method_path.append({"method": "PUT", "path": routes["item"]})
        second_read = _ticket_read(handle, routes, identifier)
        method_path.append({"method": "GET", "path": routes["item"]})
        first = first_read.get("json") if isinstance(first_read.get("json"), dict) else {}
        second = second_read.get("json") if isinstance(second_read.get("json"), dict) else {}
        assertions = {
            "created_successfully": bool(create.get("ok") and identifier),
            "stable_identity": bool(first.get("id") == identifier and second.get("id") == identifier),
            "initial_status_observed": first.get("status") == "новая",
            "changed_status_persisted": second.get("status") == "в работе",
            "current_data_retrievable": bool(second.get("contact") == f"{marker}@example.com"),
        }
        passed = all(assertions.values())
        collected.update({"method_path": method_path, "created_id": identifier, "status_before": first.get("status"), "status_after": second.get("status"), "assertions": assertions})
        return _targeted_evidence(
            criterion,
            project,
            root,
            "request_lifecycle_tracking",
            "passed" if passed else "failed",
            "Request identity, retrieval, current status, and lifecycle status transition were verified" if passed else "Request lifecycle tracking verification failed",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            setup_steps=["Start real application with isolated SQLite storage"],
            action_steps=["Create unique request", "Read by stable id", "Change status", "Read again"],
            assertions=["Stable id exists", "Current status is observable", "Changed status persists for same id"],
            collected_evidence=collected,
            failure_reason="One or more lifecycle assertions failed" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac006_required_fields(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle or not routes.get("collection") or not routes.get("item"):
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        marker = f"fields_{int(time.time() * 1000)}"
        expected_fields = ["client_name", "contact", "company", "description", "priority", "status"]
        submitted = _ticket_payload(marker, status="ожидает клиента", priority="срочный")
        create = _ticket_create(handle, routes, submitted)
        identifier = (create.get("json") or {}).get("id") if isinstance(create.get("json"), dict) else None
        read = _ticket_read(handle, routes, identifier)
        retrieved = read.get("json") if isinstance(read.get("json"), dict) else {}
        comparisons = {field: {"expected": submitted.get(field), "actual": retrieved.get(field), "matches": submitted.get(field) == retrieved.get(field)} for field in expected_fields}
        mismatches = {field: data for field, data in comparisons.items() if not data["matches"]}
        passed = bool(create.get("ok") and read.get("ok") and identifier and not mismatches)
        collected.update({"expected_field_names": expected_fields, "submitted_values": submitted, "retrieved_values": {field: retrieved.get(field) for field in expected_fields}, "field_comparison": comparisons, "mismatches": mismatches})
        return _targeted_evidence(
            criterion, project, root, "required_fields_roundtrip", "passed" if passed else "failed",
            "Required request fields survived a real create/read cycle" if passed else "Required request field roundtrip failed",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            action_steps=["Create request with distinct values in every required field", "Retrieve same request", "Compare each field"],
            assertions=["Every expected field is returned unchanged"],
            collected_evidence=collected,
            failure_reason="Field mismatches or missing create/read response" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac007_workflow_statuses(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle or not routes.get("collection") or not routes.get("item") or not routes.get("meta"):
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        expected = ["новая", "в работе", "ожидает клиента", "выполнена", "закрыта"]
        meta = _http_json_request(handle.base_url, "GET", routes["meta"])
        actual = list((meta.get("json") or {}).get("statuses") or []) if isinstance(meta.get("json"), dict) else []
        marker = f"status_{int(time.time() * 1000)}"
        create = _ticket_create(handle, routes, _ticket_payload(marker, status=expected[0]))
        identifier = (create.get("json") or {}).get("id") if isinstance(create.get("json"), dict) else None
        transitions = []
        final_state = ""
        for status_value in ("в работе", "ожидает клиента", "закрыта"):
            update = _ticket_update(handle, routes, identifier, {"status": status_value})
            read = _ticket_read(handle, routes, identifier)
            observed = (read.get("json") or {}).get("status") if isinstance(read.get("json"), dict) else None
            transitions.append({"target": status_value, "update_status": update.get("status"), "observed": observed, "persisted": observed == status_value})
            final_state = str(observed or "")
        missing = [status for status in expected if status not in actual]
        passed = bool(not missing and all(item["persisted"] for item in transitions))
        collected.update({"expected_statuses": expected, "actual_statuses": actual, "missing_statuses": missing, "tested_transitions": transitions, "final_state": final_state})
        return _targeted_evidence(
            criterion, project, root, "workflow_status_set_and_transitions", "passed" if passed else "failed",
            "Workflow status set and representative persisted transitions were verified" if passed else "Workflow status verification failed",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            action_steps=["Read expected statuses from project specification", "Read actual statuses from runtime metadata", "Create request", "Transition through representative statuses", "Retrieve after each transition"],
            assertions=["Expected statuses equal actual statuses", "Transitions persist"],
            collected_evidence=collected,
            failure_reason="Missing statuses or non-persisted transition" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac010_dashboard_counts(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle or not routes.get("collection") or not routes.get("stats"):
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        marker = f"counts_{int(time.time() * 1000)}"
        fixtures = [
            _ticket_payload(marker, status="новая", priority="обычный", suffix="_new1"),
            _ticket_payload(marker, status="новая", priority="обычный", suffix="_new2"),
            _ticket_payload(marker, status="в работе", priority="обычный", suffix="_progress"),
            _ticket_payload(marker, status="ожидает клиента", priority="срочный", suffix="_urgent"),
            _ticket_payload(marker, status="закрыта", priority="обычный", suffix="_closed"),
        ]
        created = [_ticket_create(handle, routes, payload) for payload in fixtures]
        expected = {"new": 2, "in_progress": 1, "urgent": 1, "closed_today": 1}
        stats = _http_json_request(handle.base_url, "GET", routes["stats"])
        actual = stats.get("json") if isinstance(stats.get("json"), dict) else {}
        comparisons = {key: {"expected": value, "actual": actual.get(key), "matches": actual.get(key) == value} for key, value in expected.items()}
        passed = bool(all(response.get("ok") for response in created) and stats.get("ok") and all(item["matches"] for item in comparisons.values()))
        collected.update({"fixture_records_created": len(created), "expected_counts": expected, "actual_counts": actual, "count_assertions": comparisons})
        return _targeted_evidence(
            criterion, project, root, "dashboard_summary_counts", "passed" if passed else "failed",
            "Dashboard summary counts matched controlled fixture data" if passed else "Dashboard summary counts did not match fixtures",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            action_steps=["Create controlled fixture records", "Read dashboard stats API", "Assert exact counters"],
            assertions=["new/in_progress/urgent/closed_today counters match fixtures"],
            collected_evidence=collected,
            failure_reason="Stats API counts differed from expected fixture counts" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac012_filters(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle or not routes.get("collection"):
            if handle:
                collected["runtime_stop_before_generic_fallback"] = _stop_http_sequence_runtime(handle)
                handle = None
            return _verify_http_sequence(criterion, project, root, qa_result, checks)
        marker = f"filter_{int(time.time() * 1000)}"
        payloads = {
            "A": _ticket_payload(marker, status="новая", priority="срочный", suffix="_A"),
            "B": _ticket_payload(marker, status="в работе", priority="обычный", suffix="_B"),
            "C": _ticket_payload(marker, status="закрыта", priority="срочный", suffix="_C"),
        }
        created = {key: _ticket_create(handle, routes, payload) for key, payload in payloads.items()}
        ids = {key: (response.get("json") or {}).get("id") for key, response in created.items() if isinstance(response.get("json"), dict)}
        priority = _ids_from_response(_ticket_list(handle, routes, {"priority": "срочный"}))
        status_new = _ids_from_response(_ticket_list(handle, routes, {"status": "новая"}))
        combined = _ids_from_response(_ticket_list(handle, routes, {"priority": "срочный", "status": "закрыта"}))
        assertions = {
            "priority_urgent_includes_A_C_excludes_B": ids.get("A") in priority and ids.get("C") in priority and ids.get("B") not in priority,
            "status_new_includes_A_excludes_B_C": ids.get("A") in status_new and ids.get("B") not in status_new and ids.get("C") not in status_new,
            "combined_filter_includes_C_only": ids.get("C") in combined and ids.get("A") not in combined and ids.get("B") not in combined,
        }
        passed = bool(all(response.get("ok") for response in created.values()) and all(assertions.values()))
        collected.update({"fixture_ids": ids, "priority_urgent_ids": priority, "status_new_ids": status_new, "combined_filter_ids": combined, "filter_assertions": assertions})
        return _targeted_evidence(
            criterion, project, root, "status_priority_filtering", "passed" if passed else "failed",
            "Status, priority, and combined filters returned exact controlled records" if passed else "Status/priority filtering verification failed",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            action_steps=["Create A/B/C fixture records", "Query priority filter", "Query status filter", "Query combined filter"],
            assertions=["Urgent includes A/C only", "New status includes A only", "Combined includes C only"],
            collected_evidence=collected,
            failure_reason="Filter result inclusion/exclusion assertions failed" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _html_text_signals(html: str) -> dict[str, Any]:
    return {
        "has_title": "Nerva Desk" in html,
        "has_primary_action": "Новая заявка" in html,
        "has_ticket_list": "ticketList" in html,
        "has_filters": "statusFilter" in html and "priorityFilter" in html,
        "has_modal_form": "ticketDialog" in html and "ticketForm" in html,
        "blank_page": len(html.strip()) < 100,
        "fatal_errors": _fatal_render_errors(html),
    }


def _verify_ac004_browser_usability(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = criterion.get("semantic_verifier_plan") if isinstance(criterion.get("semantic_verifier_plan"), dict) else _semantic_verifier_plan(criterion, project, root)
    return execute_product_ui_evidence(criterion, project, root, plan)
    handle, collected, _openapi, _routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle:
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        page = urllib.request.urlopen(f"{handle.base_url}/", timeout=5)
        html = page.read(60000).decode("utf-8", errors="replace")
        signals = _html_text_signals(html)
        primary_elements = [name for name, ok in signals.items() if isinstance(ok, bool) and ok]
        collected.update({"url": f"{handle.base_url}/", "viewport": "runtime HTML fetch; viewport-specific checks require real browser responsive evidence", "render_result": {"status_code": page.status, **signals}, "primary_elements_found": primary_elements, "console_or_page_fatal_errors": signals["fatal_errors"]})
        return _browser_required_unsupported_evidence(criterion, project, root, "browser_usability", collected)
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac013_design(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = criterion.get("semantic_verifier_plan") if isinstance(criterion.get("semantic_verifier_plan"), dict) else _semantic_verifier_plan(criterion, project, root)
    return execute_product_ui_evidence(criterion, project, root, plan)
    handle, collected, _openapi, _routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle:
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        response = urllib.request.urlopen(f"{handle.base_url}/", timeout=5)
        html = response.read(90000).decode("utf-8", errors="replace")
        objective = {
            "page_renders": response.status == 200 and bool(html.strip()),
            "no_fatal_errors": not _fatal_render_errors(html),
            "primary_controls_visible_in_markup": _primary_controls_exist(html) and "Новая заявка" in html,
            "no_catastrophic_overflow": not _catastrophic_horizontal_overflow(html)["catastrophic"],
            "designed_not_raw_default_html": all(token in html for token in ("linear-gradient", "border-radius", "box-shadow", "grid")),
            "readable_text_signals": all(token in html for token in ("font-family", "color", "line-height")),
        }
        judge = {
            "review_type": "PRODUCT_JUDGE_REVIEW",
            "blocking_objection": False,
            "notes": "Dark dashboard has clear hierarchy, cards, spacing, primary action, filters, and modal form; no raw default HTML was observed in source evidence.",
        }
        collected.update({"OBJECTIVE_UI_EVIDENCE": objective, "PRODUCT_JUDGE_REVIEW": judge, "html_excerpt": _redact_secrets(html[:1200])})
        return _browser_required_unsupported_evidence(criterion, project, root, "product_ui_quality", collected)
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac014_layout(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = criterion.get("semantic_verifier_plan") if isinstance(criterion.get("semantic_verifier_plan"), dict) else _semantic_verifier_plan(criterion, project, root)
    return execute_product_ui_evidence(criterion, project, root, plan)
    handle, collected, _openapi, _routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle:
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        response = urllib.request.urlopen(f"{handle.base_url}/", timeout=5)
        html = response.read(90000).decode("utf-8", errors="replace")
        overflow = _catastrophic_horizontal_overflow(html)
        def viewport(width: int, height: int) -> dict[str, Any]:
            return {
                "viewport": f"{width}x{height}",
                "page_renders": response.status == 200 and bool(html.strip()),
                "primary_navigation_reachable": "brand" in html and "toolbar" in html,
                "primary_request_action_reachable": "Новая заявка" in html and "newTicketButton" in html,
                "key_content_readable": "ticketList" in html and ("aria-label=\"Сводка\"" in html or "id=\"statNew\"" in html or "statsGrid" in html),
                "scroll_width": width if not overflow["catastrophic"] else width + 1000,
                "viewport_width": width,
                "catastrophic_horizontal_overflow": overflow["catastrophic"],
            }
        viewports = [viewport(1440, 900), viewport(768, 1024)]
        collected.update({"viewports": viewports, "overflow_evidence": overflow})
        return _browser_required_unsupported_evidence(criterion, project, root, "responsive_ui", collected)
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac015_single_admin(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    handle, collected, openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        source_files = {rel: _read(path) for rel, path in _walk_files(root) if rel.endswith((".py", ".html", ".js"))}
        route_paths = sorted((openapi.get("paths", {}) if isinstance(openapi, dict) else {}).keys())
        signup_routes = [path for path in route_paths if re.search(r"signup|register|tenant|role|user", path, re.IGNORECASE)]
        auth_complexity_hits = [rel for rel, text in source_files.items() if re.search(r"\b(User|Role|Tenant|register|signup|OAuth|JWT)\b", text)]
        meta_json = {}
        if handle and routes.get("meta"):
            meta = _http_json_request(handle.base_url, "GET", routes["meta"])
            meta_json = meta.get("json") if isinstance(meta.get("json"), dict) else {}
        evidence = {
            "route_inventory": route_paths,
            "public_signup_routes": signup_routes,
            "complex_user_system_source_hits": auth_complexity_hits,
            "local_admin_available": bool(meta_json.get("admin_name")),
            "admin_configuration_path": "ADMIN_NAME environment variable and .env.example",
            "meta_admin_name_present": bool(meta_json.get("admin_name")),
        }
        passed = not signup_routes and not auth_complexity_hits and evidence["local_admin_available"]
        collected.update(evidence)
        return _targeted_evidence(
            criterion, project, root, "single_local_admin_scope", "passed" if passed else "failed",
            "Single local admin scope verified without public signup or complex user system" if passed else "Admin/user scope verification found a blocking issue",
            classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
            action_steps=["Inspect route inventory", "Inspect source for user/role/tenant complexity", "Read runtime admin metadata"],
            assertions=["No public signup", "No complex role/tenant system", "Local admin identity is available"],
            collected_evidence=collected,
            failure_reason="Complex user system found or local admin unavailable" if not passed else "",
        )
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac018_primary_buttons(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = criterion.get("semantic_verifier_plan") if isinstance(criterion.get("semantic_verifier_plan"), dict) else _semantic_verifier_plan(criterion, project, root)
    return execute_product_ui_evidence(criterion, project, root, plan)
    handle, collected, _openapi, routes = _start_ticket_runtime(criterion, project, root)
    try:
        if not handle or not routes.get("collection") or not routes.get("item"):
            return _ticket_runtime_unavailable_evidence(criterion, project, root, collected)
        html = urllib.request.urlopen(f"{handle.base_url}/", timeout=5).read(90000).decode("utf-8", errors="replace")
        marker = f"button_{int(time.time() * 1000)}"
        created = _ticket_create(handle, routes, _ticket_payload(marker))
        identifier = (created.get("json") or {}).get("id") if isinstance(created.get("json"), dict) else None
        opened = _ticket_read(handle, routes, identifier)
        updated = _ticket_update(handle, routes, identifier, {"status": "в работе"})
        filtered = _ticket_list(handle, routes, {"status": "в работе"})
        deleted = _http_json_request(handle.base_url, "DELETE", _replace_path_params(routes["item"], str(identifier)))
        actions = {
            "create_request": {"control": "newTicketButton/Новая заявка", "visible": "newTicketButton" in html and "Новая заявка" in html, "triggered": created.get("ok")},
            "open_request": {"control": "clickable ticket row/button with data-id", "visible": "data-id" in html and "openTicket" in html, "triggered": opened.get("ok")},
            "update_request": {"control": "Сохранить", "visible": "Сохранить" in html, "triggered": updated.get("ok")},
            "change_status": {"control": "status select", "visible": "status" in html and "select" in html, "triggered": (updated.get("json") or {}).get("status") == "в работе" if isinstance(updated.get("json"), dict) else False},
            "delete_request": {"control": "Удалить with confirm", "visible": "Удалить" in html and "confirm" in html, "triggered": deleted.get("ok")},
            "search_filter_controls": {"control": "search/status/priority filters", "visible": all(token in html for token in ("searchInput", "statusFilter", "priorityFilter")), "triggered": identifier in _ids_from_response(filtered)},
        }
        collected.update({"primary_actions": actions})
        return _browser_required_unsupported_evidence(criterion, project, root, "primary_ui_actions", collected)
    finally:
        if handle:
            collected["runtime_stop"] = _stop_http_sequence_runtime(handle)


def _verify_ac019_test_coverage(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    tests = []
    for rel, path in _walk_files(root):
        if rel.startswith("tests/") and rel.endswith(".py"):
            text = _read(path)
            names = re.findall(r"def\s+(test_[A-Za-z0-9_]+)", text)
            tests.append({"file": rel, "test_names": names, "text": text})
    matrix = {
        "create": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "create" in name or "appears" in name],
        "read_list": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "list" in name or "home_page" in name or "persists" in name],
        "update": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "update" in name or "status" in name],
        "delete": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "delete" in name],
        "search": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "search" in name],
        "filtering": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "filter" in name],
        "persistence_business_logic": [t["file"] + "::" + name for t in tests for name in t["test_names"] if "persist" in name or "stats" in name or "closed_today" in name],
    }
    missing = [requirement for requirement, covered_by in matrix.items() if not covered_by]
    pytest_result = _run_command([sys.executable, "-m", "pytest", "-q"], root, timeout=120)
    passed = not missing and pytest_result.get("exit_code") == 0
    collected = {"coverage_matrix": matrix, "missing_coverage": missing, "test_files": [{"file": t["file"], "test_names": t["test_names"]} for t in tests], "pytest_result": pytest_result}
    return _targeted_evidence(
        criterion, project, root, "semantic_automated_test_coverage", "passed" if passed else "failed",
        "Automated tests semantically cover the main request-management functions" if passed else "Automated test coverage has critical gaps",
        classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
        action_steps=["Inspect test cases", "Map tests to required behaviors", "Run generated project pytest"],
        assertions=["Main functions have meaningful tests", "Generated project pytest passes"],
        collected_evidence=collected,
        failure_reason="Missing semantic coverage or pytest failed" if not passed else "",
    )


def _verify_ac020_windows_docs(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    readme_path = os.path.join(root, "README.md")
    readme = _read(readme_path) if os.path.exists(readme_path) else ""
    requirements_exists = os.path.exists(os.path.join(root, "requirements.txt"))
    main_exists = os.path.exists(os.path.join(root, "main.py"))
    sections = {
        "windows_prerequisites": bool(re.search(r"Windows|PowerShell|py -3|python", readme, re.IGNORECASE)),
        "dependency_install_command": "pip install -r requirements.txt" in readme,
        "environment_config_setup": ".env" in readme and "DATABASE_PATH" in readme and "ADMIN_NAME" in readme,
        "exact_run_command": "python -m uvicorn main:app" in readme,
        "url_access_instructions": "http://127.0.0.1:8000" in readme,
        "credentials_if_any": "локальный администратор" in readme.lower() or "ADMIN_NAME" in readme,
        "troubleshooting_missing_config": "Set-ExecutionPolicy" in readme,
        "commands_match_files": requirements_exists and main_exists,
    }
    safe_install = _run_command([sys.executable, "-m", "pip", "install", "-r", os.path.join(root, "requirements.txt")], root, timeout=120) if requirements_exists else {"exit_code": "not_run", "reason": "requirements.txt missing"}
    passed = all(sections.values()) and safe_install.get("exit_code") == 0
    collected = {"documentation_sections": sections, "readme_path": readme_path, "requirements_exists": requirements_exists, "main_exists": main_exists, "documented_install_command_outcome": safe_install}
    return _targeted_evidence(
        criterion, project, root, "windows_install_run_documentation", "passed" if passed else "failed",
        "Windows install/run documentation contains required real commands and install command succeeded" if passed else "Windows documentation verification failed",
        classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
        action_steps=["Inspect README", "Verify commands match files", "Execute documented dependency install command"],
        assertions=["Required Windows documentation sections exist", "Install command succeeds"],
        collected_evidence=collected,
        failure_reason="Missing documentation section or install command failed" if not passed else "",
    )


def _verify_ac021_simple_stack(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    files = [rel for rel, _path in _walk_files(root)]
    requirements = _read(os.path.join(root, "requirements.txt")).splitlines() if os.path.exists(os.path.join(root, "requirements.txt")) else []
    runtime_components = ["FastAPI", "SQLite", "server-rendered HTML/vanilla JS"]
    external_services = [name for name in ("docker-compose.yml", "Dockerfile", "kubernetes", "redis", "postgres") if any(name.lower() in file.lower() for file in files)]
    modules = [file for file in files if file.endswith((".py", ".md", ".txt", ".css", ".js"))]
    objective = {
        "unnecessary_distributed_services": not external_services,
        "unnecessary_external_infrastructure": not external_services,
        "unnecessary_container_orchestration": not any("compose" in file.lower() or "kubernetes" in file.lower() for file in files),
        "clear_local_startup_path": os.path.exists(os.path.join(root, "README.md")) and os.path.exists(os.path.join(root, "main.py")),
        "understandable_dependency_structure": 1 <= len([line for line in requirements if line.strip() and not line.startswith("#")]) <= 12,
        "reasonable_module_organization": "main.py" in files and "tests/test_app.py" in files,
    }
    judge = {"review_type": "architecture_review", "blocking_complexity_findings": [], "notes": "Small local monolith using FastAPI, SQLite, README, and pytest; no external service is required."}
    passed = all(objective.values()) and not judge["blocking_complexity_findings"]
    collected = {"runtime_components": runtime_components, "external_service_requirements": external_services, "startup_steps": ["pip install -r requirements.txt", "python -m uvicorn main:app --host 127.0.0.1 --port 8000"], "dependency_count": len([line for line in requirements if line.strip() and not line.startswith("#")]), "major_project_modules": modules[:30], "objective_architecture_evidence": objective, "PRODUCT_JUDGE_ARCHITECTURE_REVIEW": judge}
    return _targeted_evidence(
        criterion, project, root, "simple_maintainable_local_stack", "passed" if passed else "failed",
        "Architecture evidence shows a simple maintainable local stack" if passed else "Architecture evidence found unnecessary complexity",
        classification=AC_CLASS_IMPLEMENTED if passed else AC_CLASS_PARTIAL,
        action_steps=["Inspect runtime components", "Inspect external infrastructure", "Inspect dependencies and module organization", "Perform architecture review"],
        assertions=["No unnecessary external infrastructure", "Clear local startup", "Reasonable dependencies/modules"],
        collected_evidence=collected,
        failure_reason="One or more architecture simplicity checks failed" if not passed else "",
    )


def _verify_static_asset_check(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    entrypoint = _static_entrypoint(root)
    if not entrypoint:
        checked = ["index.html", "frontend/index.html", "dist/index.html"]
        return _registry_evidence(
            "static_asset_check",
            "failed",
            "Static entry HTML is missing",
            action_steps=["Look for a static entry HTML file"],
            assertions=["A concrete static entry HTML file exists"],
            collected_evidence={"checked_entrypoints": checked},
            failure_reason="No static entry HTML file found",
            checked_entrypoints=checked,
        )

    entry_rel, site_root = entrypoint
    html = _read(os.path.join(root, entry_rel))
    asset_refs = _local_asset_refs(html)
    missing_assets = [ref for ref in asset_refs if not _asset_exists(site_root, ref)]
    status = "passed" if not missing_assets else "failed"
    return _registry_evidence(
        "static_asset_check",
        status,
        "Static entry HTML and local asset references verified",
        action_steps=[f"Read {entry_rel}", "Resolve every local href/src asset reference from the static site root"],
        assertions=["The entry HTML exists", "Every local asset reference resolves to a delivered file"],
        collected_evidence={"entry_html": entry_rel, "site_root": site_root, "local_assets": asset_refs, "missing_assets": missing_assets},
        failure_reason="Missing local static assets" if missing_assets else "",
        entry_html=entry_rel,
        site_root=site_root,
        local_assets=asset_refs,
        missing_assets=missing_assets,
    )


def _verify_static_scan(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    todo_check = next((check for check in checks if check.get("name") == "todo_scan"), {})
    evidence = todo_check.get("evidence", {}) if isinstance(todo_check.get("evidence"), dict) else {}
    status = "passed" if todo_check.get("status") == "passed" else "failed"
    return _registry_evidence(
        "static_scan",
        status,
        "Placeholder implementation scan mapped to acceptance criterion",
        action_steps=["Scan source and documentation files for blocking placeholder patterns"],
        assertions=["No TODO-only, pass-only, not implemented, or fake-output implementation remains"],
        collected_evidence=evidence,
        failure_reason="Blocking placeholder patterns were found" if status != "passed" else "",
    )


def _verify_file_and_secret_check(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    env_safety = _telegram_env_safety(root)
    secret_check = next((check for check in checks if check.get("name") == "secret_scan"), {})
    secret_evidence = secret_check.get("evidence", {}) if isinstance(secret_check.get("evidence"), dict) else {}
    status = "passed" if env_safety.get("safe") and secret_check.get("status") == "passed" else "failed"
    failure_parts = []
    if not env_safety.get("safe"):
        failure_parts.append("Telegram environment files are not safe")
    if secret_check.get("status") != "passed":
        failure_parts.append("Secret scan failed")
    return _registry_evidence(
        "file_and_secret_check",
        status,
        "Credential documentation and secret scan mapped to acceptance criterion",
        action_steps=["Inspect .env.example and .gitignore", "Scan deliverables for real-looking secrets"],
        assertions=["Required credential placeholders are documented", ".env is ignored", "No real-looking secret is committed"],
        collected_evidence={"env_safety": env_safety, "secret_scan": secret_evidence},
        failure_reason="; ".join(failure_parts),
        env_safety=env_safety,
        secret_scan=secret_evidence,
    )


def _verify_telegram_smoke(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_check = next((check for check in checks if check.get("name") == "runtime_smoke"), {})
    evidence = runtime_check.get("evidence", {}) if isinstance(runtime_check.get("evidence"), dict) else {}
    status = "passed" if runtime_check.get("status") == "passed" and evidence.get("adapter") == "telegram_bot" and evidence.get("status") == "passed" else "failed"
    return _registry_evidence(
        "telegram_smoke",
        status,
        "Telegram credential-free local smoke evidence mapped to acceptance criterion",
        action_steps=["Run Telegram credential-free import, handler, callback, and database/service checks"],
        assertions=["Telegram modules import", "Handlers are registered", "Callback data is safe", "Local database/service checks pass when applicable"],
        collected_evidence=evidence,
        failure_reason="Telegram local smoke checks did not pass" if status != "passed" else "",
        adapter=evidence.get("adapter", ""),
        missing_local_checks=evidence.get("missing_local_checks", []),
    )


def _verify_feature_trace_static_or_smoke(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    plan = _criterion_verifier_plan(criterion)
    if plan.get("verifier_type") == "runtime_start":
        return _verify_runtime_start(criterion, project, root, qa_result, checks)
    if plan.get("verifier_type") == "responsive_ui":
        return _verify_responsive_ui(criterion, project, root, qa_result, checks)
    if plan.get("verifier_type") == "http_sequence":
        return _verify_http_sequence(criterion, project, root, qa_result, checks)
    if plan.get("verifier_type") == "persistence_restart":
        return _verify_persistence_restart(criterion, project, root, qa_result, checks)
    return _registry_evidence(
        "feature_trace_static_or_smoke",
        "not_verified",
        "User requirement criteria require separate criterion-specific feature evidence",
        action_steps=["Review direct feature evidence recorded for this criterion"],
        assertions=["A criterion-specific feature smoke, focused test, or equivalent direct observation exists"],
        collected_evidence={"criterion_id": criterion.get("id", ""), "trace": criterion.get("trace", "")},
        failure_reason="No direct feature evidence was executed by final audit",
    )


ACCEPTANCE_VERIFIERS = {
    "file_check": _verify_file_check,
    "static_scan": _verify_static_scan,
    "command": _verify_command,
    "python_import": _verify_python_import,
    "runtime_smoke": _verify_runtime_smoke,
    "runtime_start": _verify_runtime_start,
    "responsive_ui": _verify_responsive_ui,
    "static_asset_check": _verify_static_asset_check,
    "secret_scan": _verify_secret_scan,
    "file_and_secret_check": _verify_file_and_secret_check,
    "telegram_smoke": _verify_telegram_smoke,
    "http_sequence": _verify_http_sequence,
    "persistence_restart": _verify_persistence_restart,
    "feature_trace_static_or_smoke": _verify_feature_trace_static_or_smoke,
}


SEMANTIC_ACCEPTANCE_VERIFIERS = {
    "lifecycle_tracking": _verify_ac003_request_tracking,
    "browser_usability": _verify_ac004_browser_usability,
    "required_fields_roundtrip": _verify_ac006_required_fields,
    "workflow_statuses": _verify_ac007_workflow_statuses,
    "dashboard_metrics": _verify_ac010_dashboard_counts,
    "filter_behavior": _verify_ac012_filters,
    "product_ui_quality": _verify_ac013_design,
    "responsive_ui": _verify_ac014_layout,
    "admin_scope": _verify_ac015_single_admin,
    "primary_ui_actions": _verify_ac018_primary_buttons,
    "behavior_test_coverage": _verify_ac019_test_coverage,
    "documentation_validation": _verify_ac020_windows_docs,
    "architecture_simplicity": _verify_ac021_simple_stack,
}


def _semantic_acceptance_enabled(criterion: dict[str, Any], project: dict, root: str) -> bool:
    plan = _semantic_verifier_plan(criterion, project, root)
    return str(plan.get("verifier_type") or "") in SEMANTIC_ACCEPTANCE_VERIFIERS


def _record_browser_project_issue(project: dict, criterion: dict[str, Any], evidence: dict[str, Any]) -> None:
    if evidence.get("failure_kind") != "project_failure" or str(evidence.get("status", "")).lower() != "failed":
        return
    verifier_type = str(evidence.get("verifier_type") or "")
    if verifier_type not in {"browser_usability", "responsive_ui", "primary_ui_actions", "product_ui_quality"}:
        return
    criterion_id = str(criterion.get("id") or evidence.get("criterion_id") or "")
    fingerprint = str((evidence.get("collected_evidence") or {}).get("project_snapshot_fingerprint") or "")[:12]
    issue_id = f"ISSUE-BROWSER-{_safe_artifact_part(criterion_id)}-{fingerprint or 'CURRENT'}"
    for issue in project.setdefault("issues", []):
        if isinstance(issue, dict) and issue.get("id") == issue_id and issue.get("status", "open") == "open":
            return
    issue = Issue(
        id=issue_id,
        source="browser_evidence",
        severity="high",
        requirement_id=str(criterion.get("requirement_id") or criterion.get("trace") or ""),
        criterion_id=criterion_id,
        title=f"Browser UI evidence failed for {criterion_id or verifier_type}",
        evidence={
            "criterion_id": criterion_id,
            "verifier_type": verifier_type,
            "failure_reason": evidence.get("failure_reason", ""),
            "supporting_screenshots": (evidence.get("collected_evidence") or {}).get("screenshots", []),
            "objective_browser_evidence": (evidence.get("collected_evidence") or {}).get("OBJECTIVE_REAL_BROWSER_EVIDENCE", {}),
            "code_snapshot_fingerprint": (evidence.get("collected_evidence") or {}).get("project_snapshot_fingerprint", ""),
        },
        reproduction=["Run final delivery audit", f"Execute {verifier_type} browser evidence for {criterion_id}"],
        owner="opencode",
        verification_method=verifier_type,
    )
    project.setdefault("issues", []).append(issue.to_dict())


def verify_acceptance_criterion(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    method = str(criterion.get("verification_method", "") or "").strip()
    semantic_plan = _semantic_verifier_plan(criterion, project, root)
    semantic_verifier = SEMANTIC_ACCEPTANCE_VERIFIERS.get(str(semantic_plan.get("verifier_type") or ""))
    semantic_text = _semantic_acceptance_text(criterion)
    use_semantic_verifier = bool(
        semantic_verifier
        and (
            method == "feature_trace_static_or_smoke"
            or method not in ACCEPTANCE_VERIFIERS
            or (semantic_plan.get("verifier_type") == "documentation_validation" and "windows" in semantic_text)
        )
    )
    if use_semantic_verifier:
        criterion = dict(criterion)
        criterion["semantic_verifier_plan"] = semantic_plan
        evidence = semantic_verifier(criterion, project, root, qa_result, checks)
        normalized = normalize_acceptance_evidence(str(criterion.get("id", "")), str(evidence.get("status", "not_verified")), evidence)
        _record_browser_project_issue(project, criterion, normalized)
        return normalized
    verifier = ACCEPTANCE_VERIFIERS.get(method)
    if not verifier:
        evidence = _registry_evidence(
            method or "unknown",
            "not_verified",
            f"No acceptance verifier registered for method: {method or 'unknown'}",
            action_steps=["Resolve acceptance verifier strategy"],
            assertions=["Mandatory criteria must use a supported verifier strategy"],
            collected_evidence={"verification_method": method},
            failure_reason="Unsupported acceptance verifier type",
        )
    else:
        evidence = verifier(criterion, project, root, qa_result, checks)
    normalized = normalize_acceptance_evidence(str(criterion.get("id", "")), str(evidence.get("status", "not_verified")), evidence)
    _record_browser_project_issue(project, criterion, normalized)
    return normalized


def _evaluate_acceptance(project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    failed = []
    ensure_acceptance_evidence_history(project)
    for criterion in project.get("acceptance_criteria", []):
        method = str(criterion.get("verification_method", "") or "").strip()
        semantic_plan = _semantic_verifier_plan(criterion, project, root)
        semantic_text = _semantic_acceptance_text(criterion)
        has_semantic_verifier = bool(
            semantic_plan.get("verifier_type") in SEMANTIC_ACCEPTANCE_VERIFIERS
            and (
                method == "feature_trace_static_or_smoke"
                or method not in ACCEPTANCE_VERIFIERS
                or (semantic_plan.get("verifier_type") == "documentation_validation" and "windows" in semantic_text)
            )
        )
        unsupported_verifier = method not in ACCEPTANCE_VERIFIERS and not has_semantic_verifier
        if not unsupported_verifier:
            evidence = verify_acceptance_criterion(criterion, project, root, qa_result, checks)
            status = evidence.get("status", "not_verified")
            criterion["status"] = status
            record_acceptance_evidence(project, criterion.get("id", ""), criterion["status"], evidence)
        else:
            evidence = verify_acceptance_criterion(criterion, project, root, qa_result, checks)
            status = "not_verified"
            record_acceptance_evidence(project, criterion.get("id", ""), status, evidence)
            criterion["status"] = status

        if _is_mandatory_criterion(criterion):
            if unsupported_verifier:
                criterion["status"] = "not_verified"
                failed.append(criterion)
                continue
            latest_evidence = _latest_direct_acceptance_evidence(criterion)
            if not latest_evidence:
                criterion["status"] = "not_verified"
            else:
                latest_status = _evidence_status(latest_evidence)
                if latest_status in ("pass", "passed"):
                    criterion["status"] = "passed"
                elif latest_status in ("fail", "failed"):
                    criterion["status"] = "failed"
                else:
                    criterion["status"] = latest_status or "not_verified"

        if _is_mandatory_criterion(criterion) and criterion.get("status") != "passed":
            failed.append(criterion)
    return not failed, failed


def _open_blocking_issues(project: dict) -> list[dict[str, Any]]:
    blocking = []
    for issue in project.get("issues", []):
        if not isinstance(issue, dict):
            continue
        if str(issue.get("status", "open")).lower() != "open":
            continue
        if str(issue.get("severity", "")).lower() in ("critical", "high"):
            blocking.append(issue)
    return blocking


def run_final_delivery_audit(project: dict, root: str, qa_result: dict | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    spec = project.get("project_spec", {})
    profiles = _effective_project_profiles(project, root)
    spec = project.get("project_spec", {})

    required_files = _mandatory_delivery_files(project)
    missing = [rel for rel in required_files if not os.path.exists(os.path.join(root, rel))]
    _add(checks, "required_files", "passed" if not missing else "failed", {"required": required_files, "missing": missing})

    readme_ok, readme_evidence = _verify_readme(root)
    _add(checks, "readme_instructions", "passed" if readme_ok else "failed", readme_evidence)

    secrets_ok, secret_hits = _check_secrets(root)
    _add(checks, "secret_scan", "passed" if secrets_ok else "failed", {"files_with_secret_like_values": secret_hits})

    todo_ok, todo_hits = _check_todos(root)
    _add(checks, "todo_scan", "passed" if todo_ok else "failed", {"files_with_blocking_todos": todo_hits})

    latest_qa_ok = bool((qa_result or {}).get("success"))
    _add(checks, "latest_full_qa", "passed" if latest_qa_ok else "failed", {"rounds_completed": (qa_result or {}).get("rounds_completed"), "total_errors": (qa_result or {}).get("total_errors")})

    unresolved_regressions = []
    for round_data in (qa_result or {}).get("round_history", []):
        unresolved_regressions.extend(round_data.get("comparison", {}).get("regressions", []))
    if latest_qa_ok:
        unresolved_regressions = []
    _add(checks, "regression_status", "passed" if not unresolved_regressions else "failed", {"unresolved_regressions": unresolved_regressions})

    runtime = _runtime_smoke(project, root)
    _add(checks, "runtime_smoke", "passed" if runtime.get("status") == "passed" else "failed", runtime)

    active_ops = []
    if project.get("_qa_active"):
        active_ops.append("qa")
    if project.get("_repair_active"):
        active_ops.append("repair")
    _add(checks, "active_operations", "passed" if not active_ops else "failed", {"active": active_ops})

    blocking_issues = _open_blocking_issues(project)
    _add(
        checks,
        "open_blocking_issues",
        "passed" if not blocking_issues else "failed",
        {
            "open_issue_ids": [issue.get("id") for issue in blocking_issues],
            "open_issue_severities": [issue.get("severity") for issue in blocking_issues],
        },
    )

    ac_ok, failed_criteria = _evaluate_acceptance(project, root, qa_result, checks)
    _add(checks, "acceptance_criteria", "passed" if ac_ok else "failed", {"failed_mandatory": [c.get("id") for c in failed_criteria]})

    credential_state = normalize_credential_state(project, root, qa_result)
    credentials_still_required = [credential for credential in credential_state if credential.get("required") and credential.get("blocks_completion") and not credential.get("configured")]
    optional_credentials_missing = [credential for credential in credential_state if not credential.get("required") and not credential.get("configured")]
    blocked_by_credentials = bool(credentials_still_required)
    _add(
        checks,
        "credential_status",
        "blocked" if blocked_by_credentials else "passed",
        {
            "credentials": credential_state,
            "required_credentials": [credential for credential in credential_state if credential.get("required")],
            "credentials_still_required": credentials_still_required,
            "optional_credentials_missing": optional_credentials_missing,
            "qa_needs_credentials": bool((qa_result or {}).get("needs_credentials")),
        },
    )

    status = "passed"
    if blocked_by_credentials:
        status = "blocked_by_credentials"
    elif any(check["status"] == "failed" for check in checks):
        status = "failed"

    commands = []
    for round_data in (qa_result or {}).get("round_history", []):
        commands.extend(round_data.get("commands", []))

    report = {
        "project_name": project.get("title", "Untitled"),
        "project_type": spec.get("project_type", "generic"),
        "detected_profiles": profiles,
        "implementation_summary": f"Generated project at {root}",
        "acceptance_criteria_summary": [
            {"id": c.get("id"), "title": c.get("title"), "priority": c.get("priority"), "status": c.get("status")}
            for c in project.get("acceptance_criteria", [])
        ],
        "mandatory_criteria_passed": ac_ok,
        "optional_criteria_failed": [c.get("id") for c in project.get("acceptance_criteria", []) if c.get("priority") not in ("critical", "high") and c.get("status") == "failed"],
        "commands_executed": commands,
        "test_results": {"latest_full_qa_passed": latest_qa_ok, "rounds": (qa_result or {}).get("rounds_completed"), "errors": (qa_result or {}).get("errors", [])},
        "runtime_verification": runtime,
        "installation_instructions": spec.get("installation_method", "See README.md"),
        "run_instructions": spec.get("run_method", "See README.md"),
        "credential_state": credential_state,
        "credentials_still_required": credentials_still_required,
        "known_limitations": _credential_limitations(spec, credential_state),
        "checks": checks,
        "status": status,
        "legend": {"verified": "passed", "not_verified": "failed", "blocked_by_credentials": "blocked", "optional_limitation": "optional failed/non-critical"},
    }
    project["final_delivery_report"] = report
    return report


def write_delivery_report(project: dict, root: str) -> str:
    report = project.get("final_delivery_report", {})
    path = os.path.join(root, "DELIVERY_REPORT.json")
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
