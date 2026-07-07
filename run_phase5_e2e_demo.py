import argparse
import difflib
import json
import os
import shutil
import time
from pathlib import Path

import main
from qa_engine import QAEngine, OPENCODE_FIX_APPLIED


BASE = Path(__file__).resolve().parent
DEMO_ROOT = BASE / "generated_projects" / "phase5_e2e"


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def reset_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def fastapi_fixture(path: Path, broken: bool = True):
    reset_dir(path)
    status_line = "return item" if broken else "return JSONResponse(status_code=201, content=item)"
    import_line = "from fastapi.responses import JSONResponse\n" if not broken else ""
    write(path / "main.py", f'''from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
{import_line}
app = FastAPI(title="Phase 5 Demo API")


class Item(BaseModel):
    name: str = Field(min_length=1)
    quantity: int = Field(gt=0)


@app.get("/health")
def health():
    return {{"status": "ok"}}


@app.post("/items")
def create_item(item: Item):
    item = item.model_dump()
    {status_line}
''')
    write(path / "tests" / "test_api.py", '''from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_item_returns_201():
    response = client.post("/items", json={"name": "paper", "quantity": 2})
    assert response.status_code == 201
    assert response.json()["name"] == "paper"


def test_create_item_validates_input():
    response = client.post("/items", json={"name": "", "quantity": 0})
    assert response.status_code == 422
''')
    write(path / "requirements.txt", "fastapi\nuvicorn\npytest\nhttpx\npydantic\n")
    write(path / "README.md", """# Phase 5 Demo API

## Install
python -m pip install -r requirements.txt

## Run
python -m uvicorn main:app --host 127.0.0.1 --port 8000

## Test
python -m pytest -q
""")
    write(path / ".gitignore", ".env\n__pycache__/\n.pytest_cache/\n")


def credential_fixture(path: Path):
    reset_dir(path)
    write(path / "main.py", """import os


def require_key():
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing or not set")
    return key
""")
    write(path / "tests" / "test_credentials.py", """from main import require_key


def test_openai_key_required():
    assert require_key()
""")
    write(path / "requirements.txt", "pytest\n")
    write(path / "README.md", """# Credential Demo

## Install
python -m pip install -r requirements.txt

## Run
Set OPENAI_API_KEY, then run python main.py.

## Test
python -m pytest -q
""")
    write(path / ".env.example", "OPENAI_API_KEY=replace_me\n")
    write(path / ".gitignore", ".env\n")


def project(title: str, description: str) -> dict:
    data = {"project_id": title.lower().replace(" ", "-"), "id": title, "title": title, "description": description, "status": "created", "logs": [], "chat_history": []}
    main._reset_delivery_gates(data)
    return data


def prepare_contract(data: dict, path: Path):
    main._ensure_project_contract(data, str(path))
    main._mark_generation_finished(data, True)
    main._set_project_status(data, "verifying", force=True)


def complete_if_audited(data: dict, path: Path, qa_result: dict):
    main._mark_qa_passed(data, bool(qa_result.get("success")))
    if not qa_result.get("success"):
        main._set_project_status(data, "failed_qa")
        return False, qa_result.get("errors", [])
    main._set_project_status(data, "final_audit")
    ok, errors = main._run_final_delivery_audit(data, str(path), data["project_id"], qa_result=qa_result)
    if ok:
        main._mark_final_audit_passed(data, True)
        main._set_project_status(data, "completed")
    else:
        main._mark_final_audit_passed(data, False)
        main._set_project_status(data, "failed_qa")
    return ok, errors


def run_positive(real_opencode: bool, run_id: str):
    path = DEMO_ROOT / f"fastapi_repair_success_{run_id}"
    fastapi_fixture(path, broken=True)
    data = project("Phase 5 FastAPI Repair Demo", "Build a FastAPI app with a health endpoint, item creation endpoint returning HTTP 201, input validation, tests, and README.")
    prepare_contract(data, path)
    before = (path / "main.py").read_text(encoding="utf-8")
    if not real_opencode:
        raise RuntimeError("Positive demo requires real OpenCode; rerun without --skip-real-opencode.")
    engine = QAEngine(project=data, target_path=str(path), project_id=data["project_id"], provider="openai", model="gpt-5.5", temperature=0)
    qa_result = engine.run()
    after = (path / "main.py").read_text(encoding="utf-8")
    final_ok, final_errors = complete_if_audited(data, path, qa_result)
    return {
        "fixture": str(path),
        "final_status": data.get("status"),
        "qa_success": qa_result.get("success"),
        "final_audit_ok": final_ok,
        "final_audit_errors": final_errors,
        "repair_history": qa_result.get("repair_history", []),
        "round_history": qa_result.get("round_history", []),
        "file_diff": "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile="before/main.py", tofile="after/main.py", lineterm="")),
        "logs": data.get("logs", []),
        "delivery_report": data.get("final_delivery_report", {}),
    }


class NoopRepairQAEngine(QAEngine):
    def _request_opencode_fix(self, errors, error_text, repair_report=None):
        self.log("[OPENCODE FIX TEST]: Simulated direct repair made no file changes.")
        return {OPENCODE_FIX_APPLIED: True}


def run_negative(run_id: str):
    path = DEMO_ROOT / f"fastapi_persistent_failure_{run_id}"
    fastapi_fixture(path, broken=True)
    data = project("Phase 5 Persistent Failure Demo", "Build a FastAPI app with a health endpoint and item creation endpoint returning HTTP 201.")
    data["_qa_repair_limit"] = 1
    prepare_contract(data, path)
    engine = NoopRepairQAEngine(project=data, target_path=str(path), project_id=data["project_id"], provider="test", model="test", temperature=0)
    qa_result = engine.run()
    final_ok, final_errors = complete_if_audited(data, path, qa_result)
    return {
        "fixture": str(path),
        "final_status": data.get("status"),
        "qa_success": qa_result.get("success"),
        "final_audit_ok": final_ok,
        "final_audit_errors": final_errors,
        "repair_history": qa_result.get("repair_history", []),
        "round_history": qa_result.get("round_history", []),
        "logs": data.get("logs", []),
    }


def run_credentials(run_id: str):
    path = DEMO_ROOT / f"credentials_blocked_{run_id}"
    credential_fixture(path)
    data = project("Phase 5 Credential Demo", "Build an AI application that requires an OpenAI API key and must not commit secrets.")
    data["_qa_repair_limit"] = 3
    prepare_contract(data, path)
    engine = QAEngine(project=data, target_path=str(path), project_id=data["project_id"], provider="test", model="test", temperature=0)
    qa_result = engine.run()
    if qa_result.get("needs_credentials"):
        main._set_project_status(data, "needs_credentials")
    else:
        complete_if_audited(data, path, qa_result)
    return {
        "fixture": str(path),
        "final_status": data.get("status"),
        "qa_success": qa_result.get("success"),
        "needs_credentials": qa_result.get("needs_credentials"),
        "repair_history": qa_result.get("repair_history", []),
        "round_history": qa_result.get("round_history", []),
        "logs": data.get("logs", []),
    }


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-real-opencode", action="store_true", help="Skip the positive real OpenCode repair demo")
    args = parser.parse_args()
    DEMO_ROOT.mkdir(parents=True, exist_ok=True)
    result = {
        "positive_real_opencode": None,
        "negative_persistent_failure": None,
        "credential_block": None,
    }
    run_id = str(int(time.time()))
    if not args.skip_real_opencode:
        result["positive_real_opencode"] = run_positive(real_opencode=True, run_id=run_id)
    result["negative_persistent_failure"] = run_negative(run_id=run_id)
    result["credential_block"] = run_credentials(run_id=run_id)
    evidence_path = DEMO_ROOT / "phase5_e2e_evidence.json"
    evidence_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"evidence_path": str(evidence_path), "summary": {
        key: None if value is None else {"final_status": value.get("final_status"), "qa_success": value.get("qa_success"), "repairs": len(value.get("repair_history", []))}
        for key, value in result.items()
    }}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main_cli()
