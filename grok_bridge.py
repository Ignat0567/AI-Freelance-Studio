"""Bridge to the official Grok CLI (`grok.exe`) for subscription-owned chat.

Mirrors claude_bridge.py: Studio never stores Grok/xAI OAuth tokens. It only
discovers the installed CLI, reads whether `grok models` reports a login, and
runs one-shot text prompts with tools disabled so planner/Elena/BugCatcher
calls cannot write files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


DEFAULT_GROK_MODEL = "grok-4.6"
_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def _run_capture(cmd: list[str], timeout: int, cwd: str | None = None) -> tuple[int | None, str, str]:
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
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
    except FileNotFoundError:
        return None, "", "not_found"
    except Exception as exc:
        return None, "", str(exc)


def _discover_grok(explicit: str = "") -> Optional[str]:
    if explicit and Path(explicit).is_file():
        return explicit
    home_cli = os.path.expanduser(r"~\.grok\bin\grok.exe")
    candidates = [home_cli, "grok.exe", "grok"]
    for item in candidates:
        if os.path.isfile(item):
            return item
        found = shutil.which(item)
        if found:
            return found
    return None


def normalize_grok_model_id(model: str) -> str:
    raw = str(model or "").strip()
    if not raw or raw in {"grok/default", "default"}:
        return DEFAULT_GROK_MODEL
    if raw.startswith("grok/"):
        raw = raw[len("grok/"):]
    if raw.startswith("xai/"):
        raw = raw[len("xai/"):]
    return raw or DEFAULT_GROK_MODEL


def get_grok_onboarding_dependencies(executable_path: str = "") -> dict:
    binary = _discover_grok(executable_path)
    version = ""
    if binary:
        code, stdout, stderr = _run_capture([binary, "--version"], timeout=8)
        if code == 0:
            version = _strip_ansi(stdout or stderr).strip().splitlines()[0] if (stdout or stderr).strip() else ""
    return {
        "components": {
            "grok": {
                "installed": bool(binary),
                "version": version,
                "path": binary or "",
                "path_refresh_recommended": False,
            }
        },
        "restart_recommended": False,
        "restart_message": "",
    }


def test_grok_readiness(executable_path: str = "") -> dict:
    """Verify the Grok CLI is installed and its grok.com / xAI login is active.

    `grok models` prints "You are logged in with grok.com." when the subscription
    session is valid. Studio never reads the credential files themselves.
    """
    binary = _discover_grok(executable_path)
    if not binary:
        return {
            "ready": False,
            "error_code": "grok_cli_unavailable",
            "message": "Grok CLI was not found. Install Grok Build TUI, then select Detect Again.",
            "executable_path": "",
        }
    code, stdout, stderr = _run_capture([binary, "models"], timeout=20)
    text = _strip_ansi(f"{stdout}\n{stderr}")
    if code is None:
        return {
            "ready": False,
            "error_code": "grok_cli_unavailable",
            "message": "Grok CLI did not respond to `grok models`.",
            "executable_path": binary,
        }
    lowered = text.casefold()
    if "not logged" in lowered or "please login" in lowered or "sign in" in lowered:
        return {
            "ready": False,
            "error_code": "grok_not_logged_in",
            "message": "Run `grok login` in a terminal, then select Test Connection again.",
            "executable_path": binary,
        }
    if code != 0:
        return {
            "ready": False,
            "error_code": "grok_not_logged_in",
            "message": "Grok CLI is installed but not logged in. Run `grok login`, then select Test Connection again.",
            "executable_path": binary,
        }
    account = ""
    for line in text.splitlines():
        if "logged in" in line.casefold():
            account = line.strip()
            break
    return {
        "ready": True,
        "error_code": "",
        "message": "Grok CLI is installed and logged in.",
        "executable_path": binary,
        "account": account,
        "available_models": [
            {"id": "grok/grok-4.6", "display_name": "Grok 4.6 (default)"},
            {"id": "grok/grok-4.5", "display_name": "Grok 4.5"},
            {"id": "grok/default", "display_name": "Default (CLI-selected)"},
        ],
    }


def extract_grok_text(stdout: str) -> str:
    """Pull the assistant text out of `grok --output-format json` without assuming one schema."""
    raw = _strip_ansi(stdout).strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
            except ValueError:
                return raw
        else:
            return raw
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return raw
    for key in ("result", "text", "content", "message", "output", "output_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("content") or value.get("text")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
            if isinstance(nested, list):
                parts = []
                for item in nested:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                if parts:
                    return "".join(parts).strip()
    return raw


def ask_grok_cli(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = "",
    timeout: int = 180,
    executable_path: str = "",
) -> str:
    """One-shot text completion via the Grok CLI. Tools are disabled; cwd is a scratch dir."""
    binary = _discover_grok(executable_path)
    if not binary:
        return "Grok CLI is not available. Install it or run `grok login`."
    combined = f"{system_prompt}\n\n{user_prompt}".strip() if system_prompt else user_prompt
    if not combined.strip():
        return ""
    with tempfile.TemporaryDirectory(prefix="studio-grok-") as scratch:
        prompt_path = Path(scratch) / "prompt.txt"
        prompt_path.write_text(combined, encoding="utf-8")
        command = [
            binary,
            "--prompt-file",
            str(prompt_path),
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--no-subagents",
            "--disable-web-search",
            "--cwd",
            scratch,
        ]
        selected = normalize_grok_model_id(model)
        if selected:
            command.extend(["-m", selected])
        code, stdout, stderr = _run_capture(command, timeout=timeout, cwd=scratch)
    if code is None:
        return "Grok CLI timed out."
    if code != 0 and not stdout.strip():
        return f"Grok CLI error: {_strip_ansi(stderr).strip()[:500] or 'nonzero exit'}"
    text = extract_grok_text(stdout)
    return text or f"Grok CLI error: {_strip_ansi(stderr).strip()[:500] or 'empty response'}"
