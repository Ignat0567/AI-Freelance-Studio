from pathlib import Path

from delivery_audit import run_final_delivery_audit
from project_spec import ensure_project_spec_bundle


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fastapi_project(tmp_path: Path):
    _write(tmp_path / "main.py", """from fastapi import FastAPI

app = FastAPI()


@app.get('/health')
def health():
    return {'status': 'ok'}
""")
    _write(tmp_path / "README.md", """# Demo

## Install
python -m pip install -r requirements.txt

## Run
python -m uvicorn main:app --host 127.0.0.1 --port 8000

## Test
python -m pytest -q
""")
    _write(tmp_path / "requirements.txt", "fastapi\nuvicorn\npytest\n")
    project = {"title": "Audit Demo", "description": "Build a FastAPI app with a health endpoint.", "logs": [], "chat_history": []}
    ensure_project_spec_bundle(project, str(tmp_path))
    return project


def test_final_audit_passes_verified_fastapi_project(tmp_path):
    project = _fastapi_project(tmp_path)
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "passed"
    assert report["runtime_verification"]["status"] == "passed"
    assert all(check["status"] != "failed" for check in report["checks"])


def test_final_audit_fails_without_latest_full_qa(tmp_path):
    project = _fastapi_project(tmp_path)
    qa_result = {"success": False, "rounds_completed": 1, "total_errors": 1, "round_history": [], "errors": ["pytest failed"]}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "failed"
    assert any(check["name"] == "latest_full_qa" and check["status"] == "failed" for check in report["checks"])


def test_final_audit_blocks_credentials(tmp_path):
    project = _fastapi_project(tmp_path)
    project["project_spec"]["required_credentials"] = [{"name": "OPENAI_API_KEY", "description": "OpenAI API key", "required": "true"}]
    qa_result = {"success": False, "needs_credentials": True, "rounds_completed": 1, "total_errors": 1, "round_history": [], "errors": ["OPENAI_API_KEY missing"]}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "blocked_by_credentials"
    assert report["credentials_still_required"][0]["name"] == "OPENAI_API_KEY"
