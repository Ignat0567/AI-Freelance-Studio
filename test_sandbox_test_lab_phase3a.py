from __future__ import annotations

import hashlib
import json
import base64
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import sandbox_test_lab.production_evidence as evidence_module
import sandbox_test_lab.production_self_test as production_module
import sandbox_test_lab.production_runner as production_runner_module
import test_sandbox_test_lab_phase3a_external as external_module
from sandbox_test_lab.fixture_launch import INSTALL_LAUNCH_PROTOCOL
from sandbox_test_lab.evidence import SCHEMA_VERSION as PHASE1_SCHEMA_VERSION
from sandbox_test_lab.models import SandboxRunRequest
from sandbox_test_lab.models import RunStatus, SandboxCapability
from sandbox_test_lab.production_evidence import ProductionEvidenceError, validate_production_evidence_directory
from sandbox_test_lab.production_self_test import (
    BACKEND_ENDPOINT,
    BACKEND_RELATIVE,
    INSTALLER_FILENAME,
    INSTALLER_SHA256,
    INSTALLER_SIZE,
    MINIMUM_FREE_SPACE_BYTES,
    GUEST_EVIDENCE_RESERVE_SECONDS,
    HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS,
    HOST_TIMEOUT_SECONDS,
    MINIMUM_CLEANUP_RESERVE_SECONDS,
    LOGICAL_INSTALL_ROOT,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    PRODUCTION_EXTERNAL_OPT_IN,
    PRODUCTION_PROTOCOL,
    PRODUCTION_SCHEMA_VERSION,
    SIDECAR_FILENAME,
    TRUSTED_PROFILE,
    TRUSTED_PROFILE_NAME,
    ProductionSelfTestRequest,
    ProductionSelfTestWorkspaceManager,
    build_production_guest_request,
    classify_canonical_windows_image_origin,
    external_helper_trust_prerequisites_met,
    effective_phase_budget_seconds,
    production_external_opt_in_enabled,
    validate_trusted_production_artifact,
    validate_production_deadline_configuration,
)
from sandbox_test_lab.production_runner import ProductionSelfTestRunner
from sandbox_test_lab.workspace import SandboxWorkspaceManager, WorkspaceError, atomic_write_json


pytestmark = pytest.mark.unit


def _diagnostic(role: str, basename: str, *, origin: str = "verified_install_root", **updates) -> dict:
    external = origin != "verified_install_root"
    diagnostic = {
        "process_role": role,
        "image_basename": basename,
        "image_origin": origin,
        "image_regular_file": True,
        "image_reparse_free": True,
        "image_hash": "b" * 64,
        "authenticode_status": "valid" if external else "not_signed",
        "microsoft_signed": True if role == "system_helper" else False,
        "parent_relation_verified": True,
        "creation_after_launch": True,
        "eligible_for_gui_verification": not external and role in {"root", "electron_child"},
        "eligible_for_backend_verification": not external and role == "backend_candidate",
        "eligible_for_cleanup": not external,
    }
    diagnostic.update(updates)
    return diagnostic


def _trusted_tree(tmp_path: Path, monkeypatch, content: bytes = b"trusted production installer") -> Path:
    installer_dir = tmp_path / "frontend" / "installers"
    installer_dir.mkdir(parents=True)
    artifact = installer_dir / INSTALLER_FILENAME
    artifact.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(production_module, "repository_root", lambda: tmp_path)
    monkeypatch.setattr(production_module, "INSTALLER_SIZE", len(content))
    monkeypatch.setattr(production_module, "INSTALLER_SHA256", digest)
    monkeypatch.setattr(production_module, "SIDECAR_CONTENT", f"{digest.upper()} *{INSTALLER_FILENAME}\n")
    (installer_dir / SIDECAR_FILENAME).write_text(production_module.SIDECAR_CONTENT, encoding="ascii")
    return artifact


def _request(tmp_path: Path, monkeypatch) -> ProductionSelfTestRequest:
    _trusted_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(production_module, "ensure_no_active_windows_sandbox_session", lambda: None)
    return ProductionSelfTestRequest(run_id="21a509d2-9f12-43cc-a326-c126aca8187a")


def _future_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=9)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _passed_status(run_id: str, *, fallback: bool = False, guest_terminal_deadline_utc: str = "2026-01-01T00:20:00.000000Z") -> dict:
    return {
        "schema_version": 3, "protocol": PRODUCTION_PROTOCOL, "run_id": run_id, "profile_name": TRUSTED_PROFILE_NAME,
        "product": PRODUCT_NAME, "version": PRODUCT_VERSION, "status": "passed", "outcome": "passed",
        "overall_readiness": "fully_ready", "started_at": "2026-01-01T00:00:00Z",
        "guest_terminal_deadline_utc": guest_terminal_deadline_utc,
        "installer_started_at": "2026-01-01T00:00:01Z", "process_start_returned_at": "2026-01-01T00:00:01.100Z",
        "installer_finished_at": "2026-01-01T00:00:02Z",
        "launch_started_at": "2026-01-01T00:00:03Z", "process_started_at": "2026-01-01T00:00:04Z",
        "window_detected_at": "2026-01-01T00:00:05Z", "stable_started_at": "2026-01-01T00:00:05Z",
        "stable_verified_at": "2026-01-01T00:00:10Z", "backend_started_at": "2026-01-01T00:00:11Z",
        "backend_ready_at": "2026-01-01T00:00:12Z", "cleanup_started_at": "2026-01-01T00:00:13Z",
        "cleanup_finished_at": "2026-01-01T00:00:14Z", "completed_at": "2026-01-01T00:00:15Z",
        "artifact_name": "artifact.exe", "artifact_size": INSTALLER_SIZE, "artifact_sha256_host": INSTALLER_SHA256,
        "artifact_sha256_guest": INSTALLER_SHA256, "host_artifact_verified": True, "guest_artifact_verified": True,
        "network_disabled": True, "installer_exit_code": 0, "reboot_required": False, "installed_exe_found": True,
        "installer_exit_code_hex": "00000000", "install_duration_seconds": 1.0,
        "installer_process_started": True, "installer_process_exited": True, "crash_event_found": False,
        "faulting_application_basename": "unavailable", "faulting_module_basename": "unavailable",
        "exception_code": "", "fault_offset": "", "wer_event_type": "unavailable", "installer_diagnostic_source": "none",
        "installed_exe_sha256": "a" * 64, "installed_exe_regular": True, "installed_exe_non_reparse": True,
        "install_root_verified": True, "installation_passed": True, "launch_root_pid": 100, "owned_tree_count": 3,
        "all_images_contained": True, "root_process_retained": True, "window_handle": 1234,
        "window_title": "AI Freelance Studio", "window_visible": True, "window_owner_pid": 101,
        "window_owner_in_owned_tree": True, "stable_duration_seconds": 5.0, "first_launch_verified": True,
        "gui_passed": True, "backend_required": True, "backend_process_tracked": True, "backend_image_contained": True,
        "backend_status": "ready", "backend_http_status": 200, "backend_response_status": "ok",
        "backend_response_service": "FreelancerStudio", "backend_endpoint_verified": True, "backend_pid": 102,
        "backend_pid_ownership_verified": True, "backend_image_verified": True, "fully_ready": True,
        "production_cleanup_method": "owned_backend_pid_tree", "production_taskkill_observed": True,
        "graceful_close_attempted": True, "graceful_cleanup_succeeded": not fallback, "fallback_kill_used": fallback,
        "owned_processes_exited": True, "auxiliary_processes_exited": True, "cleanup_complete": True, "errors": [],
        "warnings": ["owned_tree_fallback_kill_used"] if fallback else [],
        "owned_descendant_diagnostics": [
            _diagnostic("root", "AI Freelance Studio.exe"),
            _diagnostic("electron_child", "AI Freelance Studio.exe"),
            _diagnostic("backend_candidate", "freelancerstudio-backend.exe"),
        ],
    }


def _write_evidence(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / "status.json", payload)
    atomic_write_json(directory / "production-evidence.json", payload)
    atomic_write_json(directory / "completion.json", {
        "schema_version": 3, "protocol": PRODUCTION_PROTOCOL, "run_id": payload["run_id"], "completed": True,
        "status": payload["status"], "outcome": payload["outcome"], "overall_readiness": payload["overall_readiness"],
        "completion_created_at": payload["completed_at"],
    })
    atomic_write_json(directory / "heartbeat.json", {
        "schema_version": 3, "protocol": PRODUCTION_PROTOCOL, "run_id": payload["run_id"], "phase": "completed",
        "updated_at": payload["completed_at"],
    })
    atomic_write_json(directory / "guest-system.json", {"os_version": "Windows", "architecture": "True", "powershell_version": "5.1"})
    terminal = "passed" if payload["status"] == "passed" else "failed"
    (directory / "lifecycle.log").write_text(
        "2026-01-01T00:00:00Z production_self_test_started\n"
        f"{payload['completed_at']} production_self_test_{terminal}\n", encoding="utf-8",
    )


def _external_failure(run_id: str, role: str = "unknown", origin: str = "outside_untrusted") -> dict:
    payload = _passed_status(run_id)
    payload.update(
        status="failed", outcome="failed", overall_readiness="gui_launch_failed", owned_tree_count=2,
        all_images_contained=False, window_handle=None, window_title="", window_visible=False, window_owner_pid=None,
        window_owner_in_owned_tree=False, stable_duration_seconds=None, first_launch_verified=False, gui_passed=False,
        backend_process_tracked=False, backend_image_contained=False, backend_status="not_ready", backend_http_status=None,
        backend_response_status="", backend_response_service="", backend_endpoint_verified=False, backend_pid=None,
        backend_pid_ownership_verified=False, backend_image_verified=False, fully_ready=False,
        production_cleanup_method="", production_taskkill_observed=False, graceful_close_attempted=False,
        window_detected_at=None, stable_started_at=None, stable_verified_at=None, backend_started_at=None,
        backend_ready_at=None, errors=["owned_process_image_outside_install_root"],
        owned_descendant_diagnostics=[
            _diagnostic("root", "AI Freelance Studio.exe"),
            _diagnostic(role, "conhost.exe" if role == "system_helper" else "mystery.exe", origin=origin),
        ],
    )
    return payload


def _installer_timeout_status(run_id: str) -> dict:
    payload = _passed_status(run_id)
    payload.update(
        status="failed", outcome="timed_out", overall_readiness="installation_failed",
        installer_finished_at="2026-01-01T00:10:01Z", install_duration_seconds=600.0,
        installer_exit_code=-1, installer_exit_code_hex="ffffffff", installation_passed=False,
        installed_exe_found=False, installed_exe_sha256="", installed_exe_regular=False,
        installed_exe_non_reparse=False, install_root_verified=False, launch_root_pid=None, owned_tree_count=0,
        all_images_contained=False, root_process_retained=False, window_handle=None, window_title="", window_visible=False,
        window_owner_pid=None, window_owner_in_owned_tree=False, stable_duration_seconds=None,
        first_launch_verified=False, gui_passed=False, backend_process_tracked=False, backend_image_contained=False,
        backend_status="not_ready", backend_http_status=None, backend_response_status="", backend_response_service="",
        backend_endpoint_verified=False, backend_pid=None, backend_pid_ownership_verified=False,
        backend_image_verified=False, fully_ready=False, production_cleanup_method="", production_taskkill_observed=False,
        graceful_close_attempted=False, graceful_cleanup_succeeded=False, owned_processes_exited=False,
        cleanup_complete=False, launch_started_at=None, process_started_at=None, window_detected_at=None,
        stable_started_at=None, stable_verified_at=None, backend_started_at=None, backend_ready_at=None,
        cleanup_started_at=None, cleanup_finished_at=None, completed_at="2026-01-01T00:10:02Z",
        errors=["installer_timed_out"], owned_descendant_diagnostics=[],
    )
    return payload


def test_exact_immutable_production_profile_and_request(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ProductionSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    terminal_deadline = _future_deadline()
    guest = build_production_guest_request(request, staged, digest, guest_terminal_deadline_utc=terminal_deadline)
    assert TRUSTED_PROFILE_NAME == "aifs_production_beta_1_self_test"
    assert PRODUCT_NAME == "AI Freelance Studio" and PRODUCT_VERSION == "1.0.0-beta.1"
    assert PRODUCTION_SCHEMA_VERSION == 3 and PRODUCTION_PROTOCOL != INSTALL_LAUNCH_PROTOCOL
    assert TRUSTED_PROFILE["silent_argument"] == "/S"
    assert TRUSTED_PROFILE["expected_install_root"] == LOGICAL_INSTALL_ROOT
    assert TRUSTED_PROFILE["backend_executable_relative"] == BACKEND_RELATIVE
    assert TRUSTED_PROFILE["backend_endpoint"] == BACKEND_ENDPOINT
    assert [TRUSTED_PROFILE[key] for key in ("install_timeout_seconds", "launch_timeout_seconds", "minimum_stable_duration_seconds", "backend_readiness_seconds", "cleanup_timeout_seconds")] == [600, 60, 5, 30, 15]
    assert set(guest) == {"schema_version", "protocol", "run_id", "profile_name", "product", "version", "artifact_name", "artifact_size", "artifact_sha256_host", "guest_terminal_deadline_utc", "profile"}
    assert guest["guest_terminal_deadline_utc"] == terminal_deadline
    serialized = json.dumps(guest)
    for forbidden in (str(request.source_artifact), "command", "raw_args", "environment", "8081"):
        assert forbidden not in serialized
    assert tuple(path.name for path in paths.input_directory.iterdir()) == ("artifact.exe",)
    assert SIDECAR_FILENAME not in serialized
    assert MINIMUM_FREE_SPACE_BYTES == 2 * 1024 * 1024 * 1024
    assert paths.run_root.exists()


def test_canonical_release_constants_are_exact():
    assert INSTALLER_FILENAME == "AI Freelance Studio-Setup-1.0.0-beta.1-win.exe"
    assert INSTALLER_SIZE == 215224643
    assert INSTALLER_SHA256.upper() == "B72AD863F045F7877E9BEB32826C2090D96BAFE09F73B68C1892072CD4F1EF1F"
    assert SIDECAR_FILENAME == INSTALLER_FILENAME + ".sha256"


@pytest.mark.parametrize("failure", ["filename", "size", "hash", "sidecar", "arbitrary"])
def test_artifact_trust_rejects_every_identity_change(tmp_path, monkeypatch, failure):
    artifact = _trusted_tree(tmp_path, monkeypatch)
    if failure == "filename":
        artifact.rename(artifact.with_name("wrong.exe"))
    elif failure == "size":
        artifact.write_bytes(artifact.read_bytes() + b"x")
    elif failure == "hash":
        artifact.write_bytes(b"x" * artifact.stat().st_size)
    elif failure == "sidecar":
        artifact.with_name(SIDECAR_FILENAME).write_text("wrong\n", encoding="ascii")
    else:
        monkeypatch.setattr(production_module, "canonical_installer_path", lambda: tmp_path / "arbitrary.exe")
        (tmp_path / "arbitrary.exe").write_bytes(b"arbitrary")
    with pytest.raises(WorkspaceError):
        validate_trusted_production_artifact()


def test_copy_and_prelaunch_mismatch_fail_closed(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ProductionSelfTestWorkspaceManager(tmp_path / "runtime")
    paths, staged, digest = manager.create(request)
    terminal_deadline = _future_deadline()
    manager.write_guest_request(request, paths, staged, digest, guest_terminal_deadline_utc=terminal_deadline)
    from sandbox_test_lab.wsb_config import write_wsb_config
    write_wsb_config(paths, network_enabled=False)
    staged.write_bytes(b"changed")
    with pytest.raises(WorkspaceError, match="changed before launch"):
        manager.validate_for_launch(request, paths, guest_terminal_deadline_utc=terminal_deadline)


def test_post_copy_mismatch_is_rejected_immediately(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    original_create = SandboxWorkspaceManager.create

    def corrupt_copy(self, value):
        paths, staged, digest = original_create(self, value)
        staged.write_bytes(b"changed after copy")
        return paths, staged, digest

    monkeypatch.setattr(SandboxWorkspaceManager, "create", corrupt_copy)
    with pytest.raises(WorkspaceError, match="after copy"):
        ProductionSelfTestWorkspaceManager(tmp_path / "runtime").create(request)


def test_production_preflight_requires_fixed_free_space_for_artifact_and_runtime(tmp_path, monkeypatch):
    _trusted_tree(tmp_path, monkeypatch)
    monkeypatch.setattr(production_module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=MINIMUM_FREE_SPACE_BYTES - 1))
    with pytest.raises(WorkspaceError, match="2 GiB"):
        ProductionSelfTestRequest()

    monkeypatch.setattr(production_module.shutil, "disk_usage", lambda path: SimpleNamespace(
        free=MINIMUM_FREE_SPACE_BYTES + 1 if Path(path).name == "installers" else MINIMUM_FREE_SPACE_BYTES - 1
    ))
    request = ProductionSelfTestRequest()
    with pytest.raises(WorkspaceError, match="runtime"):
        ProductionSelfTestWorkspaceManager(tmp_path / "runtime").create(request)


def test_production_workspace_exclusive_run_directory_prevents_partial_reuse(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    manager = ProductionSelfTestWorkspaceManager(tmp_path / "runtime")
    manager.create(request)
    with pytest.raises(WorkspaceError, match="already exists"):
        manager.create(request)


def test_no_caller_selected_execution_surface(tmp_path, monkeypatch):
    _trusted_tree(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        ProductionSelfTestRequest(installer=tmp_path / "other.exe")
    with pytest.raises(TypeError):
        ProductionSelfTestRequest(endpoint="http://127.0.0.1:8081/health")
    with pytest.raises(ValueError, match="timeout is fixed"):
        ProductionSelfTestRequest(timeout_seconds=591)


def test_valid_fully_ready_evidence_with_graceful_and_fallback_cleanup(tmp_path):
    run_id = "31a509d2-9f12-43cc-a326-c126aca8187a"
    _write_evidence(tmp_path, _passed_status(run_id))
    assert validate_production_evidence_directory(tmp_path, run_id).fully_ready is True
    _write_evidence(tmp_path, _passed_status(run_id, fallback=True))
    assert validate_production_evidence_directory(tmp_path, run_id).raw["fallback_kill_used"] is True


def test_terminal_timestamp_order_accepts_success_installer_failure_and_cleanup_failure(tmp_path):
    run_id = "30a509d2-9f12-43cc-a326-c126aca8187a"
    success = _passed_status(run_id)
    _write_evidence(tmp_path, success)
    validate_production_evidence_directory(tmp_path, run_id)

    installer_failure = _passed_status(run_id)
    installer_failure.update(
        status="failed", outcome="failed", overall_readiness="installation_failed", installation_passed=False,
        installed_exe_found=False, installed_exe_sha256="", installed_exe_regular=False,
        installed_exe_non_reparse=False, install_root_verified=False, launch_root_pid=None, owned_tree_count=0,
        all_images_contained=False, root_process_retained=False, window_handle=None, window_title="", window_visible=False,
        window_owner_pid=None, window_owner_in_owned_tree=False, stable_duration_seconds=None,
        first_launch_verified=False, gui_passed=False, backend_process_tracked=False, backend_image_contained=False,
        backend_status="not_ready", backend_http_status=None, backend_response_status="", backend_response_service="",
        backend_endpoint_verified=False, backend_pid=None, backend_pid_ownership_verified=False,
        backend_image_verified=False, fully_ready=False, production_cleanup_method="", production_taskkill_observed=False,
        graceful_close_attempted=False, graceful_cleanup_succeeded=False, owned_processes_exited=False,
        cleanup_complete=False, launch_started_at=None, process_started_at=None, window_detected_at=None,
        stable_started_at=None, stable_verified_at=None, backend_started_at=None, backend_ready_at=None,
        cleanup_started_at=None, cleanup_finished_at=None, installer_exit_code=-1073741819,
        installer_exit_code_hex="c0000005", crash_event_found=False, errors=["installer_exit_nonzero"],
        owned_descendant_diagnostics=[],
    )
    _write_evidence(tmp_path, installer_failure)
    validate_production_evidence_directory(tmp_path, run_id)

    cleanup_failure = _passed_status(run_id)
    cleanup_failure.update(
        status="failed", outcome="failed", overall_readiness="backend_not_ready", fully_ready=False,
        cleanup_complete=False, owned_processes_exited=False, graceful_cleanup_succeeded=False,
        production_cleanup_method="owned_backend_pid_tree", errors=["owned_tree_cleanup_incomplete"],
    )
    _write_evidence(tmp_path, cleanup_failure)
    validate_production_evidence_directory(tmp_path, run_id)


def test_terminal_timestamp_contract_rejects_reverse_and_late_markers(tmp_path):
    run_id = "30b509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload["installer_started_at"] = "2026-01-01T00:00:03Z"
    payload["installer_finished_at"] = "2026-01-01T00:00:02Z"
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="timestamps are inconsistent"):
        validate_production_evidence_directory(tmp_path, run_id)

    payload = _passed_status(run_id)
    _write_evidence(tmp_path, payload)
    (tmp_path / "lifecycle.log").write_text(
        "2026-01-01T00:00:00Z production_self_test_started\n"
        "2026-01-01T00:00:16Z production_self_test_passed\n", encoding="utf-8",
    )
    with pytest.raises(ProductionEvidenceError, match="lifecycle timestamps"):
        validate_production_evidence_directory(tmp_path, run_id)

    _write_evidence(tmp_path, payload)
    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    heartbeat["updated_at"] = "2026-01-01T00:00:16Z"
    atomic_write_json(tmp_path / "heartbeat.json", heartbeat)
    with pytest.raises(ProductionEvidenceError, match="heartbeat"):
        validate_production_evidence_directory(tmp_path, run_id)

    _write_evidence(tmp_path, payload)
    completion = json.loads((tmp_path / "completion.json").read_text(encoding="utf-8"))
    completion["completion_created_at"] = "2026-01-01T00:00:21Z"
    atomic_write_json(tmp_path / "completion.json", completion)
    with pytest.raises(ProductionEvidenceError, match="grace"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_installer_crash_diagnostics_accept_access_violation_and_unavailable_event(tmp_path):
    run_id = "30c509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(
        status="failed", outcome="failed", overall_readiness="installation_failed", installation_passed=False,
        installed_exe_found=False, installed_exe_sha256="", installed_exe_regular=False, installed_exe_non_reparse=False,
        install_root_verified=False, launch_root_pid=None, owned_tree_count=0, all_images_contained=False,
        root_process_retained=False, window_handle=None, window_title="", window_visible=False, window_owner_pid=None,
        window_owner_in_owned_tree=False, stable_duration_seconds=None, first_launch_verified=False, gui_passed=False,
        backend_process_tracked=False, backend_image_contained=False, backend_status="not_ready", backend_http_status=None,
        backend_response_status="", backend_response_service="", backend_endpoint_verified=False, backend_pid=None,
        backend_pid_ownership_verified=False, backend_image_verified=False, fully_ready=False,
        production_cleanup_method="", production_taskkill_observed=False, graceful_close_attempted=False,
        graceful_cleanup_succeeded=False, owned_processes_exited=False, cleanup_complete=False,
        launch_started_at=None, process_started_at=None, window_detected_at=None, stable_started_at=None,
        stable_verified_at=None, backend_started_at=None, backend_ready_at=None, cleanup_started_at=None,
        cleanup_finished_at=None, installer_exit_code=-1073741819, installer_exit_code_hex="c0000005",
        crash_event_found=True, faulting_application_basename="artifact.exe", faulting_module_basename="ntdll.dll",
        exception_code="c0000005", fault_offset="0000000000012345", installer_diagnostic_source="application_error_1000",
        errors=["installer_exit_nonzero"], owned_descendant_diagnostics=[],
    )
    _write_evidence(tmp_path, payload)
    assert validate_production_evidence_directory(tmp_path, run_id).raw["exception_code"] == "c0000005"

    payload.update(
        crash_event_found=False, faulting_application_basename="unavailable", faulting_module_basename="unavailable",
        exception_code="", fault_offset="", wer_event_type="unavailable", installer_diagnostic_source="none",
    )
    _write_evidence(tmp_path, payload)
    assert validate_production_evidence_directory(tmp_path, run_id).raw["crash_event_found"] is False


def test_canonical_windows_origin_and_exact_conhost_trust_prerequisites():
    roots = {
        "install_root": r"X:\SyntheticProfile\LocalAppData\Programs\AI Freelance Studio",
        "windows_root": r"C:\Windows",
        "system32_root": r"C:\Windows\System32",
        "syswow64_root": r"C:\Windows\SysWOW64",
    }
    assert classify_canonical_windows_image_origin(r"C:\Windows\System32\conhost.exe", **roots) == "canonical_system32"
    assert classify_canonical_windows_image_origin(r"c:\WINDOWS\system32\CONHOST.EXE", **roots) == "canonical_system32"
    assert classify_canonical_windows_image_origin(r"C:\Windows\System32-lookalike\conhost.exe", **roots) == "other_windows_directory"
    signed = dict(
        image_regular_file=True, image_reparse_free=True, authenticode_status="valid", microsoft_signed=True,
        parent_relation_verified=True, creation_after_launch=True, process_role="system_helper",
        eligible_for_gui_verification=False, eligible_for_backend_verification=False, eligible_for_cleanup=False,
    )
    assert production_module.EXTERNAL_HELPER_ALLOWLIST == frozenset({"conhost.exe"})
    assert external_helper_trust_prerequisites_met(r"C:\Windows\System32\conhost.exe", **roots, **signed)
    assert not external_helper_trust_prerequisites_met(
        r"C:\Windows\System32\conhost.exe", **roots, **{**signed, "image_reparse_free": False}
    )
    assert not external_helper_trust_prerequisites_met(
        r"C:\Windows\System32\conhost.exe", **roots, **{**signed, "authenticode_status": "not_signed", "microsoft_signed": False}
    )
    assert not external_helper_trust_prerequisites_met(r"C:\Windows\System32-lookalike\conhost.exe", **roots, **signed)
    assert not external_helper_trust_prerequisites_met(r"C:\Windows\SysWOW64\conhost.exe", **roots, **signed)
    assert not external_helper_trust_prerequisites_met(r"C:\Windows\System32\nested\conhost.exe", **roots, **signed)
    assert not external_helper_trust_prerequisites_met(r"C:\Windows\System32\cmd.exe", **roots, **signed)
    for field, value in (
        ("image_regular_file", False), ("parent_relation_verified", False), ("creation_after_launch", False),
        ("process_role", "unknown"), ("eligible_for_gui_verification", True),
        ("eligible_for_backend_verification", True), ("eligible_for_cleanup", True),
    ):
        assert not external_helper_trust_prerequisites_met(
            r"C:\Windows\System32\conhost.exe", **roots, **{**signed, field: value}
        )


@pytest.mark.parametrize(
    ("started", "returned", "finished", "duration"),
    [
        ("2026-01-01T00:00:01Z", "2026-01-01T00:00:01.900Z", "2026-01-01T00:00:02Z", 1.0),
        ("2026-01-01T00:00:01Z", "2026-01-01T00:00:01.100Z", "2026-01-01T00:00:02Z", 1.0),
        ("2026-01-01T00:00:01Z", "2026-01-01T00:00:01.100Z", "2026-01-01T00:00:02Z", 1.009),
        ("2026-01-01T00:00:01Z", "2026-01-01T00:00:01.001Z", "2026-01-01T00:00:01.002Z", 0.002),
    ],
)
def test_installer_duration_uses_one_utc_interval_including_start_return_delay(
    tmp_path, started, returned, finished, duration,
):
    run_id = "30d509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(
        installer_started_at=started, process_start_returned_at=returned,
        installer_finished_at=finished, install_duration_seconds=duration,
    )
    _write_evidence(tmp_path, payload)
    assert validate_production_evidence_directory(tmp_path, run_id).raw["install_duration_seconds"] == duration


def test_installer_timeout_uses_bounded_utc_interval(tmp_path):
    run_id = "30e509d2-9f12-43cc-a326-c126aca8187a"
    payload = _installer_timeout_status(run_id)
    _write_evidence(tmp_path, payload)
    validated = validate_production_evidence_directory(tmp_path, run_id)
    assert validated.outcome == "timed_out" and validated.raw["install_duration_seconds"] == 600.0


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"install_duration_seconds": -0.001}, "install duration"),
        ({"install_duration_seconds": 1.0001}, "install duration"),
        ({"install_duration_seconds": 1.011}, "contradicts installer timestamps"),
        ({"install_duration_seconds": 0.5}, "contradicts installer timestamps"),
        ({
            "started_at": "2025-12-31T23:50:00Z", "installer_started_at": "2025-12-31T23:50:02Z",
            "process_start_returned_at": "2025-12-31T23:50:02.100Z", "install_duration_seconds": 600.0,
        }, "fixed timeout"),
        ({"process_start_returned_at": "2026-01-01T00:00:00.900Z"}, "timestamps are inconsistent"),
        ({"process_start_returned_at": "2026-01-01T00:00:02.100Z"}, "timestamps are inconsistent"),
        ({"installer_finished_at": "2026-01-01T00:00:00.900Z", "install_duration_seconds": 0.1}, "timestamps are inconsistent"),
    ],
)
def test_installer_impossible_or_negative_timing_is_rejected(tmp_path, updates, message):
    run_id = "30f509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(updates)
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match=message):
        validate_production_evidence_directory(tmp_path, run_id)


def test_owned_diagnostics_are_safe_exact_and_non_elevating(tmp_path):
    run_id = "32a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    validated = validate_production_evidence_directory
    _write_evidence(tmp_path, payload)
    diagnostics = validated(tmp_path, run_id).raw["owned_descendant_diagnostics"]
    assert diagnostics[0]["image_basename"] == "AI Freelance Studio.exe"
    assert diagnostics[0]["image_origin"] == "verified_install_root"
    serialized = json.dumps(diagnostics)
    assert "\\\\" not in serialized and "command" not in serialized.lower() and ":/" not in serialized

    payload["owned_descendant_diagnostics"][0]["image_path"] = r"C:\secret\app.exe"
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="path-bearing|exact schema"):
        validated(tmp_path, run_id)


def test_unknown_external_descendant_is_structured_failure_and_cannot_pass(tmp_path):
    run_id = "33a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _external_failure(run_id)
    _write_evidence(tmp_path, payload)
    validated = validate_production_evidence_directory(tmp_path, run_id)
    assert validated.status == "failed"
    assert validated.raw["all_images_contained"] is False

    passed = _passed_status(run_id)
    passed["owned_descendant_diagnostics"].append(_diagnostic("unknown", "mystery.exe", origin="outside_untrusted"))
    passed["owned_tree_count"] = 4
    _write_evidence(tmp_path, passed)
    with pytest.raises(ProductionEvidenceError, match="external owned descendant"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_exact_trusted_conhost_is_accepted_but_never_proof_or_cleanup_target(tmp_path):
    run_id = "34a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload["owned_descendant_diagnostics"].append(
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32")
    )
    payload["owned_tree_count"] = 4
    _write_evidence(tmp_path, payload)
    validated = validate_production_evidence_directory(tmp_path, run_id)
    helper = validated.raw["owned_descendant_diagnostics"][3]
    assert validated.status == "passed" and validated.raw["all_images_contained"] is True
    assert helper["microsoft_signed"] is True
    assert not helper["eligible_for_gui_verification"]
    assert not helper["eligible_for_backend_verification"]
    assert not helper["eligible_for_cleanup"]


@pytest.mark.parametrize(
    "diagnostic",
    [
        _diagnostic("system_helper", "conhost.exe", origin="outside_untrusted"),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_syswow64"),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32", authenticode_status="not_signed", microsoft_signed=False),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32", microsoft_signed=False),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32", image_reparse_free=False),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32", creation_after_launch=False),
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32", parent_relation_verified=False),
        _diagnostic("system_helper", "cmd.exe", origin="canonical_system32"),
        _diagnostic("unknown", "conhost.exe", origin="canonical_system32"),
    ],
    ids=["outside", "syswow64", "unsigned", "wrong-signer", "reparse", "preexisting", "unverified-parent", "unrelated-system32", "wrong-role"],
)
def test_every_nonexact_conhost_or_system32_external_is_rejected(tmp_path, diagnostic):
    run_id = "34b509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload["owned_descendant_diagnostics"].append(diagnostic)
    payload["owned_tree_count"] = 4
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError):
        validate_production_evidence_directory(tmp_path, run_id)


def test_trusted_auxiliary_must_exit_naturally_before_passed_cleanup_completes(tmp_path):
    run_id = "34c509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload["owned_descendant_diagnostics"].append(
        _diagnostic("system_helper", "conhost.exe", origin="canonical_system32")
    )
    payload.update(owned_tree_count=4, auxiliary_processes_exited=False)
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="cleanup|natural-exit"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_installer_start_failure_retains_attempt_time_without_return_or_finish(tmp_path):
    run_id = "34d509d2-9f12-43cc-a326-c126aca8187a"
    payload = _installer_timeout_status(run_id)
    payload.update(
        outcome="failed", process_start_returned_at=None, installer_finished_at=None,
        install_duration_seconds=None, installer_exit_code=None, installer_exit_code_hex="",
        installer_process_started=False, installer_process_exited=False,
        completed_at="2026-01-01T00:00:03Z", errors=["installer_start_failed"],
    )
    _write_evidence(tmp_path, payload)
    validated = validate_production_evidence_directory(tmp_path, run_id)
    assert validated.raw["installer_started_at"] is not None
    assert validated.raw["process_start_returned_at"] is None


def test_runner_requires_validated_fully_ready_evidence_and_writes_host_snapshot(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    process = type("FakeProcess", (), {
        "pid": 4321, "returncode": None, "poll": lambda self: self.returncode,
        "terminate": lambda self: setattr(self, "returncode", -15), "kill": lambda self: setattr(self, "returncode", -9),
        "wait": lambda self, timeout=None: self.returncode or 0,
    })()
    capability = SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631, virtualization_available=True,
        sandbox_feature_state="enabled", executable_found=True, available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )

    def launch(argv):
        run_root = Path(argv[1]).parent
        request_payload = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        _write_evidence(run_root / "evidence", _passed_status(
            request.run_id, guest_terminal_deadline_utc=request_payload["guest_terminal_deadline_utc"],
        ))
        return process

    result = ProductionSelfTestRunner(
        workspace_manager=ProductionSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability, launcher=launch,
    ).run(request)
    assert result.status == RunStatus.PASSED
    assert result.exit_reason == "production_self_test_fully_ready"
    assert Path(result.evidence_path).name == "validated-production-evidence.json"
    assert Path(result.evidence_path).is_file()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"installed_exe_found": False}, "installation_passed"),
        ({"installed_exe_non_reparse": False}, "installed executable"),
        ({"all_images_contained": False}, "first launch"),
        ({"root_process_retained": False}, "first launch"),
        ({"window_owner_pid": 999, "window_owner_in_owned_tree": False}, "first launch"),
        ({"window_title": ""}, "title"),
        ({"window_title": "Other"}, "title"),
        ({"stable_duration_seconds": 4.9}, "stable"),
        ({"first_launch_verified": False}, "first-launch"),
        ({"backend_status": "not_ready"}, "backend"),
        ({"backend_endpoint_verified": False}, "backend"),
        ({"backend_http_status": 201}, "backend"),
        ({"backend_response_service": "Other"}, "backend"),
        ({"owned_processes_exited": False}, "cleanup"),
        ({"installer_started_at": None}, "installer lifecycle"),
        ({"process_started_at": None}, "launch and window"),
    ],
)
def test_passed_evidence_cross_validates_identity_window_backend_and_cleanup(tmp_path, updates, message):
    run_id = "41a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(updates)
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match=message):
        validate_production_evidence_directory(tmp_path, run_id)


def test_exit_zero_without_exe_cannot_claim_installation(tmp_path):
    run_id = "51a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(installed_exe_found=False, installed_exe_sha256="", installed_exe_regular=False, installed_exe_non_reparse=False)
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="installation_passed"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_backend_not_applicable_is_consistent_only_for_optional_offline_profile(tmp_path):
    run_id = "61a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(
        status="failed", outcome="failed", overall_readiness="backend_not_ready", fully_ready=False, backend_required=False,
        backend_process_tracked=False, backend_image_contained=False, backend_status="not_applicable", backend_http_status=None,
        backend_response_status="", backend_response_service="", backend_endpoint_verified=False, backend_pid=None,
        backend_pid_ownership_verified=False, backend_image_verified=False, production_cleanup_method="",
        production_taskkill_observed=False, graceful_close_attempted=False, backend_started_at=None,
        backend_ready_at=None, errors=["backend_not_applicable_offline"],
    )
    _write_evidence(tmp_path, payload)
    assert validate_production_evidence_directory(tmp_path, run_id).raw["backend_status"] == "not_applicable"
    payload["backend_required"] = True
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="not-applicable"):
        validate_production_evidence_directory(tmp_path, run_id)


@pytest.mark.parametrize(
    ("readiness", "updates"),
    [
        ("installation_failed", {"installation_passed": True}),
        ("gui_launch_failed", {"installation_passed": False}),
        ("backend_not_ready", {"gui_passed": False, "first_launch_verified": False}),
    ],
)
def test_readiness_classification_cannot_contradict_lifecycle(tmp_path, readiness, updates):
    run_id = "65a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload.update(status="failed", outcome="failed", overall_readiness=readiness, fully_ready=False, errors=["controlled_failure"])
    if readiness != "installation_failed":
        payload.update(
            backend_status="not_ready", backend_process_tracked=False, backend_image_contained=False,
            backend_http_status=None, backend_response_status="", backend_response_service="",
            backend_endpoint_verified=False, backend_pid=None, backend_pid_ownership_verified=False,
            backend_image_verified=False, backend_ready_at=None,
        )
    if readiness == "gui_launch_failed":
        payload.update(gui_passed=False, first_launch_verified=False)
    payload.update(updates)
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="readiness contradicts lifecycle"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_external_url_path_fields_duplicate_oversized_and_scripts_are_rejected(tmp_path):
    run_id = "71a509d2-9f12-43cc-a326-c126aca8187a"
    payload = _passed_status(run_id)
    payload["backend_url"] = "https://example.com/health"
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="exact schema"):
        validate_production_evidence_directory(tmp_path, run_id)
    payload = _passed_status(run_id)
    _write_evidence(tmp_path, payload)
    (tmp_path / "status.json").write_text('{"schema_version":3,"schema_version":3}', encoding="utf-8")
    with pytest.raises(ProductionEvidenceError, match="duplicate"):
        validate_production_evidence_directory(tmp_path, run_id)
    _write_evidence(tmp_path, payload)
    (tmp_path / "unexpected.ps1").write_bytes(b"x")
    with pytest.raises(ProductionEvidenceError, match="unexpected"):
        validate_production_evidence_directory(tmp_path, run_id)
    (tmp_path / "unexpected.ps1").unlink()
    (tmp_path / "lifecycle.log").write_bytes(b"x" * (evidence_module.MAX_LOG_BYTES + 1))
    with pytest.raises(ProductionEvidenceError, match="size limit"):
        validate_production_evidence_directory(tmp_path, run_id)


def test_guest_script_enforces_owned_electron_tree_child_window_backend_and_safe_cleanup():
    script = (Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8")
    for required in (
        'Arguments = "/S"', "UseShellExecute = $false", "WHERE ParentProcessId=$ParentId", "CreationDate",
        "Add-OwnedProcess", "FindOwnedVisibleWindow($GuiPids", "IsOwnedVisibleWindow",
        "owned_process_image_outside_install_root", "root_exited_early", "owned_window_not_stable",
        '$HealthEndpoint = "http://127.0.0.1:8080/health"', '$Http.Proxy = $null', 'StatusCode',
        '"FreelancerStudio"', "Sort-Object Depth -Descending", "Test-OwnedCreation", "CloseOwnedWindow",
        "owned_tree_fallback_kill_used", "Assert-NonReparsePathChain", "Assert-ContainedRegularFile",
        "Test-OwnedCreation $Owned[$WindowOwnerPid]", "backend_pid_ownership_verified", "backend_image_verified",
        'ProductionCleanupMethod = "owned_backend_pid_tree"', "Win32_ProcessStartTrace", "ParentProcessID = $RootPid",
        "owned_descendant_diagnostics", "Get-AuthenticodeSignature", "CanonicalSystem32", "CanonicalSysWOW64",
        "EligibleForCleanup", '$Origin = "verified_install_root"',
    ):
        assert required in script
    lowered = script.lower()
    for forbidden in ("taskkill", "get-process", "start-process", "invoke-expression", "windows sandbox close", "8081/health", "screenshot", "uninstall"):
        if forbidden != "taskkill":
            assert forbidden not in lowered
    assert "taskkill.exe" in script and "ProcessStartTrace" in script
    assert "Start-Process" not in script and "Diagnostics.ProcessStartInfo" in script
    assert "SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process WHERE ParentProcessId=$ParentId" in script
    assert "SELECT ProcessId,ParentProcessId,CreationDate FROM Win32_Process" in script
    assert "FROM Win32_ProcessStartTrace WHERE ParentProcessID = $RootPid" in script
    assert "EnvironmentVariables" not in script
    assert "Get-CimInstance Win32_Process" not in script
    assert "Where-Object { $_.EligibleForCleanup -and -not $_.Process.HasExited }" in script
    assert "$InstallTimer" not in script
    start_authority = script.index("$InstallerStartTimeUtc = [DateTime]::UtcNow")
    started_timestamp = script.index('$InstallerStartedAt = $InstallerStartTimeUtc.ToString("o")')
    process_start = script.index("$InstallerProcess.Start()")
    returned_authority = script.index("$ProcessStartReturnedTimeUtc = [DateTime]::UtcNow")
    returned_timestamp = script.index('$ProcessStartReturnedAt = $ProcessStartReturnedTimeUtc.ToString("o")')
    deadline = script.index("$InstallerDeadlineUtc = Get-EffectivePhaseDeadlineUtc 600")
    assert deadline < start_authority < started_timestamp < process_start < returned_authority < returned_timestamp
    assert "if ($InstallerFinishedTimeUtc -ge $InstallerDeadlineUtc)" in script
    assert "($InstallerFinishedTimeUtc - $InstallerStartTimeUtc).TotalSeconds" in script
    for required in (
        "Test-TrustedAuxiliaryDiagnostic", 'Join-Path $CanonicalSystem32 "conhost.exe"',
        "TrustedAuxiliary =", "auxiliary_processes_exited", "-not $_.TrustedAuxiliary",
    ):
        assert required in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell parser is Windows-only")
def test_guest_powershell_ast_never_invokes_taskkill():
    script_path = Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1"
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    parser = (
        "$tokens=$null;$errors=$null;"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile('{str(script_path).replace("'", "''")}',[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 2};"
        "$ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst]},$true)|"
        "ForEach-Object{$_.GetCommandName()}"
    )
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", parser], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    basenames = {name.strip().replace("/", "\\").rsplit("\\", 1)[-1].casefold() for name in result.stdout.splitlines() if name.strip()}
    assert not basenames.intersection({"taskkill", "taskkill.exe"})


def test_dual_gate_is_exact_and_fixture_opt_ins_do_not_authorize(monkeypatch):
    monkeypatch.delenv(PRODUCTION_EXTERNAL_OPT_IN, raising=False)
    monkeypatch.setenv("FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_GUI_EXTERNAL", "1")
    monkeypatch.setenv("FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_NSIS_INSTALL_EXTERNAL", "1")
    assert production_external_opt_in_enabled(True, "external") is False
    assert external_module.external_opt_in_enabled("external") is False
    monkeypatch.setenv(PRODUCTION_EXTERNAL_OPT_IN, "1")
    assert production_external_opt_in_enabled(False, "external") is False
    assert production_external_opt_in_enabled(True, "external and not slow") is False
    assert production_external_opt_in_enabled(True, "") is False
    assert production_external_opt_in_enabled(True, "external") is True
    assert external_module.external_opt_in_enabled("external") is True


def test_phase1_and_phase2_contracts_remain_unchanged_and_no_implicit_execution():
    assert PHASE1_SCHEMA_VERSION == 1
    assert set(SandboxRunRequest.__dataclass_fields__) == {"source_artifact", "run_id", "expected_sha256", "timeout_seconds", "network_enabled", "metadata"}
    assert INSTALL_LAUNCH_PROTOCOL == "controlled_fixture_install_launch_v1"
    package = Path(__file__).parent / "sandbox_test_lab"
    combined = "\n".join((package / name).read_text(encoding="utf-8") for name in (
        "production_self_test.py", "production_evidence.py", "production_runner.py",
    ))
    assert "shell=True" not in combined and "taskkill.exe" not in combined.lower()
    assert "subprocess.run(" not in combined
    external_source = (Path(__file__).parent / "test_sandbox_test_lab_phase3a_external.py").read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.external" in external_source
    assert "mark_expression.strip() == \"external\"" in external_source


def test_release_artifact_and_sidecar_are_ignored_not_git_additions():
    root = Path(__file__).parent
    result = subprocess.run(
        ["git", "check-ignore", "frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe", "frontend/installers/AI Freelance Studio-Setup-1.0.0-beta.1-win.exe.sha256"],
        cwd=root, shell=False, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 is Windows-only")
@pytest.mark.parametrize("terminal_status", ["passed", "failed"])
def test_windows_powershell_51_terminal_emission_writes_complete_contract(tmp_path, terminal_status):
    guest_script = (Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8")
    block_start = guest_script.rfind('    if ($Status -eq "passed") {')
    block_end_marker = "    $TerminalLifecycleAt = Write-Lifecycle $terminalLifecycleMessage"
    block_end = guest_script.find(block_end_marker, block_start) + len(block_end_marker)
    assert block_start >= 0 and block_end > block_start
    terminal_block = guest_script[block_start:block_end]
    assert "Write-Lifecycle (if" not in guest_script

    output_root = str(tmp_path).replace("'", "''")
    script = f'''$ErrorActionPreference = "Stop"
$OutputRoot = '{output_root}'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$LifecyclePath = Join-Path $OutputRoot "lifecycle.log"
$StatusPath = Join-Path $OutputRoot "status.json"
$ProductionEvidencePath = Join-Path $OutputRoot "production-evidence.json"
$CompletionPath = Join-Path $OutputRoot "completion.json"
$Status = "{terminal_status}"
$Errors = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]
function Write-Lifecycle {{ param([string] $Message) [IO.File]::AppendAllText($LifecyclePath, "2026-01-01T00:00:00Z $Message$([Environment]::NewLine)", $Utf8NoBom); return "2026-01-01T00:00:00Z" }}
function Write-AtomicJson {{
    param([string] $Path, [object] $Value)
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 10 -Compress), $Utf8NoBom)
}}
{terminal_block}
$Final = [ordered]@{{ schema_version = 3; protocol = "aifs_production_self_test_v1"; status = $Status; errors = @($Errors); warnings = @($Warnings) }}
Write-AtomicJson $StatusPath $Final
Write-AtomicJson $ProductionEvidencePath $Final
Write-AtomicJson $CompletionPath ([ordered]@{{ schema_version = 3; protocol = "aifs_production_self_test_v1"; completed = $true; status = $Status }})
'''
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        shell=False, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in ("status.json", "production-evidence.json", "completion.json", "lifecycle.log"):
        assert (tmp_path / name).is_file()
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8-sig"))
    evidence = json.loads((tmp_path / "production-evidence.json").read_text(encoding="utf-8-sig"))
    completion = json.loads((tmp_path / "completion.json").read_text(encoding="utf-8-sig"))
    assert status == evidence
    assert status["schema_version"] == 3 and status["errors"] == [] and status["warnings"] == []
    assert completion == {"schema_version": 3, "protocol": PRODUCTION_PROTOCOL, "completed": True, "status": terminal_status}
    assert (tmp_path / "lifecycle.log").read_text(encoding="utf-8-sig").splitlines() == [f"2026-01-01T00:00:00Z production_self_test_{terminal_status}"]


def test_runner_fails_early_when_completed_heartbeat_lacks_terminal_evidence(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    capability = SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631, virtualization_available=True,
        sandbox_feature_state="enabled", executable_found=True, available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )
    clock = type("Clock", (), {"value": 0.0})()
    process = type("FakeProcess", (), {
        "pid": 4321, "returncode": None, "terminated": False, "poll": lambda self: self.returncode,
        "terminate": lambda self: (setattr(self, "terminated", True), setattr(self, "returncode", -15)),
        "kill": lambda self: setattr(self, "returncode", -9),
        "wait": lambda self, timeout=None: self.returncode or 0,
    })()

    def launch(argv):
        run_root = Path(argv[1]).parent
        atomic_write_json(run_root / "evidence" / "heartbeat.json", {
            "schema_version": 3, "protocol": PRODUCTION_PROTOCOL, "run_id": request.run_id,
            "phase": "completed", "updated_at": "2026-01-01T00:00:00Z",
        })
        return process

    def sleep(seconds):
        clock.value += seconds

    result = ProductionSelfTestRunner(
        workspace_manager=ProductionSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability, launcher=launch,
        monotonic=lambda: clock.value, sleeper=sleep, poll_interval=1,
    ).run(request)
    assert result.status == RunStatus.INFRASTRUCTURE_ERROR
    assert result.exit_reason == "terminal_evidence_missing_after_completed_heartbeat"
    assert result.duration_seconds == production_runner_module.TERMINAL_EVIDENCE_GRACE_SECONDS
    assert result.evidence_path is None
    assert process.terminated is False


def test_deadline_configuration_and_pure_phase_budgets_fail_closed(monkeypatch):
    validate_production_deadline_configuration()
    assert HOST_TIMEOUT_SECONDS <= 600
    assert HOST_TIMEOUT_SECONDS > HOST_TIMEOUT_SECONDS - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS
    installer_reserves = 60 + 5 + 30 + MINIMUM_CLEANUP_RESERVE_SECONDS + GUEST_EVIDENCE_RESERVE_SECONDS
    assert effective_phase_budget_seconds(600, 200, installer_reserves) == 70
    assert effective_phase_budget_seconds(600, 100, installer_reserves) <= 0
    assert effective_phase_budget_seconds(60, 80, 70) == 10
    assert effective_phase_budget_seconds(30, 40, 35) == 5
    monkeypatch.setattr(production_module, "HOST_TIMEOUT_SECONDS", 601)
    with pytest.raises(ValueError, match="host deadline"):
        validate_production_deadline_configuration()


def test_guest_source_caps_every_phase_to_one_terminal_deadline_and_reserves_evidence():
    script = (Path(__file__).parent / "sandbox_test_lab" / "guest" / "production_self_test.ps1").read_text(encoding="utf-8")
    assert "Get-RemainingExecutionBudgetSeconds" in script
    assert "Get-EffectivePhaseDeadlineUtc 600 $InstallerFutureReserveSeconds" in script
    assert "$InstallerFutureReserveSeconds = 60 + 5 + 30 + $MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds" in script
    assert "Get-EffectivePhaseDeadlineUtc 60 (5 + 30 + $MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds)" in script
    assert "Get-EffectivePhaseDeadlineUtc 30 ($MinimumCleanupReserveSeconds + $GuestEvidenceReserveSeconds)" in script
    assert "Get-EffectivePhaseDeadlineUtc 15 $GuestEvidenceReserveSeconds" in script
    assert "$InstallerStartTimeUtc.AddSeconds(600)" not in script
    assert script.index("$InstallerDeadlineUtc = Get-EffectivePhaseDeadlineUtc 600") < script.index("$InstallerProcess.Start()")
    assert script.index("$LaunchDeadlineUtc = Get-EffectivePhaseDeadlineUtc 60") < script.index("$RootProcess.Start()")
    assert script.index("Add-OwnedProcess $RootProcess 0 $RootProcess.StartTime") < script.index('throw "gui_launch_timed_out"')
    assert "if ([DateTime]::UtcNow -ge $InstallerDeadlineUtc)" in script
    assert "if ([DateTime]::UtcNow -ge $LaunchDeadlineUtc)" in script


@pytest.mark.parametrize(
    ("during_validate", "expected_status", "expected_reason"),
    [
        ("cancel", RunStatus.CANCELLED, "cancelled_before_launch"),
        ("expire", RunStatus.TIMED_OUT, "timeout_before_launch"),
        ("margin", RunStatus.TIMED_OUT, "insufficient_execution_budget_before_launch"),
    ],
)
def test_runner_rechecks_cancellation_and_deadline_after_prelaunch_validation(
    tmp_path, monkeypatch, during_validate, expected_status, expected_reason,
):
    request = _request(tmp_path, monkeypatch)
    capability = SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631, virtualization_available=True,
        sandbox_feature_state="enabled", executable_found=True, available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )
    cancellation = production_runner_module.threading.Event()
    clock = SimpleNamespace(value=0.0)
    launched = []
    original = ProductionSelfTestWorkspaceManager.validate_for_launch

    def mutate_after_validation(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if during_validate == "cancel":
            cancellation.set()
        elif during_validate == "expire":
            clock.value = HOST_TIMEOUT_SECONDS
        else:
            clock.value = HOST_TIMEOUT_SECONDS - HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS + 1

    monkeypatch.setattr(ProductionSelfTestWorkspaceManager, "validate_for_launch", mutate_after_validation)
    result = ProductionSelfTestRunner(
        workspace_manager=ProductionSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability,
        launcher=lambda argv: launched.append(argv),
        monotonic=lambda: clock.value,
    ).run(request, cancellation)
    assert result.status == expected_status and result.exit_reason == expected_reason
    assert launched == []


def test_runner_host_deadline_exceeds_stable_guest_deadline_by_exact_margin(tmp_path, monkeypatch):
    request = _request(tmp_path, monkeypatch)
    capability = SandboxCapability(
        supported_os=True, windows_edition="Professional", windows_build=22631, virtualization_available=True,
        sandbox_feature_state="enabled", executable_found=True, available=True,
        executable_path=r"C:\Windows\System32\WindowsSandbox.exe", powershell_found=True,
    )
    authority = datetime.now(timezone.utc)
    observed = {}
    process = type("FakeProcess", (), {
        "pid": 4321, "returncode": None, "poll": lambda self: self.returncode,
        "terminate": lambda self: setattr(self, "returncode", -15), "kill": lambda self: setattr(self, "returncode", -9),
        "wait": lambda self, timeout=None: self.returncode or 0,
    })()

    def launch(argv):
        run_root = Path(argv[1]).parent
        first = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        second = json.loads((run_root / "guest" / "request.json").read_text(encoding="utf-8"))
        observed["deadline"] = first["guest_terminal_deadline_utc"]
        assert first == second
        _write_evidence(run_root / "evidence", _passed_status(
            request.run_id, guest_terminal_deadline_utc=observed["deadline"],
        ))
        return process

    result = ProductionSelfTestRunner(
        workspace_manager=ProductionSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability, launcher=launch, utc_clock=lambda: authority,
    ).run(request)
    terminal = datetime.fromisoformat(observed["deadline"].replace("Z", "+00:00"))
    host_deadline = authority + timedelta(seconds=HOST_TIMEOUT_SECONDS)
    assert (host_deadline - terminal).total_seconds() == HOST_TERMINAL_EVIDENCE_MARGIN_SECONDS
    assert result.status == RunStatus.PASSED


def test_evidence_deadline_is_host_authoritative_and_terminal_markers_precede_it(tmp_path):
    run_id = "80a509d2-9f12-43cc-a326-c126aca8187a"
    expected = "2026-01-01T00:20:00.000000Z"
    payload = _passed_status(run_id, guest_terminal_deadline_utc=expected)
    _write_evidence(tmp_path, payload)
    validate_production_evidence_directory(tmp_path, run_id, expected_guest_terminal_deadline_utc=expected)
    with pytest.raises(ProductionEvidenceError, match="host authority"):
        validate_production_evidence_directory(
            tmp_path, run_id, expected_guest_terminal_deadline_utc="2026-01-01T00:19:59.000000Z",
        )
    payload["guest_terminal_deadline_utc"] = "2026-01-01T00:00:14.000000Z"
    _write_evidence(tmp_path, payload)
    with pytest.raises(ProductionEvidenceError, match="after the guest terminal deadline"):
        validate_production_evidence_directory(tmp_path, run_id)
