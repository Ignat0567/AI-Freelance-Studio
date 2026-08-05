from __future__ import annotations

import pytest

from project_docs import build_architecture_mermaid, build_module_map, build_readme, generate_overview_paragraph

pytestmark = pytest.mark.unit


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_build_module_map_categorizes_real_files_and_excludes_noise(tmp_path):
    _write(tmp_path / "src" / "routes" / "api.py", "# route")
    _write(tmp_path / "src" / "models" / "user.py", "class User: pass")
    _write(tmp_path / "tests" / "test_user.py", "def test_x(): pass")
    _write(tmp_path / "node_modules" / "some_dep" / "index.js", "module.exports = {}")
    _write(tmp_path / ".git" / "config", "[core]")

    module_map = build_module_map(tmp_path)

    assert "src/routes/api.py" in module_map["routes/controllers"]
    assert "src/models/user.py" in module_map["models"]
    assert "tests/test_user.py" in module_map["tests"]
    all_paths = [path for paths in module_map.values() for path in paths]
    assert not any("node_modules" in path for path in all_paths)
    assert not any(".git" in path for path in all_paths)


def test_build_module_map_returns_only_non_empty_areas(tmp_path):
    _write(tmp_path / "plain.py", "print('hi')")

    module_map = build_module_map(tmp_path)

    assert all(len(paths) > 0 for paths in module_map.values())


def test_build_architecture_mermaid_reflects_real_module_map():
    module_map = {"models": ("src/models/user.py",), "tests": ("tests/test_user.py", "tests/test_other.py")}

    diagram = build_architecture_mermaid(module_map, "Demo Project")

    assert "```mermaid" in diagram
    assert "flowchart TD" in diagram
    assert '["Demo Project"]' in diagram
    assert "models (1 file)" in diagram
    assert "tests (2 files)" in diagram
    assert "src/models/user.py" in diagram
    assert "tests/test_user.py" in diagram


def test_generate_overview_paragraph_uses_ai_response_when_available():
    overview = generate_overview_paragraph("Build a demo API", ("Auth", "CRUD"), lambda _prompt: "A real AI-written overview.")

    assert overview == "A real AI-written overview."


def test_generate_overview_paragraph_falls_back_when_ai_raises():
    def _raising_ai_ask(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    overview = generate_overview_paragraph("Build a demo API", ("Auth",), _raising_ai_ask)

    assert overview == "Build a demo API."


def test_generate_overview_paragraph_falls_back_when_ai_returns_empty():
    overview = generate_overview_paragraph("Build a demo API.", (), lambda _prompt: "   ")

    assert overview == "Build a demo API."


def test_generate_overview_paragraph_truncates_overlong_response():
    long_response = "x" * 1000

    overview = generate_overview_paragraph("goal", (), lambda _prompt: long_response)

    assert len(overview) == 500


def test_build_readme_contains_every_real_substituted_value():
    module_map = {"models": ("src/models/user.py",)}

    readme = build_readme(
        project_name="Demo Project",
        goal="Build a demo API",
        tech_stack="Frontend: React\nBackend: FastAPI\nStorage: SQLite",
        features=("Real-time chat", "User auth"),
        setup_commands=("pip install -r requirements.txt", "uvicorn main:app"),
        module_map=module_map,
        overview="A real overview paragraph.",
    )

    assert "# Demo Project" in readme
    assert "A real overview paragraph." in readme
    assert "- Real-time chat" in readme
    assert "- User auth" in readme
    assert "Frontend: React" in readme
    assert "pip install -r requirements.txt" in readme
    assert "ARCHITECTURE.md" in readme
    assert "**models** (1 file)" in readme
