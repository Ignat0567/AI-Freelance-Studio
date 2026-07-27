from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectWorkspace:
    root: Path
    project_path: Path

    @property
    def root_reference(self) -> str:
        return self.root.name or "workspace"

    @property
    def project_reference(self) -> str:
        return self.project_path.name


def plan_project_workspace(root: str | Path, *, order_id: str, brief_id: str) -> ProjectWorkspace:
    root_path = Path(root).expanduser().resolve()
    project_name = _safe_path_part(f"{order_id}-{brief_id}")
    return ProjectWorkspace(root=root_path, project_path=root_path / project_name)


def _safe_path_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80] or "generated-project"
