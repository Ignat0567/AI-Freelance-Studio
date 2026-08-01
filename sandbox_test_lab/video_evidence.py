from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from .workspace import WorkspaceError, sha256_file, validate_source_artifact


# Filled in once a human has downloaded a real ffmpeg.exe (BtbN's LGPL-shared Windows
# build, see third_party/ffmpeg/PROVENANCE.md) and placed it at third_party/ffmpeg/ffmpeg.exe
# -- see docs/interactive-sandbox-test-lab-phase-5-design.md's Phase 5d section. Left as an
# unmatchable placeholder until then, so trusted_ffmpeg_path() fails closed (never silently
# trusts an unpinned binary) rather than failing to import.
TRUSTED_FFMPEG_SHA256 = "0" * 64

FRAME_CAPTURE_INTERVAL_SECONDS = 5.0
# 720 * 5.0 == 3600.0 == interactive_session.MAX_SESSION_SECONDS exactly, so this ring-buffer
# cap is a pure defensive backstop -- the runner's own session deadline already prevents a
# 721st capture from ever being attempted on a normal run.
MAX_RETAINED_FRAMES = 720
OUTPUT_FRAMERATE = 4.0
ENCODE_TIMEOUT_SECONDS = 20.0


def default_ffmpeg_path() -> Path:
    return Path(__file__).resolve().parent.parent / "third_party" / "ffmpeg" / "ffmpeg.exe"


def trusted_ffmpeg_path(*, ffmpeg_path: Path | None = None) -> Path:
    """Identity-verified, hash-pinned path to the bundled ffmpeg binary, mirroring
    fixture_builder.py's trusted_makensis_path(). Re-validate through this function
    immediately before every actual use -- never cache a validated Path across calls."""
    candidate = ffmpeg_path or default_ffmpeg_path()
    binary = validate_source_artifact(candidate)
    if sha256_file(binary) != TRUSTED_FFMPEG_SHA256:
        raise ValueError("trusted ffmpeg SHA-256 does not match the pinned binary")
    return binary


def _controlled_environment() -> dict[str, str]:
    return {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }


def _escape_concat_path(path: Path) -> str:
    return path.as_posix().replace("'", "'\\''")


def _write_concat_manifest(frame_paths: Sequence[Path], run_root: Path, *, framerate: float) -> Path:
    frames_dir = (run_root / "frames").resolve()
    validated: list[Path] = []
    for frame_path in frame_paths:
        resolved = frame_path.resolve()
        if frames_dir not in resolved.parents:
            raise ValueError("frame path escapes the run's frames directory")
        if not resolved.is_file():
            raise ValueError("frame path does not exist")
        validated.append(resolved)
    if not validated:
        raise ValueError("no frames to encode")

    duration = 1.0 / framerate
    lines = ["ffconcat version 1.0"]
    for frame_path in validated:
        lines.append(f"file '{_escape_concat_path(frame_path)}'")
        lines.append(f"duration {duration:.6f}")
    # ffmpeg concat-demuxer quirk: the last file's `duration` directive is ignored unless
    # the `file` line is repeated once more afterward.
    lines.append(f"file '{_escape_concat_path(validated[-1])}'")

    manifest_path = run_root / "frames.ffconcat"
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def encode_session_video(
    frame_paths: Sequence[Path],
    destination: Path,
    *,
    run_root: Path,
    ffmpeg_path: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Best-effort encode of a completed interactive_session run's captured frames into a
    VP9/WebM video via the pinned ffmpeg binary. Never raises -- returns False on any
    failure (missing/unpinned binary, encode failure, timeout), matching every other
    diagnostics operation in this subsystem."""
    if not frame_paths:
        return False
    try:
        binary = trusted_ffmpeg_path(ffmpeg_path=ffmpeg_path)
        manifest = _write_concat_manifest(frame_paths, run_root, framerate=OUTPUT_FRAMERATE)
        if destination.exists():
            destination.unlink()
        argv = [
            str(binary), "-y",
            "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-r", str(OUTPUT_FRAMERATE),
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuv420p",
            "-b:v", "0", "-crf", "32",
            "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
            "-an",
            str(destination),
        ]
        result = runner(
            argv,
            cwd=run_root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=ENCODE_TIMEOUT_SECONDS,
            env=_controlled_environment(),
        )
        return result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0
    except (WorkspaceError, ValueError, OSError, subprocess.SubprocessError):
        return False


def archive_session_video(diagnostics_root: Path | None, run_id: str, video_path: Path) -> None:
    """Best-effort copy of a completed interactive_session run's encoded video into the
    durable diagnostics store. Scoped to interactive_session only -- production_self_test
    and production_screenshot have no video. Never raises. Deliberately does not prune
    diagnostics_root itself -- the existing archive_run_diagnostics() call already prunes
    the whole diagnostics_root/run_id/ tree (video included) down to its retention count."""
    if diagnostics_root is None or not video_path.is_file():
        return
    try:
        destination = Path(diagnostics_root) / run_id
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, destination / video_path.name)
    except OSError:
        pass
