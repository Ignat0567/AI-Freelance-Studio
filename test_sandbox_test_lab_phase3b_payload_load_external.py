import json
from pathlib import Path

import pytest

from sandbox_test_lab.models import RunStatus
from sandbox_test_lab.payload_load_probe import (
    PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN,
    PayloadLoadProbeRunner,
    PayloadLoadProbeWorkspaceManager,
    payload_load_probe_external_opt_in_enabled,
    payload_load_probe_request,
)


pytestmark = pytest.mark.external


@pytest.mark.timeout(120)
def test_windows_sandbox_phase3b_full_payload_load_probe(tmp_path: Path, request: pytest.FixtureRequest):
    mark_expression = str(request.config.option.markexpr or "")
    if not payload_load_probe_external_opt_in_enabled(mark_expression):
        pytest.skip(f"Set {PAYLOAD_LOAD_PROBE_EXTERNAL_OPT_IN}=1 and select exactly -m external")
    probe = payload_load_probe_request()
    manager = PayloadLoadProbeWorkspaceManager(tmp_path / "runtime")
    result = PayloadLoadProbeRunner(workspace_manager=manager).run(probe)
    timeline_path = manager.paths_for(probe.run_id).logs_directory / "validated-payload-load-timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8")) if timeline_path.is_file() else {}
    report = {**result.to_dict(), "timeline": timeline.get("timeline", [])}
    print("PHASE3B_PAYLOAD_LOAD_REPORT=" + json.dumps(report, sort_keys=True))
    assert result.status == RunStatus.PASSED, report
