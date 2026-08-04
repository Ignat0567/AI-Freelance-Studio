from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|cookie)\s*[:=]\s*[^\s,;]+|sk-[A-Za-z0-9_-]{16,}")
NON_SECRET_TOKEN_COUNT_KEYS = {"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def mask_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET_RE.sub(lambda match: "<redacted>" if match.group(0).startswith("sk-") else f"{match.group(1)}=<redacted>", value)
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    if isinstance(value, dict):
        return {str(key): "<redacted>" if str(key).lower() not in NON_SECRET_TOKEN_COUNT_KEYS and re.search(r"(?i)(api[_-]?key|token|secret|password|authorization|cookie)", str(key)) else mask_secrets(item) for key, item in value.items()}
    return value


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = mask_secrets(payload)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(safe_payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(mask_secrets(payload), ensure_ascii=False, separators=(",", ":")) + "\n")


@dataclass
class RunArtifactStore:
    run_id: str
    root: Path = Path("artifacts") / "runs"

    @property
    def run_dir(self) -> Path:
        return self.root / self.run_id

    def initialize(self, metadata: dict[str, Any] | None = None) -> Path:
        for rel in (
            "stdout", "stderr", "snapshots/before", "snapshots/after", "diffs",
            "test_results", "browser_evidence", "product_judge",
        ):
            (self.run_dir / rel).mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "development-workflow-run.v1",
            "run_id": self.run_id,
            "created_at": utc_now(),
            "status": "initialized",
            "metadata": metadata or {},
            "required_artifacts": [
                "run_manifest.json", "effective_execution_config.json", "readiness.json",
                "execution_brief.json", "plan.json", "workflow_events.jsonl", "commands.jsonl",
                "diffs/changes.patch", "diffs/changed_files.json", "test_results/test_plan.json",
                "test_results/results.json", "final_report.json",
            ],
        }
        atomic_write_json(self.run_dir / "run_manifest.json", manifest)
        append_jsonl(self.run_dir / "workflow_events.jsonl", {"time": utc_now(), "event": "run_initialized", "run_id": self.run_id})
        return self.run_dir

    def write_json(self, relative_path: str, payload: Any) -> Path:
        path = self.run_dir / relative_path
        atomic_write_json(path, payload)
        return path

    def write_text(self, relative_path: str, text: str) -> Path:
        path = self.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mask_secrets(str(text)), encoding="utf-8")
        return path

    def record_command(self, payload: dict[str, Any]) -> None:
        append_jsonl(self.run_dir / "commands.jsonl", {"time": utc_now(), **payload})

    def mark_skipped(self, relative_path: str, reason: str, required: bool = False) -> Path:
        return self.write_json(relative_path, {"status": "skipped", "passed": False, "required": required, "reason": reason})

    def finalize(self, status: str, summary: dict[str, Any] | None = None) -> Path:
        existing = {}
        manifest_path = self.run_dir / "run_manifest.json"
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing.update({"status": status, "finished_at": utc_now(), "summary": summary or {}})
        atomic_write_json(manifest_path, existing)
        return self.write_json("final_report.json", {"run_id": self.run_id, "status": status, "summary": summary or {}, "manifest": str(manifest_path)})


def copy_tree_contents(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
