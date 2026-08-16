from __future__ import annotations

import pytest

from order_workflow.docker_qa_runner import DockerUnavailableError
from order_workflow.functional_smoke_check import (
    PLAYWRIGHT_IMAGE,
    _SMOKE_CHECK_FILENAME,
    run_functional_smoke_check_in_docker,
)

pytestmark = pytest.mark.unit


class FakeContainer:
    def __init__(self, *, exit_code=0, logs=b"FUNCTIONAL SMOKE CHECK PASSED") -> None:
        self._exit_code = exit_code
        self._logs = logs
        self.remove_calls = 0

    def wait(self, timeout=None):
        return {"StatusCode": self._exit_code}

    def logs(self):
        return self._logs

    def remove(self, force=False):
        self.remove_calls += 1


class FakeContainersAPI:
    def __init__(self, containers) -> None:
        self._containers = list(containers)
        self.run_calls: list[dict] = []

    def run(self, image, command, **kwargs):
        self.run_calls.append({"image": image, "command": command, **kwargs})
        return self._containers.pop(0)


class FakeDockerClient:
    def __init__(self, containers=(), *, ping_raises=False) -> None:
        self.containers = FakeContainersAPI(containers)
        self._ping_raises = ping_raises

    def ping(self):
        if self._ping_raises:
            raise ConnectionError("no docker daemon")
        return True


def _factory(client: FakeDockerClient):
    return lambda: client


def test_smoke_check_writes_and_cleans_up_the_script_file(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])

    run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert not (tmp_path / _SMOKE_CHECK_FILENAME).exists()


def test_smoke_check_cleans_up_the_script_file_even_if_docker_is_unavailable(tmp_path):
    client = FakeDockerClient(ping_raises=True)

    with pytest.raises(DockerUnavailableError):
        run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert not (tmp_path / _SMOKE_CHECK_FILENAME).exists()


def test_smoke_check_runs_against_the_playwright_image_not_the_plain_node_image(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])

    run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert client.containers.run_calls[0]["image"] == PLAYWRIGHT_IMAGE
    assert PLAYWRIGHT_IMAGE != "node:20-slim"


def test_smoke_check_passes_on_zero_exit_code(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0, logs=b"FUNCTIONAL SMOKE CHECK PASSED")])

    outcome = run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert outcome.passed is True


def test_smoke_check_fails_on_nonzero_exit_code_with_a_readable_reason(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=1, logs=b"FUNCTIONAL SMOKE CHECK FAILED:\n- No visible interactive elements (links, buttons, inputs) were found.")])

    outcome = run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert outcome.passed is False
    assert "No visible interactive elements" in outcome.results[0].stdout_tail


def test_smoke_check_only_runs_one_container_for_the_whole_check(tmp_path):
    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])

    run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert len(client.containers.run_calls) == 1


def test_smoke_check_script_is_written_before_the_container_runs(tmp_path):
    seen_script_exists = {}

    class ObservingContainersAPI(FakeContainersAPI):
        def run(self, image, command, **kwargs):
            seen_script_exists["value"] = (tmp_path / _SMOKE_CHECK_FILENAME).is_file()
            return super().run(image, command, **kwargs)

    client = FakeDockerClient(containers=[FakeContainer(exit_code=0)])
    client.containers = ObservingContainersAPI([FakeContainer(exit_code=0)])

    run_functional_smoke_check_in_docker(("functional smoke check",), tmp_path, docker_client_factory=_factory(client))

    assert seen_script_exists["value"] is True
