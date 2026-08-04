from __future__ import annotations

from pathlib import Path
import stat
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
SCREENSHOT_ENTRY_COMMAND = (
    r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe '
    r'-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass '
    r'-File "C:\SandboxTestLab\Guest\production_screenshot_entry.ps1"'
)


class WsbConfigError(ValueError):
    pass


def _validated_host_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise WsbConfigError("mapped host paths must be absolute")
    if not path.is_dir():
        raise WsbConfigError("mapped host paths must be existing directories")
    return path.resolve(strict=True)


def build_wsb_xml(
    paths: SandboxRunPaths,
    *,
    memory_mb: int = 4096,
    network_enabled: bool = False,
    bootstrap_command: str = BOOTSTRAP_COMMAND,
) -> str:
    if network_enabled:
        raise WsbConfigError("Windows Sandbox networking is not supported")
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
    if bootstrap_command not in {BOOTSTRAP_COMMAND, SCREENSHOT_ENTRY_COMMAND}:
        raise WsbConfigError("WSB bootstrap command is not allowlisted")
    ET.SubElement(logon, "Command").text = bootstrap_command
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=False) + "\n"


def write_wsb_config(
    paths: SandboxRunPaths,
    *,
    memory_mb: int = 4096,
    network_enabled: bool = False,
    bootstrap_command: str = BOOTSTRAP_COMMAND,
) -> Path:
    content = build_wsb_xml(
        paths,
        memory_mb=memory_mb,
        network_enabled=network_enabled,
        bootstrap_command=bootstrap_command,
    )
    temporary = paths.config_file.with_suffix(".wsb.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(paths.config_file)
    return paths.config_file


def validate_wsb_config(
    paths: SandboxRunPaths,
    *,
    memory_mb: int = 4096,
    bootstrap_command: str = BOOTSTRAP_COMMAND,
) -> None:
    try:
        info = paths.config_file.lstat()
        content = paths.config_file.read_text(encoding="utf-8")
        root = ET.fromstring(content)
    except (OSError, UnicodeError, ET.ParseError) as exc:
        raise WsbConfigError("WSB configuration is missing or invalid XML") from exc
    if paths.config_file.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise WsbConfigError("WSB configuration must be a regular non-symlink file")
    if root.tag != "Configuration":
        raise WsbConfigError("WSB configuration root is invalid")

    mappings = root.findall("./MappedFolders/MappedFolder")
    expected_mappings = (
        (paths.input_directory.resolve(strict=True), INPUT_DESTINATION, "true"),
        (paths.guest_directory.resolve(strict=True), GUEST_DESTINATION, "true"),
        (paths.evidence_directory.resolve(strict=True), EVIDENCE_DESTINATION, "false"),
    )
    if len(mappings) != len(expected_mappings):
        raise WsbConfigError("WSB mapped folder count is invalid")
    for mapping, (host, guest, read_only) in zip(mappings, expected_mappings):
        if (
            mapping.findtext("HostFolder") != str(host)
            or mapping.findtext("SandboxFolder") != guest
            or mapping.findtext("ReadOnly") != read_only
        ):
            raise WsbConfigError("WSB mapped folder contract is invalid")
    if root.findtext("./LogonCommand/Command") != bootstrap_command:
        raise WsbConfigError("WSB LogonCommand contract is invalid")
    if content != build_wsb_xml(
        paths,
        memory_mb=memory_mb,
        network_enabled=False,
        bootstrap_command=bootstrap_command,
    ):
        raise WsbConfigError("WSB configuration changed after generation")
