from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class RunStatus(str, Enum):
    CREATED = "created"
    LAUNCHING = "launching"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


TERMINAL_STATUSES = {
    RunStatus.PASSED,
    RunStatus.FAILED,
    RunStatus.TIMED_OUT,
    RunStatus.UNAVAILABLE,
    RunStatus.CANCELLED,
    RunStatus.INFRASTRUCTURE_ERROR,
}

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.LAUNCHING, RunStatus.TIMED_OUT, RunStatus.UNAVAILABLE, RunStatus.CANCELLED, RunStatus.INFRASTRUCTURE_ERROR}),
    RunStatus.LAUNCHING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED, RunStatus.INFRASTRUCTURE_ERROR}),
    RunStatus.RUNNING: frozenset({RunStatus.PASSED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED, RunStatus.INFRASTRUCTURE_ERROR}),
}


def validate_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"Invalid sandbox run transition: {current.value} -> {target.value}")


def new_run_id() -> str:
    return str(uuid4())


def validate_run_id(run_id: str) -> str:
    try:
        parsed = UUID(run_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("run_id must be a canonical UUID") from exc
    canonical = str(parsed)
    if run_id.lower() != canonical:
        raise ValueError("run_id must be a canonical UUID")
    return canonical


_SECRET_KEY_PARTS = ("api_key", "apikey", "token", "password", "passwd", "secret", "credential", "auth")


def validate_safe_metadata(value: Any, *, path: str = "metadata") -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 2048:
            raise ValueError(f"{path} string is too long")
        return
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError(f"{path} has too many items")
        for index, item in enumerate(value):
            validate_safe_metadata(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError(f"{path} has too many keys")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError(f"{path} contains an invalid key")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise ValueError(f"{path} contains a secret-like key: {key}")
            validate_safe_metadata(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains unsupported data")


@dataclass(frozen=True, slots=True)
class SandboxCapability:
    supported_os: bool
    windows_edition: str | None
    windows_build: int | None
    virtualization_available: bool | None
    sandbox_feature_state: str
    executable_found: bool
    available: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    executable_path: str | None = None
    powershell_found: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SandboxRunRequest:
    source_artifact: Path
    run_id: str = field(default_factory=new_run_id)
    expected_sha256: str | None = None
    timeout_seconds: float = 300.0
    network_enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_artifact", Path(self.source_artifact))
        validate_run_id(self.run_id)
        if self.expected_sha256 is not None:
            normalized = self.expected_sha256.strip().lower()
            if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
                raise ValueError("expected_sha256 must be a 64-character hexadecimal SHA-256")
            object.__setattr__(self, "expected_sha256", normalized)
        if not 1 <= self.timeout_seconds <= 86400:
            raise ValueError("timeout_seconds must be between 1 and 86400")
        if self.network_enabled:
            raise ValueError("Phase 1 does not allow Windows Sandbox networking")
        validate_safe_metadata(self.metadata)


@dataclass(frozen=True, slots=True)
class SandboxRunPaths:
    run_root: Path
    input_directory: Path
    guest_directory: Path
    evidence_directory: Path
    logs_directory: Path
    config_file: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    run_id: str
    status: RunStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_reason: str
    evidence_path: str | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    launcher_return_code: int | None = None
    launcher_exited_at: str | None = None
    launcher_exit_elapsed_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
