"""Preflight is the gate that answers, in seconds, what a live run previously discovered in
minutes: an expired coding-CLI login, a stopped Docker, a Node too old to build anything.

The cases that matter most here are the *non*-blocking ones. A gate that refuses to start a
working setup because its own probe glitched is worse than no gate, so every ambiguous
outcome is asserted to leave the run alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from order_workflow import (
    AgentHandoffService,
    AlexClarificationService,
    DesignPreviewService,
    EventKind,
    ExecutionMode,
    ExecutionResult,
    ExecutionServiceError,
    ExecutionStatus,
    FakeProjectExecutionAdapter,
    ProjectBriefService,
    ProjectExecutionService,
    TestSummary as WorkflowTestSummary,
    UserOrder,
)
from order_workflow.preflight import (
    MINIMUM_FREE_DISK_BYTES,
    MINIMUM_NODE_MAJOR,
    probe_coding_cli_credentials,
    run_preflight,
)


def _ok_node(_command):
    return 0, "v22.14.0\n"


def _plenty_of_disk(_path):
    return MINIMUM_FREE_DISK_BYTES * 4


def _healthy_cli(_command, _prompt, _timeout):
    return 0, json.dumps({"result": "ok", "is_error": False}), ""


def _expired_cli(_command, _prompt, _timeout):
    # Shape taken from a real run: nonzero exit, full JSON payload, 401 inside it.
    return 1, json.dumps({"api_error_status": 401, "result": "OAuth token has expired"}), ""


def _preflight(**overrides):
    kwargs = dict(
        workspace_root=Path.cwd(),
        environ={},
        binary_probe=lambda: "claude",
        cli_runner=_healthy_cli,
        version_runner=_ok_node,
        free_bytes_probe=_plenty_of_disk,
        docker_probe=lambda: True,
    )
    kwargs.update(overrides)
    return run_preflight(**kwargs)


def test_healthy_environment_is_ready_and_reports_every_check():
    report = _preflight()

    assert report.ready is True
    assert report.blockers == ()
    assert {check.code for check in report.checks} == {
        "coding_cli_credentials",
        "docker_engine",
        "node_runtime",
        "workspace_disk_space",
    }
    assert all(check.status == "ok" for check in report.checks)


def test_expired_login_blocks_before_any_work_is_committed():
    report = _preflight(cli_runner=_expired_cli)

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == ["preflight_coding_cli_auth_expired"]
    # The remedy has to name the terminal, not Settings: no amount of clicking in Studio
    # signs the CLI back in.
    assert "claude" in report.blockers[0].message


@pytest.mark.parametrize(
    "runner",
    [
        pytest.param(lambda _c, _p, _t: (1, "not json at all", "boom"), id="unparsable_output"),
        pytest.param(lambda _c, _p, _t: (1, json.dumps({"api_error_status": 429}), ""), id="rate_limited"),
        pytest.param(lambda _c, _p, _t: (_ for _ in ()).throw(OSError("cannot spawn")), id="probe_crashed"),
    ],
)
def test_an_inconclusive_login_probe_does_not_block_the_run(runner):
    report = _preflight(cli_runner=runner)

    assert report.ready is True
    check = next(c for c in report.checks if c.code == "coding_cli_credentials")
    assert check.status == "unknown"


def test_unreachable_docker_blocks_while_qa_runs_in_docker():
    report = _preflight(docker_probe=lambda: False)

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == ["preflight_docker_unavailable"]


def test_docker_is_not_required_when_qa_runs_on_the_host():
    report = _preflight(environ={"FREELANCERSTUDIO_PHASED_QA_BACKEND": "host"}, docker_probe=lambda: False)

    assert report.ready is True
    check = next(c for c in report.checks if c.code == "docker_engine")
    assert check.status == "skipped"


def test_container_deploy_still_needs_docker_even_with_host_qa():
    report = _preflight(
        environ={"FREELANCERSTUDIO_PHASED_QA_BACKEND": "host", "FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY": "1"},
        docker_probe=lambda: False,
    )

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == ["preflight_docker_unavailable"]


def test_node_older_than_the_build_toolchain_blocks():
    report = _preflight(version_runner=lambda _c: (0, f"v{MINIMUM_NODE_MAJOR - 2}.9.0\n"))

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == ["preflight_node_too_old"]


def test_unreadable_node_version_does_not_block():
    report = _preflight(version_runner=lambda _c: (0, "something else entirely\n"))

    assert report.ready is True
    check = next(c for c in report.checks if c.code == "node_runtime")
    assert check.status == "unknown"


def test_low_disk_space_blocks_and_reports_what_is_left():
    report = _preflight(free_bytes_probe=lambda _p: MINIMUM_FREE_DISK_BYTES // 2)

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == ["preflight_disk_space_low"]
    check = next(c for c in report.checks if c.code == "workspace_disk_space")
    assert "GB free" in check.message


def test_a_workspace_root_that_does_not_exist_yet_is_measured_on_its_nearest_parent(tmp_path):
    measured: list[Path] = []

    def probe(path: Path) -> int:
        measured.append(path)
        return MINIMUM_FREE_DISK_BYTES * 4

    report = _preflight(workspace_root=tmp_path / "not" / "created" / "yet", free_bytes_probe=probe)

    assert report.ready is True
    assert measured == [tmp_path]


def test_every_blocker_is_collected_rather_than_only_the_first():
    report = _preflight(
        cli_runner=_expired_cli,
        docker_probe=lambda: False,
        version_runner=lambda _c: (0, "v18.0.0\n"),
        free_bytes_probe=lambda _p: 1024,
    )

    assert report.ready is False
    assert [blocker.code for blocker in report.blockers] == [
        "preflight_coding_cli_auth_expired",
        "preflight_docker_unavailable",
        "preflight_node_too_old",
        "preflight_disk_space_low",
    ]


def test_credentials_can_be_skipped_without_a_provider_call():
    calls: list[str] = []

    def runner(_command, _prompt, _timeout):
        calls.append("called")
        return _healthy_cli(_command, _prompt, _timeout)

    report = _preflight(check_credentials=False, cli_runner=runner)

    assert report.ready is True
    assert calls == []
    check = next(c for c in report.checks if c.code == "coding_cli_credentials")
    assert check.status == "skipped"


def test_report_converts_into_the_readiness_result_the_execution_service_consumes():
    blocked = _preflight(cli_runner=_expired_cli).readiness()
    healthy = _preflight().readiness()

    assert healthy.ready is True
    assert blocked.ready is False
    assert blocked.blockers[0].code == "preflight_coding_cli_auth_expired"
    assert blocked.blockers[0].action_required is True


def test_the_login_probe_runs_outside_any_project_workspace():
    """The probe must never be able to write into generated code, so it is given a prompt
    and a scratch directory rather than the workspace the build will use."""
    seen: list[list[str]] = []

    def runner(command, prompt, timeout):
        seen.append(list(command))
        assert prompt.strip() != ""
        assert timeout > 0
        return _healthy_cli(command, prompt, timeout)

    probe_coding_cli_credentials(binary_probe=lambda: "claude", runner=runner)

    assert seen and seen[0][0] == "claude"
    # -p keeps it non-interactive; the cheapest model keeps the check's own cost negligible.
    assert "-p" in seen[0]
    assert "sonnet" in seen[0]


def test_a_missing_coding_cli_is_left_to_the_adapters_own_readiness_check():
    report = _preflight(binary_probe=lambda: None)

    assert report.ready is True
    check = next(c for c in report.checks if c.code == "coding_cli_credentials")
    assert check.status == "unknown"


# --- wiring: the execution service has to actually consult it, and only where it applies ---


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


class _SequenceIds:
    def __init__(self) -> None:
        self._index = 0

    def __call__(self) -> str:
        self._index += 1
        return f"pf-{self._index:04d}"


def _approved_contract():
    ids = _SequenceIds()
    order = UserOrder(
        id="order_preflight",
        title="PDF Voice Assistant",
        # Detailed enough that clarification never has to ask "core-features", the one
        # question with no recommended answer -- so the whole session resolves from defaults
        # and this helper needs no hand-written answers. Same text as the other execution
        # tests, for the same reason.
        description=(
            "Create a browser-based voice assistant that allows the user to upload PDF documents, "
            "ask questions about their contents by voice or text, receive answers grounded in the "
            "documents with page references, and hear the answers spoken aloud."
        ),
        product_type="web_app",
        preferred_language="en",
        created_at=NOW,
        updated_at=NOW,
    )
    clarification = AlexClarificationService(clock=lambda: NOW)
    started = clarification.begin(order)
    resolved = clarification.use_recommended_defaults(started.order, started.session)
    briefs = ProjectBriefService(id_factory=ids, clock=lambda: NOW)
    brief = briefs.generate(resolved.order, resolved.session)
    brief = briefs.approve(brief, briefs.prepare_approval(brief))
    designs = DesignPreviewService(id_factory=ids, clock=lambda: NOW)
    preview = designs.approve(designs.generate(brief), brief)
    handoff = AgentHandoffService(id_factory=ids, clock=lambda: NOW).create_implementation_handoff(brief, preview)
    return brief, handoff


def _execution_service(preflight, **kwargs):
    return ProjectExecutionService(
        id_factory=_SequenceIds(),
        clock=lambda: NOW,
        mode=ExecutionMode.PRODUCTION,
        live_adapter=FakeProjectExecutionAdapter(),
        production_adapter=FakeProjectExecutionAdapter(),
        preflight=preflight,
        **kwargs,
    )


def test_a_live_start_blocked_by_preflight_never_reaches_the_adapter():
    brief, handoff = _approved_contract()
    calls: list[str] = []

    def preflight():
        calls.append("checked")
        return _preflight(cli_runner=_expired_cli).readiness()

    service = _execution_service(preflight)
    started = service.start(brief, handoff, live=True)

    assert calls == ["checked"]
    # AWAITING_USER, not FAILED: the setup is fixable and the same order can then be started.
    assert started.status is ExecutionStatus.AWAITING_USER
    assert [blocker.code for blocker in started.blockers] == ["preflight_coding_cli_auth_expired"]
    assert started.events[0].kind is EventKind.BLOCKER


def test_a_healthy_preflight_lets_a_live_start_through():
    brief, handoff = _approved_contract()
    service = _execution_service(lambda: _preflight().readiness())

    started = service.start(brief, handoff, live=True)
    finished = service.wait(started.id, 5)

    assert finished.status is ExecutionStatus.SUCCEEDED


def test_a_simulated_run_is_not_gated_on_live_infrastructure():
    """A FAKE-mode run touches neither the coding CLI nor Docker, so a dead environment
    must not stop it -- otherwise a demo or a test cannot run on a laptop with Docker off."""
    brief, handoff = _approved_contract()
    calls: list[str] = []

    def preflight():
        calls.append("checked")
        return _preflight(docker_probe=lambda: False).readiness()

    service = ProjectExecutionService(
        id_factory=_SequenceIds(),
        clock=lambda: NOW,
        mode=ExecutionMode.FAKE,
        preflight=preflight,
    )
    started = service.start(brief, handoff, live=True)
    finished = service.wait(started.id, 5)

    assert calls == []
    assert finished.status is ExecutionStatus.SUCCEEDED


def test_a_dry_run_is_not_gated_either():
    brief, handoff = _approved_contract()
    calls: list[str] = []

    def preflight():
        calls.append("checked")
        return _preflight(cli_runner=_expired_cli).readiness()

    service = _execution_service(preflight)
    started = service.start(brief, handoff, live=False)
    service.wait(started.id, 5)

    assert calls == []


def test_retry_re_checks_the_environment_instead_of_trusting_the_first_start():
    """The point of a retry is usually that something in the environment changed. Trusting
    the original start's preflight would spend the whole build to rediscover a dead token."""
    brief, handoff = _approved_contract()
    outcomes = [_preflight().readiness(), _preflight(cli_runner=_expired_cli).readiness()]

    class FailingAdapter(FakeProjectExecutionAdapter):
        def execute(self, request, event_sink, cancellation):
            return ExecutionResult(
                success=False,
                outcome="failed",
                summary="Simulated failure.",
                test_summary=WorkflowTestSummary(failed=1),
                errors=("provider_rate_limited",),
                completed_at=NOW,
            )

    service = ProjectExecutionService(
        id_factory=_SequenceIds(),
        clock=lambda: NOW,
        mode=ExecutionMode.PRODUCTION,
        live_adapter=FailingAdapter(),
        preflight=lambda: outcomes.pop(0),
    )
    started = service.start(brief, handoff, live=True)
    failed = service.wait(started.id, 5)
    assert failed.status is ExecutionStatus.FAILED

    with pytest.raises(ExecutionServiceError) as raised:
        service.retry(started.id, brief, handoff)

    assert raised.value.code == "preflight_coding_cli_auth_expired"


def test_the_workflow_service_does_not_probe_a_provider_unless_asked_to():
    """Wiring preflight on by default made every unit test that starts a live execution
    spawn the coding CLI for a credentials probe: the suite went provider-dependent and 5x
    slower, and passed or failed according to whether a login happened to be valid that
    afternoon. A test has no environment to check; the API process does, and enables it at
    its own construction site."""
    from order_workflow.service import OrderWorkflowService

    default = OrderWorkflowService()
    enabled = OrderWorkflowService(preflight_enabled=True)

    assert default._executions._preflight is None
    assert enabled._executions._preflight is not None


def test_the_api_turns_preflight_on_where_it_owns_a_real_machine():
    """The guard above is only safe if production actually opts in -- otherwise the expired
    token this whole module exists for goes back to being discovered 20 minutes in."""
    from pathlib import Path

    source = Path("api/orders.py").read_text(encoding="utf-8")

    assert "preflight_enabled=True" in source
