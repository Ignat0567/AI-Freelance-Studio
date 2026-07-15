from fastapi.testclient import TestClient

import api.accounts as accounts_routes
import main


client = TestClient(main.app)


def test_list_accounts_returns_accounts_and_platforms(monkeypatch):
    monkeypatch.setattr(accounts_routes, "get_accounts", lambda: [{"id": "acc-1", "platform": "github"}])
    monkeypatch.setattr(accounts_routes, "PLATFORMS_LIST", {"github": {"name": "GitHub"}})

    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json() == {
        "accounts": [{"id": "acc-1", "platform": "github"}],
        "platforms": {"github": {"name": "GitHub"}},
    }


def test_create_account_rejects_unknown_platform(monkeypatch):
    monkeypatch.setattr(accounts_routes, "PLATFORMS_LIST", {"github": {"name": "GitHub"}})

    response = client.post("/api/accounts", json={"platform": "unknown", "label": "Bad"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown platform: unknown"}


def test_create_account_calls_add_account(monkeypatch):
    captured = {}

    def fake_add_account(platform, label):
        captured["platform"] = platform
        captured["label"] = label
        return "acc-123"

    monkeypatch.setattr(accounts_routes, "PLATFORMS_LIST", {"github": {"name": "GitHub"}})
    monkeypatch.setattr(accounts_routes, "add_account_fn", fake_add_account)

    response = client.post(
        "/api/accounts",
        json={"platform": "github", "label": "Work", "credentials": {"token": "secret"}},
    )

    assert response.status_code == 200
    assert response.json() == {"id": "acc-123", "status": "connected"}
    assert captured == {"platform": "github", "label": "Work"}


def test_delete_account_calls_remove_account(monkeypatch):
    captured = {}

    def fake_remove_account(account_id):
        captured["account_id"] = account_id

    monkeypatch.setattr(accounts_routes, "remove_account_fn", fake_remove_account)

    response = client.delete("/api/accounts/acc-123")

    assert response.status_code == 200
    assert response.json() == {"status": "removed"}
    assert captured == {"account_id": "acc-123"}


def test_sync_account_calls_sync_account(monkeypatch):
    captured = {}

    def fake_sync_account(account_id):
        captured["account_id"] = account_id
        return {"status": "connected", "last_sync": 123}

    monkeypatch.setattr(accounts_routes, "sync_account_fn", fake_sync_account)

    response = client.post("/api/accounts/acc-123/sync")

    assert response.status_code == 200
    assert response.json() == {"status": "connected", "last_sync": 123}
    assert captured == {"account_id": "acc-123"}
