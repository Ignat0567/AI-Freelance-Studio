"""Bridge to the Claude Code CLI for opening a project workspace terminal.

Mirrors opencode_bridge.py's discovery/terminal-launch pattern. Studio never
emulates or stores Claude Code's OAuth/session credentials itself -- it only
launches the official CLI and lets it own auth (subscription login is handled
separately by ClaudeSubscriptionAdapter in provider_adapters.py).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def _run_capture(cmd: list[str], timeout: int) -> tuple[int | None, str, str]:
    """Run a short Claude CLI probe without leaving child processes behind on timeout."""
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
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        return None, "", "timeout"
    except Exception as e:
        return None, "", str(e)


def _discover_claude() -> Optional[str]:
    """Find the claude Code CLI binary."""
    candidates = ["claude.cmd", "claude.exe", "claude"]
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
        os.path.expanduser("~\\AppData\\Roaming\\npm\\claude"),
        os.path.expanduser("~\\AppData\\Roaming\\npm\\claude.cmd"),
        "C:\\Program Files\\nodejs\\claude",
        "C:\\Program Files\\nodejs\\claude.cmd",
    ]:
        if os.path.isfile(prefix):
            return prefix
    return None


def _open_visible_terminal(binary: str, args: list[str], title: str, workdir: Optional[str], manual_command: str) -> dict:
    """Spawn a real, visible cmd.exe window running `binary *args` in workdir."""
    if os.name != "nt":
        return {
            "status": "manual_required", "error_code": "terminal_unavailable",
            "message": "Open a terminal and run the command shown below.",
            "manual_command": manual_command,
        }
    executable = shutil.which(binary) or binary
    comspec = os.environ.get("COMSPEC") or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    command_line = subprocess.list2cmdline([executable, *args])
    terminal_command = [comspec, "/d", "/k", f"title {title} & {command_line}"]
    try:
        subprocess.Popen(
            terminal_command,
            cwd=workdir or os.getcwd(),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
            shell=False,
        )
        return {"status": "started", "error_code": "", "manual_command": manual_command}
    except OSError:
        return {
            "status": "manual_required", "error_code": "terminal_launch_failed",
            "message": "The Claude Code terminal could not be opened. Run the command shown below in a terminal.",
            "manual_command": manual_command,
        }


def _standard_windows_tool_paths(tool: str) -> list[str]:
    """Mirrors opencode_bridge.py's _standard_windows_tool_paths -- same shared node/npm
    locations, plus claude's own standard npm-global install spots."""
    if os.name != "nt":
        return []
    program_files = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    app_data = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
    local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    if tool == "node":
        return [os.path.join(root, "nodejs", "node.exe") for root in program_files] + [os.path.join(local_app_data, "Programs", "nodejs", "node.exe")]
    if tool == "npm":
        return [os.path.join(root, "nodejs", "npm.cmd") for root in program_files] + [os.path.join(local_app_data, "Programs", "nodejs", "npm.cmd")]
    if tool == "claude":
        return [
            os.path.join(app_data, "npm", "claude.cmd"),
            os.path.join(app_data, "npm", "claude.exe"),
            *[os.path.join(root, "nodejs", "claude.cmd") for root in program_files],
        ]
    return []


def _detect_onboarding_tool(tool: str) -> dict:
    """Mirrors opencode_bridge.py's _detect_onboarding_tool for node/npm/claude."""
    command_names = {
        "node": ["node.exe", "node"],
        "npm": ["npm.cmd", "npm"],
        "claude": ["claude.cmd", "claude.exe", "claude"],
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


def get_claude_onboarding_dependencies() -> dict:
    components = {tool: _detect_onboarding_tool(tool) for tool in ("node", "npm", "claude")}
    restart_recommended = any(item["path_refresh_recommended"] for item in components.values())
    return {
        "components": components,
        "restart_recommended": restart_recommended,
        "restart_message": "A dependency was found in a standard Windows install location but is not visible on Studio's current PATH. Restart Studio, then select Detect Again." if restart_recommended else "",
    }


def test_claude_readiness(executable_path: str) -> dict:
    """Verify the Claude Code CLI is installed and its own `claude auth login` session is
    active. Studio never reads or stores the OAuth token itself -- only this loggedIn flag."""
    binary = executable_path.strip() if executable_path and os.path.isfile(executable_path) else _discover_claude()
    if not binary:
        return {"ready": False, "error_code": "claude_code_unavailable", "message": "Claude Code CLI was not found.", "executable_path": ""}
    code, stdout, _stderr = _run_capture([binary, "auth", "status"], timeout=15)
    if code != 0:
        return {"ready": False, "error_code": "claude_code_unavailable", "message": "Claude Code CLI did not respond to `claude auth status`.", "executable_path": binary}
    try:
        status = json.loads(_strip_ansi(stdout))
    except ValueError:
        return {"ready": False, "error_code": "claude_code_unavailable", "message": "Claude Code CLI returned an unexpected auth status response.", "executable_path": binary}
    if not status.get("loggedIn"):
        return {"ready": False, "error_code": "claude_code_not_logged_in", "message": "Run `claude auth login` in a terminal, then select Test Connection again.", "executable_path": binary}
    return {"ready": True, "error_code": "", "message": "Claude Code CLI is installed and logged in.", "executable_path": binary, "account": status.get("email") or status.get("account") or ""}


def start_claude_workspace_terminal(workdir: str) -> dict:
    """Open Claude Code in a visible terminal, scoped to a project directory."""
    manual_command = "claude"
    binary = _discover_claude()
    if not binary:
        return {
            "status": "error", "error_code": "executable_missing",
            "message": "Claude Code executable was not found. Install the Claude Code CLI first.",
            "manual_command": manual_command,
        }
    result = _open_visible_terminal(binary, [], "Claude Code", workdir, manual_command)
    if result["status"] == "started":
        result["message"] = "Claude Code opened in a new terminal for this project."
    elif "message" not in result:
        result["message"] = "Open a terminal in the project folder and run the command shown below."
    return result
