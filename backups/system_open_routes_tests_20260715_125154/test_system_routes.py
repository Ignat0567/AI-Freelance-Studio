from fastapi.testclient import TestClient

import api.system as system_routes
import main


client = TestClient(main.app)


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
