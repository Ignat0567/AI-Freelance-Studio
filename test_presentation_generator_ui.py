from pathlib import Path

ROOT = Path("frontend/src/features/presentation-generator")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_presentation_generator_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "AI Presentations" in source
    assert "presentation-generator" in source
    assert "PresentationGeneratorPage" in source


def test_api_client_uses_relative_route_and_no_renderer_token_storage():
    api = _read("presentationApi.js")
    page = _read("PresentationGeneratorPage.jsx")
    assert "/api/presentation/generate" in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined


def test_page_triggers_a_real_file_download():
    page = _read("PresentationGeneratorPage.jsx")
    for expected in ["topic", "Generate presentation", "createObjectURL", "download"]:
        assert expected in page


def test_theme_variables_are_used_in_css():
    css = _read("PresentationGenerator.css")
    assert "var(--fs-" in css
