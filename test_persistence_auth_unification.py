from types import SimpleNamespace

import delivery_audit


def _protected_openapi():
    return {
        "paths": {
            "/session/start": {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"account": {"type": "string"}, "passcode": {"type": "string"}}}}}}}},
            "/items": {"post": {}},
        }
    }


def test_protected_auth_discovers_schema_and_retains_cookie(monkeypatch):
    calls = []
    monkeypatch.setenv("FREELANCERSTUDIO_VERIFY_AUTH_PASSWORD", "safe-test-value")
    monkeypatch.setattr(delivery_audit, "_http_json_request", lambda base, method, path, payload, cookies=None: calls.append((method, path, payload, cookies)) or {"ok": True, "set_cookie": ["session=opaque; HttpOnly"]})
    handle = SimpleNamespace(base_url="http://test", cookies={})
    result = delivery_audit._authenticate_runtime(handle, _protected_openapi())
    assert result["ready"] is True
    assert result["login_route"] == "/session/start"
    assert handle.cookies == {"session": "opaque"}
    assert calls[0][2].keys() == {"account", "passcode"}


def test_no_auth_and_unsupported_auth_are_distinct():
    handle = SimpleNamespace(base_url="http://test", cookies={})
    assert delivery_audit._authenticate_runtime(handle, {"paths": {"/items": {"post": {}}}})["auth_required"] is False
    unsupported = _protected_openapi()
    del unsupported["paths"]["/session/start"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["account"]
    assert delivery_audit._authenticate_runtime(handle, unsupported)["reason"] == "unsupported_auth_schema"


def test_create_fixture_passes_retained_cookies_to_protected_create(monkeypatch):
    captured = {}
    monkeypatch.setattr(delivery_audit, "_build_create_payload", lambda *_args: ({"name": "marker"}, "name", ""))
    monkeypatch.setattr(delivery_audit, "_http_json_request", lambda _base, _method, _path, _payload, cookies=None: captured.update(cookies=cookies) or {"ok": True, "json": {"id": "1"}, "status": 201, "excerpt": ""})
    handle = SimpleNamespace(base_url="http://test", cookies={"session": "opaque"})
    result = delivery_audit._execute_create_fixture(handle, {}, {"path": "/items"}, "marker", [], {}, {})
    assert result[0] == "passed"
    assert captured["cookies"] == {"session": "opaque"}


def test_persistence_source_uses_canonical_auth_before_create():
    source = delivery_audit._verify_persistence_restart.__code__.co_names
    assert "_authenticate_runtime" in source
    assert "_execute_create_fixture" in source
