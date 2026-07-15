from fastapi.testclient import TestClient
import json

import api.system as system_routes
import config_storage
import main
import system_settings


client = TestClient(main.app)


def test_system_router_uses_main_system_settings():
    assert system_routes.SYSTEM_SETTINGS is main.SYSTEM_SETTINGS
    assert main.SYSTEM_SETTINGS is system_settings.SYSTEM_SETTINGS
    assert system_routes.SYSTEM_SETTINGS is system_settings.SYSTEM_SETTINGS


def test_system_settings_setters_are_removed():
    assert not hasattr(system_routes, "_SYSTEM_SETTINGS")
    assert not hasattr(system_routes, "set_system_settings")
    assert not hasattr(system_routes, "set_system_config_dependencies")


def test_system_settings_mutation_is_shared_in_place():
    original = dict(system_settings.SYSTEM_SETTINGS)
    try:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update({"global_provider": "openai", "global_model": "gpt-5.5"})

        assert main.SYSTEM_SETTINGS["global_provider"] == "openai"
        assert system_routes.SYSTEM_SETTINGS["global_model"] == "gpt-5.5"
    finally:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update(original)


def test_get_saved_system_settings_merges_defaults_saved_and_unknown_keys():
    settings = main._get_saved_system_settings(
        {"_system": {"theme": "light", "unknown_key": "kept"}}
    )

    assert settings["global_provider"] == system_settings.DEFAULT_SYSTEM_SETTINGS["global_provider"]
    assert settings["theme"] == "light"
    assert settings["unknown_key"] == "kept"


def test_get_saved_system_settings_ignores_non_dict_system_values():
    settings = main._get_saved_system_settings({"_system": "invalid"})

    assert settings == system_settings.DEFAULT_SYSTEM_SETTINGS


def test_system_settings_loader_uses_config_storage_file(monkeypatch, tmp_path):
    original = dict(system_settings.SYSTEM_SETTINGS)
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps({"_system": {"theme": "light"}}), encoding="utf-8")

    try:
        monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
        system_settings.reload_system_settings()

        assert main.SYSTEM_SETTINGS["theme"] == "light"
        assert system_routes.SYSTEM_SETTINGS["theme"] == "light"
    finally:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update(original)


def test_system_config_get_preserves_existing_merge(monkeypatch, tmp_path):
    original = dict(system_settings.SYSTEM_SETTINGS)
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps({"_system": {"theme": "light"}}), encoding="utf-8")

    try:
        monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
        system_settings.reload_system_settings()
        system_settings.SYSTEM_SETTINGS["theme"] = "memory-theme"
        system_settings.SYSTEM_SETTINGS["accent_color"] = "#123456"

        response = client.get("/api/config/system")

        assert response.status_code == 200
        data = response.json()
        assert data["theme"] == "light"
        assert data["accent_color"] == "#123456"
        assert data["global_provider"] == system_settings.DEFAULT_SYSTEM_SETTINGS["global_provider"]
        assert "unknown_key" not in data
    finally:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update(original)


def test_system_config_post_ignores_unknown_keys_and_mutates_in_place(monkeypatch, tmp_path):
    original = dict(system_settings.SYSTEM_SETTINGS)
    original_id = id(system_settings.SYSTEM_SETTINGS)
    config_path = tmp_path / "studio_config.json"
    config_path.write_text(json.dumps({"_system": {"theme": "dark"}}), encoding="utf-8")

    try:
        monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
        response = client.post("/api/config/system", json={"theme": "light", "unknown_key": "ignored"})

        assert response.status_code == 200
        assert response.json() == {"status": "saved"}
        assert id(system_settings.SYSTEM_SETTINGS) == original_id
        assert main.SYSTEM_SETTINGS["theme"] == "light"
        assert system_routes.SYSTEM_SETTINGS["theme"] == "light"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["_system"] == {"theme": "light"}
    finally:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update(original)


def test_editor_candidates_use_shared_system_settings():
    original = dict(system_settings.SYSTEM_SETTINGS)
    try:
        main.SYSTEM_SETTINGS["vscode_path"] = "custom-code"

        assert system_routes._editor_candidates("vscode")[0] == "custom-code"
    finally:
        system_settings.SYSTEM_SETTINGS.clear()
        system_settings.SYSTEM_SETTINGS.update(original)


def test_system_config_routes_are_not_duplicated():
    get_routes = [
        route for route in main.app.routes
        if getattr(route, "path", "") == "/api/config/system" and "GET" in getattr(route, "methods", set())
    ]
    post_routes = [
        route for route in main.app.routes
        if getattr(route, "path", "") == "/api/config/system" and "POST" in getattr(route, "methods", set())
    ]

    assert len(get_routes) == 1
    assert len(post_routes) == 1


def test_health_returns_existing_payload():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "FreelancerStudio", "port": 8080}


def test_system_requirements_uses_router_dependency(monkeypatch):
    components = [{"id": "python", "required": True}]
    monkeypatch.setattr(system_routes, "get_components", lambda: components)

    response = client.get("/api/system/requirements")

    assert response.status_code == 200
    assert response.json() == {"components": components}


def test_system_check_uses_router_dependency(monkeypatch):
    results = [{"id": "python", "installed": True}]
    monkeypatch.setattr(system_routes, "check_all", lambda: results)

    response = client.post("/api/system/check")

    assert response.status_code == 200
    assert response.json() == {"results": results}


def test_system_install_uses_router_dependency(monkeypatch):
    captured = {}

    def fake_install(component_id):
        captured["component_id"] = component_id
        return {"id": component_id, "status": "installed", "message": "ok"}

    monkeypatch.setattr(system_routes, "install_component", fake_install)

    response = client.post("/api/system/install/python")

    assert response.status_code == 200
    assert response.json() == {"id": "python", "status": "installed", "message": "ok"}
    assert captured == {"component_id": "python"}


def test_system_open_path_requires_path():
    response = client.post("/api/system/open-path", json={})

    assert response.status_code == 400
    assert response.json() == {"detail": "Path is required"}


def test_system_open_path_opens_existing_path(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(system_routes.subprocess, "Popen", fake_popen)

    response = client.get("/api/system/open-path", params={"path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {"status": "opened", "path": str(tmp_path.resolve())}
    assert str(tmp_path.resolve()) in captured["args"]


def test_system_open_path_returns_404_for_missing_path(tmp_path):
    missing_path = tmp_path / "missing"

    response = client.get("/api/system/open-path", params={"path": str(missing_path)})

    assert response.status_code == 404
    assert response.json() == {"detail": "Path not found"}


def test_system_open_editor_uses_resolved_executable(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(system_routes, "_resolve_editor_executable", lambda editor: "editor.exe")
    monkeypatch.setattr(system_routes.subprocess, "Popen", fake_popen)

    response = client.post("/api/system/open-editor", json={"editor": "vscode", "path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {
        "status": "opened",
        "editor": "vscode",
        "executable": "editor.exe",
        "path": str(tmp_path.resolve()),
    }
    assert captured == {"args": ["editor.exe", str(tmp_path.resolve())], "kwargs": {}}


def test_system_open_editor_returns_404_when_editor_missing(monkeypatch, tmp_path):
    def fake_which(candidate):
        return None

    monkeypatch.setattr(system_routes, "_editor_candidates", lambda editor: ["missing-editor"])
    monkeypatch.setattr(system_routes.shutil, "which", fake_which)

    response = client.post("/api/system/open-editor", json={"editor": "missing", "path": str(tmp_path)})

    assert response.status_code == 404
    assert response.json() == {"detail": "Editor 'missing' not found. Checked: missing-editor"}
