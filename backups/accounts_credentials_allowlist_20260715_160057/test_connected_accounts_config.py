import inspect

import config_storage
import connected_accounts


def test_connected_accounts_are_read_and_written_via_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(connected_accounts.uuid, "uuid4", lambda: "acc-1")
    monkeypatch.setattr(connected_accounts.time, "time", lambda: 1234)

    account_id = connected_accounts.add_account("github", "Work")

    assert account_id == "acc-1"
    assert connected_accounts.get_accounts() == [
        {
            "id": "acc-1",
            "platform": "github",
            "platform_name": "GitHub",
            "icon": connected_accounts.PLATFORMS["github"]["icon"],
            "label": "Work",
            "status": "connected",
            "last_sync": 1234,
            "created_at": 1234,
            "auth_type": "token",
        }
    ]

    stored = config_storage.load_studio_keys()
    assert "credentials" not in stored["_accounts"][0]


def test_connected_accounts_uses_config_storage_without_direct_studio_config_reads():
    source = inspect.getsource(connected_accounts)

    assert "studio_config.json" not in source
    assert "open(" not in source
    assert "CONFIG_PATH" not in source
