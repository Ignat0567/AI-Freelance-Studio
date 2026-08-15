from __future__ import annotations

from pathlib import Path

import pytest

from order_workflow.deployment import (
    DeploymentOutcome,
    build_and_verify_container,
    container_deploy_enabled,
)
from order_workflow.docker_qa_runner import DockerUnavailableError

pytestmark = pytest.mark.unit


class FakeImages:
    def __init__(self, *, fail=False) -> None:
        self.fail = fail
        self.built: list[dict] = []

    def build(self, **kwargs):
        self.built.append(kwargs)
        if self.fail:
            raise RuntimeError("build step 4/7 failed: npm run build exited 1")
        return object(), []


class FakeContainer:
    def __init__(self, *, logs=b"serving") -> None:
        self._logs = logs
        self.removed = False

    def logs(self):
        return self._logs

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self, *, container=None, fail=False) -> None:
        self.container = container or FakeContainer()
        self.fail = fail
        self.run_kwargs: list[dict] = []

    def run(self, image, **kwargs):
        self.run_kwargs.append({"image": image, **kwargs})
        if self.fail:
            raise RuntimeError("port is already allocated")
        return self.container


class FakeDockerClient:
    def __init__(self, *, ping_fails=False, images=None, containers=None) -> None:
        self.ping_fails = ping_fails
        self.images = images or FakeImages()
        self.containers = containers or FakeContainers()

    def ping(self):
        if self.ping_fails:
            raise RuntimeError("engine not reachable")
        return True


def _vite_project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
    return tmp_path


def test_container_deploy_is_off_unless_explicitly_enabled():
    assert container_deploy_enabled({}) is False
    assert container_deploy_enabled({"FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY": "0"}) is False
    assert container_deploy_enabled({"FREELANCERSTUDIO_ENABLE_CONTAINER_DEPLOY": "1"}) is True


def test_a_project_without_package_json_is_skipped_not_failed(tmp_path):
    outcome = build_and_verify_container(tmp_path, image_tag="demo:latest")

    assert outcome.succeeded is False
    assert "nothing to containerise" in outcome.status_message
    # Never reached Docker at all, so no Dockerfile was written into a non-web project.
    assert not (tmp_path / "Dockerfile").exists()


def test_dockerignore_excludes_host_node_modules(tmp_path, monkeypatch):
    # The host's node_modules holds binaries built for the host OS. Copying them into a
    # Linux image is the platform-mismatch bug that already bit the Docker QA runner.
    project = _vite_project(tmp_path)
    monkeypatch.setattr(
        "order_workflow.deployment._probe", lambda _url: (200, "<!doctype html><div id=root>")
    )

    build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=FakeDockerClient)

    assert "node_modules" in (project / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (project / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY package*.json ./" in dockerfile
    assert "serve -s" in dockerfile or '"serve"' in dockerfile


def test_successful_deploy_reports_the_served_status_and_run_command(tmp_path, monkeypatch):
    project = _vite_project(tmp_path)
    monkeypatch.setattr(
        "order_workflow.deployment._probe", lambda _url: (200, "<!doctype html><div id=root>")
    )
    client = FakeDockerClient()

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=lambda: client)

    assert outcome.succeeded is True
    assert outcome.served_status == 200
    assert outcome.run_command.startswith("docker run --rm -p")
    assert "demo:latest" in outcome.run_command


def test_a_container_that_never_answers_is_a_failure_not_an_exception(tmp_path, monkeypatch):
    project = _vite_project(tmp_path)
    monkeypatch.setattr("order_workflow.deployment._probe", lambda _url: (None, "connection refused"))

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=FakeDockerClient)

    assert outcome.succeeded is False
    assert "did not serve" in outcome.status_message


def test_a_200_with_an_empty_body_does_not_count_as_served(tmp_path, monkeypatch):
    # A static server can answer 200 with nothing behind it; that is not a deployed app.
    project = _vite_project(tmp_path)
    monkeypatch.setattr("order_workflow.deployment._probe", lambda _url: (200, "   "))

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=FakeDockerClient)

    assert outcome.succeeded is False


def test_image_build_failure_is_reported_with_logs(tmp_path):
    project = _vite_project(tmp_path)
    client = FakeDockerClient(images=FakeImages(fail=True))

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=lambda: client)

    assert outcome.succeeded is False
    assert "image build failed" in outcome.status_message.casefold()
    assert "npm run build exited 1" in outcome.logs_tail


def test_container_start_failure_is_reported(tmp_path):
    project = _vite_project(tmp_path)
    client = FakeDockerClient(containers=FakeContainers(fail=True))

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=lambda: client)

    assert outcome.succeeded is False
    assert "failed to start" in outcome.status_message.casefold()


def test_the_container_is_always_removed_even_on_a_failed_probe(tmp_path, monkeypatch):
    project = _vite_project(tmp_path)
    monkeypatch.setattr("order_workflow.deployment._probe", lambda _url: (None, "refused"))
    container = FakeContainer()
    client = FakeDockerClient(containers=FakeContainers(container=container))

    build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=lambda: client)

    assert container.removed is True  # a pipeline stage must not leak running containers


def test_unreachable_docker_engine_raises_rather_than_reporting_a_build_failure(tmp_path):
    project = _vite_project(tmp_path)

    with pytest.raises(DockerUnavailableError):
        build_and_verify_container(
            project, image_tag="demo:latest", docker_client_factory=lambda: FakeDockerClient(ping_fails=True)
        )


def test_deployment_outcome_status_messages_fit_the_shorttext_warning_limit(tmp_path):
    # ExecutionResult.warnings is tuple[ShortText, ...] with max_length=240; a status
    # message that overflows would turn a deploy warning into a validation crash.
    project = _vite_project(tmp_path)
    client = FakeDockerClient(images=FakeImages(fail=True))

    outcome = build_and_verify_container(project, image_tag="demo:latest", docker_client_factory=lambda: client)

    assert len(outcome.status_message) <= 240
    assert isinstance(outcome, DeploymentOutcome)
