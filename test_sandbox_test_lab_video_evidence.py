from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from sandbox_test_lab import video_evidence
from sandbox_test_lab.video_evidence import (
    archive_session_video,
    default_ffmpeg_path,
    encode_session_video,
    trusted_ffmpeg_path,
)
from sandbox_test_lab.workspace import WorkspaceError, sha256_file


pytestmark = pytest.mark.unit


def test_default_ffmpeg_path_is_repo_relative_third_party_ffmpeg():
    path = default_ffmpeg_path()
    assert path.name == "ffmpeg.exe"
    assert path.parent.name == "ffmpeg"
    assert path.parent.parent.name == "third_party"


class TestTrustedFfmpegPath:
    def _pin_exe_only(self, tmp_path, monkeypatch) -> Path:
        binary = tmp_path / "ffmpeg.exe"
        binary.write_bytes(b"fake ffmpeg binary contents")
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_SHA256", sha256_file(binary))
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_DLL_SHA256", {})
        return binary

    def test_correct_hash_passes(self, tmp_path, monkeypatch):
        binary = self._pin_exe_only(tmp_path, monkeypatch)

        resolved = trusted_ffmpeg_path(ffmpeg_path=binary)

        assert resolved == binary.resolve()

    def test_wrong_hash_raises(self, tmp_path, monkeypatch):
        self._pin_exe_only(tmp_path, monkeypatch)
        binary = tmp_path / "ffmpeg.exe"
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_SHA256", "0" * 64)

        with pytest.raises(ValueError, match="SHA-256"):
            trusted_ffmpeg_path(ffmpeg_path=binary)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(WorkspaceError):
            trusted_ffmpeg_path(ffmpeg_path=tmp_path / "does-not-exist.exe")

    def test_correct_dlls_alongside_the_exe_pass(self, tmp_path, monkeypatch):
        binary = self._pin_exe_only(tmp_path, monkeypatch)
        dll = tmp_path / "avcodec-63.dll"
        dll.write_bytes(b"fake codec dll contents")
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_DLL_SHA256", {"avcodec-63.dll": sha256_file(dll)})

        resolved = trusted_ffmpeg_path(ffmpeg_path=binary)

        assert resolved == binary.resolve()

    def test_missing_dll_raises(self, tmp_path, monkeypatch):
        binary = self._pin_exe_only(tmp_path, monkeypatch)
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_DLL_SHA256", {"avcodec-63.dll": "a" * 64})

        with pytest.raises(WorkspaceError):
            trusted_ffmpeg_path(ffmpeg_path=binary)

    def test_wrong_dll_hash_raises(self, tmp_path, monkeypatch):
        binary = self._pin_exe_only(tmp_path, monkeypatch)
        dll = tmp_path / "avcodec-63.dll"
        dll.write_bytes(b"fake codec dll contents")
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_DLL_SHA256", {"avcodec-63.dll": "a" * 64})

        with pytest.raises(ValueError, match="avcodec-63.dll"):
            trusted_ffmpeg_path(ffmpeg_path=binary)


class TestConcatManifest:
    def _frames(self, run_root: Path, count: int) -> list[Path]:
        frames_dir = run_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(count):
            path = frames_dir / f"frame-{index:06d}.png"
            path.write_bytes(b"png-bytes")
            paths.append(path)
        return paths

    def test_manifest_content_and_duplicated_last_line(self, tmp_path):
        frames = self._frames(tmp_path, 3)

        manifest = video_evidence._write_concat_manifest(frames, tmp_path, framerate=4.0)

        content = manifest.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines[0] == "ffconcat version 1.0"
        file_lines = [line for line in lines if line.startswith("file ")]
        # 3 frames + 1 duplicated final line == 4 file lines total.
        assert len(file_lines) == 4
        assert file_lines[-1] == file_lines[-2]
        assert "duration 0.250000" in content

    def test_rejects_frame_path_outside_the_run_frames_directory(self, tmp_path):
        outside = tmp_path / "elsewhere.png"
        outside.write_bytes(b"png-bytes")

        with pytest.raises(ValueError, match="escapes"):
            video_evidence._write_concat_manifest([outside], tmp_path, framerate=4.0)

    def test_rejects_missing_frame_file(self, tmp_path):
        (tmp_path / "frames").mkdir()
        missing = tmp_path / "frames" / "frame-000000.png"

        with pytest.raises(ValueError, match="does not exist"):
            video_evidence._write_concat_manifest([missing], tmp_path, framerate=4.0)

    def test_rejects_empty_frame_list(self, tmp_path):
        with pytest.raises(ValueError, match="no frames"):
            video_evidence._write_concat_manifest([], tmp_path, framerate=4.0)

    def test_escapes_single_quotes_in_path(self, tmp_path):
        odd_dir = tmp_path / "o'dd"
        (odd_dir / "frames").mkdir(parents=True)
        frame = odd_dir / "frames" / "frame-000000.png"
        frame.write_bytes(b"png-bytes")

        manifest = video_evidence._write_concat_manifest([frame], odd_dir, framerate=4.0)

        content = manifest.read_text(encoding="utf-8")
        assert "o'\\''dd" in content


class _FakeCompletedProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class TestEncodeSessionVideo:
    def _pinned_ffmpeg(self, tmp_path, monkeypatch) -> Path:
        binary = tmp_path / "ffmpeg.exe"
        binary.write_bytes(b"fake ffmpeg binary contents")
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_SHA256", sha256_file(binary))
        monkeypatch.setattr(video_evidence, "TRUSTED_FFMPEG_DLL_SHA256", {})
        return binary

    def _frame(self, run_root: Path) -> Path:
        frames_dir = run_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        frame = frames_dir / "frame-000000.png"
        frame.write_bytes(b"png-bytes")
        return frame

    def test_returns_false_for_empty_frame_list(self, tmp_path):
        assert encode_session_video([], tmp_path / "out.webm", run_root=tmp_path) is False

    def test_builds_exact_argv_and_reports_success(self, tmp_path, monkeypatch):
        binary = self._pinned_ffmpeg(tmp_path, monkeypatch)
        frame = self._frame(tmp_path)
        destination = tmp_path / "session.webm"
        captured = {}

        def fake_runner(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            destination.write_bytes(b"webm-bytes")
            return _FakeCompletedProcess(0)

        result = encode_session_video(
            [frame], destination, run_root=tmp_path, ffmpeg_path=binary, runner=fake_runner,
        )

        assert result is True
        argv = captured["argv"]
        assert argv[0] == str(binary)
        assert argv[-1] == str(destination)
        assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "libvpx-vp9"
        assert "-f" in argv and argv[argv.index("-f") + 1] == "concat"
        kwargs = captured["kwargs"]
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == video_evidence.ENCODE_TIMEOUT_SECONDS
        assert set(kwargs["env"]).issubset({"SystemRoot", "WINDIR", "TEMP", "TMP"})

    def test_returns_false_when_ffmpeg_reports_nonzero_exit(self, tmp_path, monkeypatch):
        binary = self._pinned_ffmpeg(tmp_path, monkeypatch)
        frame = self._frame(tmp_path)
        destination = tmp_path / "session.webm"

        def failing_runner(argv, **kwargs):
            return _FakeCompletedProcess(1)

        result = encode_session_video(
            [frame], destination, run_root=tmp_path, ffmpeg_path=binary, runner=failing_runner,
        )

        assert result is False

    def test_returns_false_when_destination_never_appears(self, tmp_path, monkeypatch):
        binary = self._pinned_ffmpeg(tmp_path, monkeypatch)
        frame = self._frame(tmp_path)
        destination = tmp_path / "session.webm"

        def lying_runner(argv, **kwargs):
            # Reports success without actually writing the output file.
            return _FakeCompletedProcess(0)

        result = encode_session_video(
            [frame], destination, run_root=tmp_path, ffmpeg_path=binary, runner=lying_runner,
        )

        assert result is False

    def test_resolves_logical_paths_to_their_physical_location_before_invoking_ffmpeg(self, tmp_path, monkeypatch):
        """Regression test: on a machine where Python itself runs via a virtualized
        install (observed in practice with Microsoft Store Python's LocalAppData
        redirection, even inside a venv), Path.resolve() can return a real, physical
        location that differs from the logical path used to build run_root/destination.
        ffmpeg is an external, unpackaged process and can only ever see the physical
        path -- passing it the logical one produces a silent 'file not found' failure.
        Simulated here (without needing real symlinks, which this environment lacks
        permission to create) by monkeypatching Path.resolve to map one directory tree
        onto another, mirroring what the OS-level redirect does in practice."""
        binary = self._pinned_ffmpeg(tmp_path, monkeypatch)
        logical_root = tmp_path / "logical" / "run"
        physical_root = tmp_path / "physical" / "run"
        (physical_root / "frames").mkdir(parents=True)
        (physical_root / "frames" / "frame-000000.png").write_bytes(b"png-bytes")
        logical_frame = logical_root / "frames" / "frame-000000.png"
        logical_destination = logical_root / "session.webm"

        real_resolve = Path.resolve

        def fake_resolve(path, *args, **kwargs):
            try:
                relative = path.relative_to(logical_root)
            except ValueError:
                return real_resolve(path, *args, **kwargs)
            return real_resolve(physical_root / relative, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", fake_resolve)

        captured = {}

        def fake_runner(argv, **kwargs):
            captured["argv"] = argv
            captured["cwd"] = kwargs["cwd"]
            Path(argv[-1]).write_bytes(b"webm-bytes")
            return _FakeCompletedProcess(0)

        result = encode_session_video(
            [logical_frame], logical_destination, run_root=logical_root, ffmpeg_path=binary, runner=fake_runner,
        )

        assert result is True
        assert str(logical_root) not in captured["argv"][-1]
        assert str(physical_root) in captured["argv"][-1]
        assert captured["cwd"] == physical_root

    def test_never_raises_on_subprocess_timeout(self, tmp_path, monkeypatch):
        binary = self._pinned_ffmpeg(tmp_path, monkeypatch)
        frame = self._frame(tmp_path)
        destination = tmp_path / "session.webm"

        def timing_out_runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

        result = encode_session_video(
            [frame], destination, run_root=tmp_path, ffmpeg_path=binary, runner=timing_out_runner,
        )

        assert result is False

    def test_returns_false_when_binary_is_unpinned(self, tmp_path):
        frame = self._frame(tmp_path)
        destination = tmp_path / "session.webm"

        def unexpected_runner(argv, **kwargs):
            raise AssertionError("ffmpeg must never be invoked when the binary fails hash-pinning")

        result = encode_session_video(
            [frame], destination, run_root=tmp_path,
            ffmpeg_path=tmp_path / "does-not-exist.exe", runner=unexpected_runner,
        )

        assert result is False


class TestArchiveSessionVideo:
    def test_copies_video_into_diagnostics_root(self, tmp_path):
        video = tmp_path / "session.webm"
        video.write_bytes(b"webm-bytes")
        diagnostics_root = tmp_path / "diagnostics"

        archive_session_video(diagnostics_root, "run-1", video)

        archived = diagnostics_root / "run-1" / "session.webm"
        assert archived.read_bytes() == b"webm-bytes"

    def test_noop_when_diagnostics_root_is_none(self, tmp_path):
        video = tmp_path / "session.webm"
        video.write_bytes(b"webm-bytes")

        archive_session_video(None, "run-1", video)  # must not raise

    def test_noop_when_video_path_does_not_exist(self, tmp_path):
        diagnostics_root = tmp_path / "diagnostics"

        archive_session_video(diagnostics_root, "run-1", tmp_path / "missing.webm")

        assert not (diagnostics_root / "run-1").exists()
