import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_state import project_snapshot_fingerprint
from repair_scope import EXCLUDED_REPAIR_DIRS, EXCLUDED_REPAIR_EXTENSIONS


IGNORED_DIRS = set(EXCLUDED_REPAIR_DIRS) | {"local_backups", ".opencode_backups"}
IGNORED_EXTS = set(EXCLUDED_REPAIR_EXTENSIONS)
SECRET_PATTERNS = [
    r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b",
    r"sk-[A-Za-z0-9_-]{16,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*=\s*(['\"])(?!replace_me|your_|example|changeme|<)[^'\"\r\n]{10,}\2",
]
TODO_PATTERNS = ("todo: implement", "pass  # todo", "raise notimplementederror", "not implemented", "fake output")
OUTPUT_TAIL_LIMIT = 4000


@dataclass
class RuntimeAdapterResult:
    applicable: bool
    started: bool
    verified: bool
    stopped_cleanly: bool
    evidence: dict[str, Any]
    error: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeServerHandle:
    adapter: str
    base_url: str
    process: subprocess.Popen
    result: RuntimeAdapterResult


class RuntimeAdapter:
    name = "runtime"

    def run(self, project: dict, root: str) -> RuntimeAdapterResult:
        raise NotImplementedError

    def start_http_server(self, project: dict, root: str, env_extra: dict[str, str] | None = None) -> tuple[RuntimeAdapterResult, RuntimeServerHandle | None]:
        return RuntimeAdapterResult(False, False, False, True, {"reason": f"{self.name} does not expose a reusable HTTP runtime"}, ""), None


def _walk_files(root: str):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in IGNORED_EXTS:
                continue
            path = os.path.join(current, name)
            yield os.path.relpath(path, root).replace(os.sep, "/"), path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _add(checks: list[dict[str, Any]], name: str, status: str, evidence: dict[str, Any]):
    checks.append({"name": name, "status": status, "evidence": evidence})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _redact_secrets(value: Any) -> str:
    text = "" if value is None else str(value)
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = re.sub(pattern, lambda m: m.group(0).split("=", 1)[0] + "=<redacted>" if "=" in m.group(0) else "<redacted>", redacted)
    redacted = re.sub(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s'\"]+", r"\1=<redacted>", redacted)
    return redacted


def _tail_output(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return _redact_secrets(value)[-OUTPUT_TAIL_LIMIT:]


def _run_command(command: list[str], cwd: str, timeout: int = 120) -> dict[str, Any]:
    started = time.time()
    command_text = _redact_secrets(" ".join(command))
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return {
            "command": command_text,
            "cwd": cwd,
            "timeout": timeout,
            "exit_code": result.returncode,
            "stdout_tail": _tail_output(result.stdout),
            "stderr_tail": _tail_output(result.stderr),
            "duration": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command_text,
            "cwd": cwd,
            "timeout": timeout,
            "exit_code": "timeout",
            "stdout_tail": _tail_output(getattr(exc, "stdout", "") or ""),
            "stderr_tail": _tail_output(getattr(exc, "stderr", "") or ""),
            "duration": round(time.time() - started, 3),
        }


def _check_secrets(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        try:
            text = _read(path)
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(rel)
                break
    return not hits, hits


def _check_todos(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        if not rel.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".md")):
            continue
        try:
            text = _read(path).lower()
        except Exception:
            continue
        if any(pattern in text for pattern in TODO_PATTERNS):
            hits.append(rel)
    return not hits, hits


def _static_entrypoint(root: str) -> tuple[str, str] | None:
    for rel in ("index.html", "frontend/index.html", "dist/index.html"):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return rel, os.path.dirname(path)
    return None


def _local_asset_refs(html: str) -> list[str]:
    refs = []
    for attr in ("href", "src"):
        for match in re.finditer(rf"\b{attr}\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE):
            ref = match.group(1).strip()
            if not ref or ref.startswith(("http://", "https://", "//", "mailto:", "tel:", "#", "data:")):
                continue
            refs.append(ref.split("?", 1)[0].split("#", 1)[0])
    return _dedupe_strings(refs)


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = item.replace("\\", "/")
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _asset_exists(site_root: str, ref: str) -> bool:
    if not ref or os.path.isabs(ref) and not ref.startswith("/"):
        return False
    rel = ref.lstrip("/").replace("/", os.sep)
    if ".." in Path(rel).parts:
        return False
    return os.path.exists(os.path.join(site_root, rel))


def _adapter_status(result: RuntimeAdapterResult) -> str:
    if not result.applicable:
        return "not_applicable"
    start_ok = result.started or bool(result.evidence.get("credential_free"))
    return "passed" if start_ok and result.verified and result.stopped_cleanly else "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_snapshot_fingerprint(root: str) -> str:
    return project_snapshot_fingerprint(root)
