from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import stat
import uuid
import xml.etree.ElementTree as ET

from .models import validate_run_id
from .workspace import REPARSE_POINT_ATTRIBUTE, WorkspaceError, default_runtime_root


MAX_SESSION_SECONDS = 3600.0
MEMORY_MB = 4096

INPUT_ACTION_KINDS = frozenset({"click", "type", "key"})
INPUT_BUTTONS = frozenset({"left", "right"})
INPUT_KEYS = frozenset({"enter", "escape", "tab", "backspace"})
MAX_INPUT_TEXT_LENGTH = 500

# Phase 5e: staging an arbitrary already-built generated project into an interactive_session.
PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
PROJECT_STAGING_EXCLUDES = frozenset({".env", ".git", ".freelancerstudio"})
PROJECT_DESTINATION = r"C:\Users\WDAGUtilityAccount\Desktop\Project"


@dataclass(frozen=True, slots=True)
class InteractiveSessionRequest:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_seconds: float = MAX_SESSION_SECONDS
    project_source: Path | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if self.timeout_seconds != MAX_SESSION_SECONDS:
            raise ValueError("interactive session timeout is fixed")
        if self.project_source is not None:
            if not self.project_source.is_absolute() or not self.project_source.is_dir():
                raise ValueError("project_source must be an absolute existing directory")


@dataclass(frozen=True, slots=True)
class SandboxInputAction:
    """A single, closed-vocabulary input action for an interactive_session run. This is the
    entire security-relevant validation surface for Phase 5c -- no free-form text reaches
    PowerShell without going through here first, and no action kind exists beyond these
    three. Coordinates are normalized ([0.0, 1.0], relative to the target window's *current*
    bounds) so they stay correct across resizes and independent of any past captured frame's
    downscaling.
    """

    kind: str
    x: float | None = None
    y: float | None = None
    button: str = "left"
    text: str | None = None
    key: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in INPUT_ACTION_KINDS:
            raise ValueError("input action kind is invalid")
        if self.kind == "click":
            if self.x is None or self.y is None:
                raise ValueError("click requires x and y")
            for value in (self.x, self.y):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                    raise ValueError("click coordinates must be in [0.0, 1.0]")
            if self.button not in INPUT_BUTTONS:
                raise ValueError("click button is invalid")
            if self.text is not None or self.key is not None:
                raise ValueError("click accepts only x, y, and button")
        elif self.kind == "type":
            if not isinstance(self.text, str) or not 1 <= len(self.text) <= MAX_INPUT_TEXT_LENGTH:
                raise ValueError(f"type text must be 1-{MAX_INPUT_TEXT_LENGTH} characters")
            if any(ord(char) < 0x20 for char in self.text):
                raise ValueError("type text must not contain control characters")
            if self.x is not None or self.y is not None or self.key is not None:
                raise ValueError("type accepts only text")
        elif self.kind == "key":
            if self.key not in INPUT_KEYS:
                raise ValueError("key name is invalid")
            if self.x is not None or self.y is not None or self.text is not None:
                raise ValueError("key accepts only key")


def interactive_session_run_root(run_id: str, runtime_root: Path | None = None) -> Path:
    validate_run_id(run_id)
    return (runtime_root or default_runtime_root()) / "interactive-runs" / run_id


def _is_reparse_point(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)


def _regular_non_reparse_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkspaceError(f"{label} is missing") from exc
    if path.is_symlink() or _is_reparse_point(path) or not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError(f"{label} must be a regular non-reparse directory")


def resolve_project_source(project_name: str, *, projects_root: Path) -> Path:
    """Resolve a caller-supplied project name to a validated, existing directory under
    projects_root -- the generated-project equivalent of production_self_test.py's
    validate_trusted_production_artifact(), adapted for a directory rather than a pinned
    file (there's no fixed hash to check here -- each generated project is unique, so trust
    comes from strict containment plus no symlink/reparse indirection instead of a hash
    match). Pure and side-effect-free -- safe to call repeatedly for re-validation at every
    layer, matching video_evidence.py's trusted_ffmpeg_path() "never trust a cached
    validated value" precedent.
    """
    if not isinstance(project_name, str) or not PROJECT_NAME_PATTERN.match(project_name):
        raise ValueError("project name is invalid")
    root = projects_root.resolve(strict=True)
    candidate = (root / project_name).absolute()
    _regular_non_reparse_directory(candidate, "project source")
    if root not in candidate.parents or candidate.resolve(strict=True) != candidate:
        raise WorkspaceError("project source escapes the generated-projects root or uses path indirection")
    cursor = candidate.parent
    while cursor != root:
        if cursor.is_symlink() or _is_reparse_point(cursor) or not cursor.is_dir():
            raise WorkspaceError("project source path contains indirection")
        cursor = cursor.parent
    if not any(candidate.iterdir()):
        raise WorkspaceError("project source is empty")
    return candidate


def stage_project_files(
    source: Path, run_root: Path, *, exclude: frozenset[str] = PROJECT_STAGING_EXCLUDES,
) -> Path:
    """Copy a validated project source into this run's own ephemeral workspace, so the
    folder mapped into the guest is always a disposable per-run copy -- never the canonical
    generated_projects/ source -- which lets it safely be writable without risking the
    delivery pipeline's own output. Excludes known-secret-shaped entries (.env*, .git,
    .freelancerstudio) at every directory level, and skips symlinks rather than following
    them, matching this subsystem's general no-indirection discipline.
    """
    destination = run_root / "project"

    def _ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name in exclude or name.startswith(".env"):
                ignored.add(name)
                continue
            if (Path(directory) / name).is_symlink():
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=_ignore, symlinks=False)
    return destination


def write_interactive_session_wsb(
    run_root: Path, *, memory_mb: int = MEMORY_MB, project_source: Path | None = None,
) -> Path:
    """Bare Windows Sandbox config for direct human interaction: no shared folders and no
    LogonCommand by default. Confirmed empirically (2026-07-31) that Windows Sandbox's
    default first-logon desktop is already fully mouse/keyboard interactive with no guest
    script needed -- adding one would only add attack surface for no benefit here.

    Deliberately does not set ProtectedClient, AudioInput, or VideoInput -- those hardening
    flags are proven safe for the unattended, fully-automated production self-test/screenshot
    profiles, but were not part of what was actually tested for direct interactive use, so
    they are not assumed to carry over here. Networking stays disabled, matching every other
    profile in this package.

    When project_source is set (Phase 5e), the folder is mapped in read-write (safe, since
    it is always a disposable staged copy -- see stage_project_files()) and
    ClipboardRedirection/PrinterRedirection are additionally disabled, since a real generated
    project can contain live-looking credentials (.env files) -- those two redirection paths
    are the one new way host<->guest data could otherwise cross once something sensitive is
    actually mapped in. Without project_source, output is unchanged from before Phase 5e.
    """
    if not 2048 <= memory_mb <= 32768:
        raise ValueError("memory_mb must be between 2048 and 32768")
    config = ET.Element("Configuration")
    ET.SubElement(config, "VGpu").text = "Disable"
    ET.SubElement(config, "Networking").text = "Disable"
    if project_source is not None:
        ET.SubElement(config, "ClipboardRedirection").text = "Disable"
        ET.SubElement(config, "PrinterRedirection").text = "Disable"
    ET.SubElement(config, "MemoryInMB").text = str(memory_mb)
    if project_source is not None:
        mapped_folders = ET.SubElement(config, "MappedFolders")
        mapped = ET.SubElement(mapped_folders, "MappedFolder")
        ET.SubElement(mapped, "HostFolder").text = str(project_source)
        ET.SubElement(mapped, "SandboxFolder").text = PROJECT_DESTINATION
        ET.SubElement(mapped, "ReadOnly").text = "false"
    xml_text = ET.tostring(config, encoding="unicode")
    run_root.mkdir(parents=True, exist_ok=True)
    config_file = run_root / "sandbox.wsb"
    config_file.write_text(xml_text, encoding="utf-8")
    return config_file
