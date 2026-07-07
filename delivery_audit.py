import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache", "__pycache__"}
IGNORED_EXTS = {".pyc", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".glb"}
SECRET_PATTERNS = [
    r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b",
    r"sk-[A-Za-z0-9_-]{16,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*=\s*(?!replace_me|your_|example|changeme|<)[^\s'\"]{10,}",
]
TODO_PATTERNS = ("todo: implement", "pass  # todo", "raise notimplementederror", "not implemented", "fake output")


def _walk_files(root: str):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in IGNORED_EXTS:
                continue
            path = os.path.join(current, name)
            yield os.path.relpath(path, root).replace(os.sep, "/"), path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _add(checks: list[dict[str, Any]], name: str, status: str, evidence: dict[str, Any]):
    checks.append({"name": name, "status": status, "evidence": evidence})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_command(command: list[str], cwd: str, timeout: int = 120) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {
            "command": " ".join(command),
            "working_directory": cwd,
            "exit_code": result.returncode,
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-4000:],
            "duration": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "working_directory": cwd,
            "exit_code": "timeout",
            "stdout": str(getattr(exc, "stdout", "") or "")[-4000:],
            "stderr": str(getattr(exc, "stderr", "") or "")[-4000:],
            "duration": round(time.time() - started, 3),
        }


def _project_text(root: str) -> str:
    chunks = []
    for _rel, path in _walk_files(root):
        try:
            if os.path.getsize(path) <= 250_000:
                chunks.append(_read(path))
        except Exception:
            pass
    return "\n".join(chunks)


def _check_secrets(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        try:
            text = _read(path)
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(rel)
                break
    return not hits, hits


def _check_todos(root: str) -> tuple[bool, list[str]]:
    hits = []
    for rel, path in _walk_files(root):
        if not rel.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".md")):
            continue
        try:
            text = _read(path).lower()
        except Exception:
            continue
        if any(pattern in text for pattern in TODO_PATTERNS):
            hits.append(rel)
    return not hits, hits


def _runtime_smoke(project: dict, root: str) -> dict[str, Any]:
    profiles = set(project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", []))
    if "fastapi" not in profiles:
        return {"status": "not_applicable", "reason": "No FastAPI profile"}
    candidates = [("main.py", "main:app"), ("app.py", "app:app")]
    entrypoint = None
    for filename, module_ref in candidates:
        if os.path.isfile(os.path.join(root, filename)):
            entrypoint = (filename, module_ref)
            break
    if not entrypoint:
        return {"status": "failed", "reason": "Missing root FastAPI entrypoint (main.py or app.py)"}
    port = _free_port()
    command = [sys.executable, "-m", "uvicorn", entrypoint[1], "--host", "127.0.0.1", "--port", str(port)]
    proc = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    output = []
    try:
        deadline = time.time() + 12
        last_error = ""
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            for path in ("/health", "/"):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1.5) as resp:
                        body = resp.read(500).decode("utf-8", errors="replace")
                    return {
                        "status": "passed",
                        "command": " ".join(command),
                        "url": f"http://127.0.0.1:{port}{path}",
                        "status_code": resp.status,
                        "response_sample": body,
                    }
                except Exception as exc:
                    last_error = str(exc)
            time.sleep(0.4)
        try:
            if proc.stdout:
                output.append(proc.stdout.read()[-2000:])
        except Exception:
            pass
        return {"status": "failed", "command": " ".join(command), "error": last_error, "process_output": "\n".join(output)}
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _verify_readme(root: str) -> tuple[bool, dict[str, Any]]:
    readme = os.path.join(root, "README.md")
    if not os.path.isfile(readme):
        return False, {"error": "README.md missing"}
    text = _read(readme).lower()
    missing = []
    for label, tokens in {
        "install": ("install", "pip install", "npm install", "установка"),
        "run": ("run", "uvicorn", "python", "npm run", "запуск"),
        "test": ("test", "pytest", "npm test", "провер"),
    }.items():
        if not any(token in text for token in tokens):
            missing.append(label)
    return not missing, {"missing_sections": missing}


def _criteria_mandatory(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in criteria if c.get("priority") in ("critical", "high")]


def _evaluate_acceptance(project: dict, root: str, qa_result: dict | None, checks: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    text = _project_text(root).lower()
    qa_success = bool((qa_result or {}).get("success"))
    failed = []
    for criterion in project.get("acceptance_criteria", []):
        method = criterion.get("verification_method", "")
        trace = (criterion.get("trace") or criterion.get("title") or "").lower()
        evidence = {"source": "final_delivery_audit", "method": method, "qa_success": qa_success}
        passed = False
        if method in ("file_check", "secret_scan", "static_scan"):
            passed = not any(c["status"] == "failed" for c in checks if c["name"] in ("required_files", "secret_scan", "todo_scan", "readme_instructions"))
        elif method == "feature_trace_static_or_smoke":
            tokens = [t for t in re.findall(r"[a-zA-ZА-Яа-я0-9_/-]{4,}", trace) if t not in {"feature", "user", "must", "should", "with", "that"}]
            matched = [token for token in tokens[:12] if token in text]
            evidence["matched_tokens"] = matched
            passed = qa_success and bool(matched or not tokens)
        elif method in ("python_import", "runtime_smoke", "telegram_smoke", "command", "file_and_secret_check", "static_asset_check"):
            passed = qa_success
        else:
            passed = qa_success
        criterion["status"] = "passed" if passed else "failed"
        criterion.setdefault("evidence", []).append(evidence)
        if not passed and criterion.get("priority") in ("critical", "high"):
            failed.append(criterion)
    return not failed, failed


def run_final_delivery_audit(project: dict, root: str, qa_result: dict | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    spec = project.get("project_spec", {})
    profiles = project.get("project_profiles") or spec.get("project_profiles", [])

    required_files = [item for item in spec.get("delivery_artifacts", ["README.md"]) if item and " or " not in item]
    missing = [rel for rel in required_files if not os.path.exists(os.path.join(root, rel))]
    _add(checks, "required_files", "passed" if not missing else "failed", {"required": required_files, "missing": missing})

    readme_ok, readme_evidence = _verify_readme(root)
    _add(checks, "readme_instructions", "passed" if readme_ok else "failed", readme_evidence)

    secrets_ok, secret_hits = _check_secrets(root)
    _add(checks, "secret_scan", "passed" if secrets_ok else "failed", {"files_with_secret_like_values": secret_hits})

    todo_ok, todo_hits = _check_todos(root)
    _add(checks, "todo_scan", "passed" if todo_ok else "failed", {"files_with_blocking_todos": todo_hits})

    latest_qa_ok = bool((qa_result or {}).get("success"))
    _add(checks, "latest_full_qa", "passed" if latest_qa_ok else "failed", {"rounds_completed": (qa_result or {}).get("rounds_completed"), "total_errors": (qa_result or {}).get("total_errors")})

    unresolved_regressions = []
    for round_data in (qa_result or {}).get("round_history", []):
        unresolved_regressions.extend(round_data.get("comparison", {}).get("regressions", []))
    if latest_qa_ok:
        unresolved_regressions = []
    _add(checks, "regression_status", "passed" if not unresolved_regressions else "failed", {"unresolved_regressions": unresolved_regressions})

    runtime = _runtime_smoke(project, root)
    _add(checks, "runtime_smoke", "passed" if runtime.get("status") in ("passed", "not_applicable") else "failed", runtime)

    active_ops = []
    if project.get("_qa_active"):
        active_ops.append("qa")
    if project.get("_repair_active"):
        active_ops.append("repair")
    _add(checks, "active_operations", "passed" if not active_ops else "failed", {"active": active_ops})

    ac_ok, failed_criteria = _evaluate_acceptance(project, root, qa_result, checks)
    _add(checks, "acceptance_criteria", "passed" if ac_ok else "failed", {"failed_mandatory": [c.get("id") for c in failed_criteria]})

    credentials_required = spec.get("required_credentials", [])
    blocked_by_credentials = bool((qa_result or {}).get("needs_credentials"))
    if blocked_by_credentials:
        _add(checks, "credential_status", "blocked", {"required_credentials": credentials_required})
    else:
        _add(checks, "credential_status", "passed", {"required_credentials": credentials_required})

    status = "passed"
    if blocked_by_credentials:
        status = "blocked_by_credentials"
    elif any(check["status"] == "failed" for check in checks):
        status = "failed"

    commands = []
    for round_data in (qa_result or {}).get("round_history", []):
        commands.extend(round_data.get("commands", []))

    report = {
        "project_name": project.get("title", "Untitled"),
        "project_type": spec.get("project_type", "generic"),
        "detected_profiles": profiles,
        "implementation_summary": f"Generated project at {root}",
        "acceptance_criteria_summary": [
            {"id": c.get("id"), "title": c.get("title"), "priority": c.get("priority"), "status": c.get("status")}
            for c in project.get("acceptance_criteria", [])
        ],
        "mandatory_criteria_passed": ac_ok,
        "optional_criteria_failed": [c.get("id") for c in project.get("acceptance_criteria", []) if c.get("priority") not in ("critical", "high") and c.get("status") == "failed"],
        "commands_executed": commands,
        "test_results": {"latest_full_qa_passed": latest_qa_ok, "rounds": (qa_result or {}).get("rounds_completed"), "errors": (qa_result or {}).get("errors", [])},
        "runtime_verification": runtime,
        "installation_instructions": spec.get("installation_method", "See README.md"),
        "run_instructions": spec.get("run_method", "See README.md"),
        "credentials_still_required": credentials_required if blocked_by_credentials else [],
        "known_limitations": spec.get("risks", []),
        "checks": checks,
        "status": status,
        "legend": {"verified": "passed", "not_verified": "failed", "blocked_by_credentials": "blocked", "optional_limitation": "optional failed/non-critical"},
    }
    project["final_delivery_report"] = report
    return report


def write_delivery_report(project: dict, root: str) -> str:
    report = project.get("final_delivery_report", {})
    path = os.path.join(root, "DELIVERY_REPORT.json")
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
