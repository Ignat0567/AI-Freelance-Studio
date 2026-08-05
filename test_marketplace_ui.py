from pathlib import Path

ROOT = Path("frontend/src/features/marketplace")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_marketplace_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Marketplace" in source
    assert "marketplace" in source
    assert "MarketplacePage" in source


def test_api_client_uses_relative_routes_and_no_renderer_token_storage():
    api = _read("marketplaceApi.js")
    page = _read("MarketplacePage.jsx")
    assert "/api/marketplace/sections" in api
    assert "/api/marketplace/font-pairings" in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined


def test_page_renders_sections_and_font_pairings():
    page = _read("MarketplacePage.jsx")
    for expected in ["sections", "font_pairings", "content_schema", "npm_dependencies", "heading_family", "body_family"]:
        assert expected in page


def test_theme_variables_are_used_in_css():
    css = _read("Marketplace.css")
    assert "var(--fs-" in css
