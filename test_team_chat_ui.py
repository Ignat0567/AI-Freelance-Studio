from pathlib import Path

ROOT = Path("frontend/src/features/collaboration")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_team_chat_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Team Chat" in source
    assert "team-chat" in source
    assert "TeamChatPage" in source


def test_api_client_uses_relative_routes_and_no_renderer_token_storage():
    api = _read("collaborationApi.js")
    page = _read("TeamChatPage.jsx")
    assert "/api/collaboration/channels" in api
    assert "/api/collaboration/events" in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined


def test_page_renders_channels_messages_composer_and_timeline():
    page = _read("TeamChatPage.jsx")
    for expected in ["channels", "messages", "Send", "Timeline", "tc-composer"]:
        assert expected in page


def test_theme_variables_are_used_in_css():
    css = _read("TeamChat.css")
    assert "var(--fs-" in css
