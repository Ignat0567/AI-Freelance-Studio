from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from workflow_artifacts import mask_secrets


class CommandRisk(StrEnum):
    SAFE_READ_ONLY = "safe_read_only"
    PROJECT_WRITE = "project_write"
    DEPENDENCY_INSTALL = "dependency_install"
    EXTERNAL_NETWORK = "external_network"
    DESTRUCTIVE = "destructive"
    SYSTEM_CHANGE = "system_change"


@dataclass
class CommandResult:
    command: list[str]
    working_directory: str
    risk: str
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    stdout: str
    stderr: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_command(command: list[str]) -> CommandRisk:
    text = " ".join(command).lower()
    if re.search(r"\b(rm|del|remove-item|rmdir|format|reg|shutdown)\b", text):
        return CommandRisk.DESTRUCTIVE
    if "npm install" in text or "pip install" in text:
        return CommandRisk.DEPENDENCY_INSTALL
    if any(item in text for item in ("opencode run", "pytest", "npm run build", "playwright")):
        return CommandRisk.PROJECT_WRITE if "opencode run" in text else CommandRisk.SAFE_READ_ONLY
    return CommandRisk.SAFE_READ_ONLY


def resolve_workdir(project_root: str, working_directory: str | None = None) -> Path:
    root = Path(project_root).resolve()
    workdir = Path(working_directory or project_root).resolve()
    if root != workdir and root not in workdir.parents:
        raise ValueError("working_directory_outside_project_root")
    return workdir


async def run_command(
    command: list[str],
    project_root: str,
    working_directory: str | None = None,
    timeout_seconds: int = 120,
    max_output_chars: int = 200_000,
    risk: CommandRisk | None = None,
) -> CommandResult:
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command_must_be_non_empty_string_list")
    workdir = resolve_workdir(project_root, working_directory)
    started = asyncio.get_running_loop().time()
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        shell=False,
    )
    timed_out = False
    cancelled = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        _terminate_process_tree(proc)
        stdout_b, stderr_b = await proc.communicate()
    except asyncio.CancelledError:
        cancelled = True
        _terminate_process_tree(proc)
        stdout_b, stderr_b = await proc.communicate()
    duration = round(asyncio.get_running_loop().time() - started, 3)
    stdout = mask_secrets(stdout_b.decode("utf-8", errors="replace"))[-max_output_chars:]
    stderr = mask_secrets(stderr_b.decode("utf-8", errors="replace"))[-max_output_chars:]
    return CommandResult(command=list(command), working_directory=str(workdir), risk=str(risk or classify_command(command)), exit_code=proc.returncode, timed_out=timed_out, cancelled=cancelled, stdout=stdout, stderr=stderr, duration_seconds=duration)


def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
