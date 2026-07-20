import json
import subprocess
import time

import requirements_checker as checker


def _component(component_id, check, **overrides):
    return {
        "id": component_id,
        "name": component_id.title(),
        "description": f"Checks {component_id}",
        "required": True,
        "min_version": None,
        "can_auto_install": False,
        "check": check,
        "install": lambda: (False, "Instructions only"),
        **overrides,
    }


def _set_components(monkeypatch, components):
    presentation = {
        component["id"]: {
            "category": "Required",
            "action_label": "Install Instructions",
            "description": component["description"],
        }
        for component in components
    }
    monkeypatch.setattr(checker, "COMPONENTS", components)
    monkeypatch.setattr(checker, "COMPONENT_PRESENTATION", presentation)
    monkeypatch.setattr(checker, "COMPONENT_ORDER", tuple(presentation))


def test_check_all_runs_independently_preserves_order_and_deduplicates(monkeypatch):
    def slow_success():
        time.sleep(0.03)
        return True, "1.2.3", "C:/private/tool.exe", (1, 2, 3)

    def timeout_check():
        time.sleep(0.2)
        return True, "late", "", None

    def failed_check():
        raise RuntimeError("token=secret C:/Users/private")

    components = [
        _component("fast", lambda: (True, "2.0.0", "C:/private/fast.exe", (2, 0, 0))),
        _component("slow", slow_success),
        _component("timeout", timeout_check),
        _component("failed", failed_check),
        _component("missing", lambda: (False, "", "", None)),
        _component("restart", lambda: (True, "3.0", "", (3, 0)), restart_required=True),
        _component("fast", lambda: (False, "", "", None)),
    ]
    _set_components(monkeypatch, components)

    started = time.monotonic()
    payload = checker.check_all(component_timeout=0.06)
    elapsed = time.monotonic() - started

    results = payload["results"]
    assert [result["id"] for result in results] == ["fast", "slow", "timeout", "failed", "missing", "restart"]
    assert [result["status"] for result in results] == [
        "Installed", "Installed", "Error", "Error", "Missing", "Restart required"
    ]
    assert results[2]["timed_out"] is True
    assert results[2]["error_code"] == "timeout"
    assert results[3]["error_code"] == "check_failed"
    assert all(result["path"] == "" for result in results)
    assert "secret" not in json.dumps(payload)
    assert "C:/Users" not in json.dumps(payload)
    assert elapsed < 0.15
    assert payload["summary"] == {
        "total": 6,
        "installed": 2,
        "missing": 1,
        "error": 2,
        "restart_required": 1,
        "duration_ms": payload["summary"]["duration_ms"],
    }


def test_subprocess_timeout_is_reported_as_error(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    def check():
        ok, _out, _error = checker._run(["slow-command"], timeout=0.01)
        return ok, "", "", None

    monkeypatch.setattr(checker.subprocess, "run", raise_timeout)
    _set_components(monkeypatch, [_component("subprocess", check)])

    result = checker.check_all(component_timeout=0.1)["results"][0]

    assert result["status"] == "Error"
    assert result["timed_out"] is True
    assert result["error_code"] == "subprocess_timeout"
    assert result["duration_ms"] >= 0


def test_frozen_sidecar_is_not_reported_or_spawned_as_system_python_and_pip(monkeypatch):
    commands = []
    monkeypatch.setattr(checker.sys, "frozen", True, raising=False)
    monkeypatch.setattr(checker.sys, "executable", r"C:\Program Files\Studio\freelancerstudio-backend.exe")
    monkeypatch.setattr(checker, "_executable_path", lambda _name: "")
    monkeypatch.setattr(checker, "_run", lambda command, **_kwargs: commands.append(command) or (True, "unexpected", ""))

    assert checker._check_python() == (False, "", "", None)
    assert checker._check_pip() == (False, "", "", None)
    assert commands == []


def test_user_facing_registry_has_fixed_order_categories_and_actions():
    components = checker.get_components()

    assert [component["id"] for component in components] == [
        "python", "pip", "nodejs", "npm", "opencode", "git", "github-cli",
        "vscode", "pycharm", "android-platform-tools", "docker", "ollama",
        "expo", "ios_simulator",
    ]
    assert next(component for component in components if component["id"] == "opencode")["category"] == "Required"
    assert next(component for component in components if component["id"] == "github-cli")["category"] == "Optional"
    assert all(component["category"] in {"Required", "Recommended", "Optional"} for component in components)
    assert all(component["action_label"] for component in components)
    assert all(component["can_auto_install"] is False for component in components)
    assert not {"python_packages", "node_packages", "electron"} & {component["id"] for component in components}


def test_legacy_install_dispatch_is_disabled():
    result = checker.install_component("python_packages")

    assert result["success"] is False
    assert result["status"] == "disabled"
    assert "Automatic installation is disabled" in result["message"]
