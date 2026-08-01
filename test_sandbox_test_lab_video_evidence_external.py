from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from sandbox_test_lab.video_evidence import encode_session_video, trusted_ffmpeg_path


pytestmark = pytest.mark.external

FFMPEG_EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_SANDBOX_TEST_LAB_FFMPEG_EXTERNAL"

# A minimal valid 1x1 red PNG, used so a real ffmpeg has something genuinely decodable to
# encode -- this test needs the actual vendored binary (see third_party/ffmpeg/PROVENANCE.md)
# and is skipped by default everywhere else, matching test_sandbox_test_lab_phase3a_external.py.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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
