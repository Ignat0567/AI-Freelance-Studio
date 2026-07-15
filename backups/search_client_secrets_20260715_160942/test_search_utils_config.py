import inspect

import config_storage
import search_utils


def test_search_api_credentials_are_read_from_config_storage(monkeypatch, tmp_path):
    config_path = tmp_path / "studio_config.json"
    config_storage.save_json_config(
        str(config_path),
        {"freelancer_client_id": "client", "freelancer_client_secret": "secret"},
    )
    monkeypatch.setattr(config_storage, "CONFIG_FILE", str(config_path))

    captured = {}

    class Response:
        status_code = 500

        def json(self):
            return {}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return Response()

    monkeypatch.setattr(search_utils.requests, "post", fake_post)

    assert search_utils._search_freelancer_api("python") == []
    assert captured["payload"]["client_id"] == "client"
    assert captured["payload"]["client_secret"] == "secret"


def test_search_utils_uses_config_storage_without_direct_studio_config_reads():
    source = inspect.getsource(search_utils)

    assert "studio_config.json" not in source
    assert "open(" not in source
    assert "CONFIG_FILE" not in source
