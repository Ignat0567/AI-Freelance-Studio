from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

from .models import validate_run_id
from .workspace import default_runtime_root


MAX_SESSION_SECONDS = 3600.0
MEMORY_MB = 4096

INPUT_ACTION_KINDS = frozenset({"click", "type", "key"})
INPUT_BUTTONS = frozenset({"left", "right"})
INPUT_KEYS = frozenset({"enter", "escape", "tab", "backspace"})
MAX_INPUT_TEXT_LENGTH = 500


@dataclass(frozen=True, slots=True)
class InteractiveSessionRequest:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_seconds: float = MAX_SESSION_SECONDS

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if self.timeout_seconds != MAX_SESSION_SECONDS:
            raise ValueError("interactive session timeout is fixed")


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


def write_interactive_session_wsb(run_root: Path, *, memory_mb: int = MEMORY_MB) -> Path:
    """Bare Windows Sandbox config for direct human interaction: no shared folders and no
    LogonCommand. Confirmed empirically (2026-07-31) that Windows Sandbox's default
    first-logon desktop is already fully mouse/keyboard interactive with no guest script
    needed -- adding one would only add attack surface for no benefit here.

    Deliberately does not set ProtectedClient, ClipboardRedirection, PrinterRedirection,
    AudioInput, or VideoInput -- those hardening flags are proven safe for the unattended,
    fully-automated production self-test/screenshot profiles, but were not part of what was
    actually tested for direct interactive use, so they are not assumed to carry over here.
    Networking stays disabled, matching every other profile in this package.
    """
    if not 2048 <= memory_mb <= 32768:
        raise ValueError("memory_mb must be between 2048 and 32768")
    config = ET.Element("Configuration")
    ET.SubElement(config, "VGpu").text = "Disable"
    ET.SubElement(config, "Networking").text = "Disable"
    ET.SubElement(config, "MemoryInMB").text = str(memory_mb)
    xml_text = ET.tostring(config, encoding="unicode")
    run_root.mkdir(parents=True, exist_ok=True)
    config_file = run_root / "sandbox.wsb"
    config_file.write_text(xml_text, encoding="utf-8")
    return config_file
