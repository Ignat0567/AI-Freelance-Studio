from __future__ import annotations

import os
from pathlib import Path
import struct
import zlib

import pytest

from sandbox_test_lab.video_evidence import encode_session_video, trusted_ffmpeg_path


pytestmark = pytest.mark.external

FFMPEG_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_SANDBOX_TEST_LAB_FFMPEG_EXTERNAL"


def _minimal_png(width: int = 2, height: int = 2, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Hand-built, dependency-free minimal valid PNG, so a real ffmpeg has something
    genuinely decodable to encode -- this test needs the actual vendored binary (see
    third_party/ffmpeg/PROVENANCE.md) and is skipped by default everywhere else, matching
    test_sandbox_test_lab_phase3a_external.py."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw_scanlines = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    idat = chunk(b"IDAT", zlib.compress(raw_scanlines))
    iend = chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


_ONE_PIXEL_PNG = _minimal_png()


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(FFMPEG_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == "external"


def test_real_ffmpeg_encodes_a_playable_webm(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {FFMPEG_EXTERNAL_OPT_IN}=1 and select exactly -m external")
    try:
        trusted_ffmpeg_path()
    except (OSError, ValueError) as exc:
        pytest.skip(f"No vendored, hash-pinned ffmpeg binary available: {exc}")

    run_root = tmp_path
    frames_dir = run_root / "frames"
    frames_dir.mkdir()
    frame_paths = []
    for index in range(5):
        frame = frames_dir / f"frame-{index:06d}.png"
        frame.write_bytes(_ONE_PIXEL_PNG)
        frame_paths.append(frame)
    destination = run_root / "session.webm"

    result = encode_session_video(frame_paths, destination, run_root=run_root)

    assert result is True
    assert destination.is_file()
    assert destination.stat().st_size > 0
