from pathlib import Path

from project_spec import ensure_project_spec_bundle


def _project(title: str, description: str) -> dict:
    return {"title": title, "description": description, "chat_history": [], "logs": []}


def test_scenario_1_simple_fastapi_profile_detected():
    project = _project("Todo API", "Build a simple FastAPI REST API with CRUD endpoints and pytest tests.")

    bundle = ensure_project_spec_bundle(project)

    assert "fastapi" in bundle["project_profiles"]
    assert "REST_API" in bundle["project_profiles"]
    assert project["project_spec"]["expected_entrypoint"] == "main.py"


def test_scenario_2_telegram_bot_profile_criteria_added():
    project = _project("Joke Bot", "Create a Telegram bot using aiogram with /start, /help, categories, callbacks, and favorites.")

    bundle = ensure_project_spec_bundle(project)
    titles = "\n".join(c["title"] for c in bundle["acceptance_criteria"])

    assert "telegram_bot" in bundle["project_profiles"]
    assert "Telegram bot configuration is safe" in titles
    assert "Telegram command handlers smoke test" in titles


def test_scenario_3_unknown_project_gets_generic_qa_plan():
    project = _project("Odd Artifact", "Create an unusual local artifact organizer with custom naming rules.")

    bundle = ensure_project_spec_bundle(project)

    assert bundle["project_profiles"]
    assert bundle["qa_plan"]["levels"]
    assert any(level["level"] == "A" for level in bundle["qa_plan"]["levels"])


def test_scenario_4_explicit_user_feature_maps_to_acceptance_criteria():
    feature = "user can choose AI provider and enter API key in the interface"
    project = _project("Provider UI", f"Build a settings page where {feature}. Empty and invalid keys must be handled.")

    bundle = ensure_project_spec_bundle(project)
    criteria_text = "\n".join(c["description"] + " " + c.get("trace", "") for c in bundle["acceptance_criteria"])

    assert "choose AI provider" in criteria_text or "choose ai provider" in criteria_text.lower()
    assert "api key" in criteria_text.lower()


def test_scenario_5_credential_requirement_is_explicit():
    project = _project("OpenAI Tool", "Build an AI application that calls OpenAI using an API key and stores no secrets in code.")

    bundle = ensure_project_spec_bundle(project)
    spec = bundle["project_spec"]
    titles = "\n".join(c["title"] for c in bundle["acceptance_criteria"])

    assert any(c["name"] == "OPENAI_API_KEY" for c in spec["required_credentials"])
    assert "Credentials are documented safely" in titles


def test_file_based_static_and_vite_detection(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (tmp_path / "vite.config.js").write_text("export default {};", encoding="utf-8")
    (tmp_path / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    project = _project("Frontend", "Build a frontend.")

    bundle = ensure_project_spec_bundle(project, str(tmp_path))

    assert "vite_frontend" in bundle["project_profiles"]
    assert "static_website" in bundle["project_profiles"]
