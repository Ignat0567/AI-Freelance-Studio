from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


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


def reserve_owned_project_workspace(root: str | Path, *, order_id: str, execution_id: str, brief_fingerprint: str | None) -> ProjectWorkspace:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir() or any(part == ".." for part in root_path.parts):
        raise ValueError("unsafe_workspace_path")
    slug = _safe_path_part(f"{order_id}-{execution_id}")
    project_path = (root_path / slug).resolve()
    if root_path not in project_path.parents:
        raise ValueError("unsafe_workspace_path")
    marker = project_path / ".freelancerstudio-project.json"
    if project_path.exists() and not marker.is_file():
        raise ValueError("workspace_not_owned")
    project_path.mkdir(parents=False, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "owner": "AI Freelance Studio",
                "order_id": order_id,
                "execution_id": execution_id,
                "brief_fingerprint": brief_fingerprint or "",
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return ProjectWorkspace(root=root_path, project_path=project_path)


def validate_owned_project_workspace(workspace: ProjectWorkspace, *, order_id: str, execution_id: str) -> None:
    root_path = workspace.root.expanduser().resolve()
    project_path = workspace.project_path.expanduser().resolve()
    if not root_path.is_dir() or root_path not in project_path.parents or not project_path.is_dir():
        raise ValueError("unsafe_workspace_path")
    marker = project_path / ".freelancerstudio-project.json"
    if not marker.is_file():
        raise ValueError("workspace_not_owned")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("workspace_marker_invalid") from exc
    if payload.get("owner") != "AI Freelance Studio" or payload.get("order_id") != order_id or payload.get("execution_id") != execution_id:
        raise ValueError("workspace_marker_mismatch")


def scan_meaningful_generated_artifacts(workspace: ProjectWorkspace, *, max_files: int = 200, max_depth: int = 4, limit: int = 50) -> tuple[str, ...]:
    root_path = workspace.root.expanduser().resolve()
    project_path = workspace.project_path.expanduser().resolve()
    if not project_path.is_dir() or root_path not in project_path.parents:
        raise ValueError("unsafe_workspace_path")
    found: list[str] = []
    scanned = 0
    for child in sorted(project_path.rglob("*"), key=lambda item: str(item.relative_to(project_path)).casefold()):
        if child.is_symlink():
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if resolved != project_path and project_path not in resolved.parents:
            continue
        relative = child.relative_to(project_path)
        if len(relative.parts) > max_depth:
            continue
        safe_relative = relative.as_posix()
        if any(part in {"", ".", ".."} for part in relative.parts):
            continue
        name = child.name
        if name in STUDIO_METADATA_FILES:
            continue
        if child.is_dir():
            if name in MEANINGFUL_DIR_NAMES:
                found.append(safe_relative + "/")
        elif child.is_file():
            scanned += 1
            if scanned > max_files:
                break
            if name in MEANINGFUL_FILE_NAMES or child.suffix in MEANINGFUL_SUFFIXES:
                found.append(safe_relative)
        if len(found) >= limit:
            break
    return tuple(found)


def summarize_generated_workspace(workspace: ProjectWorkspace, *, limit: int = 50) -> dict[str, object]:
    entries = []
    files_created = 0
    for child in sorted(workspace.project_path.iterdir(), key=lambda item: item.name.casefold()):
        if child.name == ".freelancerstudio-project.json":
            continue
        entries.append(child.name[:120])
        if len(entries) >= limit:
            break
    for child in workspace.project_path.rglob("*"):
        if child.is_file() and child.name != ".freelancerstudio-project.json":
            files_created += 1
    return {"files_created": files_created, "top_level_entries": entries, "workspace_path": workspace.project_reference}


def _safe_path_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80] or "generated-project"
