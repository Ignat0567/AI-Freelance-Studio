"""Local, credential-free execution bridge to an authenticated OpenCode CLI."""

from __future__ import annotations

import json
import logging
import os
import re
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
)


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
        model = str(request.get("requested_model") or self.configured_model)
        if not binary:
            return {"status": "unavailable", "error_category": "bridge_unavailable", "errors": ["OpenCode executable was not found"], "text": ""}
        if not self.enabled:
            return {"status": "unavailable", "error_category": "misconfigured", "errors": ["Connection is disabled"], "text": ""}
        if not model:
            return {"status": "unavailable", "error_category": "model_unavailable", "errors": ["No OpenCode model is configured"], "text": ""}
        attachments = [str(path) for path in request.get("image_attachments", []) + request.get("file_attachments", []) if os.path.isfile(str(path))]
        if len(attachments) != len(request.get("image_attachments", []) + request.get("file_attachments", [])):
            return {"status": "error", "error_category": "attachment_missing", "errors": ["An attachment is missing"], "text": ""}
        prompt = f"{request.get('system_instruction', '')}\n\n{request.get('user_content') or request.get('text_content') or ''}".strip()
        command = [binary, "run", prompt, "--model", model, "--format", "json"]
        for path in attachments:
            command.extend(["--file", path])
        timeout = max(1, int(request.get("timeout", 120)))
        started = time.monotonic()
        # A temporary working directory prevents the judge from being placed inside a project tree.
        with tempfile.TemporaryDirectory(prefix="freelancerstudio-opencode-") as workdir:
            code, stdout, stderr = _run_capture(command, timeout, workdir)
        duration = round(time.monotonic() - started, 3)
        text = self._extract_text(stdout)
        raw_error = _strip_ansi(stderr or stdout)[-1000:]
        if code is None:
            category = "timeout" if stderr == "timeout" else "bridge_unavailable"
            return {"status": "error", "error_category": category, "errors": [raw_error], "text": text, "duration": duration}
        if code != 0:
            lower = raw_error.lower()
            category = "unauthenticated" if any(x in lower for x in ("auth", "unauthorized", "login")) else "model_unavailable" if "model" in lower else "request_failed"
            return {"status": "error", "error_category": category, "errors": [raw_error], "text": text, "duration": duration}
        return {"status": "success", "text": text, "structured_output": None, "provider_connection": self.connection_id, "bridge": "opencode_bridge", "underlying_provider": model.split("/", 1)[0] if "/" in model else "", "model": model, "capabilities_used": ["text_input"] + (["file_input"] if attachments else []), "session_id": "fresh-cli-session", "duration": duration, "errors": []}

    def test_connection(self) -> dict[str, Any]:
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
        return {"health_status": health, "authentication_status": auth, "capabilities": report, "available_models": self.available_models(), "response": response, "last_checked_at": self.last_checked_at}

    def probe_image(self, image_path: str, marker: str) -> dict[str, Any]:
        response = self.execute({"user_content": f"Return the exact unique identifier visible in the image: {marker}", "image_attachments": [image_path], "timeout": 180})
        report = self.capability_report()
        if response.get("status") == "success" and marker in response.get("text", ""):
            report["image_input"] = capability("supported", "Real attached-image probe returned the unique visible marker.")
        else:
            report["image_input"] = capability("unsupported", f"Attached-image probe did not prove image receipt: {response.get('error_category', 'marker_not_returned')}")
        self.capabilities, self.last_checked_at = report, _utc_now()
        return {"response": response, "capabilities": report, "effective_image_input": report["image_input"]["status"] == "supported"}


def bridge_effective_capabilities(connection: OpenCodeBridgeConnection, model_capabilities: dict[str, Any] | None = None) -> dict[str, bool]:
    transport = connection.capability_report()
    adapter = {"text_input": "supported", "image_input": "supported", "file_input": "supported"}
    # The real bridge probe is authoritative for image support; model-name metadata cannot elevate it.
    model = model_capabilities or {"text_input": "supported", "image_input": transport.get("image_input", {}).get("status", "unknown"), "file_input": "supported"}
    return effective_capabilities(transport, model, adapter)
