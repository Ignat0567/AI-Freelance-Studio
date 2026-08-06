from __future__ import annotations

import pytest

from order_workflow.docker_qa_runner import docker_engine_available, run_qa_commands_in_docker

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_engine_available(), reason="Docker engine not reachable on this machine"),
]


def _passing_node_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "fixture", "scripts": {"test": "node test.js"}}', encoding="utf-8")
    (tmp_path / "test.js").write_text("console.log('ok'); process.exit(0);", encoding="utf-8")
    return tmp_path


def _failing_node_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "fixture", "scripts": {"test": "node test.js"}}', encoding="utf-8")
    (tmp_path / "test.js").write_text("console.error('boom'); process.exit(1);", encoding="utf-8")
    return tmp_path


def test_real_container_runs_a_passing_command(tmp_path):
    project = _passing_node_project(tmp_path)

    outcome = run_qa_commands_in_docker(("npm test",), project)

    assert outcome.passed is True
    assert outcome.results[0].exit_code == 0
    assert "ok" in outcome.results[0].stdout_tail


def test_real_container_reports_a_failing_command(tmp_path):
    project = _failing_node_project(tmp_path)

    outcome = run_qa_commands_in_docker(("npm test",), project)

    assert outcome.passed is False
    assert outcome.results[0].exit_code != 0
