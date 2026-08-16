from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

import docker

from .qa_runner import QACommandResult, QAOutcome

_NODE_IMAGE = "node:20-slim"
_PYTHON_IMAGE = "python:3.12-slim"
_LOG_TAIL_CHARS = 4000

# The browser-based checks (functional_smoke_check, visual_check) share this pair, and it
# lives here rather than in each of them because the two values are only correct together.
#
#   * the npm `playwright` version must match the browsers baked into the image
#     (PLAYWRIGHT_BROWSERS_PATH), or Playwright downloads a browser at QA time instead of
#     reusing the cached one
#   * the image's Node must be new enough for the toolchain of the projects being checked.
#     The previous pin (v1.48.0-jammy) shipped Node 20.18.0; Vite 8 pulls rolldown, which
#     requires ^20.19.0 || >=22.12.0, so its native binding was never installed and
#     `npm run preview` died on startup -- which both gates then reported as a broken page.
#     v1.55.0-noble ships Node 22.18.0.
PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.55.0-noble"
PLAYWRIGHT_NPM_VERSION = "1.55.0"


class DockerUnavailableError(RuntimeError):
    """Raised when the Docker engine itself cannot be reached (not a QA command failure)."""


def _default_client_factory():
    return docker.from_env()


def docker_engine_available(client_factory: Callable[[], "docker.DockerClient"] | None = None) -> bool:
    try:
        client = (client_factory or _default_client_factory)()
        client.ping()
        return True
    except Exception:
        return False


def detect_base_image(cwd: Path) -> str:
    if (cwd / "package.json").is_file():
        return _NODE_IMAGE
    if (cwd / "requirements.txt").is_file() or (cwd / "pyproject.toml").is_file():
        return _PYTHON_IMAGE
    raise ValueError("qa_stack_undetected")


def run_qa_commands_in_docker(
    qa_commands: tuple[str, ...],
    cwd: Path,
    *,
    image: str | None = None,
    timeout_seconds: int = 120,
    docker_client_factory: Callable[[], "docker.DockerClient"] | None = None,
) -> QAOutcome:
    factory = docker_client_factory or _default_client_factory
    try:
        client = factory()
        client.ping()
    except Exception as exc:
        raise DockerUnavailableError(f"Docker engine is not reachable: {exc}") from exc

    try:
        resolved_image = image or detect_base_image(cwd)
    except ValueError as exc:
        # An undetectable stack (e.g. the coding provider hasn't written package.json/
        # requirements.txt yet) is a real QA failure, not an infrastructure problem -- it
        # must flow through the normal QA-failed/repair-loop path, not crash the execution.
        return QAOutcome(passed=False, results=(QACommandResult(command=" && ".join(qa_commands) or "(no commands)", exit_code="qa_stack_undetected", stdout_tail="", stderr_tail=str(exc), duration=0.0),))

    prep = _reinstall_native_dependencies_for_container(client, resolved_image, cwd, timeout_seconds)
    if prep is not None and prep.exit_code != 0:
        # node_modules is host-mounted, installed by the coding CLI running natively on the
        # host OS -- any native-binary optional dependency (esbuild, swc...) it pulled in is
        # compiled for the HOST's platform, not this container's Linux platform, and running
        # it as-is fails deterministically ("You installed esbuild for another platform").
        # Reinstalling failed too, so there's nothing more useful the QA commands themselves
        # can report -- surface the reinstall failure directly instead of a confusing
        # downstream error from whatever command happened to touch the broken binary first.
        return QAOutcome(passed=False, results=(prep,))

    results = [_run_one_command(client, resolved_image, command_text, cwd, timeout_seconds) for command_text in qa_commands]
    passed = all(result.exit_code == 0 for result in results)
    return QAOutcome(passed=passed, results=tuple(results))


def _reinstall_native_dependencies_for_container(client, image: str, cwd: Path, timeout_seconds: int) -> QACommandResult | None:
    if image != _NODE_IMAGE or not (cwd / "package.json").is_file():
        return None
    return _run_one_command(client, image, "npm ci || npm install", cwd, timeout_seconds)


def _run_one_command(client, image: str, command_text: str, cwd: Path, timeout_seconds: int) -> QACommandResult:
    started = monotonic()
    container = None
    try:
        container = client.containers.run(
            image,
            ["sh", "-c", command_text],
            volumes={str(cwd.resolve()): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode="bridge",
            detach=True,
        )
        try:
            wait_result = container.wait(timeout=timeout_seconds)
            exit_code = wait_result.get("StatusCode", -1) if isinstance(wait_result, dict) else wait_result
        except Exception:
            exit_code = "timeout"
        logs = container.logs().decode("utf-8", errors="replace")
    except Exception as exc:
        return QACommandResult(command=command_text, exit_code="docker_error", stdout_tail="", stderr_tail=str(exc)[:_LOG_TAIL_CHARS], duration=round(monotonic() - started, 3))
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
    return QACommandResult(command=command_text, exit_code=exit_code, stdout_tail=logs[-_LOG_TAIL_CHARS:], stderr_tail="", duration=round(monotonic() - started, 3))
