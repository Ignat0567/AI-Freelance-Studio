import pytest
from fastapi.testclient import TestClient

import main


pytestmark = pytest.mark.unit


def test_development_cors_uses_explicit_local_origins():
    origins = main.get_cors_origins({"FREELANCERSTUDIO_ENV": "development"})

    assert "*" not in origins
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins
    assert "http://localhost:8080" in origins
    assert "http://127.0.0.1:8080" in origins


def test_development_cors_allows_explicit_extra_origins():
    origins = main.get_cors_origins({
        "FREELANCERSTUDIO_ENV": "development",
        "FREELANCERSTUDIO_DEV_CORS_ORIGINS": "http://127.0.0.1:4173, http://localhost:4173",
    })

    assert "http://127.0.0.1:4173" in origins
    assert "http://localhost:4173" in origins
    assert "*" not in origins


def test_production_cors_reads_origins_from_settings_only():
    origins = main.get_cors_origins({
        "FREELANCERSTUDIO_ENV": "production",
        "FREELANCERSTUDIO_CORS_ORIGINS": "https://studio.example.com, https://admin.example.com",
    })

    assert origins == ["https://studio.example.com", "https://admin.example.com"]


def test_production_cors_defaults_to_empty_origin_list():
    assert main.get_cors_origins({"FREELANCERSTUDIO_ENV": "production"}) == []


def test_app_cors_does_not_combine_wildcard_origins_with_credentials():
    cors_layers = [middleware for middleware in main.app.user_middleware if middleware.cls.__name__ == "CORSMiddleware"]

    assert cors_layers
    options = cors_layers[0].kwargs
    assert options["allow_credentials"] is False
    assert options["allow_origins"]
    assert "*" not in options["allow_origins"]
    assert options["allow_methods"] == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert options["allow_headers"] == ["Content-Type", "X-FreelancerStudio-Token"]
    assert "808[0-9]" in options["allow_origin_regex"]


def test_cors_preflight_allows_declared_header_but_not_unrelated_origin():
    client = TestClient(main.app, base_url="http://127.0.0.1:8080")
    headers = {
        "Origin": "http://127.0.0.1:8080",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-FreelancerStudio-Token",
    }
    allowed = client.options("/api/config/system", headers=headers)
    denied = client.options("/api/config/system", headers={**headers, "Origin": "https://evil.example"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_backend_bind_host_defaults_to_loopback():
    assert main.get_backend_bind_host({}) == "127.0.0.1"


def test_backend_bind_host_normalizes_localhost_and_preserves_ipv6_loopback():
    assert main.get_backend_bind_host({"BACKEND_HOST": "localhost"}) == "127.0.0.1"
    assert main.get_backend_bind_host({"BACKEND_HOST": "::1"}) == "::1"


def test_backend_bind_host_rejects_wildcard_without_explicit_allow():
    assert main.get_backend_bind_host({"BACKEND_HOST": "0.0.0.0"}) == "127.0.0.1"


def test_backend_bind_host_allows_wildcard_only_with_explicit_allow():
    assert main.get_backend_bind_host({"BACKEND_HOST": "0.0.0.0", "BACKEND_ALLOW_NETWORK": "1"}) == "0.0.0.0"
