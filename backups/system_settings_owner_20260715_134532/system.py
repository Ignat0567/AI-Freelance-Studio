import glob
import os
import shutil
import subprocess
import sys
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query

from requirements_checker import check_all, get_components, install_component


router = APIRouter()
_SYSTEM_SETTINGS: dict = {}
_DEFAULT_SYSTEM_SETTINGS: dict = {}
_ALLOWED_SYSTEM_KEYS: list[str] = []
_load_studio_keys: Callable[[], dict] | None = None
_save_studio_keys: Callable[[dict], None] | None = None


def set_system_settings(settings: dict):
    global _SYSTEM_SETTINGS
    _SYSTEM_SETTINGS = settings


def set_system_config_dependencies(
    default_settings: dict,
    allowed_keys: list[str],
    load_studio_keys: Callable[[], dict],
    save_studio_keys: Callable[[dict], None],
):
    global _DEFAULT_SYSTEM_SETTINGS, _ALLOWED_SYSTEM_KEYS, _load_studio_keys, _save_studio_keys
    _DEFAULT_SYSTEM_SETTINGS = default_settings
    _ALLOWED_SYSTEM_KEYS = allowed_keys
    _load_studio_keys = load_studio_keys
    _save_studio_keys = save_studio_keys


def _system_config_dependencies() -> tuple[dict, list[str], Callable[[], dict], Callable[[dict], None]]:
    if _load_studio_keys is None or _save_studio_keys is None:
        raise RuntimeError("System config dependencies are not configured")
    return _DEFAULT_SYSTEM_SETTINGS, _ALLOWED_SYSTEM_KEYS, _load_studio_keys, _save_studio_keys


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "FreelancerStudio", "port": 8080}


@router.get("/api/system/requirements")
def list_requirements():
    return {"components": get_components()}


@router.post("/api/system/check")
def check_requirements():
    return {"results": check_all()}


@router.post("/api/system/install/{component_id}")
def install_requirement(component_id: str):
    result = install_component(component_id)
    return result


@router.get("/api/config/system")
def get_system_config():
    default_settings, allowed_keys, load_studio_keys, _ = _system_config_dependencies()
    all_keys = load_studio_keys()
    saved = all_keys.get("_system", {})
    merged = {**default_settings, **_SYSTEM_SETTINGS, **saved}
    return {k: merged.get(k, default_settings.get(k)) for k in allowed_keys}


@router.post("/api/config/system")
def update_system_config(payload: dict[str, Any]):
    _, allowed_keys, load_studio_keys, save_studio_keys = _system_config_dependencies()
    data = load_studio_keys()
    saved = data.get("_system", {})
    for key in allowed_keys:
        if key in payload:
            saved[key] = payload[key]
            _SYSTEM_SETTINGS[key] = payload[key]
    data["_system"] = saved
    save_studio_keys(data)
    return {"status": "saved"}


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
            _SYSTEM_SETTINGS.get("vscode_path", "code"),
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
            _SYSTEM_SETTINGS.get("pycharm_path", "pycharm"),
            "pycharm",
            "pycharm64",
            os.path.expandvars(r"%LOCALAPPDATA%\JetBrains\Toolbox\scripts\pycharm.cmd"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.3\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm Community Edition 2024.2\bin\pycharm64.exe"),
            os.path.expandvars(r"%ProgramFiles%\JetBrains\PyCharm 2024.2\bin\pycharm64.exe"),
        ] + discovered
    configured = _SYSTEM_SETTINGS.get(f"{editor}_path")
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


@router.post("/api/system/open-path")
def open_system_path(payload: dict):
    return _open_local_path(payload.get("path", ""))


@router.get("/api/system/open-path")
def open_system_path_get(path: str = Query(...)):
    return _open_local_path(path)


@router.post("/api/system/open-editor")
def open_system_editor(payload: dict):
    return _open_editor(payload.get("editor", ""), payload.get("path", ""))
