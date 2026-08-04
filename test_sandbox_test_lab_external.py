import os
from pathlib import Path

import pytest

from sandbox_test_lab.capability import detect_sandbox_capability
from sandbox_test_lab.models import RunStatus, SandboxRunRequest
from sandbox_test_lab.runner import SandboxRunner
from sandbox_test_lab.workspace import SandboxWorkspaceManager


pytestmark = pytest.mark.external
EXTERNAL_OPT_IN = "FREELANCERSTUDIO_RUN_WINDOWS_SANDBOX_EXTERNAL"
EXTERNAL_MARK_EXPRESSION = "external"


def external_opt_in_enabled(mark_expression: str) -> bool:
    return os.environ.get(EXTERNAL_OPT_IN) == "1" and mark_expression.strip() == EXTERNAL_MARK_EXPRESSION


def test_windows_sandbox_phase1_real_smoke(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {EXTERNAL_OPT_IN}=1 and select -m external to run the real Windows Sandbox smoke test")
    capability = detect_sandbox_capability()
    if not capability.available:
        pytest.skip("Windows Sandbox capability is unavailable")
    artifact = tmp_path / "smoke-artifact.bin"
    artifact.write_bytes(b"AI Freelance Studio Sandbox Test Lab Phase 1")
    result = SandboxRunner(
        workspace_manager=SandboxWorkspaceManager(tmp_path / "runtime"),
        capability_detector=lambda: capability,
    ).run(SandboxRunRequest(source_artifact=artifact, timeout_seconds=90))
    assert result.status == RunStatus.PASSED
