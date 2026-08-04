from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex

from audit_runtime_common import _run_command

# Windows resolves "npm" as npm.cmd but subprocess.run(shell=False) won't find
# it under the bare name -- matches delivery_audit.py's own _npm_command() gotcha.
_NPM_EXECUTABLE = "npm.cmd" if os.name == "nt" else "npm"


@dataclass(frozen=True, slots=True)
class QACommandResult:
    command: str
    exit_code: int | str
    stdout_tail: str
    stderr_tail: str
    duration: float


@dataclass(frozen=True, slots=True)
class QAOutcome:
    passed: bool
    results: tuple[QACommandResult, ...]

    def failure_summary(self) -> str:
        lines = []
        for result in self.results:
            if result.exit_code == 0:
                continue
            lines.append(
                f"Command failed: {result.command}\n"
                f"Exit code: {result.exit_code}\n"
                f"Stderr:\n{result.stderr_tail}\n"
                f"Stdout:\n{result.stdout_tail}"
            )
        return "\n\n".join(lines)


def run_qa_commands(qa_commands: tuple[str, ...], cwd: Path, timeout_seconds: int = 120) -> QAOutcome:
    """Actually run each QA command as a real subprocess and report pass/fail.

    Reuses audit_runtime_common._run_command, the same subprocess runner
    delivery_audit.py's ReactViteRuntimeAdapter already uses for `npm run build`
    -- no need to reinvent secret redaction or output truncation here.
    Empty qa_commands vacuously passes: an honest "nothing configured" state,
    not a real QA pass.
    """
    results = []
    for command_text in qa_commands:
        argv = shlex.split(command_text)
        if argv and argv[0] == "npm":
            argv[0] = _NPM_EXECUTABLE
        raw = _run_command(argv, str(cwd), timeout=timeout_seconds)
        results.append(
            QACommandResult(
                command=command_text,
                exit_code=raw["exit_code"],
                stdout_tail=raw["stdout_tail"],
                stderr_tail=raw["stderr_tail"],
                duration=raw["duration"],
            )
        )
    passed = all(result.exit_code == 0 for result in results)
    return QAOutcome(passed=passed, results=tuple(results))
