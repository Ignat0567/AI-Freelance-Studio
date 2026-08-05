from pathlib import Path

ROOT = Path("frontend/src/features/video-generation")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_video_generation_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "AI Video" in source
    assert "video-generation" in source
    assert "VideoGenerationPage" in source


def test_api_client_uses_relative_route_and_no_renderer_token_storage():
    api = _read("videoGenerationApi.js")
    page = _read("VideoGenerationPage.jsx")
    assert "/api/video/generate" in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined


def test_page_renders_prompt_and_result():
    page = _read("VideoGenerationPage.jsx")
    for expected in ["prompt", "Generate video", "video_url", "model_slug"]:
        assert expected in page


def test_theme_variables_are_used_in_css():
    css = _read("VideoGeneration.css")
    assert "var(--fs-" in css
