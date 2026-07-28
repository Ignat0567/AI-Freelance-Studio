"""Local, credential-free execution bridge to an authenticated OpenCode CLI."""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROVIDER_REGISTRY = {
    "opencode_bridge": {
        "display_name": "OpenCode",
        "connection_type": "opencode_bridge",
        "description": "Local authenticated bridge. OpenCode owns authentication.",
    }
}
CAPABILITY_NAMES = (
    "text_input", "image_input", "file_input", "streaming", "structured_output",
    "model_listing", "session_continuity", "cancellation", "health_status",
    "single_image_input", "multi_image_input",
)
SECRET_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization|cookie)\b\s*[:=]\s*[^\s'\"]+|\bsk-[A-Za-z0-9_-]{16,}\b")
DEFAULT_OPENCODE_MODEL = "nvidia/deepseek-ai/deepseek-v4-pro"
PROVIDER_ERROR_RE = re.compile(r"(?i)\b(too many requests|rate[_ -]?limited|rate limit|quota exceeded|resourceexhausted|resource exhausted|ai_apicallerror|ai_retryerror)\b")
STUDIO_METADATA_FILES = frozenset(
    {
        ".freelancerstudio-project.json",
        "execution_package.json",
        "execution_prompt.md",
        "delivery_report.md",
        "generated_project_summary.json",
        "opencode_command.txt",
        "README_NEXT_STEPS.md",
    }
)
MEANINGFUL_FILE_NAMES = frozenset({"package.json", "README.md", "pyproject.toml", "requirements.txt", "index.html", "main.py", "server.py"})
MEANINGFUL_DIR_NAMES = frozenset({"src", "app", "frontend", "backend"})
MEANINGFUL_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".py", ".html", ".css", ".json", ".md")


class BrowserAuthConnectionAdapter(ABC):
    """Future contract for documented browser/OAuth integrations only."""

    @abstractmethod
    def start_auth(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_authorization_url(self) -> str: ...

    @abstractmethod
    def wait_for_callback(self) -> dict[str, Any]: ...

    @abstractmethod
    def refresh(self) -> dict[str, Any]: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_status(self) -> dict[str, Any]: ...


def capability(status: str, evidence: str) -> dict[str, str]:
    if status not in {"supported", "unsupported", "unknown"}:
        raise ValueError(f"Invalid capability status: {status}")
    return {"status": status, "evidence": evidence}


def effective_capabilities(*capability_sets: dict[str, Any]) -> dict[str, bool]:
    """A capability is effective only when every participating layer proved it."""
    names = {name for item in capability_sets for name in item}
    result = {}
    for name in names:
        states = []
        for item in capability_sets:
            value = item.get(name, {})
            states.append(value.get("status") if isinstance(value, dict) else value)
        result[name] = bool(states) and all(state == "supported" for state in states)
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value or "")


def _safe_summary(value: str, limit: int = 1000) -> str:
    return SECRET_VALUE_RE.sub("<redacted>", _strip_ansi(value).strip())[-limit:]


def _image_dimensions(path: str) -> tuple[int | None, int | None]:
    """Read common image dimensions without decoding or changing evidence files."""
    try:
        with open(path, "rb") as stream:
            header = stream.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            return struct.unpack(">II", header[16:24])
        if header[:3] == b"GIF" and len(header) >= 10:
            return struct.unpack("<HH", header[6:10])
    except OSError:
        pass
    return None, None


def _attachment_metadata(paths: list[str]) -> list[dict[str, Any]]:
    metadata = []
    for path in paths:
        width, height = _image_dimensions(path)
        metadata.append({
            "path": os.path.abspath(path),
            "format": Path(path).suffix.lstrip(".").lower() or "unknown",
            "bytes": os.path.getsize(path),
            "width": width,
            "height": height,
        })
    return metadata


def _parse_json_event_stream(stdout: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    non_json_lines: list[str] = []
    event_types: list[str] = []
    terminal_event = False
    permission_event = False
    explicit_errors: list[Any] = []
    session_id = ""
    for raw_line in _strip_ansi(stdout).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            non_json_lines.append(_safe_summary(line, 500))
            continue
        if not isinstance(event, dict):
            non_json_lines.append(_safe_summary(line, 500))
            continue
        events.append(event)
        event_type = str(event.get("type") or event.get("event") or event.get("kind") or "unknown")
        event_types.append(event_type)
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        reason = str(part.get("reason") or event.get("reason") or "").lower()
        terminal_event = terminal_event or (event_type == "step_finish" and reason == "stop")
        lowered = json.dumps(event, ensure_ascii=True).lower()
        permission_event = permission_event or "permission" in lowered or "approval" in lowered
        if "error" in event:
            explicit_errors.append(event.get("error"))
        if not session_id and isinstance(event.get("sessionID") or event.get("sessionId") or event.get("session_id"), str):
            session_id = str(event.get("sessionID") or event.get("sessionId") or event.get("session_id"))
    return {
        "events": events,
        "event_types": event_types,
        "valid_event_lines": len(events),
        "non_json_lines": non_json_lines,
        "non_json_line_count": len(non_json_lines),
        "terminal_event": terminal_event,
        "permission_event": permission_event,
        "explicit_errors": explicit_errors,
        "session_id": session_id[:16] + "..." if len(session_id) > 16 else session_id,
    }


def _error_category(stderr: str, stdout: str) -> str:
    text = f"{stderr}\n{stdout}".lower()
    if any(item in text for item in ("request rejected", "request was rejected", "rejected request")):
        return "request_rejected"
    if any(item in text for item in ("unknown option", "invalid option", "unknown argument", "invalid flag", "invalid value", "usage: opencode run", "missing argument")):
        return "flag_rejected"
    if any(item in text for item in ("no such file", "enoent", "failed to read", "cannot read attachment")):
        return "attachment_load_failed"
    if any(item in text for item in ("unauthorized", "forbidden", "missing credentials", "invalid credentials", "expired credential", "authentication", "login required")):
        return "authentication_failure"
    if any(item in text for item in ("unknown model", "unavailable model", "model not found", "unsupported model", "invalid model", "provider not found", "model does not exist")):
        return "model_rejected"
    if _provider_error_diagnostics(stderr, stdout)["provider_error"] or any(item in text for item in ("provider error", "api error", "worker local total request limit reached", "http 4", "bad request")):
        return "provider_error"
    if any(item in text for item in ("upload", "payload too large", "request entity too large")):
        return "provider_upload_failed"
    return "process_failed"


def _provider_error_diagnostics(stderr: str, stdout: str) -> dict[str, Any]:
    text = _safe_summary(f"{stderr}\n{stdout}", 4000)
    provider = ""
    model = ""
    provider_match = re.search(r"\bproviderID=([^\s]+)", text)
    model_match = re.search(r"\bmodelID=([^\s]+)", text)
    if provider_match:
        provider = provider_match.group(1).strip('"')[:100]
    if model_match:
        model = model_match.group(1).strip('"')[:150]
    signals = sorted({match.group(1) for match in PROVIDER_ERROR_RE.finditer(text)})
    retry_observed = bool(re.search(r"(?i)\b(retry|retryerror|failed after \d+ attempts)\b", text))
    return {
        "provider_error": bool(signals),
        "provider_id": provider,
        "model_id": model,
        "error_signals": signals,
        "retry_observed": retry_observed,
        "diagnostic_summary": text[-1000:] if signals else "",
    }


def _find_binary() -> str:
    from opencode_bridge import _discover_opencode
    return _discover_opencode() or ""


def _run_capture(command: list[str], timeout: int, cwd: str | None = None) -> tuple[int | None, str, str]:
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", str(exc)


def _terminate_owned_process_tree(process: subprocess.Popen) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, text=True, timeout=10)
            return process.poll() is not None
        except Exception:
            pass
    try:
        process.terminate()
        return True
    except Exception:
        try:
            process.kill()
            return False
        except Exception:
            return False


def _run_owned_capture(command: list[str], timeout: int, cwd: str) -> tuple[int | None, str, str, bool, bool]:
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout or "", stderr or "", False, False
        except subprocess.TimeoutExpired:
            terminated_gracefully = _terminate_owned_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                terminated_gracefully = False
                stdout, stderr = process.communicate()
            return None, stdout or "", stderr or "", True, terminated_gracefully
    except OSError as exc:
        return None, "", str(exc), False, False


def _safe_cli_invocation(binary: str, model: str, attachment_count: int, *, workspace_bound: bool = False) -> list[str]:
    invocation = [binary, "run", "<prompt>", "--model", model, "--format", "json"]
    if workspace_bound:
        invocation.extend(["--dir", "<workspace>"])
    invocation.extend(["--file", "<attachment>"] * attachment_count)
    return invocation


def _scan_meaningful_artifacts(workdir: str | None, *, max_files: int = 200, max_depth: int = 4, limit: int = 50) -> tuple[str, ...]:
    if not workdir:
        return ()
    root = Path(workdir).expanduser().resolve()
    if not root.is_dir():
        return ()
    found: list[str] = []
    scanned = 0
    for child in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root)).casefold()):
        if child.is_symlink():
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        relative = child.relative_to(root)
        if len(relative.parts) > max_depth or any(part in {"", ".", ".."} for part in relative.parts):
            continue
        safe_relative = relative.as_posix()
        if child.name in STUDIO_METADATA_FILES:
            continue
        if child.is_dir():
            if child.name in MEANINGFUL_DIR_NAMES:
                found.append(safe_relative + "/")
        elif child.is_file():
            scanned += 1
            if scanned > max_files:
                break
            if child.name in MEANINGFUL_FILE_NAMES or child.suffix in MEANINGFUL_SUFFIXES:
                found.append(safe_relative)
        if len(found) >= limit:
            break
    return tuple(found)


def _artifact_validation_failure(request: dict[str, Any], workdir: str | None) -> str:
    expected_path = str(request.get("expected_artifact_path") or "").strip()
    expected_text = request.get("expected_artifact_text")
    if not expected_path:
        return ""
    base = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
    target = Path(expected_path)
    if not target.is_absolute():
        target = base / target
    try:
        resolved = target.resolve()
    except OSError:
        return "Requested artifact path is invalid."
    if workdir and resolved != base and base not in resolved.parents:
        return "Requested artifact path escapes the workspace."
    if not resolved.is_file():
        return "Requested artifact was not created."
    if expected_text is not None:
        actual = resolved.read_text(encoding="utf-8", errors="replace")
        allowed = {str(expected_text), f"{expected_text}\n", f"{expected_text}\r\n"}
        if actual not in allowed:
            return "Requested artifact content did not match."
    return ""


@dataclass
class OpenCodeBridgeConnection:
    connection_id: str
    name: str
    configured_model: str
    enabled: bool = True
    transport_type: str = "cli"
    local_endpoint: str = ""
    executable_path: str = ""
    configured_provider: str = ""
    capabilities: dict[str, dict[str, str]] = field(default_factory=dict)
    last_checked_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OpenCodeBridgeConnection":
        return cls(
            connection_id=str(value.get("connection_id") or f"opencode-{uuid.uuid4().hex[:12]}"),
            name=str(value.get("name") or "My OpenCode"),
            configured_model=str(value.get("configured_model") or ""),
            enabled=bool(value.get("enabled", True)),
            transport_type=str(value.get("transport_type") or "cli"),
            local_endpoint=str(value.get("local_endpoint") or ""),
            executable_path=str(value.get("executable_path") or ""),
            configured_provider=str(value.get("configured_provider") or ""),
            capabilities=value.get("capabilities") if isinstance(value.get("capabilities"), dict) else {},
            last_checked_at=str(value.get("last_checked_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id, "name": self.name,
            "connection_type": "opencode_bridge", "enabled": self.enabled,
            "transport_type": self.transport_type, "local_endpoint": self.local_endpoint,
            "executable_path": self.executable_path, "configured_model": self.configured_model,
            "configured_provider": self.configured_provider, "capabilities": self.capabilities,
            "last_checked_at": self.last_checked_at,
            "authentication_owner": "OpenCode", "stores_authentication": False,
        }

    def available_models(self) -> list[str]:
        binary = self.executable_path or _find_binary()
        if not binary:
            return []
        code, stdout, _stderr = _run_capture([binary, "models"], 30)
        if code != 0:
            return []
        return [line.strip() for line in _strip_ansi(stdout).splitlines() if "/" in line and " " not in line.strip()]

    def capability_report(self) -> dict[str, dict[str, str]]:
        report = {name: capability("unknown", "Not probed") for name in CAPABILITY_NAMES}
        report.update({name: value for name, value in self.capabilities.items() if name in report and isinstance(value, dict)})
        binary = self.executable_path or _find_binary()
        if binary:
            report["file_input"] = capability("supported", "Installed OpenCode CLI documents run --file attachment.")
            report["model_listing"] = capability("supported", "Installed OpenCode CLI exposes the models command.")
            report["session_continuity"] = capability("supported", "Installed OpenCode CLI documents run --session.")
            report["cancellation"] = capability("supported", "CLI subprocess can be terminated by the bridge.")
            report["health_status"] = capability("supported", "CLI executable can be invoked for a text health probe.")
            report["streaming"] = capability("unsupported", "The initial bridge consumes completed CLI JSON events only.")
            report["structured_output"] = capability("unknown", "No native OpenCode structured-output CLI option was documented; strict JSON validation is adapter-side.")
        else:
            report["health_status"] = capability("unsupported", "OpenCode executable was not found.")
        return report

    def _extract_text(self, stdout: str) -> str:
        texts = []
        for line in _strip_ansi(stdout).splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = event.get("part") if isinstance(event, dict) else None
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "\n".join(texts).strip() or _strip_ansi(stdout).strip()

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute a fresh, local, read-only-by-default CLI request without credentials."""
        binary = self.executable_path or _find_binary()
        model = str(request.get("requested_model") or self.configured_model or DEFAULT_OPENCODE_MODEL)
        if not binary:
            return {"status": "unavailable", "failure_stage": "before_invocation", "error_category": "bridge_unavailable", "errors": ["OpenCode executable was not found"], "text": ""}
        if not self.enabled:
            return {"status": "unavailable", "failure_stage": "before_invocation", "error_category": "misconfigured", "errors": ["Connection is disabled"], "text": ""}
        if not model:
            return {"status": "unavailable", "failure_stage": "before_invocation", "error_category": "model_unavailable", "errors": ["No OpenCode model is configured"], "text": ""}
        requested_attachments = request.get("image_attachments", []) + request.get("file_attachments", [])
        attachments = [str(path) for path in requested_attachments if os.path.isfile(str(path))]
        if len(attachments) != len(requested_attachments):
            return {"status": "error", "failure_stage": "before_invocation", "error_category": "attachment_missing", "errors": ["An attachment is missing"], "text": "", "attachment_count": len(requested_attachments)}
        attachment_metadata = _attachment_metadata(attachments)
        prompt = f"{request.get('system_instruction', '')}\n\n{request.get('user_content') or request.get('text_content') or ''}".strip()
        workspace_path = request.get("workspace_path")
        workdir = str(Path(workspace_path).expanduser().resolve()) if workspace_path else None
        if workdir and not os.path.isdir(workdir):
            return {"status": "error", "failure_stage": "before_invocation", "error_category": "workspace_unavailable", "errors": ["Workspace path is not available"], "text": "", "attachment_count": len(attachments)}
        command = [binary, "run", prompt, "--model", model, "--format", "json"]
        if workdir:
            command.extend(["--dir", workdir])
        for path in attachments:
            command.extend(["--file", path])
        timeout = max(1, int(request.get("timeout", 120)))
        started = time.monotonic()
        timed_out = False
        terminated_gracefully = False
        if workdir:
            code, stdout, stderr, timed_out, terminated_gracefully = _run_owned_capture(command, timeout, workdir)
        else:
            # A temporary working directory prevents the judge from being placed inside a project tree.
            with tempfile.TemporaryDirectory(prefix="freelancerstudio-opencode-") as temp_workdir:
                code, stdout, stderr = _run_capture(command, timeout, temp_workdir)
                timed_out = code is None and stderr == "timeout"
        duration = round(time.monotonic() - started, 3)
        event_stream = _parse_json_event_stream(stdout)
        provider_diagnostics = _provider_error_diagnostics(stderr, stdout)
        text = self._extract_text(stdout)
        raw_error = _safe_summary(stderr or stdout)
        if code is None:
            if provider_diagnostics["provider_error"]:
                meaningful_artifacts = _scan_meaningful_artifacts(workdir)
                return {"status": "partial" if meaningful_artifacts else "error", "failure_stage": "model_execution", "error_category": "provider_error", "classification": "provider_error", "errors": [raw_error or provider_diagnostics["diagnostic_summary"]], "text": text, "duration": duration, "timeout": timed_out, "timed_out": timed_out, "terminated_owned_process": bool(workdir), "terminated_gracefully": terminated_gracefully, "exit_code": None, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": event_stream["terminal_event"], "provider_id": provider_diagnostics["provider_id"], "model_id": provider_diagnostics["model_id"], "provider_error_signals": provider_diagnostics["error_signals"], "provider_retry_observed": provider_diagnostics["retry_observed"], "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
            category = "timeout" if timed_out else "bridge_unavailable"
            meaningful_artifacts = _scan_meaningful_artifacts(workdir)
            classification = "opencode_usable_but_nonterminating" if timed_out and meaningful_artifacts else "opencode_timeout_without_artifact" if timed_out else "bridge_unavailable"
            return {"status": "partial" if classification == "opencode_usable_but_nonterminating" else "error", "failure_stage": "model_execution" if category == "timeout" else "cli_invocation", "error_category": category, "classification": classification, "errors": [raw_error], "text": text, "duration": duration, "timeout": timed_out, "timed_out": timed_out, "terminated_owned_process": bool(workdir), "terminated_gracefully": terminated_gracefully, "exit_code": None, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": event_stream["terminal_event"], "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
        if code != 0:
            meaningful_artifacts = _scan_meaningful_artifacts(workdir)
            category = _error_category(stderr, stdout)
            classification = category if category == "provider_error" or not meaningful_artifacts else "opencode_process_failed_with_artifacts"
            return {"status": "partial" if meaningful_artifacts else "error", "failure_stage": "cli_process_exit", "error_category": category, "classification": classification, "errors": [raw_error], "text": text, "duration": duration, "timeout": False, "timed_out": False, "exit_code": code, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": event_stream["terminal_event"], "provider_id": provider_diagnostics["provider_id"], "model_id": provider_diagnostics["model_id"], "provider_error_signals": provider_diagnostics["error_signals"], "provider_retry_observed": provider_diagnostics["retry_observed"], "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
        meaningful_artifacts = _scan_meaningful_artifacts(workdir)
        if event_stream["non_json_line_count"] and not event_stream["valid_event_lines"]:
            return {"status": "error", "failure_stage": "response_parsing", "error_category": "json_stream_failure", "classification": "opencode_json_stream_failure", "errors": event_stream["non_json_lines"][:3], "text": text, "duration": duration, "timeout": False, "timed_out": False, "exit_code": code, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": [], "opencode_json_valid_lines": 0, "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": False, "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
        if event_stream["valid_event_lines"] and not event_stream["terminal_event"]:
            return {"status": "error", "failure_stage": "response_parsing", "error_category": "json_stream_failure", "classification": "opencode_json_stream_failure", "errors": ["OpenCode JSON event stream ended without a terminal stop event."], "text": text, "duration": duration, "timeout": False, "timed_out": False, "exit_code": code, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": False, "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
        artifact_error = _artifact_validation_failure(request, workdir)
        if artifact_error:
            return {"status": "error", "failure_stage": "artifact_validation", "error_category": "artifact_validation_failed", "classification": "opencode_artifact_validation_failed", "errors": [artifact_error], "text": text, "duration": duration, "timeout": False, "timed_out": False, "exit_code": code, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": event_stream["terminal_event"], "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}
        return {"status": "success", "text": text, "structured_output": None, "provider_connection": self.connection_id, "bridge": "opencode_bridge", "underlying_provider": model.split("/", 1)[0] if "/" in model else "", "model": model, "capabilities_used": ["text_input"] + (["file_input"] if attachments else []), "session_id": event_stream["session_id"] or "fresh-cli-session", "duration": duration, "errors": [], "failure_stage": "", "timeout": False, "timed_out": False, "exit_code": code, "stdout_summary": _safe_summary(stdout), "stderr_summary": _safe_summary(stderr), "opencode_events": event_stream["event_types"], "opencode_json_valid_lines": event_stream["valid_event_lines"], "opencode_json_non_json_lines": event_stream["non_json_line_count"], "opencode_terminal_event": event_stream["terminal_event"], "files_detected": bool(meaningful_artifacts), "meaningful_artifacts": list(meaningful_artifacts), "attachment_count": len(attachments), "attachment_metadata": attachment_metadata, "prompt_characters": len(prompt), "cli_invocation": _safe_cli_invocation(binary, model, len(attachments), workspace_bound=bool(workdir))}

    def test_connection(self, include_vision: bool = False) -> dict[str, Any]:
        response = self.execute({"user_content": "Return exactly OPENCODE_BRIDGE_TEXT_OK", "timeout": 120})
        report = self.capability_report()
        if response.get("status") == "success" and response.get("text", "").strip() == "OPENCODE_BRIDGE_TEXT_OK":
            report["text_input"] = capability("supported", "Real CLI probe returned OPENCODE_BRIDGE_TEXT_OK exactly.")
            auth = "authenticated"
            health = "available_authenticated"
        else:
            report["text_input"] = capability("unsupported", f"Real CLI probe failed: {response.get('error_category', 'unknown')}")
            auth = "unauthenticated" if response.get("error_category") == "unauthenticated" else "unknown"
            health = "available_unauthenticated" if auth == "unauthenticated" else "unavailable"
        self.capabilities, self.last_checked_at = report, _utc_now()
        vision = None
        if include_vision and health == "available_authenticated":
            # This is a real attachment probe, not model-name capability inference.
            marker = "OPENCODE-BRIDGE-VISION-7391"
            with tempfile.NamedTemporaryFile("w", suffix=".svg", encoding="utf-8", delete=False) as probe:
                probe.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="240"><rect width="100%" height="100%" fill="#071426"/><text x="40" y="135" fill="#f8fafc" font-size="42">{marker}</text></svg>')
                probe_path = probe.name
            try:
                vision = self.probe_image(probe_path, marker)
                report = self.capabilities
            finally:
                try:
                    os.unlink(probe_path)
                except OSError:
                    pass
        return {"health_status": health, "authentication_status": auth, "capabilities": report, "available_models": self.available_models(), "response": response, "vision": vision, "last_checked_at": self.last_checked_at}

    def probe_image(self, image_path: str, marker: str) -> dict[str, Any]:
        response = self.execute({"user_content": f"Return the exact unique identifier visible in the image: {marker}", "image_attachments": [image_path], "timeout": 180})
        report = self.capability_report()
        if response.get("status") == "success" and marker in response.get("text", ""):
            report["image_input"] = capability("supported", "Real attached-image probe returned the unique visible marker.")
            report["single_image_input"] = capability("supported", "One real attached image returned its unique visible marker.")
            report["multi_image_input"] = capability("unknown", "A single-image probe does not prove multi-image transport.")
        else:
            report["image_input"] = capability("unsupported", f"Attached-image probe did not prove image receipt: {response.get('error_category', 'marker_not_returned')}")
            report["single_image_input"] = capability("unsupported", "The single-image marker probe failed.")
        self.capabilities, self.last_checked_at = report, _utc_now()
        return {"response": response, "capabilities": report, "effective_image_input": report["image_input"]["status"] == "supported"}


def bridge_effective_capabilities(connection: OpenCodeBridgeConnection, model_capabilities: dict[str, Any] | None = None) -> dict[str, bool]:
    transport = connection.capability_report()
    adapter = {"text_input": "supported", "image_input": "supported", "file_input": "supported"}
    # The real bridge probe is authoritative for image support; model-name metadata cannot elevate it.
    model = model_capabilities or {"text_input": "supported", "image_input": transport.get("image_input", {}).get("status", "unknown"), "file_input": "supported"}
    return effective_capabilities(transport, model, adapter)
