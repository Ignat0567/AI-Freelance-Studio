import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import claude_bridge
import config_storage
from backend_security import StrictRequestModel, get_app_security_context

from api.collaboration import router as collaboration_router
from api.embeddings import router as embeddings_router
from api.marketplace import router as marketplace_router
from api.orders import router as orders_router
from api.presentation import router as presentation_router
from api.video import router as video_router
from requirements_checker import check_all, get_components
from build_identity import build_status
from system_settings import (
    ALLOWED_SYSTEM_KEYS,
    DEFAULT_SYSTEM_SETTINGS,
    SYSTEM_SETTINGS,
    get_current_system_config,
    update_system_config as update_system_settings,
)


router = APIRouter()
# Temporary router-composition seam while main.py remains a monolithic explicit registrar.
router.include_router(orders_router)
router.include_router(collaboration_router)
router.include_router(marketplace_router)
router.include_router(embeddings_router)
router.include_router(video_router)
router.include_router(presentation_router)
SYSTEM_CONFIG_DEFAULTS = DEFAULT_SYSTEM_SETTINGS
SYSTEM_CONFIG_ALLOWED_KEYS = ALLOWED_SYSTEM_KEYS
STORAGE_PATH_KEYS = {
    "studio_root",
    "data_dir",
    "runtime_dir",
    "generated_projects",
    "projects_data",
    "opencode_config",
    "backups",
}
DIRECTORY_STORAGE_PATH_KEYS = STORAGE_PATH_KEYS
RESTART_REQUIRED_PATHS = {"studio_root", "data_dir", "runtime_dir", "opencode_config"}
DERIVED_STORAGE_PATH_KEYS = {"studio_config", "projects_state"}
WINDOWS_RESERVED_PATH_PARTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _load_storage_path_overrides() -> dict[str, str]:
    data = config_storage.load_studio_keys()
    raw = data.get("_storage_paths", {}) if isinstance(data.get("_storage_paths"), dict) else {}
    return {key: str(value) for key, value in raw.items() if key in STORAGE_PATH_KEYS and str(value).strip()}


def _save_storage_path_overrides(overrides: dict[str, str]) -> None:
    data = config_storage.load_studio_keys()
    cleaned = {key: str(value).strip() for key, value in overrides.items() if key in STORAGE_PATH_KEYS and str(value).strip()}
    if cleaned:
        data["_storage_paths"] = cleaned
    else:
        data.pop("_storage_paths", None)
    config_storage.save_studio_keys(data)


def _clean_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if "\x00" in text:
        raise ValueError("Path contains a null byte")
    return text


def _contains_reserved_windows_part(path: Path) -> str | None:
    for part in path.parts:
        normalized = part.rstrip(" .").upper()
        if normalized in WINDOWS_RESERVED_PATH_PARTS:
            return part
    return None


def _normalize_storage_path(key: str, value: Any) -> str | None:
    try:
        text = _clean_path_text(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path for {key}: {exc}") from exc
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        reserved = _contains_reserved_windows_part(path)
        if reserved:
            raise ValueError(f"Path uses reserved Windows device name: {reserved}")
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path for {key}: {exc}") from exc
    return str(resolved)


def _ensure_directory_path(key: str, resolved_text: str) -> None:
    path = Path(resolved_text)
    try:
        if path.exists() and not path.is_dir():
            raise HTTPException(status_code=400, detail=f"Invalid path for {key}: existing path is not a directory")
        path.mkdir(parents=True, exist_ok=True)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path for {key}: {exc}") from exc


def _path_override(overrides: dict[str, str], key: str, default: Path) -> Path:
    raw = overrides.get(key, "")
    return Path(raw).expanduser().resolve(strict=False) if raw else default.resolve(strict=False)


def _path_source(key: str, overrides: dict[str, str]) -> str:
    if overrides.get(key):
        return "stored_configuration"
    if key in DERIVED_STORAGE_PATH_KEYS:
        return "derived"
    env_by_key = {
        "studio_root": ("FREELANCERSTUDIO_HOME", "FREELANCERSTUDIO_USER_DATA"),
        "data_dir": ("FREELANCERSTUDIO_USER_DATA", "FREELANCERSTUDIO_HOME"),
        "runtime_dir": ("FREELANCERSTUDIO_RUNTIME_DIR",),
        "opencode_config": ("OPENCODE_CONFIG_DIR",),
    }
    if any(os.environ.get(name) for name in env_by_key.get(key, ())):
        return "environment"
    return "default"


def _studio_root() -> Path:
    configured = os.environ.get("FREELANCERSTUDIO_HOME") or os.environ.get("FREELANCERSTUDIO_USER_DATA") or ""
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def _path_status(key: str, path: Path, root: Path, overrides: dict[str, str], *, create_allowed: bool = True) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=False)
        inside_root = resolved == root or root in resolved.parents
        exists = resolved.exists()
        is_directory = resolved.is_dir() if exists else False
        writable = False
        validation_status = "ok"
        validation_error = ""
        if exists and not is_directory and key in DIRECTORY_STORAGE_PATH_KEYS:
            validation_status = "invalid"
            validation_error = "existing path is not a directory"
        if exists and is_directory:
            probe = resolved / ".freelancerstudio-write-test"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                writable = True
            except OSError:
                writable = False
                validation_status = "warning"
                validation_error = "directory is not writable"
        return {
            "key": key,
            "path": str(resolved),
            "resolved_path": str(resolved),
            "configured_value": overrides.get(key, ""),
            "configured_override": overrides.get(key, ""),
            "source": _path_source(key, overrides),
            "exists": exists,
            "is_directory": is_directory,
            "inside_portable_root": inside_root,
            "writable": writable,
            "create_allowed": create_allowed,
            "validation_status": validation_status,
            "validation_error": validation_error,
        }
    except OSError as exc:
        return {
            "key": key,
            "path": str(path),
            "resolved_path": str(path),
            "configured_value": overrides.get(key, ""),
            "configured_override": overrides.get(key, ""),
            "source": _path_source(key, overrides),
            "exists": False,
            "is_directory": False,
            "inside_portable_root": False,
            "writable": False,
            "create_allowed": create_allowed,
            "validation_status": "invalid",
            "validation_error": str(exc),
            "error": str(exc),
        }


def _portable_paths_payload() -> dict[str, Any]:
    overrides = _load_storage_path_overrides()
    root = _path_override(overrides, "studio_root", _studio_root())
    data_dir = _path_override(overrides, "data_dir", Path(os.environ.get("FREELANCERSTUDIO_USER_DATA") or os.environ.get("FREELANCERSTUDIO_HOME") or root))
    runtime_dir = _path_override(overrides, "runtime_dir", Path(os.environ.get("FREELANCERSTUDIO_RUNTIME_DIR") or data_dir))
    generated_projects = _path_override(overrides, "generated_projects", data_dir / "generated_projects")
    projects_data = _path_override(overrides, "projects_data", data_dir / "projects_data")
    opencode_config = _path_override(overrides, "opencode_config", Path(os.environ.get("OPENCODE_CONFIG_DIR") or data_dir / ".opencode"))
    backups = _path_override(overrides, "backups", root / "backups")
    paths = {
        "studio_root": _path_status("studio_root", root, root, overrides),
        "data_dir": _path_status("data_dir", data_dir, root, overrides),
        "runtime_dir": _path_status("runtime_dir", runtime_dir, root, overrides),
        "studio_config": _path_status("studio_config", data_dir / "studio_config.json", root, overrides, create_allowed=False),
        "projects_state": _path_status("projects_state", data_dir / "projects_state.json", root, overrides, create_allowed=False),
        "generated_projects": _path_status("generated_projects", generated_projects, root, overrides),
        "projects_data": _path_status("projects_data", projects_data, root, overrides),
        "opencode_config": _path_status("opencode_config", opencode_config, root, overrides),
        "backups": _path_status("backups", backups, root, overrides),
    }
    for name, item in paths.items():
        item["editable"] = name in STORAGE_PATH_KEYS
        item["restart_required"] = name in RESTART_REQUIRED_PATHS and bool(overrides.get(name))
        item["applied"] = not item["restart_required"]
    required = ["studio_root", "data_dir", "runtime_dir", "generated_projects", "projects_data", "opencode_config", "backups"]
    portable_ready = all(paths[name]["inside_portable_root"] for name in required) and all(paths[name]["exists"] or paths[name]["create_allowed"] for name in required)
    return {"portable_ready": portable_ready, "root": str(root), "paths": paths, "overrides": overrides, "restart_required": any(paths[name].get("restart_required") for name in required)}


def _ensure_portable_paths() -> dict[str, Any]:
    payload = _portable_paths_payload()
    created = []
    for name in ("generated_projects", "projects_data", "opencode_config", "backups"):
        item = payload["paths"][name]
        if item.get("inside_portable_root") and item.get("create_allowed"):
            target = Path(item["path"])
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                created.append(name)
    refreshed = _portable_paths_payload()
    return {"status": "repaired", "created": created, **refreshed}


@router.get("/health")
def health_check(request: Request):
    context = get_app_security_context(request.app)
    return {"status": "ok", "service": "FreelancerStudio", "port": context.port}


@router.get("/health/owner")
def owner_health_check(request: Request):
    challenges = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == b"x-freelancerstudio-challenge"
    ]
    if len(challenges) != 1 or not 32 <= len(challenges[0]) <= 256:
        raise HTTPException(status_code=400, detail="invalid_ownership_challenge")
    return get_app_security_context(request.app).owner_challenge_response(challenges[0])


@router.get("/api/system/requirements")
def list_requirements():
    return {"components": get_components()}


@router.post("/api/system/check")
def check_requirements():
    result = check_all()
    return result if isinstance(result, dict) else {"results": result}


@router.get("/api/system/paths")
def get_system_paths():
    return _portable_paths_payload()


@router.post("/api/system/portable-migration")
def repair_portable_paths():
    return _ensure_portable_paths()


class StoragePathsPayload(StrictRequestModel):
    studio_root: Any | None = None
    data_dir: Any | None = None
    runtime_dir: Any | None = None
    generated_projects: Any | None = None
    projects_data: Any | None = None
    opencode_config: Any | None = None
    backups: Any | None = None


@router.post("/api/system/paths")
def update_system_paths(payload: StoragePathsPayload):
    values = payload.model_dump(exclude_unset=True)
    overrides = _load_storage_path_overrides()
    for key, value in values.items():
        normalized = _normalize_storage_path(key, value)
        if normalized is None:
            overrides.pop(key, None)
            continue
        if key in DIRECTORY_STORAGE_PATH_KEYS:
            _ensure_directory_path(key, normalized)
        overrides[key] = normalized
    _save_storage_path_overrides(overrides)
    payload = _portable_paths_payload()
    restart_required = any(payload["paths"][key].get("restart_required") for key in STORAGE_PATH_KEYS)
    return {
        "status": "saved",
        "saved": True,
        "applied": not restart_required,
        "restart_required": restart_required,
        "message": "Storage paths saved. Restart Studio for runtime paths to take effect." if restart_required else "Storage paths saved.",
        **payload,
    }


@router.post("/api/system/install/{component_id}")
def install_requirement(component_id: str):
    raise HTTPException(
        status_code=410,
        detail=f"Automatic installation is disabled for {component_id}. Use the official component action in Info.",
    )


class SystemConfigPayload(StrictRequestModel):
    global_provider: Any | None = None
    global_model: Any | None = None
    theme: Any | None = None
    accent_color: Any | None = None
    animation_speed: Any | None = None
    font_size: Any | None = None
    language: Any | None = None
    auto_save: Any | None = None
    notifications_enabled: Any | None = None
    default_budget: Any | None = None
    polling_interval: Any | None = None
    log_detail: Any | None = None
    vscode_path: Any | None = None
    pycharm_path: Any | None = None


@router.get("/api/system/build")
def get_build_identity():
    """Which commit this backend process is executing, and whether the checkout has moved on.

    Not a setting, so it does not belong in /api/config/system -- it is a fact about the
    running process. Read-only and cheap enough to poll.
    """
    status = build_status()
    return {
        "running_build": status.running,
        "current_build": status.current,
        "stale": status.stale,
        "message": status.message,
    }


@router.get("/api/config/system")
def get_system_config():
    return get_current_system_config()


@router.post("/api/config/system")
def update_system_config(payload: SystemConfigPayload):
    return update_system_settings(payload.model_dump(exclude_unset=True))


def _open_local_path(raw_path: str):
    """Open a local project folder from the backend for browser-based sessions."""
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required")
    target_path = os.path.abspath(raw_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", target_path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target_path])
        else:
            subprocess.Popen(["xdg-open", target_path])
        return {"status": "opened", "path": target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _editor_candidates(editor: str) -> list[str]:
    editor = (editor or "").strip().lower()
    if editor in ("vscode", "vs_code", "code"):
        return [
            SYSTEM_SETTINGS.get("vscode_path", "code"),
            "code",
            "code.cmd",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft VS Code\bin\code.cmd"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft VS Code\bin\code.cmd"),
        ]
    if editor in ("pycharm", "pycharm64"):
        jetbrains_roots = [
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\JetBrains\PyCharm*\bin\pycharm64.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\JetBrains\PyCharm*\bin\pycharm64.exe"),
        ]
        discovered = []
        for pattern in jetbrains_roots:
            discovered.extend(sorted(glob.glob(pattern), reverse=True))
        return [
            SYSTEM_SETTINGS.get("pycharm_path", "pycharm"),
            "pycharm",
            "pycharm64",
            os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\scripts\pycharm.cmd"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.2\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.2\bin\pycharm64.exe"),
        ] + discovered
    configured = SYSTEM_SETTINGS.get(f"{editor}_path")
    return [configured or editor]


def _resolve_editor_executable(editor: str) -> str:
    checked = []
    for candidate in _editor_candidates(editor):
        if not candidate:
            continue
        candidate = os.path.expandvars(os.path.expanduser(str(candidate).strip().strip('"')))
        if not candidate or candidate in checked:
            continue
        checked.append(candidate)
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    raise HTTPException(status_code=404, detail=f"Editor '{editor}' not found. Checked: {', '.join(checked[:8])}")


def _open_editor(editor: str, raw_path: str):
    if not raw_path:
        raise HTTPException(status_code=400, detail="Path is required")
    target_path = os.path.abspath(raw_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Project path not found")
    if (editor or "").strip().lower() == "claude":
        result = claude_bridge.start_claude_workspace_terminal(target_path)
        if result.get("status") == "error":
            raise HTTPException(status_code=404, detail=result.get("message", "Claude Code was not found."))
        if result.get("status") != "started":
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to open Claude Code."))
        return {"status": "opened", "editor": "claude", "executable": result.get("manual_command", "claude"), "path": target_path}
    executable = _resolve_editor_executable(editor)
    try:
        if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
            subprocess.Popen(subprocess.list2cmdline([executable, target_path]), shell=True)
        else:
            subprocess.Popen([executable, target_path])
        return {"status": "opened", "editor": editor, "executable": executable, "path": target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open editor: {e}")


class OpenPathPayload(StrictRequestModel):
    path: str


class OpenEditorPayload(StrictRequestModel):
    editor: str
    path: str


@router.post("/api/system/open-path")
def open_system_path(payload: OpenPathPayload):
    return _open_local_path(payload.path)


@router.post("/api/system/open-editor")
def open_system_editor(payload: OpenEditorPayload):
    return _open_editor(payload.editor, payload.path)
