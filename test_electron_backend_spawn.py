from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


FRONTEND_MAIN = Path("frontend/main.js")


def _source() -> str:
    return FRONTEND_MAIN.read_text(encoding="utf-8")


def test_electron_backend_uses_spawn_without_shell_exec():
    source = _source()

    assert "const { spawn, spawnSync } = require('child_process');" in source
    assert "exec(" not in source
    assert "shell: false" in source
    assert "backendProcess = spawn(spec.command, spec.args" in source


def test_electron_backend_uses_absolute_paths_and_explicit_cwd():
    source = _source()

    assert "const rootDir = path.resolve(__dirname, '..');" in source
    assert "path.join(process.resourcesPath, 'backend'" in source
    assert "path.isAbsolute(spec.command)" in source
    assert "cwd: spec.cwd" in source


def test_electron_backend_waits_for_health_check_with_timeout():
    source = _source()

    assert "checkBackend(port" in source
    assert "waitForBackend(15000, backendProcess)" in source
    assert "Backend health check timed out" in source


def test_electron_backend_handles_error_exit_and_cleanup():
    source = _source()

    assert "backendProcess.on('error'" in source
    assert "backendProcess.on('exit'" in source
    assert "function stopBackend()" in source
    assert "app.on('before-quit'" in source
    assert "app.on('window-all-closed'" in source
    assert "taskkill.exe" in source


def test_electron_backend_shows_a_user_visible_error_when_the_executable_is_missing():
    source = _source()

    assert "backendStartupFailure = 'executable_missing'" in source
    assert "The bundled backend files are missing or damaged." in source
    assert "dialog.showErrorBox('AI Freelance Studio could not start', message)" in source


def test_electron_backend_shows_a_user_visible_error_when_it_exits_early():
    source = _source()

    assert "backendStartupFailure = backendStartupOutput.includes('Could not find any available network ports') ? 'port_in_use' : 'exited_early';" in source
    assert "The local backend stopped while starting." in source


def test_electron_backend_shows_a_user_visible_error_after_health_timeout():
    source = _source()

    assert "backendStartupFailure = 'health_timeout'" in source
    assert "The local backend did not become ready in time." in source


def test_electron_backend_shows_a_user_visible_error_when_all_local_ports_are_busy():
    source = _source()

    assert "'port_in_use'" in source
    assert "The local backend could not find an available local network port." in source


def test_electron_backend_shows_a_user_visible_error_for_unknown_launch_errors():
    source = _source()

    assert "backendStartupFailure = 'launch_error'" in source
    assert "The local backend could not be started." in source
    assert "path.join(runtimeDirectory(), 'backend-startup.log')" in source


def test_electron_backend_does_not_pass_secrets_as_command_arguments():
    source = _source()
    spawn_call = source.split("backendProcess = spawn(spec.command, spec.args", 1)[1].split(");", 1)[0]

    assert "api" not in spawn_call.lower()
    assert "token" not in spawn_call.lower()
    assert "secret" not in spawn_call.lower()
    assert "password" not in spawn_call.lower()
