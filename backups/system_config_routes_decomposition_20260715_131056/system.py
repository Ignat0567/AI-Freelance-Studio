import glob
import os
import shutil
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Query

from requirements_checker import check_all, get_components, install_component


router = APIRouter()
_SYSTEM_SETTINGS: dict = {}


def set_system_settings(settings: dict):
    global _SYSTEM_SETTINGS
    _SYSTEM_SETTINGS = settings


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
