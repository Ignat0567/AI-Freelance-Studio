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


def test_electron_backend_does_not_pass_secrets_as_command_arguments():
    source = _source()
    spawn_call = source.split("backendProcess = spawn(spec.command, spec.args", 1)[1].split(");", 1)[0]

    assert "api" not in spawn_call.lower()
    assert "token" not in spawn_call.lower()
    assert "secret" not in spawn_call.lower()
    assert "password" not in spawn_call.lower()
