from __future__ import annotations

import filecmp
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflow_artifacts import atomic_write_json


IGNORED = {".git", "node_modules", ".pytest_cache", "__pycache__"}


def _safe_rel(path: Path, root: Path) -> str:
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("path_outside_project_root")
    return resolved.relative_to(root).as_posix()


@dataclass
class SnapshotManager:
    project_root: str
    artifact_dir: str

    @property
    def root(self) -> Path:
        return Path(self.project_root).resolve()

    @property
    def artifacts(self) -> Path:
        return Path(self.artifact_dir).resolve()

    def create_snapshot(self, name: str) -> Path:
        dest = self.artifacts / "snapshots" / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for item in self.root.iterdir():
            if item.name in IGNORED:
                continue
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, ignore=shutil.ignore_patterns(*IGNORED))
            else:
                shutil.copy2(item, target)
        return dest

    def diff_snapshots(self, before: Path, after: Path) -> dict[str, list[str]]:
        before_files = _file_set(before)
        after_files = _file_set(after)
        created = sorted(after_files - before_files)
        deleted = sorted(before_files - after_files)
        modified = sorted(path for path in before_files & after_files if not filecmp.cmp(before / path, after / path, shallow=False))
        result = {"created_files": created, "deleted_files": deleted, "modified_files": modified, "changed_files": sorted(set(created + deleted + modified))}
        atomic_write_json(self.artifacts / "diffs" / "changed_files.json", result)
        patch_lines = []
        for path in result["changed_files"]:
            marker = "created" if path in created else "deleted" if path in deleted else "modified"
            patch_lines.append(f"# {marker}: {path}")
        (self.artifacts / "diffs").mkdir(parents=True, exist_ok=True)
        (self.artifacts / "diffs" / "changes.patch").write_text("\n".join(patch_lines) + ("\n" if patch_lines else ""), encoding="utf-8")
        return result

    def rollback(self, snapshot: Path) -> dict[str, Any]:
        snapshot = snapshot.resolve()
        if not snapshot.is_dir() or self.artifacts not in snapshot.parents:
            raise ValueError("snapshot_not_inside_artifacts")
        for item in list(self.root.iterdir()):
            if item.name in IGNORED:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in snapshot.iterdir():
            target = self.root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        evidence = {"status": "rolled_back", "snapshot": str(snapshot), "project_root": str(self.root)}
        atomic_write_json(self.artifacts / "rollback.json", evidence)
        return evidence


def _file_set(root: Path) -> set[str]:
    files = set()
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED]
        for name in names:
            files.add((Path(current) / name).relative_to(root).as_posix())
    return files
