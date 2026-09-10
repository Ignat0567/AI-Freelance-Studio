from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


# Agent configuration that must not survive in a generated project, and the reason the two
# entries are treated differently below.
#
# `.claude/` is executable: it declares hooks, which are shell commands, and skills, which
# are model-invocable bundled scripts. CLAUDE.md is not executable, but it is read as
# project instructions by the agent working in the directory, which for an unattended order
# means instructions arriving from outside the order's own brief.
AGENT_CONFIG_DIR = ".claude"
AGENT_INSTRUCTION_FILE = "CLAUDE.md"


def scrub_agent_config(project_path: Path | str, *, instructions_too: bool) -> tuple[str, ...]:
    """Remove agent configuration from a generated project, returning what was removed.

    Called with instructions_too=True before handing the directory to a coding agent: at that
    moment both entries are inputs to the agent, and the directory is one the pipeline's own
    output and (once orders carry them) client-supplied templates write into.

    Called with instructions_too=False at delivery: `.claude/` would execute on the client's
    machine the moment they opened the project in their own tooling, so it never ships, while
    a CLAUDE.md the build wrote is ordinary documentation and is theirs to keep.

    Never fatal. A directory that still holds one of these has failed to be tidied, and that
    is worth reporting -- the callers do -- but it is not worth losing a finished build over.
    """
    removed: list[str] = []
    targets = [AGENT_CONFIG_DIR] + ([AGENT_INSTRUCTION_FILE] if instructions_too else [])
    for name in targets:
        target = Path(project_path) / name
        try:
            if not target.exists():
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
            still_there = target.exists()
        except OSError:
            # A path that cannot be stat-ed at all: npm's Windows junctions have produced
            # exactly that in this workspace before and killed a finished order on the way
            # out. Report it as not-removed rather than raising.
            continue
        if not still_there:
            removed.append(name)
    return tuple(removed)


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


def is_regular_file(path: Path) -> bool:
    """`path.is_file()`, for a tree that npm has been let loose in.

    npm on Windows sometimes creates `node_modules/.bin` entries as NTFS junction points
    rather than symlinks. `is_symlink()` does not recognise them (wrong reparse tag) and
    `stat()` raises OSError (WinError 1920) instead of reporting a type, so any walk that
    asks "is this a file?" over a workspace crashes on one. It has cost two live runs: the
    execution of 2026-08-10 after QA had already passed, and the static-page order of
    2026-08-28 after the page was built and its repair had already run.

    Nothing a client ordered lives inside `node_modules/.bin`, so an entry that cannot answer
    is not one.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def plan_project_workspace(root: str | Path, *, order_id: str, brief_id: str, title: str = "") -> ProjectWorkspace:
    root_path = Path(root).expanduser().resolve()
    project_name = _workspace_slug(order_id, brief_id, title)
    return ProjectWorkspace(root=root_path, project_path=root_path / project_name)


def reserve_owned_project_workspace(root: str | Path, *, order_id: str, execution_id: str, brief_fingerprint: str | None, title: str = "") -> ProjectWorkspace:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir() or any(part == ".." for part in root_path.parts):
        raise ValueError("unsafe_workspace_path")
    slug = _workspace_slug(order_id, execution_id, title)
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


DELIVERY_FILE_NAMES = (
    "delivery_report.md",
    "delivery_screenshot.png",
    "README.md",
    "qa_evidence.md",
)


def find_workspace_for_order(root: str | Path, order_id: str) -> Path | None:
    """Return the newest Studio-owned project folder for this order, if any."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        return None
    matches: list[Path] = []
    for marker in root_path.glob("*/.freelancerstudio-project.json"):
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("owner") == "AI Freelance Studio" and payload.get("order_id") == order_id:
            matches.append(marker.parent)
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def delivery_files_in(workspace_path: Path) -> dict[str, bool]:
    return {name: (workspace_path / name).is_file() for name in DELIVERY_FILE_NAMES}


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
        try:
            is_dir = child.is_dir()
        except OSError:
            continue  # see is_regular_file: a Windows npm junction cannot answer
        is_file = not is_dir and is_regular_file(child)
        if is_dir:
            if name in MEANINGFUL_DIR_NAMES:
                found.append(safe_relative + "/")
        elif is_file:
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
        if is_regular_file(child) and child.name != ".freelancerstudio-project.json":
            files_created += 1
    return {"files_created": files_created, "top_level_entries": entries, "workspace_path": workspace.project_reference}


def _safe_path_part(value: str, *, max_length: int = 80) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:max_length] or "generated-project"


def _workspace_slug(order_id: str, execution_id: str, title: str = "") -> str:
    """Folder name for a reserved workspace: a human-readable title prefix (when available)
    ahead of the order/execution identity, so `generated_projects/` entries can be told apart
    at a glance instead of by matching raw UUIDs. The identity portion keeps the exact
    pre-existing format/truncation so ownership-marker validation is unaffected by title."""
    identity = _safe_path_part(f"{order_id}-{execution_id}")
    title_slug = _safe_path_part(title, max_length=40) if title else ""
    if not title_slug or title_slug == "generated-project":
        return identity
    return f"{title_slug}-{identity}"
