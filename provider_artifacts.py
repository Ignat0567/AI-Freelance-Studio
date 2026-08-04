from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from workflow_artifacts import RunArtifactStore, append_jsonl, atomic_write_json, mask_secrets


class ProviderArtifactRecorder:
    def __init__(self, run_id: str | None):
        self.run_id = run_id or ""
        self.base: Path | None = None
        if self.run_id:
            self.base = RunArtifactStore(self.run_id).run_dir / "provider"
            self.base.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_brief(cls, brief: Any) -> "ProviderArtifactRecorder":
        metadata = getattr(brief, "metadata", {}) if isinstance(getattr(brief, "metadata", {}), dict) else {}
        return cls(str(metadata.get("artifact_run_id") or metadata.get("run_id") or ""))

    def write_json(self, name: str, payload: Any) -> None:
        if not self.base:
            return
        atomic_write_json(self.base / name, _safe_payload(payload))

    def append_attempt(self, payload: dict[str, Any]) -> None:
        if not self.base:
            return
        append_jsonl(self.base / "attempts.jsonl", mask_secrets(payload))


def _safe_payload(payload: Any) -> Any:
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    if hasattr(payload, "__dataclass_fields__"):
        return asdict(payload)
    return payload
