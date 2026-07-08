import sys
from pathlib import Path

from delivery_audit import ACCEPTANCE_VERIFIERS, FastAPIRuntimeAdapter, ReactViteRuntimeAdapter, RuntimeAdapterResult, StaticWebRuntimeAdapter, run_final_delivery_audit, verify_acceptance_criterion
from project_spec import ACCEPTANCE_EVIDENCE_HISTORY_KEY, ensure_project_spec_bundle, record_acceptance_evidence


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


def test_final_audit_uses_static_web_runtime_adapter(tmp_path):
    project = _static_project(tmp_path)
    qa_result = {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []}

    report = run_final_delivery_audit(project, str(tmp_path), qa_result)

    assert report["runtime_verification"]["status"] == "passed"
    assert report["runtime_verification"]["adapter"] == "static_web"
    assert report["runtime_verification"]["status_code"] == 200


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
    record_acceptance_evidence(
        project,
        "AC-GLOBAL",
        "passed",
        {"source": "qa_engine", "method": "global_qa", "status": "passed", "summary": "Global QA passed.", "qa_success": True},
    )

    report = run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 1, "total_errors": 0, "round_history": [], "errors": []})

    assert report["status"] == "failed"
    assert criterion["status"] == "not_verified"
    assert report["mandatory_criteria_passed"] is False


def test_acceptance_verifier_registry_contains_initial_methods():
    assert set(ACCEPTANCE_VERIFIERS) == {
        "file_check",
        "command",
        "python_import",
        "runtime_smoke",
        "static_asset_check",
        "secret_scan",
    }


def test_unknown_acceptance_verifier_returns_structured_not_verified(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = {"id": "AC-X", "verification_method": "unknown_method"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "not_verified"
    assert evidence["method"] == "unknown_method"
    assert evidence["verifier"] == "final_delivery_audit"
    assert evidence["summary"]
    assert "artifacts" in evidence


def test_registered_acceptance_verifier_returns_structured_evidence(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = {"id": "AC-X", "verification_method": "python_import"}

    evidence = verify_acceptance_criterion(criterion, project, str(tmp_path), {"success": True}, [])

    assert evidence["status"] == "passed"
    assert evidence["method"] == "python_import"
    assert evidence["verifier"] == "final_delivery_audit"
    assert evidence["summary"]
    assert "artifacts" in evidence


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


def test_final_audit_preserves_acceptance_evidence_history_across_qa_rounds(tmp_path):
    project = _fastapi_project(tmp_path)
    criterion = next(c for c in project["acceptance_criteria"] if c["verification_method"] == "python_import")
    criterion_id = criterion["id"]

    run_final_delivery_audit(project, str(tmp_path), {"success": False, "rounds_completed": 1, "total_errors": 1, "round_history": [], "errors": ["pytest failed"]})
    run_final_delivery_audit(project, str(tmp_path), {"success": True, "rounds_completed": 2, "total_errors": 0, "round_history": [], "errors": []})

    history = project[ACCEPTANCE_EVIDENCE_HISTORY_KEY][criterion_id]
    assert [entry["status"] for entry in history] == ["passed", "passed"]
    assert criterion["evidence"] == history
