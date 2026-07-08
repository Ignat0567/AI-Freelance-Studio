import json
import sys
from pathlib import Path

import delivery_audit
from delivery_audit import ACCEPTANCE_VERIFIERS, FastAPIRuntimeAdapter, ReactViteRuntimeAdapter, RuntimeAdapterResult, StaticWebRuntimeAdapter, TelegramBotRuntimeAdapter, normalize_credential_state, run_final_delivery_audit, verify_acceptance_criterion, write_delivery_report
from project_spec import ACCEPTANCE_CONTRACT_FIELDS, ACCEPTANCE_EVIDENCE_HISTORY_KEY, ensure_acceptance_evidence_history, ensure_project_spec_bundle, record_acceptance_evidence


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


def _http_sequence_criterion() -> dict:
    return {
        "id": "AC-CREATE-LIST",
        "title": "After creation, the new request appears in the common request list.",
        "description": "A newly created request is visible in the shared request list.",
        "expected_result": "The request list contains the new request after creation.",
        "priority": "high",
        "verification_method": "feature_trace_static_or_smoke",
        "evidence": [],
        "verifier_plan": {
            "criterion_id": "AC-CREATE-LIST",
            "verifier_type": "http_sequence",
            "setup": ["start application"],
            "required_fixtures": ["request payload with a unique client or request identifier"],
            "actions": ["create request", "list requests"],
            "assertions": ["create succeeds", "created identifier appears in list"],
            "observable_expected_outcomes": ["create response reports success", "list response contains the created request"],
            "execution_status": "not_executed",
        },
    }


def _http_sequence_project(tmp_path: Path, *, list_contains_item: bool = True, list_fails: bool = False, response_secret: bool = True) -> tuple[dict, dict]:
    response_secret_value = "sk-1234567890abcdefSECRET" if response_secret else "safe-response-value"
    _write(tmp_path / "main.py", f"""from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
ITEMS = []
SECRET_SAMPLE = {response_secret_value!r}


class ItemIn(BaseModel):
    name: str


@app.get('/health')
def health():
    return {{'status': 'ok'}}


@app.post('/api/items', status_code=201)
def create_item(item: ItemIn):
    record = {{'id': len(ITEMS) + 1, 'name': item.name, 'api_key': SECRET_SAMPLE}}
    ITEMS.append(record)
    return record


@app.get('/api/items')
def list_items():
    if {list_fails!r}:
        raise HTTPException(status_code=500, detail='list exploded')
    if {list_contains_item!r}:
        return ITEMS
    return []
""")
    _write(tmp_path / "README.md", "# HTTP Sequence Demo\n\nInstall: python -m pip install fastapi uvicorn\n\nRun: python -m uvicorn main:app\n\nTest: python -m pytest -q\n")
    criterion = _http_sequence_criterion()
    project = {
        "title": "HTTP Sequence Demo",
        "description": "Build a FastAPI API where created items appear in the list.",
        "project_profiles": ["fastapi"],
        "project_spec": {"project_profiles": ["fastapi"], "project_type": "fastapi", "delivery_artifacts": ["README.md"]},
        "acceptance_criteria": [criterion],
        "logs": [],
    }
    return project, criterion


def _crud_criterion(criterion_id: str, title: str, description: str = "") -> dict:
    return {
        "id": criterion_id,
        "title": title,
        "description": description or title,
        "expected_result": title,
        "priority": "high",
        "verification_method": "feature_trace_static_or_smoke",
        "evidence": [],
    }


def _crud_sequence_project(tmp_path: Path, criterion: dict, *, persist_update: bool = True, delete_effective: bool = True, search_effective: bool = True, filter_effective: bool = True) -> tuple[dict, dict]:
    _write(tmp_path / "main.py", f"""from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
ITEMS = {{}}
NEXT_ID = 1


class ItemIn(BaseModel):
    name: str
    status: str = 'open'


class ItemUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


def payload(model):
    if hasattr(model, 'model_dump'):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


@app.get('/health')
def health():
    return {{'status': 'ok'}}


@app.post('/api/items', status_code=201)
def create_item(item: ItemIn):
    global NEXT_ID
    record = {{'id': NEXT_ID, 'name': item.name, 'status': item.status}}
    ITEMS[NEXT_ID] = record
    NEXT_ID += 1
    return record


@app.get('/api/items')
def list_items(q: str | None = None, status: str | None = None):
    result = list(ITEMS.values())
    if q and {search_effective!r}:
        result = [item for item in result if q.lower() in item['name'].lower()]
    if status and {filter_effective!r}:
        result = [item for item in result if item['status'] == status]
    return result


@app.get('/api/items/{{item_id}}')
def read_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail='missing')
    return ITEMS[item_id]


@app.patch('/api/items/{{item_id}}')
def update_item(item_id: int, item: ItemUpdate):
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail='missing')
    updated = {{**ITEMS[item_id], **payload(item)}}
    if {persist_update!r}:
        ITEMS[item_id] = updated
    return updated


@app.delete('/api/items/{{item_id}}')
def delete_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail='missing')
    if {delete_effective!r}:
        ITEMS.pop(item_id)
    return {{'deleted': item_id}}
""")
    _write(tmp_path / "README.md", "# CRUD Sequence Demo\n\nInstall: python -m pip install fastapi uvicorn\n\nRun: python -m uvicorn main:app\n\nTest: python -m pytest -q\n")
    project = {
        "title": "CRUD Sequence Demo",
        "description": "Build a FastAPI CRUD API for requests with search and filter.",
        "project_profiles": ["fastapi"],
        "project_spec": {"project_profiles": ["fastapi"], "project_type": "fastapi", "delivery_artifacts": ["README.md"]},
        "acceptance_criteria": [criterion],
        "logs": [],
    }
    return project, criterion


def _persistence_criterion() -> dict:
    return {
        "id": "AC-PERSIST",
        "title": "Application data persists after restart.",
        "description": "Saved request data remains available after the application process is stopped and started again.",
        "expected_result": "Data created before restart can be retrieved after restart.",
        "priority": "high",
        "verification_method": "feature_trace_static_or_smoke",
        "evidence": [],
    }


def _persistence_project(tmp_path: Path, mode: str) -> tuple[dict, dict]:
    if mode == "sqlite":
        body = """from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import sqlite3

app = FastAPI()


class ItemIn(BaseModel):
    name: str


def db_path():
    return os.environ.get('DATABASE_PATH', 'app.sqlite3')


def init_db():
    with sqlite3.connect(db_path()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/api/items', status_code=201)
def create_item(item: ItemIn):
    init_db()
    with sqlite3.connect(db_path()) as conn:
        cur = conn.execute('INSERT INTO items (name) VALUES (?)', (item.name,))
        item_id = cur.lastrowid
        conn.commit()
    return {'id': item_id, 'name': item.name}


@app.get('/api/items/{item_id}')
def read_item(item_id: int):
    init_db()
    with sqlite3.connect(db_path()) as conn:
        row = conn.execute('SELECT id, name FROM items WHERE id = ?', (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='missing')
    return {'id': row[0], 'name': row[1]}
"""
    elif mode == "memory":
        body = """from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
ITEMS = {}
NEXT_ID = 1


class ItemIn(BaseModel):
    name: str


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/api/items', status_code=201)
def create_item(item: ItemIn):
    global NEXT_ID
    record = {'id': NEXT_ID, 'name': item.name}
    ITEMS[NEXT_ID] = record
    NEXT_ID += 1
    return record


@app.get('/api/items/{item_id}')
def read_item(item_id: int):
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail='missing')
    return ITEMS[item_id]
"""
    elif mode == "restart_failure":
        body = """from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import sqlite3

db_file = os.environ.get('DATABASE_PATH', 'app.sqlite3')
counter = Path(db_file + '.starts')
start_count = int(counter.read_text(encoding='utf-8')) if counter.exists() else 0
if start_count >= 1:
    raise RuntimeError('intentional restart failure')
counter.write_text(str(start_count + 1), encoding='utf-8')

app = FastAPI()


class ItemIn(BaseModel):
    name: str


def init_db():
    with sqlite3.connect(db_file) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)')


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/api/items', status_code=201)
def create_item(item: ItemIn):
    init_db()
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute('INSERT INTO items (name) VALUES (?)', (item.name,))
        item_id = cur.lastrowid
        conn.commit()
    return {'id': item_id, 'name': item.name}


@app.get('/api/items/{item_id}')
def read_item(item_id: int):
    init_db()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute('SELECT id, name FROM items WHERE id = ?', (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='missing')
    return {'id': row[0], 'name': row[1]}
"""
    else:
        raise AssertionError(f"unknown persistence fixture mode: {mode}")
    _write(tmp_path / "main.py", body)
    _write(tmp_path / "README.md", "# Persistence Demo\n\nInstall: python -m pip install fastapi uvicorn\n\nRun: python -m uvicorn main:app\n\nTest: python -m pytest -q\n")
    criterion = _persistence_criterion()
    project = {
        "title": "Persistence Demo",
        "description": "Build a FastAPI API where data persists after restart.",
        "project_profiles": ["fastapi"],
        "project_spec": {"project_profiles": ["fastapi"], "project_type": "fastapi", "delivery_artifacts": ["README.md"]},
        "acceptance_criteria": [criterion],
        "logs": [],
    }
    return project, criterion


def _record_direct_feature_evidence(project: dict):
    for criterion in project["acceptance_criteria"]:
        if criterion["verification_method"] == "feature_trace_static_or_smoke":
            record_acceptance_evidence(
                project,
                criterion["id"],
                "passed",
                {"source": "qa_engine", "method": "feature_smoke", "summary": "Feature behavior verified directly."},
            )


def _project_with_custom_acceptance(tmp_path: Path, criterion: dict):
    _write(tmp_path / "README.md", """# Demo

## Install
python -m pip install -r requirements.txt

## Run
python main.py

## Test
python -m pytest -q
""")
    return {
        "title": "Evidence Gate Demo",
        "project_spec": {"delivery_artifacts": ["README.md"], "project_profiles": []},
        "project_profiles": [],
        "acceptance_criteria": [criterion],
        "logs": [],
    }


def _static_project(tmp_path: Path):
    _write(tmp_path / "index.html", """<!doctype html>
<html><head><link rel="stylesheet" href="/assets/site.css"></head>
<body><h1>Static Demo</h1><script src="assets/app.js"></script></body></html>
""")
    _write(tmp_path / "assets" / "site.css", "body { color: #111; }\n")
    _write(tmp_path / "assets" / "app.js", "window.demo = true;\n")
    _write(tmp_path / "README.md", """# Static Demo

## Install
No install needed.

## Run
python -m http.server 8000

## Test
Open index.html.
""")
    return {
        "title": "Static Demo",
        "description": "Build a static website landing page.",
        "project_profiles": ["static_website"],
        "project_spec": {"project_profiles": ["static_website"], "delivery_artifacts": ["README.md", "index.html"]},
        "acceptance_criteria": [],
        "logs": [],
    }


def _react_vite_fixture(tmp_path: Path):
    server_js = """const http = require('http');
const port = Number(process.env.PORT || 4173);
const host = process.env.HOST || '127.0.0.1';
const html = '<!doctype html><div id="root">React Fixture</div>';
const server = http.createServer((req, res) => {
  res.writeHead(200, {'content-type': 'text/html'});
  res.end(html);
});
server.listen(port, host);
process.on('SIGTERM', () => server.close(() => process.exit(0)));
"""
    build_js = """const fs = require('fs');
fs.mkdirSync('dist', {recursive: true});
fs.writeFileSync('dist/index.html', '<!doctype html><div id="root">built</div>');
"""
    package_json = {
        "name": "react-vite-fixture",
        "private": True,
        "type": "commonjs",
        "scripts": {"build": "node build.js", "preview": "node server.js", "dev": "node server.js"},
        "dependencies": {"@vitejs/plugin-react": "fixture", "vite": "fixture", "react": "fixture", "react-dom": "fixture"},
    }
    import json
    _write(tmp_path / "package.json", json.dumps(package_json))
    _write(tmp_path / "server.js", server_js)
    _write(tmp_path / "build.js", build_js)
    _write(tmp_path / "index.html", "<!doctype html><div id='root'></div>")
    _write(tmp_path / "README.md", """# React Vite Fixture

## Install
npm install

## Run
npm run preview

## Test
npm run build
""")
    return {
        "title": "React Vite Fixture",
        "description": "Build a React Vite app.",
        "project_profiles": ["react_frontend", "vite_frontend"],
        "project_spec": {"project_profiles": ["react_frontend", "vite_frontend"], "delivery_artifacts": ["README.md", "package.json"]},
        "acceptance_criteria": [],
        "logs": [],
    }


def _telegram_fixture(tmp_path: Path):
    _write(tmp_path / ".env.example", "BOT_TOKEN=replace_me\nADMIN_ID=0\nDATABASE_PATH=data/bot.db\n")
    _write(tmp_path / ".gitignore", ".env\n__pycache__/\n")
    _write(tmp_path / "README.md", """# Telegram Bot

## Install
python -m pip install -r requirements.txt

## Run
python bot.py

## Test
python -m pytest -q
""")
    _write(tmp_path / "requirements.txt", "python-dotenv\n")
    _write(tmp_path / "config.py", """import os
BOT_TOKEN = os.getenv('BOT_TOKEN', 'replace_me')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
DATABASE_PATH = os.getenv('DATABASE_PATH', 'data/bot.db')
""")
    _write(tmp_path / "bot.py", """from handlers import callbacks, commands

class Dispatcher:
    def __init__(self):
        self.routers = []
    def include_router(self, router):
        self.routers.append(router)

dp = Dispatcher()
dp.include_router(commands.router)
dp.include_router(callbacks.router)
""")
    _write(tmp_path / "handlers" / "__init__.py", "")
    _write(tmp_path / "handlers" / "commands.py", """class Router:
    pass

def Command(name):
    return name

router = Router()
START = Command('start')
HELP = Command('help')
""")
    _write(tmp_path / "handlers" / "callbacks.py", """class Router:
    pass

router = Router()
FAV = {'callback_data': 'fav:abc123'}
MORE = {'callback_data': 'more:random'}
""")
    _write(tmp_path / "services" / "__init__.py", "")
    _write(tmp_path / "services" / "database.py", """class Database:
    def __init__(self, path=':memory:'):
        self.path = path
    def init(self):
        return True
""")
    _write(tmp_path / "services" / "joke_service.py", """from services.database import Database

class JokeService:
    def __init__(self, db=None):
        self.db = db or Database()
""")
    return {
        "title": "Telegram Fixture",
        "description": "Build a Telegram bot with start and help commands.",
        "project_profiles": ["telegram_bot", "python_application"],
        "project_spec": {
            "project_profiles": ["telegram_bot", "python_application"],
            "delivery_artifacts": ["README.md", "requirements.txt", ".env.example", ".gitignore"],
            "required_credentials": [{"name": "TELEGRAM_BOT_TOKEN"}],
        },
        "acceptance_criteria": [],
        "logs": [],
    }


def test_final_audit_passes_verified_fastapi_project(tmp_path):
    project = _fastapi_project(tmp_path)
    _record_direct_feature_evidence(project)
    project["acceptance_criteria"] = [
        criterion
        for criterion in project["acceptance_criteria"]
        if criterion["verification_method"] in ACCEPTANCE_VERIFIERS
    ]
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "passed"
    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "fastapi"
    assert report["runtime_verification"]["owned_process_only"] is True
    assert all(check["status"] != "failed" for check in report["checks"])


def test_runtime_adapter_result_has_minimal_interface_fields():
    result = RuntimeAdapterResult(
        applicable=True,
        started=True,
        verified=False,
        stopped_cleanly=True,
        evidence={"detail": "checked"},
        error="not ready",
    )

    assert set(result.to_dict()) == {"applicable", "started", "verified", "stopped_cleanly", "evidence", "error"}


def test_fastapi_runtime_adapter_not_applicable_for_other_profiles(tmp_path):
    result = FastAPIRuntimeAdapter().run({"project_profiles": ["static_website"]}, str(tmp_path))

    assert result.to_dict() == {
        "applicable": False,
        "started": False,
        "verified": False,
        "stopped_cleanly": True,
        "evidence": {"reason": "No FastAPI profile"},
        "error": "",
    }


def test_fastapi_runtime_adapter_verifies_and_stops_cleanly(tmp_path):
    project = _fastapi_project(tmp_path)

    result = FastAPIRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is True
    assert result.verified is True
    assert result.stopped_cleanly is True
    assert result.error == ""
    assert result.evidence["status_code"] == 200
    assert isinstance(result.evidence["free_port"], int)
    assert result.evidence["free_port"] > 0
    assert result.evidence["pid"]
    assert result.evidence["owned_process_only"] is True
    assert result.evidence["shutdown_method"] in ("terminate", "already_exited", "taskkill_tree")
    assert result.evidence["killed"] is False
    assert any(probe["success"] and probe["path"] == "/health" for probe in result.evidence["probes"])


def test_fastapi_runtime_adapter_uses_free_port_in_command_and_probe(tmp_path):
    project = _fastapi_project(tmp_path)

    result = FastAPIRuntimeAdapter().run(project, str(tmp_path))
    port = result.evidence["free_port"]

    assert f"--port {port}" in result.evidence["command"]
    assert f":{port}" in result.evidence["url"]
    assert all(f":{port}" in probe["url"] for probe in result.evidence["probes"])


def test_fastapi_runtime_adapter_failure_still_stops_owned_process(tmp_path):
    _write(tmp_path / "main.py", "raise RuntimeError('boom before app')\n")
    project = {"project_profiles": ["fastapi"], "project_spec": {"project_profiles": ["fastapi"]}}

    result = FastAPIRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is True
    assert result.verified is False
    assert result.stopped_cleanly is True
    assert result.evidence["owned_process_only"] is True
    assert result.evidence["shutdown_method"] in ("terminate", "already_exited")
    assert result.evidence["killed"] is False
    assert result.error


def test_static_web_runtime_adapter_not_applicable_for_other_profiles(tmp_path):
    result = StaticWebRuntimeAdapter().run({"project_profiles": ["fastapi"]}, str(tmp_path))

    assert result.to_dict() == {
        "applicable": False,
        "started": False,
        "verified": False,
        "stopped_cleanly": True,
        "evidence": {"reason": "No static website profile"},
        "error": "",
    }


def test_static_web_runtime_adapter_verifies_assets_http_200_and_shutdown(tmp_path):
    project = _static_project(tmp_path)

    result = StaticWebRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is True
    assert result.verified is True
    assert result.stopped_cleanly is True
    assert result.error == ""
    assert result.evidence["entry_html"] == "index.html"
    assert result.evidence["status_code"] == 200
    assert result.evidence["missing_assets"] == []
    assert set(result.evidence["local_assets"]) == {"/assets/site.css", "assets/app.js"}
    assert result.evidence["owned_process_only"] is True
    assert result.evidence["shutdown_method"] in ("terminate", "already_exited")
    assert result.evidence["killed"] is False


def test_static_web_runtime_adapter_uses_free_port_in_command_and_probe(tmp_path):
    project = _static_project(tmp_path)

    result = StaticWebRuntimeAdapter().run(project, str(tmp_path))
    port = result.evidence["free_port"]

    assert isinstance(port, int)
    assert port > 0
    assert f"http.server {port}" in result.evidence["command"]
    assert f":{port}" in result.evidence["url"]
    assert any(probe["success"] and probe["status_code"] == 200 for probe in result.evidence["probes"])


def test_static_web_runtime_adapter_blocks_missing_local_assets_before_server_start(tmp_path):
    _write(tmp_path / "index.html", "<link rel='stylesheet' href='missing.css'><h1>Broken</h1>")
    project = {"project_profiles": ["static_website"], "project_spec": {"project_profiles": ["static_website"]}}

    result = StaticWebRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is False
    assert result.verified is False
    assert result.stopped_cleanly is True
    assert result.evidence["entry_html"] == "index.html"
    assert result.evidence["missing_assets"] == ["missing.css"]
    assert result.error == "Missing local static assets"


def test_react_vite_runtime_adapter_not_applicable_for_other_profiles(tmp_path):
    result = ReactViteRuntimeAdapter().run({"project_profiles": ["static_website"]}, str(tmp_path))

    assert result.to_dict() == {
        "applicable": False,
        "started": False,
        "verified": False,
        "stopped_cleanly": True,
        "evidence": {"reason": "No React/Vite profile"},
        "error": "",
    }


def test_react_vite_runtime_adapter_requires_package_json(tmp_path):
    project = {"project_profiles": ["react_frontend", "vite_frontend"], "project_spec": {"project_profiles": ["react_frontend", "vite_frontend"]}}

    result = ReactViteRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is False
    assert result.verified is False
    assert result.stopped_cleanly is True
    assert result.error == "Missing package.json"


def test_react_vite_runtime_adapter_requires_build_and_runtime_scripts(tmp_path):
    import json
    _write(tmp_path / "package.json", json.dumps({"scripts": {"build": "node build.js"}}))
    project = {"project_profiles": ["vite_frontend"], "project_spec": {"project_profiles": ["vite_frontend"]}}

    result = ReactViteRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is False
    assert result.verified is False
    assert result.evidence["missing_scripts"] == ["preview_or_dev"]
    assert result.error == "Missing required package scripts"


def test_react_vite_runtime_adapter_builds_starts_probes_and_stops(tmp_path):
    project = _react_vite_fixture(tmp_path)

    result = ReactViteRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is True
    assert result.verified is True
    assert result.stopped_cleanly is True
    assert result.error == ""
    assert result.evidence["package_json"] == "package.json"
    assert result.evidence["build_result"]["exit_code"] == 0
    assert result.evidence["runtime_script"] == "preview"
    assert result.evidence["status_code"] == 200
    assert "React Fixture" in result.evidence["response_sample"]
    assert result.evidence["owned_process_only"] is True
    assert result.evidence["shutdown_method"] in ("terminate", "already_exited")
    assert result.evidence["killed"] is False


def test_react_vite_runtime_adapter_uses_free_port_for_probe(tmp_path):
    project = _react_vite_fixture(tmp_path)

    result = ReactViteRuntimeAdapter().run(project, str(tmp_path))
    port = result.evidence["free_port"]

    assert isinstance(port, int)
    assert port > 0
    assert result.evidence["command"] == "npm.cmd run preview" if sys.platform == "win32" else result.evidence["command"] == "npm run preview"
    assert f":{port}" in result.evidence["url"]
    assert any(probe["success"] and f":{port}" in probe["url"] for probe in result.evidence["probes"])


def test_final_audit_uses_react_vite_runtime_adapter_before_static(tmp_path):
    project = _react_vite_fixture(tmp_path)
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "react_vite"
    assert report["runtime_verification"]["status_code"] == 200


def test_telegram_runtime_adapter_not_applicable_for_other_profiles(tmp_path):
    result = TelegramBotRuntimeAdapter().run({"project_profiles": ["fastapi"]}, str(tmp_path))

    assert result.to_dict() == {
        "applicable": False,
        "started": False,
        "verified": False,
        "stopped_cleanly": True,
        "evidence": {"reason": "No Telegram bot profile"},
        "error": "",
    }


def test_telegram_runtime_adapter_verifies_credential_free_local_smoke(tmp_path):
    project = _telegram_fixture(tmp_path)

    result = TelegramBotRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.started is False
    assert result.verified is True
    assert result.stopped_cleanly is True
    assert result.error == ""
    assert result.evidence["credential_free"] is True
    assert result.evidence["imports"]["failed"] == []
    assert "bot" in result.evidence["imports"]["imported"]
    assert result.evidence["env_safety"]["safe"] is True
    assert result.evidence["handler_registration"]["handler_registration_detected"] is True
    assert result.evidence["handler_registration"]["key_command_smoke_passed"] == {"start": True, "help": True}
    assert result.evidence["handler_registration"]["callback_data_too_long"] == []
    assert result.evidence["database_service_smoke"]["status"] == "passed"
    assert result.evidence["network_behavior"]["status"] == "not_verified"


def test_telegram_runtime_adapter_rejects_real_looking_env_secret(tmp_path):
    project = _telegram_fixture(tmp_path)
    _write(tmp_path / ".env.example", "BOT_TOKEN=123456789:abcdefghijklmnopqrstuvwxyzABCDE\n")

    result = TelegramBotRuntimeAdapter().run(project, str(tmp_path))

    assert result.applicable is True
    assert result.verified is False
    assert result.evidence["env_safety"]["safe"] is False
    assert "config/.env safety" in result.evidence["missing_local_checks"]


def test_telegram_runtime_adapter_rejects_long_callback_data(tmp_path):
    project = _telegram_fixture(tmp_path)
    _write(tmp_path / "handlers" / "callbacks.py", """class Router:
    pass

router = Router()
BAD = {'callback_data': 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'}
""")

    result = TelegramBotRuntimeAdapter().run(project, str(tmp_path))

    assert result.verified is False
    assert result.evidence["handler_registration"]["callback_data_too_long"]
    assert "callback_data length" in result.evidence["missing_local_checks"]


def test_final_audit_uses_telegram_runtime_adapter_without_network_credentials(tmp_path):
    project = _telegram_fixture(tmp_path)
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "telegram_bot"
    assert report["runtime_verification"]["network_behavior"]["status"] == "not_verified"


def test_final_audit_uses_static_web_runtime_adapter(tmp_path):
    project = _static_project(tmp_path)
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "static_web"
    assert report["runtime_verification"]["status_code"] == 200


def test_final_audit_reconciles_generic_spec_with_fastapi_file_evidence(tmp_path):
    _write(tmp_path / "main.py", """from fastapi import FastAPI

app = FastAPI()

@app.get('/health')
def health():
    return {'status': 'ok'}
""")
    _write(tmp_path / "requirements.txt", "fastapi\nuvicorn\npytest\n")
    _write(tmp_path / "README.md", """# Demo

## Install
python -m pip install -r requirements.txt

## Run
python -m uvicorn main:app --host 127.0.0.1 --port 8000

## Test
python -m pytest -q
""")
    project = {
        "title": "Generic Label",
        "description": "Build a local app.",
        "project_profiles": ["generic"],
        "project_spec": {"project_type": "generic", "project_profiles": ["generic"], "delivery_artifacts": ["README.md", "requirements.txt"]},
        "acceptance_criteria": [],
        "logs": [],
    }

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["project_type"] == "fastapi"
    assert "fastapi" in report["detected_profiles"]
    assert report["runtime_verification"]["adapter"] == "fastapi"
    assert report["runtime_verification"]["status"] == "passed"


def test_russian_criterion_title_survives_delivery_report_json(tmp_path):
    _write(tmp_path / "README.md", "# Demo\n\nInstall: none\n\nRun: open manually\n\nTest: inspect\n")
    project = {
        "title": "Unicode Audit",
        "description": "Иметь возможность создать новую заявку клиента",
        "project_spec": {"delivery_artifacts": ["README.md"], "project_profiles": ["generic"], "project_type": "generic"},
        "project_profiles": ["generic"],
        "acceptance_criteria": [
            {"id": "AC-RU", "title": "Иметь возможность создать новую заявку клиента", "priority": "low", "verification_method": "file_check", "evidence": []}
        ],
        "logs": [],
    }

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    path = write_delivery_report(project, str(tmp_path))
    restored = json.loads(Path(path).read_text(encoding="utf-8"))

    assert report["acceptance_criteria_summary"][0]["title"] == "Иметь возможность создать новую заявку клиента"
    assert restored["acceptance_criteria_summary"][0]["title"] == "Иметь возможность создать новую заявку клиента"


def test_known_runnable_profile_fails_without_applicable_runtime_adapter(tmp_path, monkeypatch):
    project = _fastapi_project(tmp_path)
    monkeypatch.setattr(delivery_audit, "RUNTIME_ADAPTERS", [])
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "failed"
    assert report["runtime_verification"]["status"] == "failed"
    assert report["runtime_verification"]["known_runnable_profiles"] == ["fastapi"]
    assert any(check["name"] == "runtime_smoke" and check["status"] == "failed" for check in report["checks"])


def test_unknown_project_type_uses_limited_generic_runtime_verification(tmp_path):
    project = _project_with_custom_acceptance(
        tmp_path,
        {"id": "AC-FILE", "title": "Files", "priority": "low", "verification_method": "file_check", "evidence": []},
    )
    project["project_profiles"] = ["custom_unknown_profile"]
    project["project_spec"]["project_profiles"] = ["custom_unknown_profile"]
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "generic"
    assert "limited" in report["runtime_verification"]["limitation"]


def test_runtime_smoke_not_applicable_does_not_pass_mandatory_acceptance(tmp_path):
    criterion = {"id": "AC-RUNTIME", "title": "Runtime", "priority": "high", "verification_method": "runtime_smoke"}
    checks = [{"name": "runtime_smoke", "status": "passed", "evidence": {"status": "not_applicable"}}]

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, checks)

    assert evidence["status"] == "failed"
    assert evidence["runtime_status"] == "not_applicable"


def test_mandatory_feature_cannot_pass_from_global_qa_alone(tmp_path):
    project = _fastapi_project(tmp_path)
    feature = next(c for c in project["acceptance_criteria"] if c["verification_method"] == "feature_trace_static_or_smoke")
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "failed"
    assert feature["status"] == "not_verified"
    assert any(check["name"] == "acceptance_criteria" and check["status"] == "failed" for check in report["checks"])


def test_mandatory_acceptance_passes_with_existing_direct_evidence(tmp_path):
    criterion = {"id": "AC-DIRECT", "title": "Direct feature", "priority": "high", "verification_method": "feature_trace_static_or_smoke", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)
    record_acceptance_evidence(
        project,
        "AC-DIRECT",
        "passed",
        {"source": "qa_engine", "method": "feature_smoke", "status": "passed", "summary": "Feature behavior verified directly."},
    )

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "passed"
    assert criterion["status"] == "passed"
    assert report["mandatory_criteria_passed"] is True


def test_mandatory_acceptance_missing_direct_evidence_is_not_verified(tmp_path):
    criterion = {"id": "AC-MISSING", "title": "Missing evidence", "priority": "high", "verification_method": "feature_trace_static_or_smoke", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "not_verified"
    assert report["mandatory_criteria_passed"] is False
    assert any(check["name"] == "acceptance_criteria" and check["evidence"]["failed_mandatory"] == ["AC-MISSING"] for check in report["checks"])


def test_mandatory_acceptance_failed_direct_evidence_is_failed(tmp_path):
    criterion = {"id": "AC-FAILED", "title": "Failed evidence", "priority": "high", "verification_method": "feature_trace_static_or_smoke", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)
    record_acceptance_evidence(
        project,
        "AC-FAILED",
        "failed",
        {"source": "qa_engine", "method": "feature_smoke", "status": "failed", "summary": "Feature smoke failed."},
    )

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "failed"
    assert report["mandatory_criteria_passed"] is False


def test_mandatory_acceptance_rejects_global_qa_success_evidence(tmp_path):
    criterion = {"id": "AC-GLOBAL", "title": "Global evidence", "priority": "high", "verification_method": "feature_trace_static_or_smoke", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)
    legacy_global_evidence = {"criterion_id": "AC-GLOBAL", "source": "qa_engine", "method": "global_qa", "status": "passed", "summary": "Global QA passed.", "qa_success": True}
    history = ensure_acceptance_evidence_history(project)
    history["AC-GLOBAL"].append(legacy_global_evidence)
    criterion["evidence"] = history["AC-GLOBAL"]

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "not_verified"
    assert report["mandatory_criteria_passed"] is False


def test_acceptance_verifier_registry_contains_initial_methods():
    assert set(ACCEPTANCE_VERIFIERS) == {
        "file_check",
        "static_scan",
        "command",
        "python_import",
        "runtime_smoke",
        "static_asset_check",
        "secret_scan",
        "file_and_secret_check",
        "telegram_smoke",
        "http_sequence",
        "persistence_restart",
        "feature_trace_static_or_smoke",
    }


def test_unknown_acceptance_verifier_returns_structured_not_verified(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = {"id": "AC-X", "verification_method": "unknown_method"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "not_verified"
    assert evidence["method"] == "unknown_method"
    assert evidence["verifier"] == "final_delivery_audit"
    assert evidence["verdict"] == "not_executed"
    assert evidence["summary"]
    assert "artifacts" in evidence


def test_registered_acceptance_verifier_returns_structured_evidence(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = {"id": "AC-X", "verification_method": "python_import"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "passed"
    assert evidence["method"] == "python_import"
    assert evidence["verifier"] == "final_delivery_audit"
    assert all(field in evidence for field in ACCEPTANCE_CONTRACT_FIELDS)
    assert evidence["verifier_type"] == "python_import"
    assert evidence["verdict"] == "passed"
    assert evidence["collected_evidence"]["imported"] is True
    assert evidence["summary"]
    assert "artifacts" in evidence


def test_http_sequence_verifier_passes_successful_create_list(tmp_path):
    project, criterion = _http_sequence_project(tmp_path)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    evidence_text = str(evidence)
    assert evidence["status"] == "passed"
    assert evidence["verdict"] == "passed"
    assert evidence["criterion_id"] == "AC-CREATE-LIST"
    assert evidence["method_path"] == [{"method": "POST", "path": "/api/items"}, {"method": "GET", "path": "/api/items"}]
    assert evidence["safe_request_summary"]["json_keys"] == ["name"]
    assert evidence["safe_request_summary"]["marker_field"] == "name"
    assert evidence["response_status"] == {"create": 201, "list": 200}
    assert evidence["created_identifier"] == "1"
    assert evidence["assertion_result"] is True
    assert evidence["collected_evidence"]["runtime_stop"]["stopped_cleanly"] is True
    assert "sk-1234567890abcdefSECRET" not in evidence_text
    assert "<redacted>" in evidence_text


def test_final_audit_stores_http_sequence_criterion_evidence(tmp_path):
    project, criterion = _http_sequence_project(tmp_path, response_secret=False)

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    evidence = criterion["evidence"][-1]
    assert report["mandatory_criteria_passed"] is True
    assert criterion["status"] == "passed"
    assert evidence["criterion_id"] == "AC-CREATE-LIST"
    assert evidence["verifier_type"] == "http_sequence"
    assert evidence["assertion_result"] is True
    assert project[ACCEPTANCE_EVIDENCE_HISTORY_KEY]["AC-CREATE-LIST"][-1] == evidence


def test_http_sequence_verifier_fails_when_created_item_absent_from_list(tmp_path):
    project, criterion = _http_sequence_project(tmp_path, list_contains_item=False)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "failed"
    assert evidence["response_status"] == {"create": 201, "list": 200}
    assert evidence["assertion_result"] is False
    assert "absent" in evidence["summary"]
    assert "created identifier or unique marker" in evidence["failure_reason"]


def test_http_sequence_verifier_fails_when_list_endpoint_fails(tmp_path):
    project, criterion = _http_sequence_project(tmp_path, list_fails=True)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "failed"
    assert evidence["response_status"] == {"create": 201, "list": 500}
    assert evidence["assertion_result"] is False
    assert "List endpoint" in evidence["summary"]


def test_http_sequence_verifier_fails_when_runtime_unavailable(tmp_path, monkeypatch):
    project, criterion = _http_sequence_project(tmp_path)
    monkeypatch.setattr(delivery_audit, "RUNTIME_ADAPTERS", [])

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "failed"
    assert evidence["assertion_result"] is None
    assert "no applicable HTTP runtime adapter" in evidence["failure_reason"]


def test_crud_verifier_create_observes_created_record(tmp_path):
    criterion = _crud_criterion("AC-CREATE", "User can create a new client request.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["criterion_id"] == "AC-CREATE"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "create"
    assert evidence["method_path"] == [{"method": "POST", "path": "/api/items"}, {"method": "GET", "path": "/api/items"}]
    assert evidence["assertion_result"] is True


def test_crud_verifier_update_persists_changed_value(tmp_path):
    criterion = _crud_criterion("AC-UPDATE", "User can open an existing request and update its data.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "update"
    assert {step["method"] for step in evidence["method_path"]} == {"POST", "GET", "PATCH"}
    assert evidence["response_status"]["read_after_update"] == 200
    assert evidence["collected_evidence"]["updated_field"] == "name"
    assert evidence["assertion_result"] is True


def test_crud_verifier_delete_removes_record(tmp_path):
    criterion = _crud_criterion("AC-DELETE", "User can delete an existing request.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "delete"
    assert any(step == {"method": "DELETE", "path": "/api/items/1"} for step in evidence["method_path"])
    assert evidence["response_status"]["read_after_delete"] == 404
    assert evidence["assertion_result"] is True


def test_crud_verifier_search_isolates_target_record(tmp_path):
    criterion = _crud_criterion("AC-SEARCH", "User can search requests by client or request text.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "search"
    assert evidence["collected_evidence"]["target_present"] is True
    assert evidence["collected_evidence"]["control_present"] is False
    assert evidence["assertion_result"] is True


def test_crud_verifier_filter_isolates_target_record(tmp_path):
    criterion = _crud_criterion("AC-FILTER", "User can filter requests by status and priority.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "filter"
    assert evidence["collected_evidence"]["filter_field"] == "status"
    assert evidence["collected_evidence"]["target_present"] is True
    assert evidence["collected_evidence"]["control_present"] is False


def test_crud_verifier_status_change_persists_status_value(tmp_path):
    criterion = _crud_criterion("AC-STATUS", "User can change request status.")
    project, criterion = _crud_sequence_project(tmp_path, criterion)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["collected_evidence"]["crud_sequence_kind"] == "status_change"
    assert evidence["collected_evidence"]["updated_field"] == "status"
    assert evidence["collected_evidence"]["updated_value"] == "closed"
    assert evidence["assertion_result"] is True


def test_crud_verifier_rejects_update_response_without_persisted_state_change(tmp_path):
    criterion = _crud_criterion("AC-UPDATE-FALSE", "User can open an existing request and update its data.")
    project, criterion = _crud_sequence_project(tmp_path, criterion, persist_update=False)

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "failed"
    assert evidence["response_status"]["update"] == 200
    assert evidence["response_status"]["read_after_update"] == 200
    assert evidence["assertion_result"] is False
    assert "not persisted" in evidence["summary"]


def test_persistence_restart_verifier_passes_with_persistent_store(tmp_path):
    project, criterion = _persistence_project(tmp_path, "sqlite")

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["criterion_id"] == "AC-PERSIST"
    assert evidence["created_marker"].startswith("persist_ac_persist_")
    assert evidence["created_identifier"] == "1"
    assert evidence["pre_restart_verification"]["record_exists"] is True
    assert evidence["pre_restart_verification"]["required_fields_present"] is True
    assert evidence["stop_result"]["stopped_cleanly"] is True
    assert evidence["restart_result"]["status"] == "passed"
    assert evidence["restart_result"]["real_restart"] is True
    assert evidence["post_restart_verification"]["record_exists"] is True
    assert evidence["post_restart_verification"]["required_fields_present"] is True
    assert evidence["final_assertion"]["passed"] is True
    assert "DATABASE_PATH" in evidence["collected_evidence"]["storage"]["env_override_names"]


def test_persistence_restart_verifier_fails_for_in_memory_store(tmp_path):
    project, criterion = _persistence_project(tmp_path, "memory")

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["pre_restart_verification"]["record_exists"] is True
    assert evidence["restart_result"]["status"] == "passed"
    assert evidence["restart_result"]["real_restart"] is True
    assert evidence["post_restart_verification"]["record_exists"] is False
    assert evidence["final_assertion"]["passed"] is False
    assert "in-memory" in evidence["failure_reason"]


def test_persistence_restart_verifier_reports_restart_failure(tmp_path):
    project, criterion = _persistence_project(tmp_path, "restart_failure")

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["pre_restart_verification"]["record_exists"] is True
    assert evidence["stop_result"]["stopped_cleanly"] is True
    assert evidence["restart_result"]["status"] == "failed"
    assert evidence["restart_result"]["real_restart"] is False
    assert evidence["final_assertion"]["passed"] is False
    assert "restart" in evidence["summary"].lower()


def test_unsupported_mandatory_verifier_does_not_pass_from_stored_direct_evidence(tmp_path):
    criterion = {"id": "AC-UNKNOWN", "title": "Unknown", "priority": "high", "verification_method": "unknown_method", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)
    record_acceptance_evidence(
        project,
        "AC-UNKNOWN",
        "passed",
        {"source": "qa_engine", "method": "feature_smoke", "summary": "Focused feature smoke passed.", "collected_evidence": {"observed": True}},
    )

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "not_verified"
    assert criterion["evidence"][-1]["verdict"] == "not_executed"
    assert report["mandatory_criteria_passed"] is False


def test_mandatory_criterion_without_verifier_strategy_is_not_executed(tmp_path):
    criterion = {"id": "AC-NO-VERIFIER", "title": "No verifier", "priority": "high", "verification_method": "", "evidence": []}
    project = _project_with_custom_acceptance(tmp_path, criterion)

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "not_verified"
    assert criterion["evidence"][-1]["verifier_type"] == "unknown"
    assert criterion["evidence"][-1]["verdict"] == "not_executed"


def test_python_import_passes_by_importing_module_not_global_qa(tmp_path):
    _write(tmp_path / "valid_module.py", "VALUE = 1\n")
    criterion = {"id": "AC-IMPORT", "verification_method": "python_import", "module": "valid_module"}

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["module"] == "valid_module"
    assert evidence["imported"] is True
    assert evidence["exception_summary"] == ""
    assert "qa_success" not in evidence


def test_python_import_fails_with_exception_summary_not_global_qa(tmp_path):
    _write(tmp_path / "broken_module.py", "raise RuntimeError('import exploded')\n")
    criterion = {"id": "AC-IMPORT", "verification_method": "python_import", "module": "broken_module"}

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, [])

    evidence_text = str(evidence)

    assert evidence["status"] == "failed"
    assert evidence["module"] == "broken_module"
    assert evidence["imported"] is False
    assert evidence["exception_type"] == "RuntimeError"
    assert "import exploded" in evidence["exception_summary"]
    assert "qa_success" not in evidence
    assert "qa_success" not in evidence_text


def test_command_verifier_passes_on_expected_exit_code(tmp_path):
    criterion = {
        "id": "AC-CMD",
        "verification_method": "command",
        "command": [sys.executable, "-c", "print('ok')"],
        "expected_exit_code": 0,
        "timeout": 5,
    }

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": False}, [])

    assert evidence["status"] == "passed"
    assert evidence["exit_code"] == 0
    assert evidence["cwd"] == str(tmp_path)
    assert evidence["timeout"] == 5
    assert evidence["stdout_tail"].strip() == "ok"
    assert evidence["stderr_tail"] == ""
    assert isinstance(evidence["duration"], float)


def test_command_verifier_fails_on_unexpected_exit_code(tmp_path):
    criterion = {
        "id": "AC-CMD",
        "verification_method": "command",
        "command": [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"],
        "expected_exit_code": 0,
        "timeout": 5,
    }

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["exit_code"] == 3
    assert evidence["expected_exit_codes"] == [0]
    assert evidence["stdout_tail"].strip() == "bad"


def test_command_verifier_fails_on_timeout(tmp_path):
    criterion = {
        "id": "AC-CMD",
        "verification_method": "command",
        "command": [sys.executable, "-c", "import time; time.sleep(2)"],
        "expected_exit_code": 0,
        "timeout": 1,
    }

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["exit_code"] == "timeout"
    assert evidence["timeout"] == 1


def test_command_verifier_redacts_secrets_from_evidence(tmp_path):
    secret = "sk-1234567890abcdefSECRET"
    criterion = {
        "id": "AC-CMD",
        "verification_method": "command",
        "command": [sys.executable, "-c", f"import sys; print('API_KEY={secret}'); print('token={secret}', file=sys.stderr)"],
        "expected_exit_code": 0,
        "timeout": 5,
    }

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, [])
    evidence_text = str(evidence)

    assert evidence["status"] == "passed"
    assert secret not in evidence_text
    assert "<redacted>" in evidence["command"]
    assert "<redacted>" in evidence["stdout_tail"]
    assert "<redacted>" in evidence["stderr_tail"]


def test_file_check_requires_exact_required_paths_not_keyword_mentions(tmp_path):
    _write(tmp_path / "README.md", "requirements.txt is documented here but the file is absent.")
    project = {
        "project_spec": {"delivery_artifacts": ["README.md", "requirements.txt"]},
        "acceptance_criteria": [],
    }
    criterion = {"id": "AC-FILE", "verification_method": "file_check"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["required_paths"] == ["README.md", "requirements.txt"]
    assert evidence["missing_required"] == ["requirements.txt"]
    assert evidence["missing_optional"] == []


def test_file_check_missing_optional_artifact_returns_optional_fail(tmp_path):
    _write(tmp_path / "README.md", "# Demo")
    project = {
        "project_spec": {
            "delivery_artifacts": ["README.md"],
            "optional_artifacts": ["CHANGELOG.md"],
        },
        "acceptance_criteria": [],
    }
    criterion = {"id": "AC-FILE", "verification_method": "file_check"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "optional_fail"
    assert evidence["missing_required"] == []
    assert evidence["missing_optional"] == ["CHANGELOG.md"]


def test_static_asset_check_uses_direct_asset_evidence_not_global_qa(tmp_path):
    _write(tmp_path / "index.html", "<!doctype html><script src='missing.js'></script>")
    criterion = {"id": "AC-ASSET", "verification_method": "static_asset_check"}

    evidence = verify_acceptance_criterion(criterion, {}, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "failed"
    assert evidence["verdict"] == "failed"
    assert evidence["missing_assets"] == ["missing.js"]
    assert "qa_success" not in str(evidence)


def test_final_audit_file_check_fails_missing_mandatory_file(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = [c for c in project["acceptance_criteria"] if c["verification_method"] == "file_check"]
    (tmp_path / "requirements.txt").unlink()
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)
    file_check = project["acceptance_criteria"][0]
    evidence = file_check["evidence"][-1]

    assert report["status"] == "failed"
    assert file_check["status"] == "failed"
    assert evidence["missing_required"] == ["requirements.txt"]


def test_final_audit_file_check_optional_fail_blocks_mandatory_gate(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = [c for c in project["acceptance_criteria"] if c["verification_method"] == "file_check"]
    project["project_spec"]["optional_artifacts"] = ["CHANGELOG.md"]
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)
    file_check = project["acceptance_criteria"][0]

    assert report["status"] == "failed"
    assert file_check["status"] == "optional_fail"
    assert report["mandatory_criteria_passed"] is False
    assert file_check["evidence"][-1]["missing_optional"] == ["CHANGELOG.md"]


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


def test_credential_state_reports_no_credentials_without_phantom_limitation(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = []

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    credential_check = next(check for check in report["checks"] if check["name"] == "credential_status")

    assert report["credential_state"] == []
    assert report["credentials_still_required"] == []
    assert credential_check["status"] == "passed"
    assert not any("external credentials" in limitation for limitation in report["known_limitations"])


def test_required_missing_credential_blocks_and_is_still_required(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = []
    project["project_spec"]["required_credentials"] = [{"name": "OPENAI_API_KEY", "description": "OpenAI API key", "required": True}]

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    credential_check = next(check for check in report["checks"] if check["name"] == "credential_status")

    assert report["status"] == "blocked_by_credentials"
    assert [credential["name"] for credential in report["credentials_still_required"]] == ["OPENAI_API_KEY"]
    assert credential_check["status"] == "blocked"


def test_required_configured_credential_does_not_appear_still_required(tmp_path, monkeypatch):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = []
    project["project_spec"]["required_credentials"] = [{"name": "OPENAI_API_KEY", "description": "OpenAI API key", "required": True}]
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    state = report["credential_state"][0]

    assert state["configured"] is True
    assert state["blocks_completion"] is False
    assert report["credentials_still_required"] == []
    assert next(check for check in report["checks"] if check["name"] == "credential_status")["status"] == "passed"


def test_optional_missing_credential_is_not_blocking(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = []
    project["project_spec"]["required_credentials"] = [{"name": "SMTP_PASSWORD", "description": "SMTP credentials", "required": False}]

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    state = report["credential_state"][0]

    assert state["required"] is False
    assert state["configured"] is False
    assert state["blocks_completion"] is False
    assert report["credentials_still_required"] == []
    assert next(check for check in report["checks"] if check["name"] == "credential_status")["status"] == "passed"


def test_generated_project_credential_is_discovered_as_optional_from_env_example(tmp_path):
    _write(tmp_path / "README.md", "# Demo\n\nInstall: none\n\nRun: local\n\nTest: inspect\n")
    _write(tmp_path / ".env.example", "SMTP_PASSWORD=\n")
    project = {"project_spec": {"delivery_artifacts": ["README.md", ".env.example"], "project_profiles": ["generic"]}, "project_profiles": ["generic"], "acceptance_criteria": []}

    state = normalize_credential_state(project, str(tmp_path))

    assert state == [
        {
            "name": "SMTP_PASSWORD",
            "source": "env_example",
            "required": False,
            "configured": False,
            "externally_verifiable": True,
            "blocks_completion": False,
            "description": "SMTP credentials",
        }
    ]


def test_credential_report_fields_are_not_contradictory(tmp_path):
    project = _fastapi_project(tmp_path)
    project["acceptance_criteria"] = []
    project["project_spec"]["required_credentials"] = [{"name": "OPENAI_API_KEY", "required": True}]

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})
    credential_check = next(check for check in report["checks"] if check["name"] == "credential_status")

    assert credential_check["status"] == "blocked"
    assert credential_check["evidence"]["credentials_still_required"] == report["credentials_still_required"]
    assert all(credential["required"] and credential["blocks_completion"] for credential in report["credentials_still_required"])


def test_final_audit_blocks_open_high_or_critical_issue(tmp_path):
    project = _fastapi_project(tmp_path)
    _record_direct_feature_evidence(project)
    project["acceptance_criteria"] = [
        criterion
        for criterion in project["acceptance_criteria"]
        if criterion["verification_method"] in ACCEPTANCE_VERIFIERS
    ]
    project["issues"] = [
        {
            "id": "ISSUE-QA-ABC",
            "source": "qa_engine",
            "severity": "high",
            "requirement_id": "",
            "criterion_id": "",
            "title": "Runtime failed before repair",
            "evidence": {"fingerprint": "abc"},
            "reproduction": ["python -m pytest -q"],
            "owner": "codex",
            "status": "open",
            "attempts": 1,
            "verification_method": "build_and_tests",
        }
    ]
    qa_result = {"success": True, "rounds_completed": 2, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "failed"
    assert any(
        check["name"] == "open_blocking_issues"
        and check["status"] == "failed"
        and check["evidence"]["open_issue_ids"] == ["ISSUE-QA-ABC"]
        for check in report["checks"]
    )


def test_final_audit_allows_closed_high_issue_with_verification_evidence(tmp_path):
    project = _fastapi_project(tmp_path)
    _record_direct_feature_evidence(project)
    project["acceptance_criteria"] = [
        criterion
        for criterion in project["acceptance_criteria"]
        if criterion["verification_method"] in ACCEPTANCE_VERIFIERS
    ]
    project["issues"] = [
        {
            "id": "ISSUE-QA-ABC",
            "source": "qa_engine",
            "severity": "high",
            "requirement_id": "",
            "criterion_id": "",
            "title": "Runtime failed before repair",
            "evidence": {"fingerprint": "abc", "resolution": {"source": "qa_engine", "status": "passed", "round": 2}},
            "reproduction": ["python -m pytest -q"],
            "owner": "codex",
            "status": "closed",
            "attempts": 1,
            "verification_method": "build_and_tests",
        }
    ]
    qa_result = {"success": True, "rounds_completed": 2, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["status"] == "passed"
    assert any(check["name"] == "open_blocking_issues" and check["status"] == "passed" for check in report["checks"])


def test_final_audit_preserves_acceptance_evidence_history_across_qa_rounds(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = next(c for c in project["acceptance_criteria"] if c["verification_method"] == "python_import")
    criterion_id = criterion["id"]

    run_final_delivery_audit(project, str(tmp_path), {"success": False, "rounds_completed": 1, "total_errors": 1, "round_history": [], "errors": ["pytest failed"]})
    run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 2, "total_errors": 0, "round_history": [], "errors": []})

    history = project[ACCEPTANCE_EVIDENCE_HISTORY_KEY][criterion_id]
    assert [entry["status"] for entry in history] == ["passed", "passed"]
    assert criterion["evidence"] == history
