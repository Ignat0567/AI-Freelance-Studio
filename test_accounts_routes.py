from fastapi.testclient import TestClient

import api.accounts as accounts_routes
import config_storage
import connected_accounts
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
    assert response.json() == {"id": "acc-123", "status": "connected", "warning": "credentials_ignored"}
    assert captured == {"platform": "github", "label": "Work"}


def test_create_account_does_not_persist_credentials(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(connected_accounts.uuid, "uuid4", lambda: "acc-1")
    monkeypatch.setattr(connected_accounts.time, "time", lambda: 1234)

    response = client.post(
        "/api/accounts",
        json={
            "platform": "github",
            "label": "Work",
            "credentials": {
                "access_token": "redacted",
                "refresh_token": "redacted",
                "api_key": "redacted",
                "password": "redacted",
                "cookie": "redacted",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"id": "acc-1", "status": "connected", "warning": "credentials_ignored"}
    assert "redacted" not in response.text
    stored_account = config_storage.load_studio_keys()["_accounts"][0]
    assert set(stored_account) == set(connected_accounts.ACCOUNT_METADATA_FIELDS)
    assert not {"credentials", "access_token", "refresh_token", "api_key", "password", "cookie"} & set(stored_account)


def test_list_accounts_hides_legacy_credentials(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    config_storage.save_studio_keys(
        {
            "_accounts": [
                {
                    "id": "acc-legacy",
                    "platform": "github",
                    "label": "Legacy",
                    "credentials": {"access_token": "redacted"},
                    "status": "connected",
                    "last_sync": 1234,
                    "created_at": 1234,
                }
            ]
        }
    )

    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json()["accounts"][0]["warning"] == "legacy_credentials_present"
    assert "credentials" not in response.json()["accounts"][0]
    assert "redacted" not in response.text


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
