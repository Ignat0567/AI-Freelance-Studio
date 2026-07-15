"""Build the self-contained backend sidecar consumed by electron-builder."""

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend_dist"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "build" / "backend_sidecar.spec"
HIDDEN_IMPORTS = (
    "ai_utils",
    "search_utils",
    "docker_tester",
    "goldie_agent",
    "qa_engine",
    "requirements_checker",
    "proposal_generator",
    "project_spec",
    "delivery_audit",
    "agent_contracts",
    "opencode_provider",
    "opencode_bridge",
    "api.accounts",
    "api.android",
    "api.system",
)


def build_python() -> str:
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return str(candidate) if candidate.is_file() else sys.executable


def main() -> None:
    shutil.rmtree(OUTPUT, ignore_errors=True)
    shutil.rmtree(ROOT / "freelancerstudio-backend", ignore_errors=True)
    command = [
        build_python(), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "freelancerstudio-backend", "--distpath", str(OUTPUT),
        "--workpath", str(WORK), "--specpath", str(SPEC.parent),
    ]
    command.extend(f"--hidden-import={module}" for module in HIDDEN_IMPORTS)
    command.append(str(ROOT / "backend_entry.py"))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
