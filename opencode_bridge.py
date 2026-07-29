"""
Bridge between FreelancerStudio and OpenCode for real AI-assisted development.

OpenCode (https://opencode.ai) is a real AI coding agent that can read/write files,
run commands, and execute tests. This module lets FreelancerStudio agents delegate
actual coding work to OpenCode instead of simulating it with text-only AI calls.
"""
import subprocess
import time
import json
import os
import sys
import atexit
import logging
import threading
import uuid
import re
import queue
import socket
import shutil
from pathlib import Path
from typing import Optional

import httpx
import config_storage
import secret_store
from execution_config import build_effective_execution_config, normalize_model_id, validate_native_model_id
from repair_scope import resolve_inside

logger = logging.getLogger(__name__)

OPCODE_SERVE_PORT = 4096
OPCODE_SERVE_HOST = "127.0.0.1"
TASK_TIMEOUT = 600  # 10 minutes max per task

_opencode_web_lock = threading.Lock()
_opencode_web_process: Optional[subprocess.Popen] = None
_opencode_web_url = ""
_opencode_web_output: list[str] = []


def _resolve_cli_task_file(project_dir: str, task_file_argument: str) -> tuple[Path, Path, str]:
    """Resolve a CLI task file against its subprocess cwd without duplicate paths."""
    cwd = Path(project_dir).resolve()
    argument = Path(task_file_argument)
    if ".." in argument.parts:
        return cwd, cwd / ".opencode_task.md", "task_file_escapes_project_root"
    if not argument.is_absolute() and len(argument.parts) > 1:
        parent = str(argument.parent).replace("/", "\\").casefold()
        if parent and str(cwd).replace("/", "\\").casefold().endswith(parent):
            return cwd, cwd / argument, "duplicated_project_relative_path"
    try:
        resolved = resolve_inside(cwd, argument)
    except ValueError:
        return cwd, cwd / ".opencode_task.md", "task_file_escapes_project_root"
    return cwd, resolved, ""

# ── Provider mapping: FreelancerStudio → OpenCode ─────────────────────────
# OpenCode uses "provider/model" format, e.g. "anthropic/claude-sonnet-4-20250514"
_PROVIDER_MAP = {
    "nvidia": "nvidia",
    "openai": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "ollama": "ollama",
    "groq": "groq",
    "together": "together",
    "mistral": "mistral",
    "google": "google",
}

_MODEL_MAP = {
    "nvidia": "meta/llama-3.3-70b-instruct",
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-20250514",
    "deepseek": "deepseek-chat",
    "ollama": "codellama",
    "groq": "llama-3.1-70b-versatile",
    "together": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "mistral": "mistral-large-latest",
    "google": "gemini-2.0-flash-001",
}

_OPENCODE_MODEL_LIST_CACHE: dict[str, object] = {"binary": "", "models": [], "checked_at": 0.0}


def normalize_opencode_model_id(model: str, connection_id: str = "", known_connection_ids: set[str] | None = None) -> dict:
    """Return a native OpenCode model ID, removing only proven Studio connection prefixes."""
    return normalize_model_id(model, connection_id, known_connection_ids)


def _is_internal_model_reference(model: str, known_connection_ids: set[str] | None = None) -> bool:
    raw = str(model or "").strip()
    if "/" not in raw:
        return False
    first, rest = raw.split("/", 1)
    return bool(rest and "/" in rest and first in {str(item) for item in (known_connection_ids or set()) if item})


def _native_opencode_model(model: str, connection_id: str = "", known_connection_ids: set[str] | None = None) -> str:
    return str(normalize_opencode_model_id(model, connection_id, known_connection_ids).get("model_id") or "")


def _provider_key(config: dict, provider: str) -> str:
    return secret_store.get_secret(f"{provider}_key", config) or secret_store.get_secret("api_key", config)


def _get_studio_config() -> dict:
    """Read FreelancerStudio config for provider + api_key."""
    return config_storage.load_studio_keys()


def _get_opencode_config_dir() -> str:
    """Return the OpenCode config directory, creating it if needed."""
    configured = os.environ.get("OPENCODE_CONFIG_DIR") or ""
    if configured and os.path.isabs(configured):
        config_dir = configured
    else:
        studio_home = os.environ.get("FREELANCERSTUDIO_HOME") or os.environ.get("FREELANCERSTUDIO_USER_DATA") or os.path.dirname(os.path.abspath(__file__))
        config_dir = os.path.join(studio_home, ".opencode")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def _ensure_opencode_config() -> bool:
    """
    Ensure OpenCode has a valid config with permissions and API key.
    Creates ~/.config/opencode/opencode.json if missing.
    Returns True if config is ready.
    """
    config_dir = _get_opencode_config_dir()
    config_path = os.path.join(config_dir, "opencode.json")

    # Read Studio config for provider + key
    studio_cfg = _get_studio_config()
    system_cfg = studio_cfg.get("_system", {}) if isinstance(studio_cfg.get("_system"), dict) else {}
    provider_raw = system_cfg.get("global_provider") or studio_cfg.get("global_provider") or "nvidia"
    model = system_cfg.get("global_model") or studio_cfg.get("global_model") or _MODEL_MAP.get(provider_raw, "meta/llama-3.3-70b-instruct")
    # Map provider. Effective OpenCode model can differ from Studio model only
    # when the selected provider is known to fail OpenCode tool/function calls
    # and a logged-in compatible OpenCode credential is available.
    oc_provider, model, effective_note = _effective_opencode_provider_model(provider_raw, model, studio_cfg)
    api_key = _provider_key(studio_cfg, oc_provider)

    mcp_server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode_mcp_server.py")

    # Preserve the user's existing config and only merge the fields Studio owns.
    config = {}
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config = loaded
        except Exception as e:
            logger.warning(f"Could not read existing OpenCode config, recreating safe config: {e}")

    config.setdefault("$schema", "https://opencode.ai/config.json")
    config["model"] = f"{oc_provider}/{model}" if oc_provider != "opencode" else model
    if effective_note:
        config.setdefault("experimental", {})
        config["experimental"].setdefault("mcp_timeout", 30000)
    config.setdefault("permission", {})
    config["permission"].update({
        "edit": "allow",
        "bash": "allow",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "webfetch": "allow",
        "websearch": "allow",
    })
    config.setdefault("mcp", {})
    config["mcp"]["freelancer-studio"] = {
        "type": "local",
        "command": [sys.executable, mcp_server_path],
        "enabled": True,
        "environment": {
            "FREELANCER_STUDIO_PORT": "8080",
        },
    }

    existing_providers = config.get("provider", {}) if isinstance(config.get("provider"), dict) else {}
    config["provider"] = {oc_provider: existing_providers.get(oc_provider, {})}
    for provider_id, provider_config in existing_providers.items():
        if provider_id != oc_provider:
            config["provider"][provider_id] = provider_config
    config["provider"].setdefault(oc_provider, {})
    config["provider"][oc_provider].pop("api_key", None)
    if api_key:
        config["provider"][oc_provider].setdefault("options", {})
        config["provider"][oc_provider]["options"]["apiKey"] = api_key
    elif isinstance(config["provider"].get(oc_provider), dict):
        options = config["provider"][oc_provider].get("options")
        if isinstance(options, dict):
            options.pop("apiKey", None)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"OpenCode config created at {config_path} (provider: {oc_provider}, model: {model})")
        return True
    except Exception as e:
        logger.error(f"Failed to write OpenCode config: {e}")
        return False


def sync_opencode_config() -> bool:
    """Regenerate OpenCode config from FreelancerStudio settings without starting OpenCode."""
    return _ensure_opencode_config()


def _preferred_provider_model() -> tuple[str, str]:
    """Return provider/model using the same Studio config rules as OpenCode config generation."""
    studio_cfg = _get_studio_config()
    effective = build_effective_execution_config(studio_cfg)
    model = effective.model_id or _MODEL_MAP.get(effective.provider_id, "meta/llama-3.3-70b-instruct")
    provider = model.split("/", 1)[0] if effective.backend_type == "opencode" and "/" in model else effective.provider_id
    if model.startswith(f"{provider}/"):
        model = model.split("/", 1)[1]
    return provider, model


def _discover_opencode() -> Optional[str]:
    """Find the opencode binary path."""
    candidates = ["opencode.cmd", "opencode.exe", "opencode"]
    for c in candidates:
        try:
            returncode, _stdout, _stderr = _run_capture([c, "--version"], timeout=5)
            if returncode == 0:
                return c
        except FileNotFoundError:
            continue
        except Exception:
            continue
    for prefix in [
        os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode"),
        os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode.cmd"),
        "C:\\Program Files\\nodejs\\opencode",
        "C:\\Program Files\\nodejs\\opencode.cmd",
    ]:
        if os.path.isfile(prefix):
            return prefix
    return None


def _standard_windows_tool_paths(tool: str) -> list[str]:
    if os.name != "nt":
        return []
    program_files = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    app_data = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
    local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    if tool == "node":
        return [os.path.join(root, "nodejs", "node.exe") for root in program_files] + [os.path.join(local_app_data, "Programs", "nodejs", "node.exe")]
    if tool == "npm":
        return [os.path.join(root, "nodejs", "npm.cmd") for root in program_files] + [os.path.join(local_app_data, "Programs", "nodejs", "npm.cmd")]
    if tool == "opencode":
        return [
            os.path.join(app_data, "npm", "opencode.cmd"),
            os.path.join(app_data, "npm", "opencode.exe"),
            os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "opencode.exe"),
        ]
    return []


def _detect_onboarding_tool(tool: str) -> dict:
    command_names = {
        "node": ["node.exe", "node"],
        "npm": ["npm.cmd", "npm"],
        "opencode": ["opencode.cmd", "opencode.exe", "opencode"],
    }[tool]
    path = ""
    for name in command_names:
        path = shutil.which(name) or ""
        if path:
            break
    found_via_standard_path = False
    if not path:
        path = next((candidate for candidate in _standard_windows_tool_paths(tool) if os.path.isfile(candidate)), "")
        found_via_standard_path = bool(path)
    if not path:
        return {"installed": False, "version": "", "path": "", "path_refresh_recommended": False}
    code, stdout, stderr = _run_capture([path, "--version"], timeout=10)
    if code != 0:
        return {"installed": False, "version": "", "path": "", "path_refresh_recommended": found_via_standard_path}
    return {
        "installed": True,
        "version": _strip_ansi(stdout or stderr).strip(),
        "path": path,
        "path_refresh_recommended": found_via_standard_path,
    }


def get_opencode_onboarding_dependencies() -> dict:
    components = {tool: _detect_onboarding_tool(tool) for tool in ("node", "npm", "opencode")}
    restart_recommended = any(item["path_refresh_recommended"] for item in components.values())
    return {
        "components": components,
        "restart_recommended": restart_recommended,
        "restart_message": "A dependency was found in a standard Windows install location but is not visible on Studio's current PATH. Restart Studio, then select Detect Again." if restart_recommended else "",
    }


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def _terminate_process_tree(proc: subprocess.Popen, timeout: int = 5) -> None:
    """Terminate a process and its children; opencode.cmd can orphan opencode.exe on Windows."""
    if not proc or proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_capture(cmd: list[str], timeout: int) -> tuple[int | None, str, str]:
    """Run a short OpenCode probe without leaving child processes behind on timeout."""
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        if proc:
            _terminate_process_tree(proc)
        return None, "", "timeout"
    except Exception as e:
        return None, "", str(e)


def _opencode_auth_provider_ids() -> set[str]:
    binary = _discover_opencode()
    if not binary:
        return set()
    try:
        _returncode, stdout, stderr = _run_capture([binary, "auth", "list"], timeout=20)
        output = _strip_ansi(stdout + "\n" + stderr)
    except Exception:
        return set()
    return {item["provider"] for item in _parse_opencode_auth_list(output)}


def _normalize_provider_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _parse_opencode_auth_list(output: str) -> list[dict[str, str]]:
    """Extract only provider and auth type labels from `auth list` output."""
    credentials = []
    for line in _strip_ansi(output).splitlines():
        clean = line.strip(" |•—\t")
        if not clean or "credential" in clean.lower() or clean.startswith("~"):
            continue
        parts = clean.split()
        if len(parts) < 2:
            continue
        provider = _normalize_provider_id(" ".join(parts[:-1]))
        auth_type = _normalize_provider_id(parts[-1])
        if provider and auth_type:
            credentials.append({"provider": provider, "auth_type": auth_type})
    return credentials


def _readiness_result(error_code: str, message: str, checks: dict, **extra) -> dict:
    return {"status": "error", "ready": False, "error_code": error_code, "message": message, "checks": checks, **extra}


def test_opencode_readiness(executable_path: str = "", selected_model: str = "", server_url: str = "") -> dict:
    """Verify local OpenCode CLI readiness for opencode run without requiring Web/server."""
    selected_model = _native_opencode_model(selected_model, known_connection_ids={"opencode_bridge", "opencode_oauth_bridge"})
    checks = {
        "executable": {"status": "not_checked"},
        "server": {"status": "not_required"},
        "authentication": {"status": "not_checked"},
        "models": {"status": "not_checked"},
        "selection": {"status": "not_checked", "provider": "", "model": selected_model},
    }
    binary = executable_path.strip() or _discover_opencode()
    if not binary:
        checks["executable"] = {"status": "failed", "error_code": "opencode_not_installed"}
        return _readiness_result("opencode_not_installed", "OpenCode is not installed or could not be detected.", checks)

    code, stdout, stderr = _run_capture([binary, "--version"], timeout=10)
    if code is None:
        error_code = "timeout" if stderr == "timeout" else "executable_not_detected"
        checks["executable"] = {"status": "failed", "error_code": error_code}
        return _readiness_result(error_code, "OpenCode executable detection timed out." if error_code == "timeout" else "The selected OpenCode executable could not be started.", checks)
    if code != 0:
        checks["executable"] = {"status": "failed", "error_code": "cli_command_failed"}
        return _readiness_result("cli_command_failed", "OpenCode executable validation failed.", checks)
    checks["executable"] = {"status": "passed", "version": _strip_ansi(stdout or stderr).strip()}

    code, stdout, stderr = _run_capture([binary, "auth", "list"], timeout=20)
    if code is None:
        error_code = "timeout" if stderr == "timeout" else "cli_command_failed"
        checks["authentication"] = {"status": "failed", "error_code": error_code, "providers": []}
        return _readiness_result(error_code, "OpenCode authentication check timed out." if error_code == "timeout" else "OpenCode authentication check failed.", checks, executable_path=binary, server_url="")
    if code != 0:
        checks["authentication"] = {"status": "failed", "error_code": "cli_command_failed", "providers": []}
        return _readiness_result("cli_command_failed", "OpenCode authentication check failed.", checks, executable_path=binary, server_url="")
    credentials = _parse_opencode_auth_list(f"{stdout}\n{stderr}")
    providers = sorted({item["provider"] for item in credentials})
    auth_types = sorted({item["auth_type"] for item in credentials})
    if not providers:
        checks["authentication"] = {"status": "failed", "error_code": "provider_not_authenticated", "providers": [], "auth_types": []}
        return _readiness_result("provider_not_authenticated", "No authenticated OpenCode provider was found. Select Authenticate Provider first.", checks, executable_path=binary, server_url="")
    checks["authentication"] = {"status": "passed", "providers": providers, "auth_types": auth_types}

    code, stdout, stderr = _run_capture([binary, "models"], timeout=30)
    if code is None:
        error_code = "timeout" if stderr == "timeout" else "cli_command_failed"
        checks["models"] = {"status": "failed", "error_code": error_code, "count": 0}
        return _readiness_result(error_code, "OpenCode model discovery timed out." if error_code == "timeout" else "OpenCode model discovery failed.", checks, executable_path=binary, server_url="", authorized_providers=providers)
    if code != 0:
        checks["models"] = {"status": "failed", "error_code": "cli_command_failed", "count": 0}
        return _readiness_result("cli_command_failed", "OpenCode model discovery failed.", checks, executable_path=binary, server_url="", authorized_providers=providers)
    models = sorted({line.strip() for line in _strip_ansi(stdout).splitlines() if re.fullmatch(r"[^\s/]+/.+", line.strip())})
    if not models:
        checks["models"] = {"status": "failed", "error_code": "models_unavailable", "count": 0}
        return _readiness_result("models_unavailable", "OpenCode authentication exists, but no models are available.", checks, executable_path=binary, server_url="", authorized_providers=providers, available_models=[])
    checks["models"] = {"status": "passed", "count": len(models)}

    selected = selected_model.strip()
    selected_provider = _normalize_provider_id(selected.split("/", 1)[0]) if "/" in selected else ""
    checks["selection"] = {"status": "failed", "provider": selected_provider, "model": selected}
    valid_model, invalid_reason = validate_native_model_id(selected, "opencode")
    if not valid_model:
        checks["selection"]["error_code"] = invalid_reason
        return _readiness_result(invalid_reason, "Select a native OpenCode model ID in provider/model format.", checks, executable_path=binary, server_url="", authorized_providers=providers, available_models=models)
    if not selected_provider or not selected:
        checks["selection"]["error_code"] = "selected_model_missing"
        return _readiness_result("selected_model_missing", "Select an OpenCode provider/model before testing.", checks, executable_path=binary, server_url="", authorized_providers=providers, available_models=models)
    if selected_provider not in providers:
        checks["selection"]["error_code"] = "selected_provider_not_authenticated"
        return _readiness_result("selected_provider_not_authenticated", "The selected OpenCode provider is not authenticated.", checks, executable_path=binary, server_url="", authorized_providers=providers, available_models=models)
    if selected not in models:
        checks["selection"]["error_code"] = "selected_model_unavailable"
        return _readiness_result("selected_model_unavailable", "The selected OpenCode model is not available.", checks, executable_path=binary, server_url="", authorized_providers=providers, available_models=models)
    checks["selection"] = {"status": "passed", "provider": selected_provider, "model": selected}
    selected_auth_types = sorted({item["auth_type"] for item in credentials if item["provider"] == selected_provider})
    return {
        "status": "ok", "ready": True, "error_code": "", "message": "OpenCode executable, provider authentication, and selected model are ready. Local server is not required for opencode run.",
        "checks": checks, "executable_path": binary, "server_url": "",
        "authorized_providers": providers, "auth_types": auth_types, "selected_auth_types": selected_auth_types, "available_models": models,
    }


def _effective_opencode_provider_model(provider_raw: str, model: str, studio_cfg: dict) -> tuple[str, str, str]:
    """Choose an OpenCode-compatible provider/model while staying inside OpenCode."""
    oc_provider = _PROVIDER_MAP.get(provider_raw, provider_raw)
    selected_model = model or _MODEL_MAP.get(provider_raw, "meta/llama-3.3-70b-instruct")
    if provider_raw in {"opencode_bridge", "opencode_oauth_bridge", "opencode"} and "/" in str(selected_model):
        provider_from_model, model_part = str(selected_model).split("/", 1)
        return provider_from_model, model_part, ""
    if isinstance(selected_model, str) and selected_model.startswith(f"{oc_provider}/"):
        selected_model = selected_model.split("/", 1)[1]

    auth_providers = _opencode_auth_provider_ids()
    has_openai = "openai" in auth_providers or bool(_provider_key(studio_cfg, "openai"))

    if oc_provider == "nvidia" and has_openai:
        return "openai", _MODEL_MAP["openai"], "NVIDIA is selected in Studio, but OpenCode code generation is using OpenAI because NVIDIA returned degraded tool/function support."

    return oc_provider, selected_model, ""


def _friendly_opencode_error(text: str) -> str:
    clean = _strip_ansi(text or "").strip()
    if "model not found" in clean.lower():
        return clean + "\nThe selected OpenCode model is not available. Select an available model in Settings -> AI Provider."
    if "ResourceExhausted" in clean or "Worker local total request limit reached" in clean:
        return (
            clean
            + "\nOpenCode hit a local/provider worker request limit while using the selected coding model. "
            + "Apply an OpenCode-compatible coding model such as OpenAI in Settings -> AI Provider, then retry the project."
        )
    if "DEGRADED function cannot be invoked" in clean:
        return (
            clean
            + "\nOpenCode reached the provider, but the selected model/provider cannot invoke the tool/function calls required for build mode. "
            + "Use an OpenCode-compatible coding provider such as OpenAI OAuth/API, then Apply to OpenCode and retry."
        )
    return clean


def _find_free_local_port(host: str = OPCODE_SERVE_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((host, 0))
        return int(listener.getsockname()[1])


def _opencode_web_ready(url: str) -> bool:
    try:
        response = httpx.get(f"{url}/global/health", timeout=1)
        return response.status_code in (200, 401, 403)
    except Exception:
        return False


def _capture_web_output(stream) -> None:
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            clean = _strip_ansi(line).strip()
            if clean:
                _opencode_web_output.append(clean[-1000:])
                del _opencode_web_output[:-20]
    except Exception:
        return


def _clear_opencode_web_process() -> None:
    global _opencode_web_process, _opencode_web_url
    _opencode_web_process = None
    _opencode_web_url = ""


def stop_opencode_web() -> None:
    """Stop only the OpenCode Web process started and owned by Studio."""
    with _opencode_web_lock:
        if _opencode_web_process and _opencode_web_process.poll() is None:
            _terminate_process_tree(_opencode_web_process)
        _clear_opencode_web_process()


def get_opencode_status() -> dict:
    """Return safe OpenCode install/auth/config status. Never returns secrets."""
    binary = _discover_opencode()
    status = {
        "installed": bool(binary),
        "binary": binary or "",
        "version": "",
        "credentials": [],
        "credentials_count": 0,
        "selected_provider": "",
        "selected_model": "",
        "effective_provider": "",
        "effective_model": "",
        "effective_note": "",
        "config_path": os.path.join(_get_opencode_config_dir(), "opencode.json"),
        "web_url": _opencode_web_url if _opencode_web_url and _opencode_web_ready(_opencode_web_url) else "",
    }
    studio_cfg = _get_studio_config()
    effective = build_effective_execution_config(studio_cfg)
    provider_raw = effective.provider_id
    model = effective.model_id
    eff_provider = effective.provider_id
    eff_model = effective.model_id
    eff_note = ""
    status.update({
        "selected_provider": provider_raw,
        "selected_model": model,
        "effective_provider": eff_provider,
        "effective_model": eff_model,
        "effective_note": eff_note,
        "effective_execution_config": effective.to_dict(),
    })
    if not binary:
        return status
    try:
        _returncode, stdout, stderr = _run_capture([binary, "--version"], timeout=10)
        status["version"] = _strip_ansi((stdout or stderr or "").strip())
    except Exception:
        pass
    try:
        _returncode, stdout, stderr = _run_capture([binary, "auth", "list"], timeout=20)
        output = _strip_ansi(stdout + "\n" + stderr)
        credentials = [{"provider": item["provider"], "method": item["auth_type"]} for item in _parse_opencode_auth_list(output)]
        status["credentials"] = credentials
        status["credentials_count"] = len(credentials)
    except Exception:
        pass
    return status


def start_opencode_web(workdir: Optional[str] = None, readiness_timeout: float = 15.0) -> dict:
    """Start one Studio-owned OpenCode Web process and return only after readiness."""
    global _opencode_web_process, _opencode_web_url
    with _opencode_web_lock:
        if _opencode_web_url and _opencode_web_ready(_opencode_web_url):
            return {
                "status": "ready", "error_code": "", "url": _opencode_web_url,
                "port": int(_opencode_web_url.rsplit(":", 1)[1]), "reused": True,
                "message": "OpenCode Web is already running. Opening the existing local interface.",
            }
        if _opencode_web_process and _opencode_web_process.poll() is None:
            _terminate_process_tree(_opencode_web_process)
        _clear_opencode_web_process()

        binary = _discover_opencode()
        if not binary:
            return {"status": "error", "error_code": "executable_missing", "message": "OpenCode executable was not found. Install or detect OpenCode first.", "url": ""}

        try:
            port = _find_free_local_port()
        except OSError:
            return {"status": "error", "error_code": "port_unavailable", "message": "No free localhost port is available for OpenCode Web.", "url": ""}

        url = f"http://{OPCODE_SERVE_HOST}:{port}"
        command = [binary, "web", "--hostname", OPCODE_SERVE_HOST, "--port", str(port)]
        _opencode_web_output.clear()
        try:
            _opencode_web_process = subprocess.Popen(
                command,
                cwd=workdir or os.getcwd(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError:
            _clear_opencode_web_process()
            return {"status": "error", "error_code": "start_failed", "message": "OpenCode Web could not be started.", "url": ""}

        threading.Thread(target=_capture_web_output, args=(_opencode_web_process.stdout,), daemon=True).start()
        threading.Thread(target=_capture_web_output, args=(_opencode_web_process.stderr,), daemon=True).start()
        deadline = time.monotonic() + readiness_timeout
        while time.monotonic() < deadline:
            if _opencode_web_process.poll() is not None:
                output = "\n".join(_opencode_web_output).lower()
                code = "port_unavailable" if any(marker in output for marker in ("address already in use", "eaddrinuse", "port is already in use")) else "early_exit"
                message = "The selected localhost port became unavailable before OpenCode Web started." if code == "port_unavailable" else "OpenCode Web exited before it became ready."
                _clear_opencode_web_process()
                return {"status": "error", "error_code": code, "message": message, "url": ""}
            if _opencode_web_ready(url):
                _opencode_web_url = url
                return {
                    "status": "ready", "error_code": "", "url": url, "port": port,
                    "reused": False, "message": "OpenCode Web is ready. Opening the local interface.",
                }
            time.sleep(0.2)

        _terminate_process_tree(_opencode_web_process)
        _clear_opencode_web_process()
        return {"status": "error", "error_code": "readiness_timeout", "message": "OpenCode Web did not become ready in time.", "url": ""}


def start_opencode_auth_terminal(workdir: Optional[str] = None) -> dict:
    """Open the interactive OpenCode-owned provider authentication flow."""
    manual_command = "opencode auth login"
    binary = _discover_opencode()
    if not binary:
        return {
            "status": "error", "error_code": "executable_missing",
            "message": "OpenCode executable was not found. Install or detect OpenCode first.",
            "manual_command": manual_command,
        }
    if os.name != "nt":
        return {
            "status": "manual_required", "error_code": "terminal_unavailable",
            "message": "Open a terminal and run the command shown below.",
            "manual_command": manual_command,
        }

    executable = shutil.which(binary) or binary
    comspec = os.environ.get("COMSPEC") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    command_line = subprocess.list2cmdline([executable, "auth", "login"])
    terminal_command = [comspec, "/d", "/k", f"title OpenCode Provider Authentication & {command_line}"]
    try:
        subprocess.Popen(
            terminal_command,
            cwd=workdir or os.getcwd(),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            shell=False,
        )
        return {
            "status": "started", "error_code": "", "manual_command": manual_command,
            "message": "Complete the authentication steps in the OpenCode terminal. When finished, return here and select Test Connection.",
        }
    except OSError:
        return {
            "status": "manual_required", "error_code": "terminal_launch_failed",
            "message": "The OpenCode terminal could not be opened. Run the command shown below in a terminal.",
            "manual_command": manual_command,
        }


def _ensure_opencode_installed() -> bool:
    """Validate OpenCode presence without downloading or installing software."""
    binary = _discover_opencode()
    if not binary:
        logger.warning("OpenCode not found. Installation must be completed by the user from the official download page.")
        return False

    # Ensure config with permissions + API key exists
    if not _ensure_opencode_config():
        logger.warning("OpenCode config setup failed — agent will run with defaults")
    return True


class OpencodeBridge:
    """
    Manages an OpenCode server process and provides high-level task methods
    for the FreelancerStudio agent pipeline.
    """

    def __init__(self, port: int = OPCODE_SERVE_PORT, host: str = OPCODE_SERVE_HOST):
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self._process: Optional[subprocess.Popen] = None
        self._http: Optional[httpx.Client] = None
        self._started_by_us = False
        self._binary: Optional[str] = None
        self._active_processes: dict[str, subprocess.Popen] = {}
        self._cancelled_sessions: set[str] = set()
        self._active_http_sessions: set[str] = set()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Quick health check via direct HTTP."""
        try:
            r = httpx.get(f"{self.base_url}/global/health", timeout=2)
            return r.status_code in (200, 401, 403)
        except Exception:
            return False

    def _http_api_ready(self) -> bool:
        """True only when the OpenCode HTTP API accepts unauthenticated local calls."""
        try:
            r = httpx.get(f"{self.base_url}/config", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_running(self, workdir: Optional[str] = None) -> bool:
        """Start OpenCode server if not already running. Returns True if ready."""
        _ensure_opencode_config()

        if self._http and self.is_running():
            return True

        if self.is_running():
            if self._http_api_ready():
                self._http = httpx.Client(base_url=self.base_url, timeout=TASK_TIMEOUT)
            return True

        if not _ensure_opencode_installed():
            return False

        binary = _discover_opencode()
        if not binary:
            return False
        self._binary = binary

        cmd = [binary, "serve", "--port", str(self.port), "--hostname", self.host]
        logger.info(f"Starting OpenCode server: {' '.join(cmd)}")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir or os.getcwd(),
            )
            self._started_by_us = True
        except Exception as e:
            logger.error(f"Failed to start OpenCode: {e}")
            return False

        for i in range(30):
            if self.is_running():
                if self._http_api_ready():
                    self._http = httpx.Client(base_url=self.base_url, timeout=TASK_TIMEOUT)
                logger.info("OpenCode server is ready")
                return True
            time.sleep(1)

        logger.error("OpenCode server did not start in time")
        return False

    def stop(self):
        if self._process and self._started_by_us:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self._started_by_us = False
            if self._http:
                self._http.close()
                self._http = None
            logger.info("OpenCode server stopped")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()

    # ── config discovery ───────────────────────────────────────────────────

    def _get_config(self) -> dict:
        """Get server config."""
        if not self._http:
            raise RuntimeError("OpenCode not connected")
        r = self._http.get("/config")
        r.raise_for_status()
        return r.json()

    def _get_providers(self) -> list:
        """Get available provider IDs."""
        try:
            r = self._http.get("/config/providers")
            r.raise_for_status()
            data = r.json()
            return list(data.get("providers", data.keys())) if isinstance(data, dict) else []
        except Exception:
            cfg = self._get_config()
            return list(cfg.get("provider", {}).keys())

    # ── high-level task methods ────────────────────────────────────────────

    def execute_coding_task(self, project_dir: str, task_spec: str, log_callback=None, autonomous: bool = False, model_override: str = "") -> dict:
        """Delegate a coding task to OpenCode."""
        return self._run_agent_session(
            project_dir=project_dir,
            system_prompt=(
                "You are a senior software engineer implementing features for a project. "
                "Read the existing files, understand the codebase, implement the requested feature, "
                "and write all necessary code to disk. Run tests to verify. "
                "Delivery is not complete until the project can be run from the project root using documented commands."
            ),
            user_prompt=(
                f"Task:\n{task_spec}\n\n"
                + ("Autonomous Mode is enabled. Make reasonable engineering decisions, do not ask for confirmation unless blocked by missing secrets or external access.\n\n" if autonomous else "") +
                "1. Read existing files to understand the codebase\n"
                "2. Implement the changes required\n"
                "3. Write/modify files on disk\n"
                "4. Create root-level README.md with exact install/run/test commands\n"
                "5. For Python projects, provide root requirements.txt and a root main.py/bot.py/app.py entrypoint when applicable\n"
                "6. For Python projects with tests, run python -m pytest -q from the project root\n"
                "7. For JS/Vite projects, run npm install and npm run build from the project root\n"
                "8. Do not leave placeholder/stub code, fake tests, stale audit notes, or contradictory documentation\n"
                "9. Tell me exactly what files you changed and the real command results"
            ),
            log_callback=log_callback,
            model_override=model_override,
        )

    def execute_review_task(self, project_dir: str, review_type: str, context: str, model_override: str = "") -> dict:
        """Delegate code review to OpenCode."""
        prompts = {
            "bugcatcher": "You are a QA engineer reviewing code for bugs, edge cases, and functionality issues.",
            "sentinel": "You are a security auditor reviewing code for vulnerabilities: XSS, injection, auth flaws, data exposure.",
            "lupa": "You are a senior code reviewer focused on code quality, maintainability, performance, and best practices.",
        }
        return self._run_agent_session(
            project_dir=project_dir,
            system_prompt=prompts.get(review_type, prompts["lupa"]),
            user_prompt=(
                f"Context:\n{context}\n\n"
                "1. Read the project files\n"
                "2. Review the code thoroughly\n"
                "3. List ALL issues found\n"
                "4. If no issues, say 'VERDICT: PASS'\n"
                "5. If issues exist, say 'VERDICT: FAIL' and suggest specific fixes"
            ),
            model_override=model_override,
        )

    def execute_test_task(self, project_dir: str, test_spec: str, model_override: str = "") -> dict:
        """Delegate test writing and execution to OpenCode."""
        return self._run_agent_session(
            project_dir=project_dir,
            system_prompt="You are a QA engineer writing and running tests. Write real test files and run them.",
            user_prompt=(
                f"Test specification:\n{test_spec}\n\n"
                "1. Read the existing code\n"
                "2. Write comprehensive tests\n"
                "3. Run the tests\n"
                "4. Fix any failures\n"
                "5. Report which tests passed/failed"
            ),
            model_override=model_override,
        )

    def execute_fix_task(self, project_dir: str, issues: str, log_callback=None, model_override: str = "") -> dict:
        """Delegate bug fixing to OpenCode based on review findings."""
        return self._run_agent_session(
            project_dir=project_dir,
            system_prompt="You are a senior developer fixing issues identified in a code review. Fix them properly.",
            user_prompt=(
                f"Issues to fix:\n{issues}\n\n"
                "1. Read the relevant files\n"
                "2. Fix each issue listed above\n"
                "3. Run tests to verify fixes work\n"
                "4. Report what was fixed and the results"
            ),
            log_callback=log_callback,
            model_override=model_override,
        )

    # ── session management ────────────────────────────────────────────────

    def cancel_session(self, session_id: str | None = None) -> dict:
        """Cancel an active OpenCode session/process if possible."""
        cancelled = []
        targets = [session_id] if session_id else list(self._active_processes.keys()) + list(self._active_http_sessions)
        for sid in targets:
            if not sid:
                continue
            self._cancelled_sessions.add(sid)
            proc = self._active_processes.get(sid)
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    cancelled.append(sid)
                except Exception:
                    try:
                        proc.kill()
                        cancelled.append(sid)
                    except Exception:
                        pass
            if self._http and sid in self._active_http_sessions:
                for method, path in (("post", f"/session/{sid}/abort"), ("delete", f"/session/{sid}")):
                    try:
                        getattr(self._http, method)(path, timeout=3)
                        cancelled.append(sid)
                        break
                    except Exception:
                        continue
        return {"status": "cancelled" if cancelled else "not_found", "sessions": cancelled}

    def _run_agent_session(self, project_dir: str, system_prompt: str, user_prompt: str, log_callback=None, model_override: str = "") -> dict:
        """
        Create an OpenCode session, send a task prompt, collect the result.
        Uses OpenCode CLI by default. The local HTTP API is useful for status/web,
        but its session message schema changes across OpenCode releases.
        """
        if os.environ.get("OPENCODE_USE_HTTP", "0") != "1":
            return self._run_cli_task(project_dir, system_prompt, user_prompt, log_callback=log_callback, model_override=model_override)

        if not self._http:
            return self._run_cli_task(project_dir, system_prompt, user_prompt, log_callback=log_callback, model_override=model_override)

        try:
            # 1. Create a session
            r = self._http.post("/session", json={})
            r.raise_for_status()
            session_id = r.json().get("id") or r.json().get("session_id")
            if session_id:
                self._active_http_sessions.add(session_id)
                if log_callback:
                    log_callback(f"[Codex] OpenCode session started: {session_id}")

            # 2. Use Studio's effective OpenCode coding model, not a stale server default.
            provider_id, model_id = _preferred_provider_model()
            if model_override:
                native = _native_opencode_model(model_override, known_connection_ids={"opencode_bridge", "opencode_oauth_bridge"})
                provider_id = native.split("/", 1)[0] if "/" in native else provider_id
                model_id = native
            if log_callback:
                log_callback(f"[Codex] OpenCode model: {provider_id}/{model_id}")

            # 3. Send the task message
            r = self._http.post(
                f"/session/{session_id}/message",
                json={
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "system": [system_prompt],
                    "parts": [{"type": "text", "text": user_prompt}],
                    "mode": "build",
                },
                timeout=TASK_TIMEOUT,
            )
            r.raise_for_status()
            msg_data = r.json()
            if log_callback:
                log_callback("[Codex] OpenCode accepted the task and is generating code...")

            # 4. Get all session messages to extract assistant response
            time.sleep(1)
            r = self._http.get(f"/session/{session_id}/messages", timeout=10)
            r.raise_for_status()
            messages_data = r.json()

            # Extract the assistant text response
            response_text = self._extract_assistant_text(messages_data) or msg_data.get("id", "")

            # Check for errors in the response
            error = msg_data.get("error") or (messages_data[-1].get("error") if isinstance(messages_data, list) and messages_data else None)

            return {
                "success": error is None,
                "session_id": session_id,
                "summary": response_text,
                "error": str(error) if error else None,
            }

        except httpx.HTTPStatusError as e:
            body = _strip_ansi(e.response.text if e.response is not None else "")
            logger.exception(f"OpenCode HTTP task failed: {body}")
            if log_callback:
                log_callback("[Codex] OpenCode HTTP API rejected the task; retrying through OpenCode CLI...")
            return self._run_cli_task(project_dir, system_prompt, user_prompt, log_callback=log_callback, model_override=model_override)
        except Exception as e:
            logger.exception(f"OpenCode task failed")
            if "401" in str(e) or "403" in str(e) or "Unauthorized" in str(e):
                return self._run_cli_task(project_dir, system_prompt, user_prompt, log_callback=log_callback, model_override=model_override)
            return {"success": False, "error": str(e), "session_id": None}
        finally:
            try:
                if 'session_id' in locals() and session_id:
                    self._active_http_sessions.discard(session_id)
            except Exception:
                pass

    def _run_cli_task(self, project_dir: str, system_prompt: str, user_prompt: str, log_callback=None, model_override: str = "") -> dict:
        """Run OpenCode via CLI when the local HTTP API requires auth."""
        binary = self._binary or _discover_opencode()
        if not binary:
            return {"success": False, "error": "OpenCode binary not found", "session_id": None, "subprocess_started": False, "exit_code": None, "command_shape": []}
        provider, model = _preferred_provider_model()
        if model_override:
            native = _native_opencode_model(model_override, known_connection_ids={"opencode_bridge", "opencode_oauth_bridge"})
            provider = native.split("/", 1)[0] if "/" in native else provider
            model = native
        prompt = (
            f"SYSTEM:\n{system_prompt}\n\n"
            f"USER TASK:\n{user_prompt}\n\n"
            "You are running inside the project directory. Create or modify files directly. "
            "Do not ask for confirmation. Run npm install/build or tests when relevant. "
            "Before finishing, ensure root README.md, root dependency files, runnable entrypoints, and real verification commands are present."
        )
        try:
            project_dir = str(resolve_inside(project_dir, "."))
        except ValueError as e:
            return {"success": False, "error": str(e), "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "preflight_status": "project_dir_escape", "command_shape": []}
        task_file_argument = ".opencode_task.md"
        cwd, prompt_path, path_error = _resolve_cli_task_file(project_dir, task_file_argument)
        task_metadata = {
            "task_file_argument": task_file_argument,
            "task_file_resolved_path": str(prompt_path),
            "task_file_exists": False,
        }
        if path_error:
            return {"success": False, "error": path_error, "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "preflight_status": "task_file_not_found", **task_metadata}
        try:
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
        except Exception as e:
            return {"success": False, "error": f"Failed to write OpenCode task file: {e}", "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "preflight_status": "task_file_not_found", **task_metadata}
        task_metadata["task_file_exists"] = prompt_path.is_file()
        if not task_metadata["task_file_exists"]:
            return {"success": False, "error": "OpenCode task file was not created", "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "preflight_status": "task_file_not_found", **task_metadata}
        native_model_id = model if "/" in model else f"{provider}/{model}"
        if not native_model_id:
            return {"success": False, "error": "OpenCode model is empty", "error_category": "opencode_model_reference_invalid", "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "command_shape": [], **task_metadata}
        valid_model, invalid_reason = validate_native_model_id(native_model_id, "opencode")
        if not valid_model:
            return {"success": False, "error": f"Invalid OpenCode model reference: {native_model_id}", "error_category": invalid_reason, "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "command_shape": [], **task_metadata}
        if _is_internal_model_reference(native_model_id, {"opencode_bridge", "opencode_oauth_bridge"}):
            return {"success": False, "error": f"Invalid OpenCode model reference: {native_model_id}", "error_category": "opencode_model_reference_invalid", "session_id": "opencode-cli", "subprocess_started": False, "exit_code": None, "command_shape": [], **task_metadata}
        cmd = [
            binary,
            "run",
            "Read the attached .opencode_task.md file and complete the task exactly. Modify project files on disk.",
            "--model",
            native_model_id,
            "--dangerously-skip-permissions",
            f"--file={task_file_argument}",
        ]
        command_shape = [str(binary), "run", "<task>", "--model", native_model_id, "--dangerously-skip-permissions", "--file=<task-file>"]
        session_id = f"opencode-cli-{uuid.uuid4().hex[:8]}"
        if log_callback:
            log_callback(f"[Codex] Starting OpenCode CLI session: {session_id}")
            log_callback(f"[Codex] OpenCode model: {native_model_id}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._active_processes[session_id] = proc
            output_parts = []
            output_queue: queue.Queue[str] = queue.Queue()

            def _read_stdout() -> None:
                try:
                    if proc.stdout:
                        for raw_line in proc.stdout:
                            output_queue.put(raw_line)
                except Exception as e:
                    output_queue.put(f"[stdout reader error] {e}\n")

            reader = threading.Thread(target=_read_stdout, daemon=True)
            reader.start()
            started = time.time()

            def _drain_output() -> None:
                while True:
                    try:
                        line = output_queue.get_nowait()
                    except queue.Empty:
                        break
                    clean = _strip_ansi(line.rstrip())
                    output_parts.append(clean)
                    if log_callback and clean:
                        log_callback(f"[OpenCode] {clean[:1000]}")

            while True:
                _drain_output()
                if session_id in self._cancelled_sessions:
                    _terminate_process_tree(proc)
                    return {"success": False, "error": "OpenCode task cancelled", "session_id": session_id, "cancelled": True, "timed_out": False, "subprocess_started": True, "exit_code": proc.returncode, "command_shape": command_shape, **task_metadata}
                if proc.poll() is not None:
                    break
                if time.time() - started > TASK_TIMEOUT:
                    _terminate_process_tree(proc)
                    reader.join(timeout=1)
                    _drain_output()
                    output = _strip_ansi("\n".join(output_parts))
                    return {
                        "success": False,
                        "error": "OpenCode task timed out",
                        "session_id": session_id,
                        "timed_out": True,
                        "summary": output[-4000:],
                        "subprocess_started": True,
                        "exit_code": proc.returncode,
                        "timeout_seconds": TASK_TIMEOUT,
                        "command_shape": command_shape,
                        **task_metadata,
                    }
                time.sleep(0.1)
            reader.join(timeout=1)
            _drain_output()
            output = _strip_ansi("\n".join(output_parts))
            return {
                "success": proc.returncode == 0,
                "session_id": session_id,
                "summary": output[-4000:],
                "error": None if proc.returncode == 0 else _friendly_opencode_error(output[-2000:]),
                "timed_out": False,
                "subprocess_started": True,
                "exit_code": proc.returncode,
                "command_shape": command_shape,
                **task_metadata,
            }
        except Exception as e:
            logger.exception("OpenCode CLI task failed")
            return {"success": False, "error": str(e), "session_id": session_id, "timed_out": False, "subprocess_started": False, "exit_code": None, "command_shape": command_shape, **task_metadata}
        finally:
            self._active_processes.pop(session_id, None)
            self._cancelled_sessions.discard(session_id)
            try:
                if prompt_path.exists():
                    prompt_path.unlink()
            except Exception:
                pass

    def _get_default_provider(self, cfg: dict) -> str:
        """Get the default provider ID from config."""
        providers = cfg.get("provider", {})
        if providers:
            return next(iter(providers.keys()))
        return "opencode"

    def _extract_assistant_text(self, messages_data) -> str:
        """Extract the last assistant text response from messages."""
        if not messages_data:
            return ""
        if isinstance(messages_data, dict):
            items = messages_data.get("data", messages_data.get("items", [messages_data]))
        elif isinstance(messages_data, list):
            items = messages_data
        else:
            return str(messages_data)

        for item in reversed(items):
            msg = item if isinstance(item, dict) else {}
            if msg.get("role") == "assistant":
                parts = msg.get("parts", msg.get("message", {}).get("parts", []))
                texts = []
                for p in parts:
                    if isinstance(p, dict) and p.get("type") == "text":
                        texts.append(p.get("text", ""))
                if texts:
                    return "\n".join(texts)
        return json.dumps(items[-1] if items else "", indent=2)


# Module-level singleton
_bridge_instance: Optional[OpencodeBridge] = None
_bridge_lock = threading.Lock()


def get_bridge(port: int = OPCODE_SERVE_PORT) -> OpencodeBridge:
    """Get or create the singleton bridge instance."""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = OpencodeBridge(port=port)
    return _bridge_instance


def ensure_opencode(workdir: Optional[str] = None) -> bool:
    """Convenience: ensure OpenCode is running. Called at pipeline start."""
    bridge = get_bridge()
    return bridge.ensure_running(workdir=workdir)


def stop_opencode():
    """Convenience: stop OpenCode if we started it."""
    bridge = get_bridge()
    bridge.stop()


atexit.register(stop_opencode_web)
