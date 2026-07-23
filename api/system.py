import glob
import os
import shutil
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend_security import StrictRequestModel, get_app_security_context

from requirements_checker import check_all, get_components
from system_settings import (
    ALLOWED_SYSTEM_KEYS,
    DEFAULT_SYSTEM_SETTINGS,
    SYSTEM_SETTINGS,
    get_current_system_config,
    update_system_config as update_system_settings,
)


router = APIRouter()
SYSTEM_CONFIG_DEFAULTS = DEFAULT_SYSTEM_SETTINGS
SYSTEM_CONFIG_ALLOWED_KEYS = ALLOWED_SYSTEM_KEYS


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
