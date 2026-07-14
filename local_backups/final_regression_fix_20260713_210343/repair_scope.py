from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


EXCLUDED_REPAIR_DIRS = {
    ".git",
    ".freelancerstudio",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    ".cache",
    ".parcel-cache",
    ".turbo",
    ".next",
    ".nuxt",
    "__pycache__",
    "evidence_artifacts",
}

EXCLUDED_REPAIR_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".glb",
    ".zip",
    ".tar",
    ".gz",
}

SUPPORTED_LOCK_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
}


def _casefold(path: Path) -> str:
    return str(path).replace("/", os.sep).casefold() if os.name == "nt" else str(path)


def resolve_inside(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> Path:
    root_path = Path(root).resolve(strict=False)
    raw_candidate = Path(candidate)
    target = raw_candidate if raw_candidate.is_absolute() else root_path / raw_candidate
    resolved = target.resolve(strict=False)
    root_cmp = _casefold(root_path)
    resolved_cmp = _casefold(resolved)
    if resolved_cmp != root_cmp and not resolved_cmp.startswith(root_cmp + os.sep):
        raise ValueError(f"path escapes project root: {candidate}")
    return resolved


def is_inside(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> bool:
    try:
        resolve_inside(root, candidate)
        return True
    except ValueError:
        return False


def is_excluded_relative_path(relative_path: str | os.PathLike[str]) -> bool:
    parts = Path(relative_path).parts
    return any(part in EXCLUDED_REPAIR_DIRS or part.endswith(".egg-info") for part in parts)


def is_repairable_file(root: str | os.PathLike[str], path: str | os.PathLike[str]) -> bool:
    resolved = resolve_inside(root, path)
    rel = resolved.relative_to(Path(root).resolve(strict=False))
    if is_excluded_relative_path(rel):
        return False
    if resolved.name in SUPPORTED_LOCK_FILES:
        return True
    return resolved.suffix.lower() not in EXCLUDED_REPAIR_EXTENSIONS


def walk_repairable_files(root: str | os.PathLike[str], extensions: Iterable[str] | None = None):
    root_path = Path(root).resolve(strict=False)
    allowed = {ext.lower() for ext in extensions} if extensions is not None else None
    for current, dirs, files in os.walk(root_path):
        current_path = Path(current).resolve(strict=False)
        if not is_inside(root_path, current_path):
            dirs[:] = []
            continue
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDED_REPAIR_DIRS and not d.endswith(".egg-info") and is_inside(root_path, current_path / d)
        )
        for name in sorted(files):
            path = current_path / name
            try:
                rel = path.relative_to(root_path).as_posix()
            except ValueError:
                continue
            if is_excluded_relative_path(rel):
                continue
            if allowed is not None and path.suffix.lower() not in allowed:
                continue
            if name not in SUPPORTED_LOCK_FILES and path.suffix.lower() in EXCLUDED_REPAIR_EXTENSIONS:
                continue
            yield rel, path


def classify_change(project_root: str | os.PathLike[str], workspace_root: str | os.PathLike[str], path: str | os.PathLike[str], proven_current_run_paths: Iterable[str] | None = None) -> dict[str, object]:
    workspace = Path(workspace_root).resolve(strict=False)
    project = Path(project_root).resolve(strict=False)
    target = Path(path).resolve(strict=False)
    proven = {str(Path(item).resolve(strict=False)) for item in (proven_current_run_paths or [])}
    in_project = is_inside(project, target)
    in_workspace = is_inside(workspace, target)
    if in_project and is_excluded_relative_path(target.relative_to(project)):
        classification = "generated_project_dependency_files"
    elif in_project:
        classification = "generated_project_source_files"
    elif in_workspace:
        classification = "freelancerstudio_root_files"
    else:
        classification = "outside_workspace"
    return {
        "path": str(target),
        "classification": classification,
        "changed_by_current_run_proven": str(target) in proven,
        "should_have_been_in_repair_scope": in_project and classification == "generated_project_source_files" and is_repairable_file(project, target),
    }
