from fastapi.testclient import TestClient

import api.system as system_routes
import main


client = TestClient(main.app)


def test_system_router_uses_main_system_settings():
    assert system_routes._SYSTEM_SETTINGS is main.SYSTEM_SETTINGS


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
