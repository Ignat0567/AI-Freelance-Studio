from __future__ import annotations

import pytest

from order_workflow.docker_qa_runner import (
    DockerUnavailableError,
    detect_base_image,
    docker_engine_available,
    run_qa_commands_in_docker,
)

pytestmark = pytest.mark.unit


# --- detect_base_image -------------------------------------------------------


def test_detect_base_image_node_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert detect_base_image(tmp_path) == "node:20-slim"


def test_detect_base_image_python_from_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    assert detect_base_image(tmp_path) == "python:3.12-slim"


def test_detect_base_image_python_from_pyproject_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert detect_base_image(tmp_path) == "python:3.12-slim"


def test_detect_base_image_raises_when_stack_is_unrecognized(tmp_path):
    with pytest.raises(ValueError):
        detect_base_image(tmp_path)


# --- fakes for the docker SDK surface used by run_qa_commands_in_docker ----


class FakeContainer:
    def __init__(self, *, exit_code=0, logs=b"ok", wait_raises=False):
        self._exit_code = exit_code
        self._logs = logs
        self._wait_raises = wait_raises
        self.removed = False
        self.remove_calls = 0

    def wait(self, timeout=None):
        if self._wait_raises:
            raise TimeoutError("container did not exit before the timeout")
        return {"StatusCode": self._exit_code}

    def logs(self):
        return self._logs

    def remove(self, force=False):
        self.remove_calls += 1
        self.removed = True


class FakeContainersAPI:
    def __init__(self, containers, *, run_raises=False):
        self._containers = list(containers)
        self._run_raises = run_raises
        self.run_calls = []

    def run(self, image, command, **kwargs):
        self.run_calls.append({"image": image, "command": command, **kwargs})
        if self._run_raises:
            raise RuntimeError("image pull failed")
        return self._containers.pop(0)


class FakeDockerClient:
    def __init__(self, containers=(), *, ping_raises=False, run_raises=False):
        self.containers = FakeContainersAPI(containers, run_raises=run_raises)
        self._ping_raises = ping_raises

    def ping(self):
        if self._ping_raises:
            raise ConnectionError("no docker daemon")
        return True


def _factory(client: FakeDockerClient):
    return lambda: client


# --- docker_engine_available --------------------------------------------------


def test_docker_engine_available_true_when_ping_succeeds():
    assert docker_engine_available(_factory(FakeDockerClient())) is True


def test_docker_engine_available_false_when_ping_fails():
    assert docker_engine_available(_factory(FakeDockerClient(ping_raises=True))) is False


def test_docker_engine_available_false_when_factory_raises():
    def _raising_factory():
        raise RuntimeError("docker sdk not configured")

    assert docker_engine_available(_raising_factory) is False


# --- run_qa_commands_in_docker -------------------------------------------------


def test_run_qa_commands_raises_docker_unavailable_when_engine_unreachable(tmp_path):
    client = FakeDockerClient(ping_raises=True)

    with pytest.raises(DockerUnavailableError):
        run_qa_commands_in_docker(("npm test",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))


def test_run_qa_commands_passes_on_zero_exit_code(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0, logs=b"all good")])

    outcome = run_qa_commands_in_docker(("npm test",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    assert outcome.passed is True
    assert outcome.results[0].exit_code == 0
    assert "all good" in outcome.results[0].stdout_tail


def test_run_qa_commands_fails_on_nonzero_exit_code(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=1, logs=b"test failed")])

    outcome = run_qa_commands_in_docker(("npm test",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    assert outcome.passed is False
    assert outcome.results[0].exit_code == 1


def test_run_qa_commands_builds_correct_volume_and_workdir(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])

    run_qa_commands_in_docker(("npm run build",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    call = client.containers.run_calls[0]
    assert call["image"] == "node:20-slim"
    assert call["command"] == ["sh", "-c", "npm run build"]
    assert call["working_dir"] == "/workspace"
    assert str(tmp_path.resolve()) in call["volumes"]
    assert call["volumes"][str(tmp_path.resolve())]["bind"] == "/workspace"


def test_run_qa_commands_reports_timeout_and_still_removes_container(tmp_path):
    container = FakeContainer(wait_raises=True)
    client = FakeDockerClient(containers=[container])

    outcome = run_qa_commands_in_docker(("npm test",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    assert outcome.passed is False
    assert outcome.results[0].exit_code == "timeout"
    assert container.remove_calls == 1


def test_run_qa_commands_container_creation_failure_is_a_qa_failure_not_docker_unavailable(tmp_path):
    client = FakeDockerClient(run_raises=True)

    outcome = run_qa_commands_in_docker(("npm test",), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    assert outcome.passed is False
    assert outcome.results[0].exit_code == "docker_error"


def test_run_qa_commands_runs_each_command_in_its_own_container(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0), FakeContainer(exit_code=0)])

    outcome = run_qa_commands_in_docker(("npm install", "npm test"), tmp_path, image="node:20-slim", docker_client_factory=_factory(client))

    assert len(outcome.results) == 2
    assert len(client.containers.run_calls) == 2
    assert outcome.passed is True


def test_run_qa_commands_detects_image_when_not_given(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])

    run_qa_commands_in_docker(("npm test",), tmp_path, docker_client_factory=_factory(client))

    assert client.containers.run_calls[0]["image"] == "node:20-slim"


def test_undetectable_stack_is_a_qa_failure_not_a_raised_exception(tmp_path):
    """Regression test: a real live run hit this exact case -- the coding provider hadn't
    written package.json yet when QA ran, and detect_base_image's plain ValueError used to
    propagate uncaught out of run_qa_commands_in_docker, crashing the whole execution as a
    generic "internal error" instead of being treated as an ordinary QA failure."""
    client = FakeDockerClient()

    outcome = run_qa_commands_in_docker(("npm run build",), tmp_path, docker_client_factory=_factory(client))

    assert outcome.passed is False
    assert outcome.results[0].exit_code == "qa_stack_undetected"
    assert len(client.containers.run_calls) == 0
