from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.marketplace import router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from design_system import FONT_LIBRARY
from website_sections import SECTION_LIBRARY

pytestmark = pytest.mark.unit
TOKEN = "marketplace-api-focused-token-32-bytes"
ORIGIN = "http://127.0.0.1:8080"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(
            token=TOKEN,
            bind_host="127.0.0.1",
            port=8080,
            launch_id="marketplace-api-test-launch",
            allow_test_client=True,
        ),
    )
    app.include_router(router)
    return app


def _client() -> TestClient:
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    return TestClient(_app(), base_url=ORIGIN, headers=headers)


def test_list_sections_returns_every_real_section_with_the_documented_shape():
    response = _client().get("/api/marketplace/sections")

    assert response.status_code == 200
    payload = response.json()
    sections = payload["sections"]
    assert {item["slug"] for item in sections} == {section.slug for section in SECTION_LIBRARY}
    for item in sections:
        section = next(s for s in SECTION_LIBRARY if s.slug == item["slug"])
        assert item["display_name"] == section.display_name
        assert item["description"] == section.description
        assert item["when_to_use"] == section.when_to_use
        assert {field["field"] for field in item["content_schema"]} == set(section.content_fields())
        assert item["npm_dependencies"] == list(section.npm_dependencies)


def test_list_font_pairings_returns_every_real_pairing_with_the_documented_shape():
    response = _client().get("/api/marketplace/font-pairings")

    assert response.status_code == 200
    payload = response.json()
    pairings = payload["font_pairings"]
    assert {item["slug"] for item in pairings} == {pairing.slug for pairing in FONT_LIBRARY}
    for item in pairings:
        pairing = next(p for p in FONT_LIBRARY if p.slug == item["slug"])
        assert item["heading_family"] == pairing.heading_family
        assert item["body_family"] == pairing.body_family
        assert item["google_fonts_import_url"] == pairing.google_fonts_import_url


def test_sections_response_never_leaks_raw_component_source():
    response = _client().get("/api/marketplace/sections")
    body_text = response.text

    # These substrings only ever appear inside SectionSpec.files (real JSX/CSS
    # source) -- if any of them show up, .files leaked onto the wire. (Note:
    # npm_dependencies legitimately contains package names like "@react-three
    # /fiber" -- that's intended catalog data, not a source leak, so it's not
    # checked here.)
    for leaked_marker in ("gsap.registerPlugin", "useEffect", "className=", "PIPELINE_STAGES", "%%CONTENT:"):
        assert leaked_marker not in body_text, f"raw section source leaked via API: {leaked_marker!r}"


def test_marketplace_endpoints_require_the_security_token():
    unauthenticated = TestClient(_app(), base_url=ORIGIN)

    response = unauthenticated.get("/api/marketplace/sections")

    assert response.status_code in (401, 403)
