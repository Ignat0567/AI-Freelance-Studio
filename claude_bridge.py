"""Bridge to the Claude Code CLI for status checks and interactive login.

Mirrors opencode_bridge.py's discovery/status/terminal-launch pattern so
Claude Code can be connected the same way OpenCode is: a status check and
a visible interactive terminal for the official CLI's own login flow.
Studio never emulates or stores Claude Code's OAuth/session credentials
itself -- it only launches the official CLI and lets it own auth.
"""

from __future__ import annotations

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


def get_claude_status() -> dict:
    """Return safe Claude Code CLI install/version status. Never returns secrets."""
    binary = _discover_claude()
    status = {
        "installed": bool(binary),
        "binary": binary or "",
        "version": "",
    }
    if not binary:
        return status
    try:
        _returncode, stdout, stderr = _run_capture([binary, "--version"], timeout=10)
        status["version"] = _strip_ansi((stdout or stderr or "").strip())
    except Exception:
        pass
    return status


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


def start_claude_auth_terminal(workdir: Optional[str] = None) -> dict:
    """Open the interactive Claude Code CLI-owned login flow."""
    manual_command = "claude login"
    binary = _discover_claude()
    if not binary:
        return {
            "status": "error", "error_code": "executable_missing",
            "message": "Claude Code executable was not found. Install the Claude Code CLI first.",
            "manual_command": manual_command,
        }
    result = _open_visible_terminal(binary, ["login"], "Claude Code Authentication", workdir, manual_command)
    if result["status"] == "started":
        result["message"] = "Complete the login steps in the Claude Code terminal. When finished, return here and select Test Connection."
    elif "message" not in result:
        result["message"] = "Open a terminal and run the command shown below."
    return result


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
