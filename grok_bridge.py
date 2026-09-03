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
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional


DEFAULT_GROK_MODEL = "grok-4.6"
_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def _run_capture(
    cmd: list[str],
    timeout: int,
    cwd: str | None = None,
    stdin=None,
) -> tuple[int | None, str, str]:
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=stdin,
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


_THOUGHT_KINDS = frozenset({"thought", "thinking", "agent_thought_chunk"})


def _nested_stream_text(value: object, *, include_thoughts: bool = False) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested_kind = str(value.get("type") or "")
        if nested_kind in {"thought", "thinking"} and not include_thoughts:
            return ""
        if nested_kind == "text" and value.get("text"):
            return str(value.get("text"))
        if nested_kind in {"thought", "thinking"} and include_thoughts:
            return str(value.get("text") or value.get("data") or "")
        for key in ("text", "data", "content"):
            inner = _nested_stream_text(value.get(key), include_thoughts=include_thoughts)
            if inner:
                return inner
    if isinstance(value, list):
        return "".join(_nested_stream_text(item, include_thoughts=include_thoughts) for item in value)
    return ""


def stream_event_text(event: dict, *, include_thoughts: bool = False) -> str:
    """Visible assistant text from one streaming-json / ACP line.

    Thoughts stay out of the file body by default: mixing them in produced
    `THREE.P CFSoftShadowMap` on the 2026-09-01 Alethia run. Callers that got
    an empty body after a long think pass include_thoughts as a fallback.
    """
    if not isinstance(event, dict):
        return ""
    kind = str(event.get("type") or event.get("sessionUpdate") or "")
    if kind in _THOUGHT_KINDS:
        if not include_thoughts:
            return ""
        if kind == "agent_thought_chunk":
            return _nested_stream_text(event.get("content"), include_thoughts=True)
        return str(event.get("data") or event.get("text") or "")
    if kind == "text":
        return str(event.get("data") or event.get("text") or "")
    if kind == "agent_message_chunk":
        return _nested_stream_text(event.get("content"), include_thoughts=include_thoughts)
    if kind == "content_block_delta":
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        return str(delta.get("text") or "")
    update = event.get("update")
    if isinstance(update, dict):
        return stream_event_text(update, include_thoughts=include_thoughts)
    params = event.get("params")
    if isinstance(params, dict) and isinstance(params.get("update"), dict):
        return stream_event_text(params["update"], include_thoughts=include_thoughts)
    return ""


def _looks_like_emitted_source(text: str) -> bool:
    lowered = (text or "").casefold()
    return "<<<file" in lowered or "<html" in lowered or "<!doctype" in lowered


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


def _run_capture_stream(
    cmd: list[str],
    timeout: int,
    cwd: str | None = None,
    on_line: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    stdin=None,
) -> tuple[int | None, str, str]:
    proc = None
    popen_kwargs: dict = {
        "stdin": stdin,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "bufsize": 1,
    }
    if os.name == "nt":
        # Hidden console so the child treats stdout as a TTY and line-flushes NDJSON.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        popen_kwargs["startupinfo"] = startupinfo
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        return None, "", "not_found"
    except Exception as exc:
        return None, "", str(exc)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        try:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                stderr_chunks.append(line)
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()
    started = time.monotonic()
    timed_out = False
    cancelled = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            if on_line is not None:
                on_line(line)
            if cancel_check is not None and cancel_check():
                cancelled = True
                break
            if time.monotonic() - started > timeout:
                timed_out = True
                break
        if timed_out or cancelled:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            err_thread.join(timeout=2)
            return None, "".join(stdout_chunks), "timeout" if timed_out else "cancelled"
        proc.wait(timeout=5)
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return None, "".join(stdout_chunks), str(exc)
    err_thread.join(timeout=2)
    return proc.returncode, "".join(stdout_chunks), "".join(stderr_chunks)


def ask_grok_cli(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = "",
    timeout: int = 180,
    executable_path: str = "",
    on_chunk: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    working_directory: str | None = None,
) -> str:
    """One-shot text completion via the Grok CLI. Tools are disabled; cwd is a scratch dir.

    When on_chunk is set, stdout is `--output-format streaming-json` so the coding pane
    can show assistant text as it arrives. Other callers keep a single json object.

    Scratch stays empty of the project on purpose. Copying index.html into it made the
    2026-09-02 retry exit after one planning sentence: plan mode tried to read the file
    with tools, could not, and stopped. The current page belongs in the prompt, not cwd.
    working_directory is accepted so callers can pass the workspace without changing cwd.
    """
    binary = _discover_grok(executable_path)
    if not binary:
        return "Grok CLI is not available. Install it or run `grok login`."
    combined = f"{system_prompt}\n\n{user_prompt}".strip() if system_prompt else user_prompt
    if not combined.strip():
        return ""
    streaming = on_chunk is not None
    with tempfile.TemporaryDirectory(prefix="studio-grok-") as scratch:
        prompt_path = Path(scratch) / "prompt.txt"
        prompt_path.write_text(combined, encoding="utf-8")
        command = [
            binary,
            "--prompt-file",
            str(prompt_path),
            "--output-format",
            "streaming-json" if streaming else "json",
            "--permission-mode",
            "plan",
            "--no-subagents",
            "--disable-web-search",
            "--cwd",
            scratch,
            "--leader-socket",
            str(Path(scratch) / "leader.sock"),
        ]
        selected = normalize_grok_model_id(model)
        if selected:
            command.extend(["-m", selected])
        streamed: list[str] = []
        thoughts: list[str] = []
        stream_errors: list[str] = []

        def _on_line(line: str) -> None:
            try:
                event = json.loads(line)
            except ValueError:
                return
            if not isinstance(event, dict):
                return
            if str(event.get("type") or "") == "error" and event.get("message"):
                stream_errors.append(str(event.get("message")))
            piece = stream_event_text(event)
            if piece:
                streamed.append(piece)
                if on_chunk is not None:
                    on_chunk(piece)
                return
            thought = stream_event_text(event, include_thoughts=True)
            if thought:
                thoughts.append(thought)

        if streaming:
            code, stdout, stderr = _run_capture_stream(
                command,
                timeout=timeout,
                cwd=scratch,
                on_line=_on_line,
                cancel_check=cancel_check,
                stdin=subprocess.DEVNULL,
            )
        else:
            code, stdout, stderr = _run_capture(command, timeout=timeout, cwd=scratch, stdin=subprocess.DEVNULL)
    if code is None:
        if (stderr or "").strip() == "cancelled":
            return "Grok CLI cancelled."
        return "Grok CLI timed out."
    if code != 0 and not stdout.strip() and not streamed and not thoughts:
        return f"Grok CLI error: {_strip_ansi(stderr).strip()[:500] or 'nonzero exit'}"
    text = "".join(streamed)
    thought_text = "".join(thoughts)
    if thought_text and not _looks_like_emitted_source(text) and _looks_like_emitted_source(thought_text):
        text = text + thought_text
    if text:
        return text
    if stream_errors:
        return f"Grok CLI error: {_strip_ansi(stream_errors[-1])[:500]}"
    extracted = extract_grok_text(stdout)
    return extracted or f"Grok CLI error: {_strip_ansi(stderr).strip()[:500] or 'empty response'}"
