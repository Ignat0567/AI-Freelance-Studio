from __future__ import annotations

import argparse
from enum import IntEnum
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .capability import detect_sandbox_capability
from .evidence import EvidenceError, validate_guest_evidence
from .installer import (
    ApplicationTestRequest,
    InstallScope,
    InstallationRecipe,
    InstallerKind,
    plan_installation,
)
from .fixture_installation import (
    FixtureInstallationRequest,
    FixtureInstallationWorkspaceManager,
    installation_external_opt_in_enabled,
)
from .installation_runner import InstallationSandboxRunner
from .fixture_builder import FIXTURE_GUI_EXECUTABLE_NAME
from .fixture_launch import (
    CONTROLLED_LAUNCH_PROFILE,
    LAUNCH_TIMEOUT_SECONDS,
    FixtureLaunchRequest,
    FixtureLaunchWorkspaceManager,
    launch_external_opt_in_enabled,
)
from .launch_runner import InstallLaunchSandboxRunner
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
    parser = argparse.ArgumentParser(prog="python -m sandbox_test_lab", description="Independent Windows Sandbox Test Lab harness")
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

    plan_install = subparsers.add_parser("plan-install", help="Validate and print a dry-run installation plan")
    plan_install.add_argument("--artifact", required=True, type=Path)
    plan_install.add_argument("--sha256", required=True)
    plan_install.add_argument("--installer-kind", required=True, choices=[kind.value for kind in InstallerKind])
    plan_install.add_argument("--expected-executable")
    plan_install.add_argument("--expected-process-name")
    plan_install.add_argument("--install-scope", choices=[scope.value for scope in InstallScope], default=InstallScope.USER.value)
    plan_install.add_argument("--install-timeout", type=int, default=300)
    plan_install.add_argument("--launch-timeout", type=int, default=60)
    plan_install.add_argument("--json", action="store_true", dest="as_json")

    run_install = subparsers.add_parser("run-install", help="Run the controlled NSIS fixture installation in Windows Sandbox")
    run_install.add_argument("--artifact", required=True, type=Path)
    run_install.add_argument("--sha256", required=True)
    run_install.add_argument("--installer-kind", required=True, choices=[InstallerKind.NSIS_EXE.value])
    run_install.add_argument("--expected-fixture-profile", required=True)
    run_install.add_argument("--timeout", type=float, default=240.0)
    run_install.add_argument("--runtime-root", type=Path)
    run_install.add_argument("--external", action="store_true", help="Explicitly allow the external controlled fixture run")
    run_install.add_argument("--json", action="store_true", dest="as_json")

    run_install_launch = subparsers.add_parser("run-install-launch", help="Install and launch the controlled GUI fixture in Windows Sandbox")
    run_install_launch.add_argument("--artifact", required=True, type=Path)
    run_install_launch.add_argument("--sha256", required=True)
    run_install_launch.add_argument("--launch-profile", required=True, choices=[CONTROLLED_LAUNCH_PROFILE])
    run_install_launch.add_argument("--timeout", type=float, default=300.0)
    run_install_launch.add_argument("--runtime-root", type=Path)
    run_install_launch.add_argument("--external", action="store_true", help="Explicitly allow the external controlled GUI run")
    run_install_launch.add_argument("--json", action="store_true", dest="as_json")
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

        if args.command == "plan-install":
            recipe = InstallationRecipe(
                installer_kind=args.installer_kind,
                artifact_name=args.artifact.name,
                artifact_sha256=args.sha256,
                install_timeout_seconds=args.install_timeout,
                launch_timeout_seconds=args.launch_timeout,
                expected_executable=args.expected_executable,
                expected_process_name=args.expected_process_name,
                expected_install_scope=args.install_scope,
            )
            request = ApplicationTestRequest(
                artifact=args.artifact,
                expected_sha256=args.sha256,
                installation_recipe=recipe,
            )
            plan = plan_installation(request)
            _print(plan.to_dict(), args.as_json)
            return int(ExitCode.SUCCESS if plan.supported else ExitCode.INVALID_INPUT)

        if args.command == "run-install":
            if not installation_external_opt_in_enabled(args.external):
                raise ValueError("run-install requires --external and the installation external opt-in environment variable")
            install_timeout = min(180, int(args.timeout) - 30)
            recipe = InstallationRecipe(
                installer_kind=args.installer_kind,
                artifact_name=args.artifact.name,
                artifact_sha256=args.sha256,
                install_timeout_seconds=install_timeout,
                launch_timeout_seconds=1,
                success_requirements=("artifact_hash_verified", "installer_exit_zero"),
            )
            application_request = ApplicationTestRequest(
                artifact=args.artifact,
                expected_sha256=args.sha256,
                installation_recipe=recipe,
            )
            request = FixtureInstallationRequest(
                application_request=application_request,
                expected_fixture_profile=args.expected_fixture_profile,
                timeout_seconds=args.timeout,
            )
            result = InstallationSandboxRunner(
                workspace_manager=FixtureInstallationWorkspaceManager(args.runtime_root)
            ).run(request)
            _print(result.to_dict(), args.as_json)
            return int(_result_exit_code(result.status, result.exit_reason))

        if args.command == "run-install-launch":
            if not launch_external_opt_in_enabled(args.external):
                raise ValueError("run-install-launch requires --external and the GUI external opt-in environment variable")
            install_timeout = min(180, int(args.timeout) - 73)
            recipe = InstallationRecipe(
                installer_kind=InstallerKind.NSIS_EXE,
                artifact_name=args.artifact.name,
                artifact_sha256=args.sha256,
                install_timeout_seconds=install_timeout,
                launch_timeout_seconds=LAUNCH_TIMEOUT_SECONDS,
                expected_executable=FIXTURE_GUI_EXECUTABLE_NAME,
                expected_process_name=FIXTURE_GUI_EXECUTABLE_NAME,
                success_requirements=(
                    "artifact_hash_verified", "installer_exit_zero", "expected_executable_found",
                    "process_started", "first_launch_verified",
                ),
            )
            application_request = ApplicationTestRequest(args.artifact, args.sha256, recipe)
            request = FixtureLaunchRequest(application_request, args.launch_profile, args.timeout)
            result = InstallLaunchSandboxRunner(
                workspace_manager=FixtureLaunchWorkspaceManager(args.runtime_root)
            ).run(request)
            _print(result.to_dict(), args.as_json)
            return int(_result_exit_code(result.status, result.exit_reason))

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
