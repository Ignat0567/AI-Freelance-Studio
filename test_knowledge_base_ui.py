from pathlib import Path

ROOT = Path("frontend/src/features/knowledge-base")


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_knowledge_base_entry_is_visible_in_dashboard():
    source = Path("frontend/src/components/StudioDashboard.jsx").read_text(encoding="utf-8")
    assert "Knowledge Base" in source
    assert "knowledge-base" in source
    assert "KnowledgeBasePage" in source


def test_api_client_uses_relative_route_and_no_renderer_token_storage():
    api = _read("knowledgeBaseApi.js")
    page = _read("KnowledgeBasePage.jsx")
    assert "/api/embeddings/ask" in api
    combined = api + page
    assert "X-FreelancerStudio-Token" not in combined
    assert "backend_token" not in combined


def test_page_renders_documents_query_and_ranked_results():
    page = _read("KnowledgeBasePage.jsx")
    for expected in ["documents", "query", "ranked", "answer", "Add document", "Ask"]:
        assert expected in page


def test_theme_variables_are_used_in_css():
    css = _read("KnowledgeBase.css")
    assert "var(--fs-" in css
