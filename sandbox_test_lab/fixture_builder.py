from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Sequence

from .workspace import REPARSE_POINT_ATTRIBUTE, atomic_write_json, sha256_file, validate_source_artifact


TRUSTED_NSIS_VERSION = "nsis-3.0.4.1"
FIXTURE_EXECUTABLE_NAME = "aifs-sandbox-fixture-v1.exe"
FIXTURE_GUI_EXECUTABLE_NAME = "AIFS Sandbox Fixture.exe"
FIXTURE_PROVENANCE_NAME = "fixture-provenance.json"
TRUSTED_MAKENSIS_SHA256 = "e277b7378931b74392015f5ad6b1d744dcd8a347baa4480350a75ebeab8d8e3d"
TRUSTED_FIXTURE_GUI_SHA256 = "ce0b92f797ad93657705d3696e03897be9ee556e820f2ee9e53f2bd9a5c02c15"
TRUSTED_FIXTURE_ARTIFACT_SHA256 = "b550df0f7fab343d5c60c00885a1b20716634475843db3e7d8497975e3eabeb0"
TRUSTED_FIXTURE_SOURCE_SHA256 = {
    "aifs_sandbox_fixture.nsi": "c4f38330451dbb99a6a3ede05007e5138d7156f1e07e51f5e801e6736ebd88b7",
    "aifs_sandbox_fixture_gui.nsi": "ba39741230f8e31d1adab5d79549730a358c1400886e03d3186baaf4487685c1",
    "fixture-manifest.json": "67dffef1163a35e7679640db25d8d9807e8cf7df5665a05cd917a4edc8576909",
    "payload.txt": "7c624bc81b7dbeda5693dce774f0263e4481db811d9a8e897a73cd7abac84dd8",
}


def fixture_source_root() -> Path:
    return Path(__file__).with_name("fixtures") / "nsis"


def fixture_output_path() -> Path:
    return fixture_source_root() / "build" / FIXTURE_EXECUTABLE_NAME


def fixture_provenance_path() -> Path:
    return fixture_output_path().with_name(FIXTURE_PROVENANCE_NAME)


def fixture_gui_output_path() -> Path:
    return fixture_output_path().with_name(FIXTURE_GUI_EXECUTABLE_NAME)


def _prepare_build_directory(source_root: Path, output_path: Path) -> None:
    build_directory = output_path.parent
    expected = source_root / "build"
    if os.path.normcase(str(build_directory.absolute())) != os.path.normcase(str(expected.absolute())):
        raise ValueError("controlled fixture build directory is invalid")
    try:
        info = build_directory.lstat()
    except FileNotFoundError:
        build_directory.mkdir()
        info = build_directory.lstat()
    if (
        build_directory.is_symlink()
        or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE)
        or not stat.S_ISDIR(info.st_mode)
    ):
        raise ValueError("controlled fixture build directory must be a regular non-reparse directory")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("controlled fixture provenance contains duplicate JSON keys")
        result[key] = value
    return result


def trusted_makensis_path(*, local_app_data: Path | None = None) -> Path:
    if local_app_data is None:
        environment_root = os.environ.get("LOCALAPPDATA")
        if not environment_root:
            raise FileNotFoundError("LOCALAPPDATA is unavailable")
        root = Path(environment_root)
    else:
        root = local_app_data
    candidate = root / "electron-builder" / "Cache" / "nsis" / TRUSTED_NSIS_VERSION / "Bin" / "makensis.exe"
    compiler = validate_source_artifact(candidate)
    if sha256_file(compiler) != TRUSTED_MAKENSIS_SHA256:
        raise ValueError("trusted makensis SHA-256 does not match the pinned compiler")
    return compiler


@dataclass(frozen=True, slots=True)
class FixtureBuildResult:
    compiler: str
    compiler_version: str
    output: str
    sha256: str
    gui_output: str
    gui_sha256: str
    provenance: str

    def to_dict(self) -> dict[str, str]:
        return {
            "compiler": self.compiler,
            "compiler_version": self.compiler_version,
            "output": self.output,
            "sha256": self.sha256,
            "gui_output": self.gui_output,
            "gui_sha256": self.gui_sha256,
            "provenance": self.provenance,
        }


def _run(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    controlled_environment = {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }
    return subprocess.run(
        list(argv),
        cwd=cwd,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=controlled_environment,
    )


def build_fixture(*, compiler: Path | None = None, output: Path | None = None) -> FixtureBuildResult:
    source_root = fixture_source_root().resolve(strict=True)
    for source_name, expected_hash in TRUSTED_FIXTURE_SOURCE_SHA256.items():
        source = validate_source_artifact(source_root / source_name)
        if sha256_file(source) != expected_hash:
            raise ValueError(f"controlled fixture source hash mismatch: {source_name}")
    script = source_root / "aifs_sandbox_fixture.nsi"
    gui_script = source_root / "aifs_sandbox_fixture_gui.nsi"
    canonical_compiler = trusted_makensis_path()
    compiler_path = validate_source_artifact(compiler) if compiler is not None else canonical_compiler
    if compiler_path != canonical_compiler or sha256_file(compiler_path) != TRUSTED_MAKENSIS_SHA256:
        raise ValueError("fixture compiler must be the pinned electron-builder makensis.exe")
    canonical_output = fixture_output_path().absolute()
    output_path = (output or canonical_output).absolute()
    if output_path != canonical_output:
        raise ValueError("controlled fixture output must use the ignored fixture build directory")
    _prepare_build_directory(source_root, output_path)
    gui_output_path = fixture_gui_output_path().absolute()
    provenance_path = fixture_provenance_path().absolute()
    for stale in (output_path, gui_output_path, provenance_path):
        if stale.exists():
            if stale.is_symlink() or not stale.is_file():
                raise ValueError("controlled fixture build output contains unsafe stale state")
            stale.unlink()

    version = _run([str(compiler_path), "/VERSION"], cwd=source_root)
    compiler_version = (version.stdout or version.stderr).strip()
    if version.returncode != 0 or compiler_version != "v3.04":
        raise RuntimeError("trusted makensis version check failed")
    gui_result = _run(
        [str(compiler_path), "/V2", f"/DGUI_OUTPUT_FILE={gui_output_path}", str(gui_script)],
        cwd=source_root,
    )
    if gui_result.returncode != 0:
        raise RuntimeError(f"makensis GUI stage failed with exit code {gui_result.returncode}")
    gui_built = validate_source_artifact(gui_output_path)
    gui_sha256 = sha256_file(gui_built)
    if gui_sha256 != TRUSTED_FIXTURE_GUI_SHA256:
        raise ValueError("controlled fixture GUI build is not byte-reproducible with the pinned artifact")
    os.utime(gui_built, (946684800, 946684800))

    result = _run(
        [str(compiler_path), "/V2", f"/DOUTPUT_FILE={output_path}", str(script)],
        cwd=source_root,
    )
    if result.returncode != 0:
        raise RuntimeError(f"makensis failed with exit code {result.returncode}")
    built = validate_source_artifact(output_path)
    artifact_sha256 = sha256_file(built)
    if artifact_sha256 != TRUSTED_FIXTURE_ARTIFACT_SHA256:
        raise ValueError("controlled fixture build is not byte-reproducible with the pinned artifact")
    provenance = {
        "schema_version": 2,
        "profile": "aifs_sandbox_fixture_v1",
        "compiler_version": compiler_version,
        "compiler_sha256": TRUSTED_MAKENSIS_SHA256,
        "source_sha256": dict(TRUSTED_FIXTURE_SOURCE_SHA256),
        "gui_artifact_name": FIXTURE_GUI_EXECUTABLE_NAME,
        "gui_artifact_sha256": gui_sha256,
        "artifact_name": FIXTURE_EXECUTABLE_NAME,
        "artifact_sha256": artifact_sha256,
    }
    atomic_write_json(provenance_path, provenance)
    return FixtureBuildResult(
        str(compiler_path), compiler_version, str(built), artifact_sha256,
        str(gui_built), gui_sha256, str(provenance_path),
    )


def verify_fixture_provenance(artifact: Path, expected_sha256: str) -> None:
    controlled_output = fixture_output_path().resolve(strict=True)
    selected = validate_source_artifact(artifact)
    if selected != controlled_output:
        raise ValueError("controlled_fixture_required")
    if expected_sha256.lower() != TRUSTED_FIXTURE_ARTIFACT_SHA256:
        raise ValueError("controlled fixture request hash does not match the pinned artifact")
    if sha256_file(selected) != TRUSTED_FIXTURE_ARTIFACT_SHA256:
        raise ValueError("controlled fixture artifact hash does not match the request")
    for source_name, pinned_hash in TRUSTED_FIXTURE_SOURCE_SHA256.items():
        source = validate_source_artifact(fixture_source_root() / source_name)
        if sha256_file(source) != pinned_hash:
            raise ValueError(f"controlled fixture source hash mismatch: {source_name}")
    gui_artifact = validate_source_artifact(fixture_gui_output_path())
    if sha256_file(gui_artifact) != TRUSTED_FIXTURE_GUI_SHA256:
        raise ValueError("controlled fixture GUI artifact hash does not match the pinned build")
    provenance_path = validate_source_artifact(fixture_provenance_path())
    if provenance_path.stat().st_size > 16 * 1024:
        raise ValueError("controlled fixture provenance exceeds its size limit")
    try:
        payload = json.loads(
            provenance_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("controlled fixture provenance is missing or invalid") from exc
    expected = {
        "schema_version": 2,
        "profile": "aifs_sandbox_fixture_v1",
        "compiler_version": "v3.04",
        "compiler_sha256": TRUSTED_MAKENSIS_SHA256,
        "source_sha256": dict(TRUSTED_FIXTURE_SOURCE_SHA256),
        "gui_artifact_name": FIXTURE_GUI_EXECUTABLE_NAME,
        "gui_artifact_sha256": TRUSTED_FIXTURE_GUI_SHA256,
        "artifact_name": FIXTURE_EXECUTABLE_NAME,
        "artifact_sha256": TRUSTED_FIXTURE_ARTIFACT_SHA256,
    }
    if payload != expected:
        raise ValueError("controlled fixture provenance does not match the pinned build contract")


def main() -> int:
    result = build_fixture()
    print(f"compiler={result.compiler}")
    print(f"compiler_version={result.compiler_version}")
    print(f"output={result.output}")
    print(f"sha256={result.sha256}")
    print(f"gui_output={result.gui_output}")
    print(f"gui_sha256={result.gui_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
