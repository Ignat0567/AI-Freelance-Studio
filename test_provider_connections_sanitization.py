import main


def test_provider_connection_storage_removes_nested_credential_fields():
    connection = {
        "connection_id": "provider-test",
        "provider": "test",
        "display_name": "Test provider",
        "api_key": "redacted",
        "Access-Token": "redacted",
        "nested": {
            "refresh token": "redacted",
            "safe_metadata": "kept",
            "items": [
                {"client_secret": "redacted", "model": "kept"},
                {"PRIVATE KEY": "redacted", "enabled": True},
            ],
        },
    }

    data = {}
    main._upsert_provider_connection(data, connection)

    stored = data["_provider_connections"][0]
    assert stored == {
        "connection_id": "provider-test",
        "provider": "test",
        "display_name": "Test provider",
        "nested": {
            "safe_metadata": "kept",
            "items": [{"model": "kept"}, {"enabled": True}],
        },
    }


def test_provider_connection_response_hides_all_credential_fields():
    connection = {
        "connection_id": "provider-test",
        "provider": "test",
        "connection_type": "api_provider",
        "credentials": {"token": "redacted"},
        "authorization": "redacted",
        "cookie": "redacted",
        "available_models": [{"id": "test-model", "password": "redacted"}],
    }

    response = main._sanitize_provider_connection(connection)

    assert response["available_models"] == [{"id": "test-model"}]
    assert "credentials" not in response
    assert "authorization" not in response
    assert "cookie" not in response
    assert "redacted" not in str(response)
