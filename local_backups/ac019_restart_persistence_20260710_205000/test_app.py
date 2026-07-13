import importlib
import uuid

from fastapi.testclient import TestClient


# A unique fixture credential cannot become a reusable production default.
TEST_ADMIN_PASSWORD = f"test-fixture-{uuid.uuid4().hex}"


def prepare_app(tmp_path, monkeypatch):
    db_path = tmp_path / "tickets.sqlite"
    monkeypatch.setenv("TICKET_TRACKER_DB", str(db_path))
    monkeypatch.setenv("TICKET_TRACKER_ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    import main

    return importlib.reload(main)


def test_create_view_update_comment_search_and_delete_ticket(tmp_path, monkeypatch):
    module = prepare_app(tmp_path, monkeypatch)

    with TestClient(module.app) as client:
        login = client.post("/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD})
        assert login.status_code == 200

        payload = {
            "client_name": "Morgan Lee",
            "contact": "morgan@example.com",
            "company": "Northwind IT",
            "problem_description": "Tablet cannot connect to VPN",
            "priority": "urgent",
            "status": "new",
        }
        created = client.post("/api/tickets", json=payload)
        assert created.status_code == 201
        ticket = created.json()
        assert ticket["client_name"] == "Morgan Lee"
        assert ticket["priority"] == "urgent"

        dashboard = client.get("/api/dashboard").json()
        assert dashboard["new"] == 1
        assert dashboard["urgent"] == 1

        found = client.get("/api/tickets", params={"search": "VPN", "priority": "urgent"}).json()
        assert len(found) == 1
        assert found[0]["id"] == ticket["id"]

        payload["status"] = "closed"
        updated = client.put(f"/api/tickets/{ticket['id']}", json=payload)
        assert updated.status_code == 200
        assert updated.json()["status"] == "closed"
        assert updated.json()["closed_at"] is not None

        comment = client.post(f"/api/tickets/{ticket['id']}/comments", json={"body": "Client confirmed VPN profile reset."})
        assert comment.status_code == 201
        detail = client.get(f"/api/tickets/{ticket['id']}").json()
        assert detail["comments"][0]["body"] == "Client confirmed VPN profile reset."

        denied = client.delete(f"/api/tickets/{ticket['id']}")
        assert denied.status_code == 400
        deleted = client.delete(f"/api/tickets/{ticket['id']}", params={"confirm": "true"})
        assert deleted.status_code == 200
        assert client.get(f"/api/tickets/{ticket['id']}").status_code == 404


def test_requires_login_and_valid_contact(tmp_path, monkeypatch):
    module = prepare_app(tmp_path, monkeypatch)
    with TestClient(module.app) as client:
        assert client.get("/api/tickets").status_code == 401
        assert client.post("/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        assert client.post("/login", json={"username": "admin", "password": TEST_ADMIN_PASSWORD}).status_code == 200
        invalid = client.post(
            "/api/tickets",
            json={
                "client_name": "No Contact",
                "contact": "none",
                "problem_description": "Needs support",
                "priority": "normal",
                "status": "new",
            },
        )
        assert invalid.status_code == 422
