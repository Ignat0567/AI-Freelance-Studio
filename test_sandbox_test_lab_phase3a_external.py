import json
import os
from pathlib import Path

import pytest

from sandbox_test_lab.capability import detect_sandbox_capability
from sandbox_test_lab.models import RunStatus
from sandbox_test_lab.production_runner import ProductionSelfTestRunner
from sandbox_test_lab.production_self_test import (
    PRODUCTION_EXTERNAL_OPT_IN,
    ProductionSelfTestRequest,
    ProductionSelfTestWorkspaceManager,
)


pytestmark = pytest.mark.external


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(PRODUCTION_EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == "external"


@pytest.mark.timeout(600)
def test_windows_sandbox_production_beta_1_self_test(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {PRODUCTION_EXTERNAL_OPT_IN}=1 and select exactly -m external")
    capability = detect_sandbox_capability()
    if not capability.available:
        pytest.skip("Windows Sandbox capability is unavailable")

    self_test = ProductionSelfTestRequest()
    result = ProductionSelfTestRunner(
        workspace_manager=ProductionSelfTestWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability,
    ).run(self_test)
    evidence = json.loads(Path(result.evidence_path).read_text(encoding="utf-8")) if result.evidence_path else {}
    report = {
        "run_id": result.run_id, "status": result.status.value, "exit_reason": result.exit_reason,
        "overall_readiness": evidence.get("overall_readiness"), "installation_passed": evidence.get("installation_passed"),
        "owned_tree_count": evidence.get("owned_tree_count"), "window_owner_pid": evidence.get("window_owner_pid"),
        "first_launch_verified": evidence.get("first_launch_verified"), "backend_status": evidence.get("backend_status"),
        "backend_pid": evidence.get("backend_pid"), "backend_pid_ownership_verified": evidence.get("backend_pid_ownership_verified"),
        "backend_image_verified": evidence.get("backend_image_verified"), "production_cleanup_method": evidence.get("production_cleanup_method"),
        "production_taskkill_observed": evidence.get("production_taskkill_observed"),
        "installer_exit_code": evidence.get("installer_exit_code"),
        "installer_exit_code_hex": evidence.get("installer_exit_code_hex"),
        "install_duration_seconds": evidence.get("install_duration_seconds"),
        "process_start_returned_at": evidence.get("process_start_returned_at"),
        "installer_process_started": evidence.get("installer_process_started"),
        "installer_process_exited": evidence.get("installer_process_exited"),
        "crash_event_found": evidence.get("crash_event_found"),
        "faulting_application_basename": evidence.get("faulting_application_basename"),
        "faulting_module_basename": evidence.get("faulting_module_basename"),
        "exception_code": evidence.get("exception_code"), "fault_offset": evidence.get("fault_offset"),
        "wer_event_type": evidence.get("wer_event_type"),
        "installer_diagnostic_source": evidence.get("installer_diagnostic_source"),
        "owned_descendant_diagnostics": evidence.get("owned_descendant_diagnostics", []),
        "fully_ready": evidence.get("fully_ready"), "cleanup_complete": evidence.get("cleanup_complete"),
        "auxiliary_processes_exited": evidence.get("auxiliary_processes_exited"),
        "errors": list(result.errors), "warnings": list(result.warnings),
    }
    print("PHASE3A_EXTERNAL_REPORT=" + json.dumps(report, sort_keys=True))
    assert result.status == RunStatus.PASSED, report
    assert evidence["overall_readiness"] == "fully_ready"
    assert evidence["installation_passed"] is True
    assert evidence["first_launch_verified"] is True
    assert evidence["backend_status"] == "ready"
    assert evidence["backend_pid_ownership_verified"] is True
    assert evidence["backend_image_verified"] is True
    assert evidence["production_cleanup_method"] == "owned_backend_pid_tree"
    assert evidence["fully_ready"] is True
    assert evidence["cleanup_complete"] is True
