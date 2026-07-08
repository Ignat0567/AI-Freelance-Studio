import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from project_spec import ensure_acceptance_evidence_history, record_acceptance_evidence


IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache", "__pycache__"}
IGNORED_EXTS = {".pyc", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".glb"}
SECRET_PATTERNS = [
    r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b",
    r"sk-[A-Za-z0-9_-]{16,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*=\s*(?!replace_me|your_|example|changeme|<)[^\s'\"]{10,}",
]
TODO_PATTERNS = ("todo: implement", "pass  # todo", "raise notimplementederror", "not implemented", "fake output")
OUTPUT_TAIL_LIMIT = 4000


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


class RuntimeAdapter:
    name = "runtime"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        raise NotImplementedError


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
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
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


def _adapter_status(result: RuntimeAdapterResult) -> str:
    if not result.applicable:
        return "not_applicable"
    start_ok = result.started or bool(result.evidence.get("credential_free"))
    return "passed" if start_ok and result.verified and result.stopped_cleanly else "failed"


def _runtime_smoke(project: dict, root: str) -> dict[str, Any]:
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

    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    return {"status": "not_applicable", "applicable": False, "started": False, "verified": False, "stopped_cleanly": True, "evidence": {"profiles": list(profiles)}, "error": ""}


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
    if not isinstance(evidence, dict):
        return False
    if "qa_success" in evidence:
        return False
    return bool(
        evidence.get("summary")
        or evidence.get("command")
        or evidence.get("exit_code") is not None
        or evidence.get("artifacts")
        or evidence.get("traceback_tail")
    )


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


def _registry_evidence(method: str, status: str, summary: str, **extra: Any) -> dict[str, Any]:
    evidence = {
        "source": "final_delivery_audit",
        "verifier": "final_delivery_audit",
        "method": method,
        "status": status,
        "summary": summary,
        "artifacts": [],
    }
    evidence.update(extra)
    return evidence


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
        required_paths=mandatory,
        optional_paths=optional,
        missing_required=missing_mandatory,
        missing_optional=missing_optional,
    )


def _mandatory_delivery_files(project: dict) -> list[str]:
    mandatory, _optional = _artifact_requirements(project)
    return mandatory


def _verify_secret_scan(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    status = "passed" if _check_status(checks, "secret_scan") == "passed" else "failed"
    return _registry_evidence("secret_scan", status, "Secret scan result mapped to acceptance criterion")


def _verify_global_qa(method: str, criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    qa_success = bool((qa_result or {}).get("success"))
    status = "passed" if qa_success else "failed"
    return _registry_evidence(method, status, "Global QA result supports system-level acceptance criterion", qa_success=qa_success)


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
    return _registry_evidence("command", status, summary, expected_exit_codes=expected_codes, **result)


def _verify_runtime_smoke(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    status = "passed" if _check_status(checks, "runtime_smoke") == "passed" else "failed"
    return _registry_evidence("runtime_smoke", status, "Runtime smoke result mapped to acceptance criterion")


def _verify_static_asset_check(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return _verify_global_qa("static_asset_check", criterion, project, root, qa_result, checks)


ACCEPTANCE_VERIFIERS = {
    "file_check": _verify_file_check,
    "command": _verify_command,
    "python_import": _verify_python_import,
    "runtime_smoke": _verify_runtime_smoke,
    "static_asset_check": _verify_static_asset_check,
    "secret_scan": _verify_secret_scan,
}


def verify_acceptance_criterion(criterion: dict[str, Any], project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    method = criterion.get("verification_method", "")
    verifier = ACCEPTANCE_VERIFIERS.get(method)
    if not verifier:
        return _registry_evidence(method, "not_verified", f"No acceptance verifier registered for method: {method}")
    return verifier(criterion, project, root, qa_result, checks)


def _evaluate_acceptance(project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    failed = []
    ensure_acceptance_evidence_history(project)
    for criterion in project.get("acceptance_criteria", []):
        method = criterion.get("verification_method", "")
        if method in ACCEPTANCE_VERIFIERS:
            evidence = verify_acceptance_criterion(criterion, project, root, qa_result, checks)
            status = evidence.get("status", "not_verified")
            criterion["status"] = status
            record_acceptance_evidence(project, criterion.get("id", ""), criterion["status"], evidence)
        else:
            latest_evidence = _latest_direct_acceptance_evidence(criterion)
            if latest_evidence:
                status = _evidence_status(latest_evidence)
            else:
                evidence = _registry_evidence(method, "not_verified", f"No direct acceptance evidence recorded for method: {method}")
                status = "not_verified"
                record_acceptance_evidence(project, criterion.get("id", ""), status, evidence)
            criterion["status"] = status

        if _is_mandatory_criterion(criterion):
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


def run_final_delivery_audit(project: dict, root: str, qa_result: dict | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    spec = project.get("project_spec", {})
    profiles = project.get("project_profiles") or spec.get("project_profiles", [])

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
    _add(checks, "runtime_smoke", "passed" if runtime.get("status") in ("passed", "not_applicable") else "failed", runtime)

    active_ops = []
    if project.get("_qa_active"):
        active_ops.append("qa")
    if project.get("_repair_active"):
        active_ops.append("repair")
    _add(checks, "active_operations", "passed" if not active_ops else "failed", {"active": active_ops})

    ac_ok, failed_criteria = _evaluate_acceptance(project, root, qa_result, checks)
    _add(checks, "acceptance_criteria", "passed" if ac_ok else "failed", {"failed_mandatory": [c.get("id") for c in failed_criteria]})

    credentials_required = spec.get("required_credentials", [])
    blocked_by_credentials = bool((qa_result or {}).get("needs_credentials"))
    if blocked_by_credentials:
        _add(checks, "credential_status", "blocked", {"required_credentials": credentials_required})
    else:
        _add(checks, "credential_status", "passed", {"required_credentials": credentials_required})

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
        "credentials_still_required": credentials_required if blocked_by_credentials else [],
        "known_limitations": spec.get("risks", []),
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
