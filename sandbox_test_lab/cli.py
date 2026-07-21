from __future__ import annotations

import argparse
from enum import IntEnum
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .capability import detect_sandbox_capability
from .evidence import EvidenceError, validate_guest_evidence
from .models import RunStatus, SandboxRunRequest, validate_run_id
from .runner import SandboxRunner
from .workspace import SandboxWorkspaceManager, WorkspaceError
from .wsb_config import write_wsb_config


class ExitCode(IntEnum):
    SUCCESS = 0
    UNAVAILABLE = 3
    INVALID_INPUT = 4
    LAUNCH_FAILURE = 5
    TIMEOUT = 6
    CANCELLED = 7
    GUEST_FAILURE = 8
    INVALID_EVIDENCE = 9
    INTERNAL_ERROR = 70


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sandbox_test_lab", description="Independent Windows Sandbox Test Lab Phase 1 harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capability = subparsers.add_parser("capability", help="Read-only Windows Sandbox capability detection")
    capability.add_argument("--json", action="store_true", dest="as_json")

    prepare = subparsers.add_parser("prepare", help="Prepare a run workspace without launching Windows Sandbox")
    _add_artifact_arguments(prepare, include_timeout=False)

    run = subparsers.add_parser("run", help="Prepare and run the Windows Sandbox bootstrap")
    _add_artifact_arguments(run, include_timeout=True)

    inspect = subparsers.add_parser("inspect", help="Inspect persisted host and guest evidence")
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--runtime-root", type=Path)
    inspect.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _add_artifact_arguments(parser: argparse.ArgumentParser, *, include_timeout: bool) -> None:
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--sha256")
    if include_timeout:
        parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")


def _print(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    summary = [f"{key}={value}" for key, value in payload.items() if value not in (None, [], (), {})]
    print(" ".join(summary))


def _request(args: argparse.Namespace) -> SandboxRunRequest:
    return SandboxRunRequest(
        source_artifact=args.artifact,
        expected_sha256=args.sha256,
        timeout_seconds=getattr(args, "timeout", 300.0),
    )


def _result_exit_code(status: RunStatus, reason: str) -> ExitCode:
    if status == RunStatus.PASSED:
        return ExitCode.SUCCESS
    if status == RunStatus.UNAVAILABLE:
        return ExitCode.UNAVAILABLE
    if status == RunStatus.TIMED_OUT:
        return ExitCode.TIMEOUT
    if status == RunStatus.CANCELLED:
        return ExitCode.CANCELLED
    if reason == "invalid_evidence":
        return ExitCode.INVALID_EVIDENCE
    if status == RunStatus.FAILED:
        return ExitCode.GUEST_FAILURE
    return ExitCode.LAUNCH_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capability":
            capability = detect_sandbox_capability()
            _print(capability.to_dict(), args.as_json)
            return int(ExitCode.SUCCESS if capability.available else ExitCode.UNAVAILABLE)

        manager = SandboxWorkspaceManager(args.runtime_root)
        if args.command == "prepare":
            request = _request(args)
            paths, artifact, digest = manager.create(request)
            manager.write_guest_request(request, paths, artifact, digest)
            write_wsb_config(paths)
            _print({"run_id": request.run_id, "status": "created", "run_root": str(paths.run_root), "artifact_sha256": digest}, args.as_json)
            return int(ExitCode.SUCCESS)
        if args.command == "run":
            request = _request(args)
            result = SandboxRunner(workspace_manager=manager).run(request)
            _print(result.to_dict(), args.as_json)
            return int(_result_exit_code(result.status, result.exit_reason))
        if args.command == "inspect":
            run_id = validate_run_id(args.run_id)
            paths = manager.paths_for(run_id)
            host_result = paths.run_root / "host-result.json"
            payload: dict[str, Any] = {"run_id": run_id}
            if host_result.is_file():
                payload["host_result"] = json.loads(host_result.read_text(encoding="utf-8"))
            status_path = paths.evidence_directory / "status.json"
            if status_path.is_file():
                payload["guest_evidence"] = validate_guest_evidence(status_path, run_id).raw
            if len(payload) == 1:
                raise EvidenceError("run evidence does not exist")
            _print(payload, args.as_json)
            return int(ExitCode.SUCCESS)
    except (ValueError, WorkspaceError, EvidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"error={type(exc).__name__}: {exc}", file=sys.stderr)
        return int(ExitCode.INVALID_INPUT)
    except Exception as exc:  # CLI boundary: return a stable code without a traceback or secrets.
        print(f"error={type(exc).__name__}", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)
    return int(ExitCode.INTERNAL_ERROR)
