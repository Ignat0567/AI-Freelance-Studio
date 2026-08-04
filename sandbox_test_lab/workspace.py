from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

from .models import SandboxRunPaths, SandboxRunRequest, validate_run_id


REPARSE_POINT_ATTRIBUTE = 0x400


class WorkspaceError(ValueError):
    pass


def default_runtime_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local_app_data:
        return Path(local_app_data) / "AI Freelance Studio" / "sandbox-test-lab"
    return Path.home() / ".ai-freelance-studio" / "sandbox-test-lab"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _is_reparse_point(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _validate_no_path_indirection(path: Path, boundary: Path) -> None:
    absolute_path = path.absolute()
    absolute_boundary = boundary.absolute()
    if absolute_path != absolute_boundary and absolute_boundary not in absolute_path.parents:
        raise WorkspaceError("workspace path escapes the controlled runtime root")
    cursor = absolute_path
    while True:
        if not cursor.exists():
            raise WorkspaceError("workspace path is missing")
        if cursor.is_symlink() or _is_reparse_point(cursor):
            raise WorkspaceError("workspace path may not contain symlinks or reparse points")
        if cursor == absolute_boundary:
            break
        cursor = cursor.parent
    if absolute_path.resolve(strict=True) != absolute_path:
        raise WorkspaceError("workspace path resolves through unexpected indirection")


def validate_source_artifact(source: Path) -> Path:
    source = source.expanduser()
    if not source.exists():
        raise WorkspaceError("source artifact does not exist")
    if source.is_symlink() or _is_reparse_point(source):
        raise WorkspaceError("source artifact may not be a symlink or reparse point")
    if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
        raise WorkspaceError("source artifact must be a regular file")
    resolved = source.resolve(strict=True)
    if resolved != source.absolute():
        raise WorkspaceError("source artifact resolves through an unexpected path")
    return resolved


class SandboxWorkspaceManager:
    def __init__(
        self,
        runtime_root: Path | None = None,
        bootstrap_source: Path | None = None,
        bootstrap_destination_name: str = "bootstrap.ps1",
    ):
        self.runtime_root = (runtime_root or default_runtime_root()).expanduser().absolute()
        self.bootstrap_source = bootstrap_source or Path(__file__).with_name("guest") / "bootstrap.ps1"
        if not bootstrap_destination_name or Path(bootstrap_destination_name).name != bootstrap_destination_name:
            raise WorkspaceError("guest bootstrap destination name is invalid")
        self.bootstrap_destination_name = bootstrap_destination_name

    def paths_for(self, run_id: str) -> SandboxRunPaths:
        validate_run_id(run_id)
        run_root = self.runtime_root / "runs" / run_id
        return SandboxRunPaths(
            run_root=run_root,
            input_directory=run_root / "input",
            guest_directory=run_root / "guest",
            evidence_directory=run_root / "evidence",
            logs_directory=run_root / "logs",
            config_file=run_root / "sandbox.wsb",
        )

    def create(self, request: SandboxRunRequest) -> tuple[SandboxRunPaths, Path, str]:
        source = validate_source_artifact(request.source_artifact)
        paths = self.paths_for(request.run_id)
        try:
            paths.run_root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WorkspaceError("run workspace already exists") from exc
        for directory in (paths.input_directory, paths.guest_directory, paths.evidence_directory, paths.logs_directory):
            directory.mkdir()

        suffix = source.suffix if len(source.suffix) <= 16 and source.suffix.replace(".", "").isalnum() else ".bin"
        staged_artifact = paths.input_directory / f"artifact{suffix.lower()}"
        shutil.copyfile(source, staged_artifact, follow_symlinks=False)
        copied_hash = sha256_file(staged_artifact)
        if request.expected_sha256 and copied_hash != request.expected_sha256:
            raise WorkspaceError("copied artifact SHA-256 does not match expected_sha256")
        if not self.bootstrap_source.is_file():
            raise WorkspaceError("guest bootstrap resource is missing")
        shutil.copyfile(
            self.bootstrap_source,
            paths.guest_directory / self.bootstrap_destination_name,
            follow_symlinks=False,
        )
        return paths, staged_artifact, copied_hash

    def write_guest_request(
        self,
        request: SandboxRunRequest,
        paths: SandboxRunPaths,
        staged_artifact: Path,
        artifact_sha256: str,
    ) -> Path:
        request_path = paths.guest_directory / "request.json"
        atomic_write_json(
            request_path,
            {
                "schema_version": 1,
                "run_id": request.run_id,
                "artifact_name": staged_artifact.name,
                "artifact_sha256_host": artifact_sha256,
                "expected_sha256": request.expected_sha256,
            },
        )
        return request_path

    def validate_for_launch(self, request: SandboxRunRequest, paths: SandboxRunPaths) -> None:
        expected = self.paths_for(request.run_id)
        if paths != expected:
            raise WorkspaceError("run paths do not match the controlled workspace layout")
        for directory in (
            self.runtime_root,
            paths.run_root,
            paths.input_directory,
            paths.guest_directory,
            paths.evidence_directory,
            paths.logs_directory,
        ):
            if not directory.is_dir():
                raise WorkspaceError("required workspace directory is missing")
            _validate_no_path_indirection(directory, self.runtime_root)
        required_files = (
            paths.config_file,
            paths.guest_directory / self.bootstrap_destination_name,
            paths.guest_directory / "request.json",
        )
        input_files = tuple(paths.input_directory.iterdir())
        if len(input_files) != 1 or not input_files[0].is_file():
            raise WorkspaceError("input directory must contain exactly one staged artifact")
        for file_path in (*required_files, input_files[0]):
            if not file_path.is_file():
                raise WorkspaceError("required workspace file is missing")
            _validate_no_path_indirection(file_path, self.runtime_root)
