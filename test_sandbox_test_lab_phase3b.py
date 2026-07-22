from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import zlib

import pytest

import sandbox_test_lab.production_self_test as production_module
import sandbox_test_lab.payload_load_probe as payload_probe_module
import sandbox_test_lab.runner as runner_module
import sandbox_test_lab.sandbox_session as session_module
import sandbox_test_lab.screenshot_runner as screenshot_runner_module
import sandbox_test_lab.screenshot_self_test as screenshot_module
import test_sandbox_test_lab_phase3b_external as external_module
import test_sandbox_test_lab_phase3b_payload_load_external as payload_external_module
from sandbox_test_lab.models import RunStatus, SandboxCapability
from sandbox_test_lab.screenshot_runner import ScreenshotSelfTestRunner
from sandbox_test_lab.screenshot_self_test import (
    ENTRY_SCRIPT_FILENAME,
    ENTRY_STARTUP_STAGES,
    PAYLOAD_SCRIPT_FILENAME,
    PAYLOAD_SHA256,
    PAYLOAD_STARTUP_STAGES,
    PROFILE_NAME,
    SCREENSHOT_EXTERNAL_OPT_IN,
    SCREENSHOT_FILENAME,
    SCREENSHOT_MANIFEST_FILENAME,
    SCREENSHOT_PROTOCOL,
    ScreenshotEvidenceError,
    ScreenshotSelfTestRequest,
    ScreenshotSelfTestWorkspaceManager,
    derive_screenshot_bootstrap,
    derive_screenshot_payload,
    image_summary_passes,
    production_capture_prerequisites_met,
    read_startup_marker,
    startup_marker_filename,
    screenshot_external_opt_in_enabled,
    validate_png_and_summarize,
    validate_screenshot_evidence_directory,
    window_capture_state_is_valid,
)
from sandbox_test_lab.payload_load_probe import (
    PAYLOAD_LOAD_PROBE_COMPLETION,
    PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN,
    PROBE_PAYLOAD_STAGES,
    PayloadLoadProbeRunner,
    PayloadLoadProbeWorkspaceManager,
    payload_load_probe_external_opt_in_enabled,
    payload_load_probe_request,
    read_payload_load_probe_completion,
)
from sandbox_test_lab.workspace import WorkspaceError, atomic_write_json, sha256_file
from sandbox_test_lab.wsb_config import SCREENSHOT_ENTRY_COMMAND, WsbConfigError, validate_wsb_config, write_wsb_config
from test_sandbox_test_lab_phase3a import _passed_status, _trusted_tree, _write_evidence


pytestmark = pytest.mark.unit
RUN_ID = "81a509d2-9f12-43cc-a326-c126aca8187a"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _png(width: int = 320, height: int = 200, mode: str = "varied") -> bytes:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            if mode == "varied": pixel = ((x * 7 + y * 3) & 255, (x * 2 + y * 11) & 255, (x * 13 + y * 5) & 255, 255)
            elif mode == "black": pixel = (0, 0, 0, 255)
            elif mode == "white": pixel = (255, 255, 255, 255)
            elif mode == "transparent": pixel = (20, 40, 60, 0)
            else: pixel = (128, 128, 128, 255)
            rows.extend(pixel)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(rows))) + _chunk(b"IEND", b"")


def _png_with_color_metadata(
    *, extras=(), between_idat=(), after_idat=(), trailing=b"", include_phys=True, iend_payload=b"",
) -> bytes:
    base = _png()
    ihdr_length = struct.unpack(">I", base[8:12])[0]
    ihdr = base[16:16 + ihdr_length]
    idat_offset = 8 + 12 + ihdr_length
    idat_length = struct.unpack(">I", base[idat_offset:idat_offset + 4])[0]
    compressed = base[idat_offset + 8:idat_offset + 8 + idat_length]
    midpoint = len(compressed) // 2
    chunks = [
        _chunk(b"IHDR", ihdr),
        *(_chunk(kind, payload) for kind, payload in extras),
        *([_chunk(b"pHYs", struct.pack(">IIB", 3780, 3780, 1))] if include_phys else []),
        _chunk(b"IDAT", compressed[:midpoint]),
        *(_chunk(kind, payload) for kind, payload in between_idat),
        _chunk(b"IDAT", compressed[midpoint:]),
        *(_chunk(kind, payload) for kind, payload in after_idat),
        _chunk(b"IEND", iend_payload),
    ]
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks) + trailing


def _production(run_id: str = RUN_ID, base: datetime = BASE) -> dict:
    value = _passed_status(run_id, guest_terminal_deadline_utc=_iso(base + timedelta(minutes=9)))
    offsets = {
        "started_at": 0, "installer_started_at": 1, "process_start_returned_at": 1.1, "installer_finished_at": 2,
        "launch_started_at": 3, "process_started_at": 4, "window_detected_at": 5, "stable_started_at": 5,
        "stable_verified_at": 10, "backend_started_at": 11, "backend_ready_at": 12,
        "cleanup_started_at": 14, "cleanup_finished_at": 15, "completed_at": 16,
    }
    value.update({name: _iso(base + timedelta(seconds=seconds)) for name, seconds in offsets.items()})
    return value


def _manifest(data: bytes, *, base: datetime = BASE, status: str = "passed", **updates) -> dict:
    summary = validate_png_and_summarize(data, 320, 200) if status == "passed" else {
        "blank": True, "uniform": True, "transparent": True, "black": True, "white": False,
        "total_pixels": 0, "visible_pixels": 0, "mean_luminance": 0.0, "min_luminance": 0.0,
        "max_luminance": 0.0, "luminance_range": 0.0, "luminance_variance": 0.0,
    }
    passed = status == "passed"
    value = {
        "schema_version": 1, "protocol": SCREENSHOT_PROTOCOL, "run_id": RUN_ID, "profile": PROFILE_NAME,
        "captured_at": _iso(base + timedelta(seconds=13))[:-1] + "0Z", "source": "owned_window",
        "owned_window_verified": passed, "owner_pid": 101, "hwnd_present": True,
        "exact_title_verified": True, "window_visible": True,
        "window_bounds": {"x": 10, "y": 20, "width": 320 if passed else 0, "height": 200 if passed else 0},
        "client_bounds": {"x": 18, "y": 50, "width": 304 if passed else 0, "height": 162 if passed else 0},
        "dpi_scale": 1.25 if passed else 0.0, "image_filename": SCREENSHOT_FILENAME, "image_format": "png",
        "width": 320 if passed else 0, "height": 200 if passed else 0, "file_size": len(data) if passed else 0,
        "sha256": hashlib.sha256(data).hexdigest() if passed else "", "capture_method": "print_window" if passed else "none",
        "blank_check": passed, "uniformity_check": passed, "image_summary": summary,
        "validation_status": status, "errors": [] if passed else ["screenshot_capture_failed"], "warnings": [],
    }
    value.update(updates)
    return value


def _evidence(directory: Path, *, base: datetime = BASE, production_updates=None, manifest_updates=None, failure=False, data=None):
    image = data if data is not None else _png()
    production = _production(base=base)
    if production_updates: production.update(production_updates)
    _write_evidence(directory, production)
    offset = -2.0
    for prefix, stages in (("entry", ENTRY_STARTUP_STAGES), ("payload", PAYLOAD_STARTUP_STAGES)):
        for stage in stages:
            marker_run_id = "" if stage in {"entry_script_started", "request_found"} else RUN_ID
            marker = {
                "run_id": marker_run_id, "stage": stage,
                "timestamp": _iso(base + timedelta(seconds=offset))[:-1] + "0Z",
            }
            if prefix == "entry":
                marker["schema_version"] = 1
            else:
                marker["result"] = "started" if stage.endswith("_started") else "entered" if stage == "payload_interpreter_entered" else "completed"
            atomic_write_json(directory / startup_marker_filename(prefix, stage), marker)
            offset += 0.1
    (directory / "lifecycle.log").write_text(
        f"{production['started_at']} production_self_test_started\n"
        f"{production['completed_at']} production_self_test_passed\n", encoding="utf-8",
    )
    if not failure: (directory / SCREENSHOT_FILENAME).write_bytes(image)
    atomic_write_json(directory / SCREENSHOT_MANIFEST_FILENAME, _manifest(image, base=base, status="failed" if failure else "passed", **(manifest_updates or {})))
    return image


def _validate(directory: Path, *, base: datetime = BASE):
    return validate_screenshot_evidence_directory(
        directory,
        RUN_ID,
        run_started_at=base,
        expected_guest_terminal_deadline_utc=_iso(base + timedelta(minutes=9)),
    )


def _request(tmp_path: Path, monkeypatch) -> ScreenshotSelfTestRequest:
    _trusted_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(production_module, "ensure_no_active_windows_sandbox_session", lambda: None)
    return ScreenshotSelfTestRequest(run_id=RUN_ID)


def _window_state(**updates):
    state = {
        "hwnd_exists": True, "owner_in_owned_tree": True, "owner_creation_verified": True,
        "owner_gui_eligible": True, "visible": True, "minimized": False, "exact_title": True,
        "owner_image_verified": True, "owner_image_contained": True,
        "owner_image_basename": "AI Freelance Studio.exe",
        "window_bounds": {"x": 10, "y": 20, "width": 1000, "height": 700},
        "client_bounds": {"x": 18, "y": 50, "width": 984, "height": 662},
        "virtual_bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    }
    state.update(updates); return state


def _capability() -> SandboxCapability:
    return SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631,
        virtualization_available=True, sandbox_feature_state="enabled",
        executable_found=True, available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class _Process:
    def __init__(self, return_code=None, on_terminate=None, pid=4242):
        self.return_code = return_code
        self.on_terminate = on_terminate
        self.pid = pid
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self): return self.return_code
    def terminate(self):
        if self.on_terminate is not None: self.on_terminate()
        self.terminate_calls += 1; self.return_code = 0
    def kill(self): self.kill_calls += 1; self.return_code = 0
    def wait(self, timeout=None): return self.return_code or 0


class _OwnedSession:
    def __init__(
        self, launcher_pid=4242, *, active=True, close_succeeds=True, terminate_succeeds=True,
    ):
        self.launcher_pid = launcher_pid
        self.active = active
        self.close_succeeds = close_succeeds
        self.terminate_succeeds = terminate_succeeds
        self.close_calls = 0
        self.terminate_calls = 0

    def guard(self) -> None:
        if self.active:
            raise WorkspaceError("active_windows_sandbox_session")

    def factory(self, launcher_pid, launched_at):
        assert launcher_pid == self.launcher_pid
        assert launched_at.tzinfo is not None
        return self

    def request_close(self) -> None:
        self.close_calls += 1
        if self.close_succeeds:
            self.active = False

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_succeeds:
            self.active = False


def _no_active_session_for(session: _OwnedSession) -> None:
    if session.active:
        raise WorkspaceError("active_windows_sandbox_session")


def test_exact_contract_filename_and_fields(tmp_path):
    _evidence(tmp_path)
    manifest = json.loads((tmp_path / SCREENSHOT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert SCREENSHOT_FILENAME == "production-main-window.png"
    assert SCREENSHOT_MANIFEST_FILENAME == "screenshot-evidence.json"
    assert set(manifest) == {
        "schema_version", "protocol", "run_id", "profile", "captured_at", "source", "owned_window_verified",
        "owner_pid", "hwnd_present", "exact_title_verified", "window_visible", "window_bounds", "client_bounds",
        "dpi_scale", "image_filename", "image_format", "width", "height", "file_size", "sha256", "capture_method",
        "blank_check", "uniformity_check", "image_summary", "validation_status", "errors", "warnings",
    }
    assert manifest["source"] == "owned_window" and manifest["image_format"] == "png"


def test_static_payload_is_byte_stable_and_orders_capture_before_cleanup():
    source = (Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8")
    derived = derive_screenshot_payload(source)
    tracked = (Path(__file__).parent / "sandbox_test_lab" / "guest" / PAYLOAD_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert tracked == derived
    assert hashlib.sha256(tracked.encode()).hexdigest() == PAYLOAD_SHA256
    assert hashlib.sha256(source.encode()).hexdigest() == "cc72713eac077c0b4e63350756a2cb9f46e94167d602a1d74f33c4d8483bf625"
    cleanup_marker = "finally {\n    if ($null -ne $InstallerProcess"
    assert derived.index("Write-AtomicJson $ScreenshotManifestPath") < derived.index(cleanup_marker)
    capture_start = derived.index("$ScreenshotCapturedAt = Get-UtcTimestamp")
    assert derived.index("screenshot_readiness_prerequisite_failed", capture_start) < derived.index("ForegroundVerifiedWindow", capture_start) < derived.index("AifsScreenshotApi]::Capture", capture_start)
    assert "Start-Sleep -Milliseconds 250" in derived
    assert source.count("Write-PayloadStartupMarker") == 0
    assert tracked.startswith("param()\n& {")
    assert tracked.index('stage = "payload_interpreter_entered"') < tracked.index("Add-Type")
    assert "StackTrace" not in screenshot_module._PAYLOAD_PRELUDE
    assert "Exception.Message" not in screenshot_module._PAYLOAD_PRELUDE


def test_initialization_completed_is_common_before_probe_and_production_branch():
    payload = (Path(__file__).parent / "sandbox_test_lab" / "guest" / PAYLOAD_SCRIPT_FILENAME).read_text(encoding="utf-8")
    marker = 'Write-PayloadStartupMarker "initialization_completed" $PayloadRunId'
    branch = 'if ([string]$PayloadRequest.execution_mode -ceq "payload_load_probe")'
    assert payload.count(marker) == 1
    assert payload.index(marker) < payload.index(branch) < payload.index('Set-Phase "installing"')


def test_missing_initialization_completed_has_specific_host_error(tmp_path):
    _evidence(tmp_path)
    (tmp_path / startup_marker_filename("payload", "initialization_completed")).unlink()
    with pytest.raises(ScreenshotEvidenceError, match="required startup marker missing: initialization_completed"):
        _validate(tmp_path)


@pytest.mark.parametrize("defect", ["run_id", "schema", "stage"])
def test_initialization_completed_rejects_wrong_identity_or_contract(tmp_path, defect):
    _evidence(tmp_path)
    path = tmp_path / startup_marker_filename("payload", "initialization_completed")
    marker = json.loads(path.read_text(encoding="utf-8"))
    if defect == "run_id":
        marker["run_id"] = "00000000-0000-0000-0000-000000000000"
    elif defect == "schema":
        marker["schema_version"] = 1
    else:
        marker["stage"] = "request_loading_completed"
    atomic_write_json(path, marker)
    with pytest.raises(ScreenshotEvidenceError, match="startup marker"):
        _validate(tmp_path)


def test_generated_guest_enforces_all_capture_prerequisites_and_owner_image():
    script = derive_screenshot_bootstrap((Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8"), RUN_ID)
    for required in (
        "$InstallationPassed", "$InstalledExeRegular", "$InstalledExeNonReparse", "$InstalledExeHash", "$InstallRootVerified",
        "$Owned.Count", "$AllImagesContained", "$ExactTitle", "$WindowVisible", "$StableDuration", "$FirstLaunchVerified",
        '$BackendStatus -cne "ready"', "$BackendEndpointVerified", "$BackendPidOwnershipVerified", "$BackendImageVerified",
        "Test-OwnedCreation", "EligibleForGui", "Test-ImageContained", "$InstalledExeName", "IsIconic", "GetClientRect",
        "GetVirtualBounds", "GetForegroundWindow", "SetThreadDpiAwarenessContext", "GetDpiForWindow",
    ):
        assert required in script


def test_python_production_prerequisite_helper_and_backend_gate():
    value = _production()
    assert production_capture_prerequisites_met(value)
    for field, replacement in (
        ("installation_passed", False), ("installed_exe_regular", False), ("installed_exe_non_reparse", False),
        ("install_root_verified", False), ("all_images_contained", False), ("window_visible", False),
        ("first_launch_verified", False), ("backend_status", "not_ready"), ("backend_endpoint_verified", False),
    ):
        changed = dict(value); changed[field] = replacement
        assert not production_capture_prerequisites_met(changed), field


@pytest.mark.parametrize(
    ("updates", "case"),
    [
        ({"minimized": True}, "minimized"), ({"visible": False}, "invisible"),
        ({"exact_title": False}, "wrong-title"), ({"owner_in_owned_tree": False}, "unowned-hwnd"),
        ({"owner_creation_verified": False}, "creation"), ({"owner_gui_eligible": False}, "eligibility"),
        ({"owner_image_contained": False}, "image-containment"), ({"owner_image_basename": "other.exe"}, "basename"),
        ({"window_bounds": {"x": 1800, "y": 20, "width": 1000, "height": 700}}, "outside-screen"),
        ({"client_bounds": {"x": 0, "y": 0, "width": 0, "height": 0}}, "empty-client"),
    ],
)
def test_immediate_window_gate_rejects_minimized_invisible_outside_unowned_and_invalid_identity(updates, case):
    assert window_capture_state_is_valid(_window_state())
    assert not window_capture_state_is_valid(_window_state(**updates)), case


def test_printwindow_pixel_inspection_triggers_exact_bounds_fallback():
    script = derive_screenshot_bootstrap((Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8"), RUN_ID)
    assert "if (!printed || !Healthy(result))" in script
    assert 'Summarize(bitmap, "print_window"' in script
    assert 'Summarize(bitmap, "copy_from_screen_exact_bounds"' in script
    assert "CopyFromScreen(windowBounds[0], windowBounds[1]" in script
    assert "screenshot_fallback_revalidation_failed" in script
    assert "full-screen" not in script.lower()


def test_host_pixel_summary_is_independent_and_cross_checked(tmp_path):
    image = _evidence(tmp_path)
    summary = validate_png_and_summarize(image, 320, 200)
    assert image_summary_passes(summary)
    validated = _validate(tmp_path)
    assert validated.host_image_summary == summary
    manifest = json.loads((tmp_path / SCREENSHOT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["image_summary"]["mean_luminance"] += 1
    atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, manifest)
    with pytest.raises(ScreenshotEvidenceError, match="contradicts"):
        _validate(tmp_path)


def test_system_drawing_srgb_gamma_phys_two_idat_sequence_is_accepted():
    image = _png_with_color_metadata(extras=((b"sRGB", b"\x00"), (b"gAMA", struct.pack(">I", 45455))))
    summary = validate_png_and_summarize(image, 320, 200)
    assert image_summary_passes(summary)


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_srgb", "srgb_after_idat", "srgb_length", "srgb_value",
        "duplicate_gamma", "gamma_after_idat", "gamma_length", "gamma_zero",
        "phys_after_idat", "noncontiguous_idat", "iend_payload",
        "text", "compressed_text", "international_text", "unknown", "after_iend",
    ],
)
def test_png_rejects_invalid_or_unknown_ancillary_chunks(case):
    gamma = struct.pack(">I", 45455)
    physical = struct.pack(">IIB", 3780, 3780, 1)
    variants = {
        "duplicate_srgb": {"extras": ((b"sRGB", b"\x00"), (b"sRGB", b"\x01"))},
        "srgb_after_idat": {"after_idat": ((b"sRGB", b"\x00"),)},
        "srgb_length": {"extras": ((b"sRGB", b"\x00\x01"),)},
        "srgb_value": {"extras": ((b"sRGB", b"\x04"),)},
        "duplicate_gamma": {"extras": ((b"gAMA", gamma), (b"gAMA", gamma))},
        "gamma_after_idat": {"after_idat": ((b"gAMA", gamma),)},
        "gamma_length": {"extras": ((b"gAMA", b"\x00\x01\x02"),)},
        "gamma_zero": {"extras": ((b"gAMA", struct.pack(">I", 0)),)},
        "phys_after_idat": {"include_phys": False, "after_idat": ((b"pHYs", physical),)},
        "noncontiguous_idat": {"include_phys": False, "between_idat": ((b"pHYs", physical),)},
        "iend_payload": {"iend_payload": b"x"},
        "text": {"extras": ((b"tEXt", b"key=value"),)},
        "compressed_text": {"extras": ((b"zTXt", b"key\x00payload"),)},
        "international_text": {"extras": ((b"iTXt", b"key\x00payload"),)},
        "unknown": {"extras": ((b"iCCP", b"unknown"),)},
        "after_iend": {"trailing": _chunk(b"tEXt", b"after=iend")},
    }
    image = _png_with_color_metadata(**variants[case])
    with pytest.raises(ScreenshotEvidenceError):
        validate_png_and_summarize(image, 320, 200)


def test_png_rejects_ancillary_chunk_crc_corruption():
    image = bytearray(_png_with_color_metadata(extras=((b"sRGB", b"\x00"), (b"gAMA", struct.pack(">I", 45455)))))
    srgb_crc_offset = 8 + 12 + 13 + 4 + 4 + 1
    image[srgb_crc_offset] ^= 1
    with pytest.raises(ScreenshotEvidenceError, match="CRC"):
        validate_png_and_summarize(bytes(image), 320, 200)


@pytest.mark.parametrize("mode", ["black", "white", "uniform", "transparent"])
def test_host_rejects_blank_black_white_uniform_and_transparent(tmp_path, mode):
    image = _png(mode=mode)
    summary = validate_png_and_summarize(image, 320, 200)
    assert not image_summary_passes(summary)
    _evidence(tmp_path, data=image, manifest_updates={"image_summary": summary})
    with pytest.raises(ScreenshotEvidenceError, match="pixel sanity"):
        _validate(tmp_path)


def test_structured_capture_failure_is_emitted_without_sabotaging_phase3a_and_cleanup(tmp_path):
    _evidence(tmp_path, failure=True)
    production = json.loads((tmp_path / "production-evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / SCREENSHOT_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert production["status"] == "passed" and production["fully_ready"] is True and production["cleanup_complete"] is True
    assert manifest["validation_status"] == "failed" and manifest["errors"] == ["screenshot_capture_failed"]
    assert not (tmp_path / SCREENSHOT_FILENAME).exists()
    with pytest.raises(ScreenshotEvidenceError, match="guest screenshot capture failed"):
        _validate(tmp_path)


def test_success_requires_valid_production_gui_evidence(tmp_path):
    _evidence(tmp_path, production_updates={"first_launch_verified": False, "gui_passed": False})
    with pytest.raises(ScreenshotEvidenceError):
        _validate(tmp_path)


def test_terminal_validation_requires_host_authoritative_guest_deadline(tmp_path):
    _evidence(tmp_path, production_updates={
        "guest_terminal_deadline_utc": _iso(BASE + timedelta(minutes=8)),
    })
    with pytest.raises(ScreenshotEvidenceError, match="deadline does not match host authority"):
        _validate(tmp_path)


def test_success_requires_cleanup_after_capture_ordering(tmp_path):
    _evidence(tmp_path, manifest_updates={"captured_at": _iso(BASE + timedelta(seconds=15))[:-1] + "0Z"})
    with pytest.raises(ScreenshotEvidenceError, match="before cleanup"):
        _validate(tmp_path)


def test_exact_png_path_hash_dimensions_size_and_current_run_time(tmp_path):
    image = _evidence(tmp_path)
    validated = _validate(tmp_path)
    assert validated.png_sha256 == hashlib.sha256(image).hexdigest()
    for updates in ({"image_filename": "other.png"}, {"file_size": 1}, {"sha256": "0" * 64}, {"width": 321}):
        manifest = _manifest(image); manifest.update(updates)
        atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, manifest)
        with pytest.raises(ScreenshotEvidenceError): _validate(tmp_path)
    atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, _manifest(image))
    future_start = datetime.now(timezone.utc) + timedelta(minutes=1)
    with pytest.raises(ScreenshotEvidenceError, match="current run"):
        validate_screenshot_evidence_directory(
            tmp_path,
            RUN_ID,
            run_started_at=future_start,
            expected_guest_terminal_deadline_utc=_iso(BASE + timedelta(minutes=9)),
        )


def test_multiple_images_and_terminal_temporary_png_are_rejected(tmp_path):
    _evidence(tmp_path)
    (tmp_path / "second.png").write_bytes(_png())
    with pytest.raises(ScreenshotEvidenceError, match="unexpected|multiple"):
        _validate(tmp_path)
    (tmp_path / "second.png").unlink()
    (tmp_path / "production-main-window.png.tmp").write_bytes(_png())
    with pytest.raises(ScreenshotEvidenceError, match="unexpected|multiple"):
        _validate(tmp_path)


def test_terminal_completion_without_entry_payload_result_is_valid_race(tmp_path):
    _evidence(tmp_path)
    assert not (tmp_path / "entry-payload-result.json").exists()
    assert _validate(tmp_path).production.status == "passed"


def test_terminal_completion_with_valid_entry_payload_result_passes(tmp_path):
    _evidence(tmp_path)
    atomic_write_json(tmp_path / "entry-payload-result.json", {
        "schema_version": 1,
        "run_id": RUN_ID,
        "stage": "payload_process_exited",
        "timestamp": _iso(BASE + timedelta(seconds=16.1))[:-1] + "0Z",
        "exit_code": 0,
        "status": "passed",
    })
    assert _validate(tmp_path).production.status == "passed"


@pytest.mark.parametrize(
    "defect",
    [
        "extra", "duplicate", "schema", "run_id", "stage", "timestamp", "boolean_exit",
        "failed_exit", "status", "oversized", "directory", "schema_bool", "schema_float",
        "timestamp_impossible", "timestamp_unicode_digits",
    ],
)
def test_terminal_completion_rejects_invalid_entry_payload_result(tmp_path, defect):
    _evidence(tmp_path)
    path = tmp_path / "entry-payload-result.json"
    result = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "stage": "payload_process_exited",
        "timestamp": _iso(BASE + timedelta(seconds=16.1))[:-1] + "0Z",
        "exit_code": 0,
        "status": "passed",
    }
    if defect == "extra":
        result["extra"] = True
    elif defect == "duplicate":
        path.write_text(json.dumps(result)[:-1] + ',"status":"passed"}', encoding="utf-8")
    elif defect == "schema":
        result["schema_version"] = 2
    elif defect == "schema_bool":
        result["schema_version"] = True
    elif defect == "schema_float":
        result["schema_version"] = 1.0
    elif defect == "run_id":
        result["run_id"] = "00000000-0000-0000-0000-000000000000"
    elif defect == "stage":
        result["stage"] = "payload_process_started"
    elif defect == "timestamp":
        result["timestamp"] = "2026-01-01T00:00:16Z"
    elif defect == "timestamp_impossible":
        result["timestamp"] = "2026-02-30T00:00:16.0000000Z"
    elif defect == "timestamp_unicode_digits":
        result["timestamp"] = "".join(
            chr(0x660 + int(character)) if character.isdigit() else character
            for character in "2026-01-01T00:00:16.0000000Z"
        )
    elif defect == "boolean_exit":
        result["exit_code"] = False
    elif defect == "failed_exit":
        result.update(exit_code=1, status="failed")
    elif defect == "oversized":
        path.write_bytes(b"x" * 4097)
    elif defect == "directory":
        path.mkdir()
    else:
        result["status"] = "failed"
    if defect not in {"duplicate", "oversized", "directory"}:
        atomic_write_json(path, result)
    with pytest.raises(ScreenshotEvidenceError, match="entry payload result|failed payload result|screenshot evidence"):
        _validate(tmp_path)


def test_executable_renamed_png_is_rejected(tmp_path):
    data = b"MZ" + b"\0" * 1024
    _evidence(tmp_path)
    (tmp_path / SCREENSHOT_FILENAME).write_bytes(data)
    manifest = _manifest(_png())
    manifest.update(file_size=len(data), sha256=hashlib.sha256(data).hexdigest())
    atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, manifest)
    with pytest.raises(ScreenshotEvidenceError, match="executable disguised"):
        _validate(tmp_path)


def test_wrong_png_signature_is_rejected(tmp_path):
    data = b"not-a-png"
    _evidence(tmp_path)
    (tmp_path / SCREENSHOT_FILENAME).write_bytes(data)
    manifest = _manifest(_png())
    manifest.update(file_size=len(data), sha256=hashlib.sha256(data).hexdigest())
    atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, manifest)
    with pytest.raises(ScreenshotEvidenceError, match="signature"):
        _validate(tmp_path)


def test_oversized_png_is_rejected_before_decode(tmp_path, monkeypatch):
    image = _evidence(tmp_path)
    monkeypatch.setattr(screenshot_module, "MAX_SCREENSHOT_BYTES", len(image) - 1)
    with pytest.raises(ScreenshotEvidenceError, match="size"):
        _validate(tmp_path)


def test_png_crc_full_decode_metadata_and_single_image_are_strict(tmp_path):
    original = _evidence(tmp_path)
    variants = [
        original[:-5] + bytes([original[-5] ^ 1]) + original[-4:],
        original + b"trailing",
        original[:-12] + _chunk(b"tEXt", b"secret=value") + original[-12:],
        _png(321, 200),
    ]
    for data in variants:
        (tmp_path / SCREENSHOT_FILENAME).write_bytes(data)
        manifest = _manifest(original); manifest.update(file_size=len(data), sha256=hashlib.sha256(data).hexdigest())
        atomic_write_json(tmp_path / SCREENSHOT_MANIFEST_FILENAME, manifest)
        with pytest.raises(ScreenshotEvidenceError): _validate(tmp_path)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_reparse_or_symlink_png_is_rejected(tmp_path):
    _evidence(tmp_path)
    target = tmp_path / "target.bin"; target.write_bytes(_png())
    (tmp_path / SCREENSHOT_FILENAME).unlink()
    try: os.symlink(target, tmp_path / SCREENSHOT_FILENAME)
    except OSError: pytest.skip("symlink creation unavailable")
    with pytest.raises(ScreenshotEvidenceError, match="regular|reparse|unexpected"):
        _validate(tmp_path)


def test_workspace_revalidates_deterministic_staged_bootstrap(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    deadline = _iso(datetime.now(timezone.utc) + timedelta(minutes=9))
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=deadline)
    write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    entry = paths.guest_directory / ENTRY_SCRIPT_FILENAME
    payload = paths.guest_directory / PAYLOAD_SCRIPT_FILENAME
    assert sha256_file(entry) == sha256_file(manager.bootstrap_source)
    assert sha256_file(payload) == PAYLOAD_SHA256
    manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=deadline)
    payload.write_text(payload.read_text(encoding="utf-8") + "#changed", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="payload changed"):
        manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=deadline)


def test_phase3a_equivalent_wsb_structure_is_validated(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    deadline = _iso(datetime.now(timezone.utc) + timedelta(minutes=9))
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=deadline)
    write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    validate_wsb_config(paths, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    content = paths.config_file.read_text(encoding="utf-8")
    assert SCREENSHOT_ENTRY_COMMAND in content
    assert '-File "C:\\SandboxTestLab\\Guest\\production_screenshot_self_test.ps1"' not in content


def test_prelaunch_rejects_missing_bootstrap_and_request(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    deadline = _iso(datetime.now(timezone.utc) + timedelta(minutes=9))
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=deadline)
    write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    (paths.guest_directory / ENTRY_SCRIPT_FILENAME).unlink()
    with pytest.raises(WorkspaceError, match="required workspace file is missing"):
        manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=deadline)


@pytest.mark.parametrize(("remove", "message"), [(True, "required workspace file is missing"), (False, "payload changed")])
def test_prelaunch_rejects_payload_missing_or_hash_mismatch(tmp_path, monkeypatch, remove, message):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    deadline = _iso(datetime.now(timezone.utc) + timedelta(minutes=9))
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=deadline)
    write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    payload = paths.guest_directory / PAYLOAD_SCRIPT_FILENAME
    if remove:
        payload.unlink()
    else:
        payload.write_text(payload.read_text(encoding="utf-8") + "#drift", encoding="utf-8")
    with pytest.raises(WorkspaceError, match=message):
        manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=deadline)
    (paths.guest_directory / ENTRY_SCRIPT_FILENAME).write_bytes(manager.bootstrap_source.read_bytes())
    (paths.guest_directory / "request.json").unlink()
    with pytest.raises(WorkspaceError, match="required workspace file is missing"):
        manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=deadline)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (r"C:\SandboxTestLab\Guest", r"C:\SandboxTestLab\WrongGuest", "mapped folder"),
        ('-File "C:\\SandboxTestLab\\Guest\\production_screenshot_entry.ps1"', '-File C:\\SandboxTestLab\\Guest\\production_screenshot_entry.ps1', "LogonCommand"),
    ],
)
def test_wsb_validation_rejects_wrong_guest_destination_and_malformed_logon_quoting(tmp_path, monkeypatch, old, new, message):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    deadline = _iso(datetime.now(timezone.utc) + timedelta(minutes=9))
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=deadline)
    write_wsb_config(paths, network_enabled=False, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)
    content = paths.config_file.read_text(encoding="utf-8")
    assert old in content
    paths.config_file.write_text(content.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(WsbConfigError, match=message):
        validate_wsb_config(paths, bootstrap_command=SCREENSHOT_ENTRY_COMMAND)


def test_entry_and_payload_markers_are_append_only_and_preserve_timestamps(tmp_path):
    marker = tmp_path / startup_marker_filename("entry", "entry_script_started")
    atomic_write_json(marker, {
        "schema_version": 1, "run_id": "", "stage": "entry_script_started",
        "timestamp": "2026-01-01T00:00:00.0000000Z",
    })
    value = read_startup_marker(marker, RUN_ID, allow_pending_run_id=True)
    assert value["stage"] == "entry_script_started"
    assert value["timestamp"] == "2026-01-01T00:00:00.0000000Z"
    entry = (Path(__file__).parent / "sandbox_test_lab" / "guest" / ENTRY_SCRIPT_FILENAME).read_text(encoding="utf-8")
    payload = (Path(__file__).parent / "sandbox_test_lab" / "guest" / PAYLOAD_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert "if (Test-Path -LiteralPath $Path)" in entry
    assert "if (Test-Path -LiteralPath $Path)" in payload


def test_entry_marker_absent_is_logon_command_not_observed_at_30_seconds(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    clock = _Clock()
    process = _Process(return_code=0)
    session = _OwnedSession()
    runner = ScreenshotSelfTestRunner(
        workspace_manager=ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_capability, launcher=lambda argv: process,
        monotonic=clock, sleeper=lambda seconds: setattr(clock, "value", clock.value + seconds),
        session_guard=session.guard, session_factory=session.factory,
    )
    result = runner.run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "logon_command_not_observed"
    assert result.duration_seconds == 46.0
    assert result.launcher_return_code == 0
    assert process.terminate_calls == 0 and process.kill_calls == 0
    assert session.close_calls == 1 and session.active is False
    diagnostics = json.loads((tmp_path / "runtime" / "runs" / RUN_ID / "logs" / "startup-diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["entry_marker_absent"] is True and diagnostics["wsb_validation"] == "passed"


def test_payload_marker_absent_times_out_without_restarting_host_deadline(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    clock = _Clock()
    process = _Process()
    session = _OwnedSession()
    evidence_directory = None
    entry_written = False
    host_now = datetime.now(timezone.utc)

    def launch(argv):
        nonlocal evidence_directory
        evidence_directory = Path(argv[1]).parent / "evidence"
        return process

    def sleep(seconds):
        nonlocal entry_written
        clock.value += seconds
        if not entry_written and clock.value >= 29:
            entry_written = True
            atomic_write_json(evidence_directory / startup_marker_filename("entry", "entry_script_started"), {
                "schema_version": 1, "run_id": "", "stage": "entry_script_started",
                "timestamp": _iso(host_now)[:-1] + "0Z",
            })
            atomic_write_json(evidence_directory / startup_marker_filename("entry", "payload_process_started"), {
                "schema_version": 1, "run_id": RUN_ID, "stage": "payload_process_started",
                "timestamp": _iso(host_now + timedelta(seconds=0.1))[:-1] + "0Z",
            })

    result = ScreenshotSelfTestRunner(
        workspace_manager=ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_capability, launcher=launch, monotonic=clock, sleeper=sleep,
        utc_clock=lambda: host_now,
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "payload_start_timeout"
    assert result.duration_seconds == 75.0
    assert process.terminate_calls == 1
    assert result.launcher_return_code == 0
    assert session.close_calls == 1 and session.active is False


def test_payload_process_exit_before_entry_is_immediate(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    clock = _Clock()
    process = _Process()
    session = _OwnedSession()
    evidence_directory = None
    evidence_written = False
    host_now = datetime.now(timezone.utc)

    def launch(argv):
        nonlocal evidence_directory
        evidence_directory = Path(argv[1]).parent / "evidence"
        return process

    def sleep(seconds):
        nonlocal evidence_written
        clock.value += seconds
        if not evidence_written and clock.value >= 1:
            evidence_written = True
            atomic_write_json(evidence_directory / startup_marker_filename("entry", "entry_script_started"), {
                "schema_version": 1, "run_id": "", "stage": "entry_script_started",
                "timestamp": _iso(host_now)[:-1] + "0Z",
            })
            atomic_write_json(evidence_directory / startup_marker_filename("entry", "payload_process_started"), {
                "schema_version": 1, "run_id": RUN_ID, "stage": "payload_process_started",
                "timestamp": _iso(host_now + timedelta(seconds=0.1))[:-1] + "0Z",
            })
            atomic_write_json(evidence_directory / "entry-payload-result.json", {
                "schema_version": 1, "run_id": RUN_ID, "stage": "payload_process_exited",
                "timestamp": _iso(host_now + timedelta(seconds=0.2))[:-1] + "0Z",
                "exit_code": 1, "status": "failed",
            })
            session.active = False

    result = ScreenshotSelfTestRunner(
        workspace_manager=ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_capability, launcher=launch, monotonic=clock, sleeper=sleep,
        utc_clock=lambda: host_now - timedelta(seconds=20),
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "payload_process_exited_before_entry"
    assert result.duration_seconds == 2.0


def test_zero_exit_result_before_completion_cannot_authorize_success(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    clock = _Clock()
    process = _Process()
    session = _OwnedSession(active=False)
    host_now = datetime.now(timezone.utc)

    def launch(argv):
        evidence = Path(argv[1]).parent / "evidence"
        atomic_write_json(evidence / startup_marker_filename("entry", "entry_script_started"), {
            "schema_version": 1, "run_id": "", "stage": "entry_script_started",
            "timestamp": _iso(host_now)[:-1] + "0Z",
        })
        atomic_write_json(evidence / startup_marker_filename("payload", "payload_interpreter_entered"), {
            "run_id": RUN_ID, "stage": "payload_interpreter_entered", "result": "entered",
            "timestamp": _iso(host_now + timedelta(seconds=0.1))[:-1] + "0Z",
        })
        atomic_write_json(evidence / "entry-payload-result.json", {
            "schema_version": 1, "run_id": RUN_ID, "stage": "payload_process_exited",
            "timestamp": _iso(host_now + timedelta(seconds=0.2))[:-1] + "0Z",
            "exit_code": 0, "status": "passed",
        })
        return process

    result = ScreenshotSelfTestRunner(
        workspace_manager=manager, capability_detector=_capability, launcher=launch,
        monotonic=clock, sleeper=lambda seconds: setattr(clock, "value", clock.value + 600),
        utc_clock=lambda: host_now,
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.status == RunStatus.TIMED_OUT
    assert result.exit_reason == "timeout"
    assert result.launcher_return_code == 0
    assert process.terminate_calls == 1
    assert not (manager.paths_for(request.run_id).logs_directory / "validated-screenshot-evidence.json").exists()


def test_production_runner_cancellation_cleans_owned_session(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    process = _Process()
    session = _OwnedSession()
    clock = _Clock()
    cancellation = threading.Event()

    def sleep(seconds):
        clock.value += seconds
        cancellation.set()

    result = ScreenshotSelfTestRunner(
        workspace_manager=manager,
        capability_detector=_capability,
        launcher=lambda argv: process,
        monotonic=clock,
        sleeper=sleep,
        session_guard=session.guard,
        session_factory=session.factory,
    ).run(request, cancellation)

    assert result.status == RunStatus.CANCELLED
    assert result.exit_reason == "cancelled"
    assert result.launcher_return_code == 0
    assert process.terminate_calls == 1
    _no_active_session_for(session)
    assert session.close_calls == 1


def test_payload_initialization_failure_reports_exact_stage(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    clock = _Clock()
    process = _Process()
    session = _OwnedSession()
    evidence_directory = None
    evidence_written = False
    host_now = datetime.now(timezone.utc)

    def launch(argv):
        nonlocal evidence_directory
        evidence_directory = Path(argv[1]).parent / "evidence"
        return process

    def sleep(seconds):
        nonlocal evidence_written
        clock.value += seconds
        if not evidence_written:
            evidence_written = True
            atomic_write_json(evidence_directory / startup_marker_filename("entry", "entry_script_started"), {
                "schema_version": 1, "run_id": "", "stage": "entry_script_started",
                "timestamp": _iso(host_now)[:-1] + "0Z",
            })
            atomic_write_json(evidence_directory / startup_marker_filename("payload", "payload_interpreter_entered"), {
                "run_id": RUN_ID, "stage": "payload_interpreter_entered", "result": "entered",
                "timestamp": _iso(host_now + timedelta(seconds=0.1))[:-1] + "0Z",
            })
            atomic_write_json(evidence_directory / "payload-initialization-failure.json", {
                "schema_version": 1, "run_id": RUN_ID, "stage": "screenshot_api_add_type_started",
                "exception_type": "System.InvalidOperationException", "fully_qualified_error_id": "TYPE_ALREADY_EXISTS",
                "category": "InvalidOperation", "hresult": -1, "exit_code": 23,
                "timestamp": _iso(host_now + timedelta(seconds=0.2))[:-1] + "0Z",
                "reason": "screenshot_api_initialization_failed",
                "normalized_message": "screenshot_api_initialization_failed",
            })

    result = ScreenshotSelfTestRunner(
        workspace_manager=ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=_capability, launcher=launch, monotonic=clock, sleeper=sleep,
        utc_clock=lambda: host_now,
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.exit_reason == "payload_initialization_failed"
    assert result.errors == ("payload_initialization_failed_screenshot_api_add_type_started",)
    assert result.duration_seconds == 16.5
    assert session.close_calls == 1 and session.active is False


def test_full_payload_load_probe_is_non_executable_and_exactly_gated(tmp_path, monkeypatch):
    request = payload_load_probe_request()
    manager = PayloadLoadProbeWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    assert staged.name == "artifact.md" and staged.suffix == ".md"
    assert not any(path.suffix.casefold() == ".exe" for path in paths.input_directory.iterdir())
    payload = (paths.guest_directory / PAYLOAD_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert payload.index('execution_mode -ceq "payload_load_probe"') < payload.index('Set-Phase "installing"')
    monkeypatch.delenv(PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN, raising=False)
    assert not payload_load_probe_external_opt_in_enabled("external")
    monkeypatch.setenv(PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN, "1")
    assert payload_load_probe_external_opt_in_enabled("external")
    assert not payload_load_probe_external_opt_in_enabled("external and not slow")


def test_payload_load_probe_completion_parser_accepts_exact_contract(tmp_path):
    path = tmp_path / PAYLOAD_LOAD_PROBE_COMPLETION
    completion = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "status": "passed",
        "timestamp": _iso(BASE)[:-1] + "0Z",
    }
    atomic_write_json(path, completion)
    assert read_payload_load_probe_completion(path, RUN_ID) == completion


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate", "extra", "schema_bool", "schema_float", "run_id", "status",
        "timestamp_format", "timestamp_impossible", "oversized", "directory",
    ],
)
def test_payload_load_probe_completion_parser_rejects_invalid_contract(tmp_path, defect):
    path = tmp_path / PAYLOAD_LOAD_PROBE_COMPLETION
    completion = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "status": "passed",
        "timestamp": _iso(BASE)[:-1] + "0Z",
    }
    if defect == "duplicate":
        path.write_text(json.dumps(completion)[:-1] + ',"status":"passed"}', encoding="utf-8")
    elif defect == "extra":
        completion["extra"] = True
    elif defect == "schema_bool":
        completion["schema_version"] = True
    elif defect == "schema_float":
        completion["schema_version"] = 1.0
    elif defect == "run_id":
        completion["run_id"] = "00000000-0000-0000-0000-000000000000"
    elif defect == "status":
        completion["status"] = "failed"
    elif defect == "timestamp_format":
        completion["timestamp"] = "2026-01-01T00:00:00Z"
    elif defect == "timestamp_impossible":
        completion["timestamp"] = "2026-02-30T00:00:00.0000000Z"
    elif defect == "oversized":
        path.write_bytes(b"x" * 4097)
    else:
        path.mkdir()
    if defect not in {"duplicate", "oversized", "directory"}:
        atomic_write_json(path, completion)
    with pytest.raises(WorkspaceError, match="payload load probe completion"):
        read_payload_load_probe_completion(path, RUN_ID)


@pytest.mark.parametrize(
    ("result_case", "expected_status", "expected_reason"),
    [
        ("absent", RunStatus.PASSED, "payload_load_probe_completed"),
        ("valid", RunStatus.PASSED, "payload_load_probe_completed"),
        ("failed", RunStatus.INFRASTRUCTURE_ERROR, "payload_initialization_failed"),
        ("duplicate", RunStatus.INFRASTRUCTURE_ERROR, "infrastructure_error"),
        ("directory", RunStatus.INFRASTRUCTURE_ERROR, "infrastructure_error"),
        ("invalid_completion", RunStatus.INFRASTRUCTURE_ERROR, "infrastructure_error"),
    ],
)
def test_full_payload_load_probe_runner_validates_terminal_entry_result(
    tmp_path, monkeypatch, result_case, expected_status, expected_reason,
):
    monkeypatch.setattr(payload_probe_module, "ensure_no_active_windows_sandbox_session", lambda: None)
    request = payload_load_probe_request()
    manager = PayloadLoadProbeWorkspaceManager(tmp_path / "runtime")
    process = _Process()
    session = _OwnedSession(active=False)
    base = datetime.now(timezone.utc) - timedelta(seconds=1)

    def launch(argv):
        evidence = Path(argv[1]).parent / "evidence"
        offset = 0
        for prefix, stages in (("entry", ENTRY_STARTUP_STAGES), ("payload", PROBE_PAYLOAD_STAGES)):
            for stage in stages:
                run_id = "" if stage in {"entry_script_started", "request_found"} else request.run_id
                marker = {"run_id": run_id, "stage": stage, "timestamp": _iso(base + timedelta(milliseconds=offset))[:-1] + "0Z"}
                if prefix == "entry": marker["schema_version"] = 1
                else: marker["result"] = "started" if stage.endswith("_started") else "entered" if stage == "payload_interpreter_entered" else "completed"
                atomic_write_json(evidence / startup_marker_filename(prefix, stage), marker)
                offset += 10
        completion_payload = {
            "schema_version": 1, "run_id": request.run_id, "status": "passed",
            "timestamp": _iso(base + timedelta(milliseconds=offset))[:-1] + "0Z",
        }
        completion_path = evidence / PAYLOAD_LOAD_PROBE_COMPLETION
        if result_case == "invalid_completion":
            completion_path.write_text(
                json.dumps(completion_payload)[:-1] + ',"status":"passed"}',
                encoding="utf-8",
            )
        else:
            atomic_write_json(completion_path, completion_payload)
        if result_case in {"valid", "failed", "duplicate", "directory"}:
            payload_result = {
                "schema_version": 1, "run_id": request.run_id, "stage": "payload_process_exited",
                "timestamp": _iso(base + timedelta(milliseconds=offset + 10))[:-1] + "0Z",
                "exit_code": 1 if result_case == "failed" else 0,
                "status": "failed" if result_case == "failed" else "passed",
            }
            result_path = evidence / "entry-payload-result.json"
            if result_case == "directory":
                result_path.mkdir()
            elif result_case == "duplicate":
                result_path.write_text(json.dumps(payload_result)[:-1] + ',"status":"passed"}', encoding="utf-8")
            else:
                atomic_write_json(result_path, payload_result)
        return process

    result = PayloadLoadProbeRunner(
        workspace_manager=manager, capability_detector=_capability, launcher=launch,
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.status == expected_status, result
    assert result.exit_reason == expected_reason
    snapshot = manager.paths_for(request.run_id).logs_directory / "validated-payload-load-timeline.json"
    if expected_status == RunStatus.PASSED:
        timeline = json.loads(snapshot.read_text(encoding="utf-8"))
        assert [item["stage"] for item in timeline["timeline"]] == [*ENTRY_STARTUP_STAGES, *PROBE_PAYLOAD_STAGES]
        assert result.launcher_return_code == 0
        assert process.terminate_calls == 1
        _no_active_session_for(session)
    else:
        assert not snapshot.exists()


def test_payload_probe_timeout_and_cancellation_cleanup_owned_session(tmp_path, monkeypatch):
    monkeypatch.setattr(payload_probe_module, "ensure_no_active_windows_sandbox_session", lambda: None)

    for cancelled in (False, True):
        request = payload_load_probe_request()
        manager = PayloadLoadProbeWorkspaceManager(tmp_path / str(cancelled) / "runtime")
        process = _Process()
        session = _OwnedSession()
        clock = _Clock()
        cancellation = threading.Event()

        def sleep(seconds):
            clock.value += 100 if not cancelled else seconds
            if cancelled:
                cancellation.set()

        result = PayloadLoadProbeRunner(
            workspace_manager=manager,
            capability_detector=_capability,
            launcher=lambda argv: process,
            monotonic=clock,
            sleeper=sleep,
            session_guard=session.guard,
            session_factory=session.factory,
        ).run(request, cancellation)

        assert result.status == (RunStatus.CANCELLED if cancelled else RunStatus.INFRASTRUCTURE_ERROR)
        assert result.exit_reason == ("cancelled" if cancelled else "payload_load_probe_timeout")
        assert result.launcher_return_code == 0
        assert process.terminate_calls == 1
        _no_active_session_for(session)
        assert session.close_calls == 1


def test_phase3b_runners_share_canonical_owned_session_lifecycle_contract():
    assert screenshot_runner_module.complete_owned_sandbox_session is runner_module.complete_owned_sandbox_session
    assert payload_probe_module.complete_owned_sandbox_session is runner_module.complete_owned_sandbox_session


def test_launcher_exit_zero_does_not_prove_session_exit_and_uses_soft_owned_fallback():
    clock = _Clock()
    process = _Process(return_code=0)
    session = _OwnedSession()

    code, exited_at = runner_module.complete_owned_sandbox_session(
        process,
        session=session,
        session_guard=session.guard,
        monotonic=clock,
        sleeper=lambda seconds: setattr(clock, "value", clock.value + seconds),
    )

    assert code == 0 and exited_at.endswith("Z")
    assert clock.value == runner_module.GUEST_SANDBOX_SHUTDOWN_GRACE_SECONDS + 1
    assert session.close_calls == 1 and session.terminate_calls == 0
    assert process.terminate_calls == 0
    _no_active_session_for(session)


def test_host_polls_until_guest_shutdown_really_removes_session():
    clock = _Clock()
    process = _Process(return_code=0)
    session = _OwnedSession()

    def sleep(seconds):
        clock.value += seconds
        if clock.value >= 1:
            session.active = False

    runner_module.complete_owned_sandbox_session(
        process,
        session=session,
        session_guard=session.guard,
        monotonic=clock,
        sleeper=sleep,
    )

    assert clock.value == 2.0
    assert session.close_calls == 0 and session.terminate_calls == 0


def test_owned_fallback_revalidates_exact_client_without_name_or_global_kill(monkeypatch):
    calls = []
    identity = session_module.OwnedSandboxSession(
        launcher_pid=4242,
        client_pid=4343,
        client_start_ticks=638800000000000000,
        client_started_at="2026-07-22T10:00:00.0000000Z",
        client_path=r"C:\Windows\System32\WindowsSandboxClient.exe",
    )

    monkeypatch.setattr(session_module, "_powershell_path", lambda: Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})

    def run(argv, **kwargs):
        calls.append(argv[-1])
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(session_module.subprocess, "run", run)
    identity.request_close()
    identity.terminate()

    assert all("Get-Process -Id 4343" in command for command in calls)
    assert all("638800000000000000" in command for command in calls)
    assert all("taskkill" not in command.casefold() and "Stop-Process" not in command for command in calls)
    assert all("Get-Process -Name" not in command for command in calls)


def test_session_capture_pins_only_direct_current_run_client_identity(monkeypatch):
    launched_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    client_path = Path(r"C:\Windows\System32\WindowsSandboxClient.exe")
    commands = []
    payload = {
        "process_id": 4343,
        "parent_process_id": 4242,
        "start_ticks": 638800000000000000,
        "started_at": _iso(launched_at + timedelta(milliseconds=100)),
        "path": str(client_path),
    }

    monkeypatch.setattr(session_module, "_powershell_path", lambda: Path("powershell.exe"))
    monkeypatch.setattr(session_module, "_client_path", lambda: client_path)
    monkeypatch.setattr(session_module, "_controlled_environment", lambda: {})

    def run(argv, **kwargs):
        commands.append(argv[-1])
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload)})()

    monkeypatch.setattr(session_module.subprocess, "run", run)
    identity = session_module.capture_owned_sandbox_session(4242, launched_at)

    assert identity.launcher_pid == 4242 and identity.client_pid == 4343
    assert "ParentProcessId=4242" in commands[0]
    assert "Name='WindowsSandboxClient.exe'" in commands[0]
    assert "SELECT ProcessId,ParentProcessId,CreationDate" in commands[0]


def test_guest_shutdown_is_after_atomic_terminal_publication_on_all_entry_paths():
    entry = (Path(__file__).parent / "sandbox_test_lab" / "guest" / ENTRY_SCRIPT_FILENAME).read_text(encoding="utf-8")
    shutdown = entry.index("$ShutdownInfo = New-Object Diagnostics.ProcessStartInfo")
    assert entry.index("Write-AtomicJson $ResultPath") < shutdown
    assert entry.index("Write-AtomicJson $FailurePath") < shutdown
    assert '$ShutdownInfo.FileName = $CanonicalShutdown' in entry
    assert '$ShutdownInfo.Arguments = "/s /t 0"' in entry


def test_payload_probe_plain_pytest_skips_before_sandbox(monkeypatch, tmp_path):
    monkeypatch.delenv(PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN, raising=False)
    fake = type("Request", (), {"config": type("Config", (), {"option": type("Option", (), {"markexpr": ""})()})()})()
    with pytest.raises(pytest.skip.Exception):
        payload_external_module.test_windows_sandbox_phase3b_full_payload_load_probe(tmp_path, fake)


def test_entry_script_is_small_static_and_has_no_raw_command_surface():
    entry = (Path(__file__).parent / "sandbox_test_lab" / "guest" / ENTRY_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert len(entry.encode("utf-8")) < 8 * 1024
    assert SCREENSHOT_ENTRY_COMMAND.endswith(f'-File "C:\\SandboxTestLab\\Guest\\{ENTRY_SCRIPT_FILENAME}"')
    assert "Invoke-Expression" not in entry and "Start-Process" not in entry and "shell=" not in entry.casefold()
    assert '$StartInfo.FileName = $CanonicalPowerShell' in entry
    assert '$StartInfo.Arguments = \'-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\\SandboxTestLab\\Guest\\production_screenshot_self_test.ps1"\'' in entry
    assert "$Request." not in entry[entry.index("$StartInfo ="):]


def test_runner_snapshot_has_full_contract_bounds_dpi_summary_and_provenance(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    expected_snapshot = manager.paths_for(request.run_id).logs_directory / "validated-screenshot-evidence.json"
    process = _Process(on_terminate=lambda: expected_snapshot.read_bytes())
    session = _OwnedSession(active=False)
    capability = SandboxCapability(supported_os=True, windows_edition="Professional", windows_build=22631, virtualization_available=True, sandbox_feature_state="enabled", executable_found=True, available=True, executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True)
    host_now = datetime.now(timezone.utc)
    host_started = host_now - timedelta(seconds=30)
    base = host_now - timedelta(seconds=20)
    expected_deadline = _iso(host_started + timedelta(
        seconds=request.timeout_seconds - production_module.HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    ))
    observed_deadlines = []
    original_validator = screenshot_runner_module.validate_screenshot_evidence_directory

    def validate_with_deadline(*args, **kwargs):
        observed_deadlines.append(kwargs.get("expected_guest_terminal_deadline_utc"))
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(screenshot_runner_module, "validate_screenshot_evidence_directory", validate_with_deadline)

    def launch(argv):
        _evidence(
            Path(argv[1]).parent / "evidence",
            base=base,
            production_updates={"guest_terminal_deadline_utc": expected_deadline},
        )
        return process
    result = ScreenshotSelfTestRunner(
        workspace_manager=manager, capability_detector=lambda: capability,
        launcher=launch, utc_clock=lambda: host_started,
        session_guard=session.guard, session_factory=session.factory,
    ).run(request)
    assert result.status == RunStatus.PASSED, result
    assert result.launcher_return_code == 0
    assert process.terminate_calls == 1
    _no_active_session_for(session)
    assert observed_deadlines == [expected_deadline]
    evidence_directory = manager.paths_for(request.run_id).evidence_directory
    assert not (evidence_directory / "entry-payload-result.json").exists()
    snapshot_path = Path(result.evidence_path)
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_bytes)
    assert snapshot["validation_status"] == "passed"
    assert snapshot["window_bounds"]["width"] == 320 and snapshot["client_bounds"]["width"] == 304
    assert snapshot["dpi_scale"] == 1.25 and image_summary_passes(snapshot["host_image_summary"])
    assert snapshot["provenance"]["production_cleanup_complete"] is True
    atomic_write_json(evidence_directory / "entry-payload-result.json", {
        "schema_version": 1, "run_id": request.run_id, "stage": "payload_process_exited",
        "timestamp": _iso(datetime.now(timezone.utc))[:-1] + "0Z", "exit_code": 0, "status": "passed",
    })
    assert snapshot_path.read_bytes() == snapshot_bytes


def test_production_runner_malformed_evidence_cleans_owned_session(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    process = _Process()
    session = _OwnedSession()
    clock = _Clock()
    host_started = datetime.now(timezone.utc) - timedelta(seconds=30)
    base = host_started + timedelta(seconds=10)
    expected_deadline = _iso(host_started + timedelta(
        seconds=request.timeout_seconds - production_module.HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    ))

    def launch(argv):
        evidence = Path(argv[1]).parent / "evidence"
        _evidence(evidence, base=base, production_updates={"guest_terminal_deadline_utc": expected_deadline})
        (evidence / SCREENSHOT_MANIFEST_FILENAME).write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
        return process

    result = ScreenshotSelfTestRunner(
        workspace_manager=manager,
        capability_detector=_capability,
        launcher=launch,
        utc_clock=lambda: host_started,
        monotonic=clock,
        sleeper=lambda seconds: setattr(clock, "value", clock.value + seconds),
        session_guard=session.guard,
        session_factory=session.factory,
    ).run(request)

    assert result.status == RunStatus.FAILED
    assert result.exit_reason == "invalid_screenshot_evidence"
    assert result.launcher_return_code == 0
    assert process.terminate_calls == 1
    _no_active_session_for(session)
    assert session.close_calls == 1


def test_valid_evidence_cannot_pass_while_owned_session_remains_active(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ScreenshotSelfTestWorkspaceManager(tmp_path / "runtime")
    process = _Process(return_code=0)
    session = _OwnedSession(close_succeeds=False, terminate_succeeds=False)
    clock = _Clock()
    host_started = datetime.now(timezone.utc) - timedelta(seconds=30)
    base = host_started + timedelta(seconds=10)
    expected_deadline = _iso(host_started + timedelta(
        seconds=request.timeout_seconds - production_module.HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    ))

    def launch(argv):
        _evidence(
            Path(argv[1]).parent / "evidence",
            base=base,
            production_updates={"guest_terminal_deadline_utc": expected_deadline},
        )
        return process

    result = ScreenshotSelfTestRunner(
        workspace_manager=manager,
        capability_detector=_capability,
        launcher=launch,
        utc_clock=lambda: host_started,
        monotonic=clock,
        sleeper=lambda seconds: setattr(clock, "value", clock.value + seconds),
        session_guard=session.guard,
        session_factory=session.factory,
    ).run(request)

    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "sandbox_session_cleanup_failed"
    assert session.close_calls == 1 and session.terminate_calls == 1
    assert session.active is True
    assert (manager.paths_for(request.run_id).logs_directory / "validated-screenshot-evidence.json").is_file()


def test_phase3a_gate_does_not_authorize_phase3b(monkeypatch):
    monkeypatch.delenv(SCREENSHOT_EXTERNAL_OPT_IN, raising=False)
    monkeypatch.setenv(production_module.PRODUCTION_EXTERNAL_OPT_IN, "1")
    assert not screenshot_external_opt_in_enabled(True, "external")
    assert not external_module.external_opt_in_enabled("external")


def test_no_opt_in_plain_pytest_skips_before_sandbox_detection(monkeypatch, tmp_path):
    monkeypatch.delenv(SCREENSHOT_EXTERNAL_OPT_IN, raising=False)
    monkeypatch.setattr(external_module, "detect_sandbox_capability", lambda: (_ for _ in ()).throw(AssertionError("Sandbox must not run")))
    fake = type("Request", (), {"config": type("Config", (), {"option": type("Option", (), {"markexpr": ""})()})()})()
    with pytest.raises(pytest.skip.Exception):
        external_module.test_windows_sandbox_production_window_screenshot(tmp_path, fake)


def test_dual_gate_requires_exact_external_marker(monkeypatch):
    monkeypatch.setenv(SCREENSHOT_EXTERNAL_OPT_IN, "1")
    assert screenshot_external_opt_in_enabled(True, "external")
    assert not screenshot_external_opt_in_enabled(False, "external")
    assert not screenshot_external_opt_in_enabled(True, "external and not slow")


def test_no_click_clipboard_shell_network_or_taskkill_invocation_in_phase3b_insertions():
    capture = (screenshot_module._API_INSERTION + screenshot_module._CAPTURE_INSERTION).lower()
    for forbidden in ("clipboard", "sendkeys", "mouse_event", "invoke-expression", "start-process", "taskkill", "http://", "https://"):
        assert forbidden not in capture
    python_source = (Path(__file__).parent / "sandbox_test_lab" / "screenshot_runner.py").read_text(encoding="utf-8")
    assert "shell=True" not in python_source and "subprocess.run(" not in python_source


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell parser is Windows-only")
def test_exact_tracked_payload_screenshot_csharp_compiles_under_windows_powershell_51():
    payload = Path(__file__).parent / "sandbox_test_lab" / "guest" / PAYLOAD_SCRIPT_FILENAME
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    audit = r'''
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:AIFS_TEST_PAYLOAD,[ref]$tokens,[ref]$parseErrors)
$source=$null;$reference=$null
foreach($command in @($ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq "Add-Type"},$true))){
    $elements=@($command.CommandElements)
    for($index=0;$index -lt $elements.Count;$index++){
        if($elements[$index].Extent.Text -eq "-TypeDefinition" -and $index+1 -lt $elements.Count -and ($elements[$index+1].PSObject.Properties.Name -contains "Value")){$source=[string]$elements[$index+1].Value}
        if($elements[$index].Extent.Text -eq "-ReferencedAssemblies" -and $index+1 -lt $elements.Count){$reference=[string]$elements[$index+1].Extent.Text}
    }
}
if($parseErrors.Count -or $null -eq $source -or $reference -cne "System.Drawing"){exit 3}
$compilerErrors=@()
Add-Type -TypeDefinition $source -ReferencedAssemblies $reference -ErrorAction SilentlyContinue -ErrorVariable +compilerErrors
$structured=@($compilerErrors|Where-Object{$null-ne$_.TargetObject}|ForEach-Object{"$($_.TargetObject.ErrorNumber):$($_.TargetObject.Line):$($_.TargetObject.Column)"})
if($structured.Count){$structured;exit 2}
exit 0
'''
    environment = dict(os.environ)
    environment["AIFS_TEST_PAYLOAD"] = str(payload)
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", audit],
        env=environment, capture_output=True, text=True, errors="replace", check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell parser is Windows-only")
@pytest.mark.parametrize("filename", [ENTRY_SCRIPT_FILENAME, PAYLOAD_SCRIPT_FILENAME])
def test_static_phase3b_powershell_51_parser_and_ast_have_no_taskkill_command(filename):
    script = Path(__file__).parent / "sandbox_test_lab" / "guest" / filename
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    parser = (
        "$tokens=$null;$errors=$null;"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile('{str(script).replace("'", "''")}',[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 2};"
        "$ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst]},$true)|ForEach-Object{$_.GetCommandName()}"
    )
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", parser], capture_output=True, text=True, errors="replace", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = {line.strip().replace("/", "\\").rsplit("\\", 1)[-1].casefold() for line in result.stdout.splitlines() if line.strip()}
    assert not commands.intersection({"taskkill", "taskkill.exe"})
