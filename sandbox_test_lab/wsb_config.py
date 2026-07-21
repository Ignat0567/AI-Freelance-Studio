from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .models import SandboxRunPaths


INPUT_DESTINATION = r"C:\SandboxTestLab\Input"
GUEST_DESTINATION = r"C:\SandboxTestLab\Guest"
EVIDENCE_DESTINATION = r"C:\SandboxTestLab\Evidence"
BOOTSTRAP_COMMAND = (
    r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe '
    r'-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass '
    r'-File "C:\SandboxTestLab\Guest\bootstrap.ps1"'
)


class WsbConfigError(ValueError):
    pass


def _validated_host_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WsbConfigError("mapped host paths must be absolute")
    if not path.is_dir():
        raise WsbConfigError("mapped host paths must be existing directories")
    return path.resolve(strict=True)


def build_wsb_xml(paths: SandboxRunPaths, *, memory_mb: int = 4096, network_enabled: bool = False) -> str:
    if network_enabled:
        raise WsbConfigError("Phase 1 does not allow Windows Sandbox networking")
    if not 2048 <= memory_mb <= 32768:
        raise WsbConfigError("memory_mb must be between 2048 and 32768")

    mappings = (
        (_validated_host_directory(paths.input_directory), INPUT_DESTINATION, True),
        (_validated_host_directory(paths.guest_directory), GUEST_DESTINATION, True),
        (_validated_host_directory(paths.evidence_directory), EVIDENCE_DESTINATION, False),
    )
    host_keys = [str(host).casefold() for host, _, _ in mappings]
    guest_keys = [guest.casefold() for _, guest, _ in mappings]
    if len(set(host_keys)) != len(host_keys) or len(set(guest_keys)) != len(guest_keys):
        raise WsbConfigError("mapped folders must have unique host and guest destinations")

    root = ET.Element("Configuration")
    settings = (
        ("VGpu", "Disable"),
        ("Networking", "Disable"),
        ("ClipboardRedirection", "Disable"),
        ("PrinterRedirection", "Disable"),
        ("AudioInput", "Disable"),
        ("VideoInput", "Disable"),
        ("ProtectedClient", "Enable"),
        ("MemoryInMB", str(memory_mb)),
    )
    for name, value in settings:
        ET.SubElement(root, name).text = value

    mapped_folders = ET.SubElement(root, "MappedFolders")
    for host, guest, read_only in mappings:
        mapped = ET.SubElement(mapped_folders, "MappedFolder")
        ET.SubElement(mapped, "HostFolder").text = str(host)
        ET.SubElement(mapped, "SandboxFolder").text = guest
        ET.SubElement(mapped, "ReadOnly").text = "true" if read_only else "false"

    logon = ET.SubElement(root, "LogonCommand")
    ET.SubElement(logon, "Command").text = BOOTSTRAP_COMMAND
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=False) + "\n"


def write_wsb_config(paths: SandboxRunPaths, *, memory_mb: int = 4096, network_enabled: bool = False) -> Path:
    content = build_wsb_xml(paths, memory_mb=memory_mb, network_enabled=network_enabled)
    temporary = paths.config_file.with_suffix(".wsb.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(paths.config_file)
    return paths.config_file
