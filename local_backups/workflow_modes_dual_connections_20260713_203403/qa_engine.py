import os
import sys
import time
import subprocess
import json
import re
import traceback
import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from ai_utils import ask_studio_ai_with_history
from project_spec import Issue, detect_project_profiles
from project_state import persist_project_state
from repair_scope import EXCLUDED_REPAIR_DIRS, EXCLUDED_REPAIR_EXTENSIONS, resolve_inside, walk_repairable_files

MAX_ROUNDS = 4
MAX_REPAIR_ATTEMPTS = 3
OPENCODE_FIX_APPLIED = "__opencode_fix_applied__"
REPAIR_ATTEMPT_STATUSES = {
    "issue_not_selected", "prompt_build_failed", "executable_not_found",
    "subprocess_start_failed", "subprocess_timeout", "subprocess_nonzero_exit",
    "subprocess_zero_exit_no_changes", "subprocess_zero_exit_changes_detected",
    "task_file_not_found", "exception", "completed",
}


class RepairAttemptResult(dict):
    """Sanitized, durable outcome of one OpenCode repair invocation."""
POLICY_GROUPS = ("python", "fastapi", "telegram", "node", "react_vite", "static_web", "generic")
POLICY_GROUP_RULES = {
    "python": ("python_requirements", "python_pytest", "python_source_quality", "python_getenv_defaults"),
    "fastapi": ("fastapi_root_entrypoint", "fastapi_uvicorn_docs", "cors_safety", "fastapi_route_style"),
    "telegram": ("telegram_env_safety", "telegram_gitignore", "telegram_local_smoke", "python_getenv_defaults"),
    "node": ("npm_build", "js_dependency_manifest"),
    "react_vite": ("npm_build", "vite_runtime_scripts", "esm_modules", "tailwind_postcss"),
    "static_web": ("static_assets",),
    "generic": ("readme_instructions", "placeholder_scan"),
}
POLICY_GROUP_PROMPT_RULES = {
    "python": (
        "- Python: os.getenv() calls must provide safe defaults when used for optional/local config.\n"
        "- Python: dependencies used by code must appear in requirements.txt. Use python-dotenv, not dotenv; use PyMuPDF, not fitz, as the package name.\n"
        "- Python: use targeted try/except around I/O, network, parsing, subprocess, and external-service boundaries; do not wrap every pure function blindly.\n"
        "- Python: Optional must come from typing consistently; package directories containing .py modules need __init__.py.\n"
    ),
    "fastapi": (
        "- FastAPI: expose a root main.py or app.py entrypoint.\n"
        "- FastAPI: use python -m uvicorn in docs; uvicorn.run() must not use debug=True.\n"
        "- FastAPI: keep route handler sync/async style consistent with the database/client stack.\n"
        "- FastAPI: if CORS is enabled, do not combine wildcard origins with credentials.\n"
    ),
    "telegram": (
        "- Telegram: document BOT_TOKEN in .env.example with placeholders only, never a real-looking token.\n"
        "- Telegram: .gitignore must exclude .env, and local smoke checks must not require real network credentials.\n"
    ),
    "node": (
        "- Node: dependencies used by code must appear in package.json.\n"
        "- Node: run npm install and npm run build when a build script exists.\n"
    ),
    "react_vite": (
        "- React/Vite: use ESM import/export consistently; do not mix with CommonJS require/module.exports.\n"
        "- React/Vite: package.json must have type=module when ESM config files exist.\n"
        "- React/Vite: use Tailwind CSS with PostCSS for generated React frontend styling.\n"
        "- React frontend with backend: backend API should return JSON, not HTML templates.\n"
    ),
    "static_web": (
        "- Static web: keep assets local or explicitly documented, and ensure referenced local files exist.\n"
    ),
    "generic": (
        "- Generic: keep README install/run/test instructions accurate for the delivered artifact.\n"
    ),
}

IGNORED_QA_DIRS = EXCLUDED_REPAIR_DIRS
IGNORED_QA_EXTENSIONS = EXCLUDED_REPAIR_EXTENSIONS


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _safe_repair_text(value: str) -> str:
    """Keep repair diagnostics useful without retaining credential-shaped output."""
    return re.sub(r"(?i)(authorization|api[_-]?key|token|secret|password|cookie)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", value or "")


def _walk_project_files(root_path):
    for rel, fpath in walk_repairable_files(root_path):
        yield os.path.dirname(str(fpath)), os.path.basename(str(fpath)), str(fpath)


def _is_safe_fix_path(path):
    parts = path.replace("\\", "/").split("/")
    return not any(part in IGNORED_QA_DIRS or part.startswith(".") for part in parts)


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _redact_secrets(text):
    text = str(text or "")
    patterns = [
        r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b",
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s'\"]+",
        r"sk-[A-Za-z0-9_-]{16,}",
    ]
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, lambda m: m.group(0).split("=", 1)[0] + "=<redacted>" if "=" in m.group(0) else "<redacted>", redacted)
    return redacted


def _normalize_failure_text(text):
    text = _redact_secrets(text).lower()
    text = re.sub(r"[a-z]:\\[^\s:]+", "<path>", text)
    text = re.sub(r"/[^\s:]+", "<path>", text)
    text = re.sub(r"\bport\s+\d+\b", "port <num>", text)
    text = re.sub(r"\b127\.0\.0\.1:\d+\b", "127.0.0.1:<port>", text)
    text = re.sub(r"0x[0-9a-f]+", "0x<addr>", text)
    text = re.sub(r"\bline \d+\b", "line <num>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "<timestamp>", text)
    text = re.sub(r"\b\d+\.\d+s\b", "<duration>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def _failure_fingerprint(check, message):
    normalized = f"{check}:{_normalize_failure_text(message)}"
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]


def _qa_issue_id(fingerprint):
    return f"ISSUE-QA-{str(fingerprint).upper()}"


def _qa_issue_severity(check):
    return {
        "syntax": "critical",
        "dependencies": "high",
        "build_and_tests": "high",
        "delivery_readiness": "high",
        "profile_checks": "medium",
        "file_verification": "medium",
    }.get(check, "medium")


def _likely_files(message):
    candidates = []
    for match in re.findall(r"[\w./\\-]+\.(?:py|js|jsx|ts|tsx|json|md|txt|html|css|env|yml|yaml)", str(message or "")):
        cleaned = match.strip(" .,:;()[]{}'\"").replace("\\", "/")
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    return candidates[:10]


def _project_files(root_path):
    return [os.path.relpath(fpath, root_path) for _root, _fname, fpath in _walk_project_files(root_path)]


def _project_profiles(project, root_path=None):
    if not isinstance(project, dict):
        return []
    if root_path and os.path.isdir(root_path):
        spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
        text = "\n".join(str(part) for part in (project.get("title", ""), project.get("description", ""), spec.get("original_user_request", "")) if part)
        profiles = detect_project_profiles(spec, project_path=root_path, text=text)
        if profiles:
            project["project_profiles"] = profiles
            if isinstance(spec, dict):
                spec["project_profiles"] = profiles
                spec["project_type"] = profiles[0]
            return profiles
    profiles = project.get("project_profiles") or project.get("project_spec", {}).get("project_profiles", [])
    return [str(profile) for profile in profiles if profile]


def select_policy_groups(project, root_path=None):
    profiles = set(_project_profiles(project, root_path))
    groups = []
    if profiles.intersection({"python_application", "python_cli", "fastapi", "telegram_bot"}):
        groups.append("python")
    if "fastapi" in profiles:
        groups.append("fastapi")
    if "telegram_bot" in profiles:
        groups.append("telegram")
    if profiles.intersection({"node_project", "node_backend"}):
        groups.append("node")
    if profiles.intersection({"react_frontend", "vite_frontend"}):
        groups.append("react_vite")
        if "node" not in groups:
            groups.append("node")
    if "static_website" in profiles:
        groups.append("static_web")

    if not groups and root_path and os.path.isdir(root_path):
        files = {p.replace("\\", "/") for p in _project_files(root_path)}
        if any(path.endswith(".py") for path in files):
            groups.append("python")
        if "package.json" in files:
            groups.append("node")
        if "vite.config.js" in files or "vite.config.ts" in files or any(path.endswith((".jsx", ".tsx")) for path in files):
            groups.append("react_vite")
            if "node" not in groups:
                groups.append("node")
        if "index.html" in files or any(path.endswith(".html") for path in files):
            groups.append("static_web")

    if not groups:
        groups.append("generic")
    return [group for group in POLICY_GROUPS if group in set(groups)]


def policy_prompt_rules(policy_groups):
    selected = [group for group in POLICY_GROUPS if group in set(policy_groups or [])]
    if not selected:
        selected = ["generic"]
    return "".join(POLICY_GROUP_PROMPT_RULES[group] for group in selected)


_SNAPSHOT_FILE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".html", ".css", ".env", ".yml", ".yaml", ".cfg", ".ini", ".toml", ".xml", ".svg"}
_SNAPSHOT_DIR_IGNORE = EXCLUDED_REPAIR_DIRS | {"data", ".egg-info"}


def _snapshot_project_files(root_path: str) -> dict[str, dict]:
    snapshot = {}
    for rel, fpath in walk_repairable_files(root_path, _SNAPSHOT_FILE_EXTS):
        try:
            stat = os.stat(fpath)
            digest = hashlib.sha256()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            snapshot[rel] = {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": digest.hexdigest()}
        except OSError:
            continue
    return snapshot


def _compare_snapshots(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    changed = []
    for path, info_after in after.items():
        info_before = before.get(path)
        if info_before is None:
            changed.append(f"{path} [NEW]")
            continue
        if info_after["sha256"] != info_before["sha256"]:
            changed.append(f"{path} [MODIFIED]")
            continue
    for path in before:
        if path not in after:
            changed.append(f"{path} [DELETED]")
    return changed


def _has_any_file(root_path, names):
    wanted = set(names)
    return any(fname in wanted for _root, fname, _fpath in _walk_project_files(root_path))

_SHARED_QA_RULES = (
    "CRITICAL RULES — VIOLATING ANY WILL CAUSE REJECTION:\n"
    "- Do not hardcode secrets or credentials; use config, environment variables, or explicit placeholders.\n"
    "- Import names MUST exactly match the exports of the dependency files.\n"
    "- Dependencies used in code MUST be listed in the applicable dependency manifest.\n"
    "- All tests must have correct field names matching actual model definitions.\n"
    "- Tests must only test endpoints that exist in the actual API code.\n"
    "- Do not leave placeholder/stub implementation, fake output, or skipped/falsified tests.\n"
)

CODEX_FIX_PROMPT = (
    "You are Codex, an expert software developer. The application you generated "
    "failed the following verification checks:\n\n{errors}\n\n"
    "Please FIX the issues.\n"
    f"{_SHARED_QA_RULES}\n"
    "PROFILE-SPECIFIC RULES:\n{policy_rules}\n"
    "Return ONLY valid JSON with the same structure as before:\n"
    '{{"files": {{"filename.py": "fixed code using \\\\n for newlines", ...}}}}\n'
    "Only include files that need fixing. Do NOT include files that are already correct. "
    "Ensure there are no syntax errors, missing imports, or logical issues."
)


def parse_codex_json(raw_reply):
    cleaned = raw_reply.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    for strategy in [
        lambda: json.loads(cleaned),
        lambda: json.loads(cleaned.replace('\n', '\\n').replace('\r', '\\r')),
        lambda: json.loads(re.sub(r'[\x00-\x1F]+', ' ', cleaned)),
    ]:
        try:
            parsed = strategy()
            if isinstance(parsed, dict) and "files" in parsed:
                return parsed
        except Exception:
            continue

    m = re.search(r'\{.*"files".*\}', raw_reply, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


class QAEngine:
    def __init__(self, project, target_path, project_id, provider, model, temperature, state_callback=None):
        self.project = project
        self.target_path = str(resolve_inside(target_path, "."))
        self.project_id = project_id
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.logs = []
        self.manual_steps = []
        self.state_callback = state_callback
        self.max_repair_attempts = int(project.get("_qa_repair_limit", MAX_REPAIR_ATTEMPTS)) if isinstance(project, dict) else MAX_REPAIR_ATTEMPTS
        self.command_history = []
        self.current_round_commands = []
        self.round_history = []
        self.failure_registry = {}
        self.repair_history = []
        self.previous_round_failures = {}
        self.previous_check_status = {}
        self.needs_credentials = False
        self.needs_human_input = False
        self.policy_groups = select_policy_groups(project, target_path)

    def log(self, msg):
        self.logs.append(msg)
        if "logs" in self.project:
            self.project["logs"].append(msg)

    def set_state(self, state: str, reason: str = ""):
        if self.state_callback:
            try:
                self.state_callback(state, reason)
                return
            except Exception:
                pass
        self.log(f"[PROJECT STATE] {state.replace('_', ' ').title()}")

    def run_command(self, command, cwd=None, timeout=120):
        started = time.time()
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            record = {
                "command": " ".join(str(part) for part in command),
                "working_directory": cwd or self.target_path,
                "exit_code": result.returncode,
                "stdout": _redact_secrets(result.stdout)[-8000:],
                "stderr": _redact_secrets(result.stderr)[-8000:],
                "duration": round(time.time() - started, 3),
            }
            self.command_history.append(record)
            self.current_round_commands.append(record)
            return result
        except subprocess.TimeoutExpired as e:
            record = {
                "command": " ".join(str(part) for part in command),
                "working_directory": cwd or self.target_path,
                "exit_code": "timeout",
                "stdout": _redact_secrets(getattr(e, "stdout", "") or "")[-8000:],
                "stderr": _redact_secrets(getattr(e, "stderr", "") or "")[-8000:],
                "duration": round(time.time() - started, 3),
            }
            self.command_history.append(record)
            self.current_round_commands.append(record)
            raise

    # ─── Stage 1: Syntax check ───────────────────────────────────────────

    def stage_check_syntax(self):
        self.log("[QA Syntax]: Checking source code syntax...")
        errors = []
        for root, fname, fpath in _walk_project_files(self.target_path):
                ext = os.path.splitext(fname)[1]
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    errors.append(f"[{fname}] Read error: {e}")
                    continue

                if fname.endswith(".py"):
                    try:
                        compile(content, fname, "exec")
                    except SyntaxError as e:
                        errors.append(f"[{fname}] SyntaxError line {e.lineno}: {e.msg}")
                elif fname == "Dockerfile":
                    if "FROM" not in content:
                        errors.append("[Dockerfile] Missing FROM instruction")
                    if "CMD" not in content and "ENTRYPOINT" not in content:
                        errors.append("[Dockerfile] Missing CMD or ENTRYPOINT")
                elif fname == "requirements.txt":
                    for line in content.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#") and " " in line and "==" not in line and not line.startswith("git+"):
                            pass

        if errors:
            self.log(f"[QA Syntax]: {len(errors)} issue(s) found.")
            return False, errors
        self.log("[QA Syntax]: All files pass syntax check.")
        return True, []

    # ─── Stage 2: Dependency install ────────────────────────────────────

    def stage_install_deps(self):
        self.log("[QA Deps]: Checking and installing dependencies...")
        req_path = os.path.join(self.target_path, "requirements.txt")
        if not os.path.exists(req_path):
            self.log("[QA Deps]: No requirements.txt found. Skipping.")
            return True, [], []

        errors = []
        manual = []

        try:
            # First try: install all from requirements.txt
            result = self.run_command(
                [sys.executable, "-m", "pip", "install", "-r", req_path],
                cwd=self.target_path,
                timeout=120,
            )
            if result.returncode == 0:
                self.log("[QA Deps]: All dependencies installed successfully.")
                return True, [], []
            # Second try: install packages one by one, skip failures
            self.log("[QA Deps]: Batch install had issues. Trying individual installs...")
            with open(req_path, "r", encoding="utf-8", errors="replace") as f:
                packages = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            failed_one_by_one = []
            for pkg in packages:
                pkg_name = pkg.split("==")[0].split(">=")[0].split("<")[0].strip()
                try:
                    self.run_command(
                        [sys.executable, "-m", "pip", "install", pkg_name],
                        cwd=self.target_path,
                        timeout=60,
                    )
                    if self.command_history[-1]["exit_code"] != 0:
                        raise RuntimeError(f"pip install failed for {pkg_name}")
                except Exception:
                    failed_one_by_one.append(pkg_name)
            if failed_one_by_one:
                for pkg in failed_one_by_one[:5]:
                    manual.append(f"Package '{pkg}' could not be auto-installed. Try: pip install {pkg}")
                errors.append(f"Failed to install individually: {', '.join(failed_one_by_one[:5])}")
                self.log(f"[QA Deps]: {len(failed_one_by_one)} package(s) failed individual install.")
                return False, errors, manual
            self.log("[QA Deps]: All dependencies installed individually.")
            return True, [], []
        except subprocess.TimeoutExpired:
            errors.append("pip install timed out (>120s)")
            manual.append("Dependencies install timed out. Try: pip install -r requirements.txt manually.")
            self.log("[QA Deps]: Timed out.")
            return False, errors, manual
        except FileNotFoundError:
            errors.append("Python not found for pip")
            manual.append("Python is required. Install from https://python.org")
            return False, errors, manual
        except Exception as e:
            errors.append(f"pip error: {e}")
            return False, errors, manual

    # ─── Stage 3: Run syntax check + try-start (Windows-compatible) ──────

    def stage_build_and_run(self):
        self.log("[QA Build]: Checking project structure and doing try-start...")
        errors = []
        manual = []

        # Check requirements.txt exists
        req_path = os.path.join(self.target_path, "requirements.txt")
        main_py = None
        entrypoint_names = ("main.py", "bot.py", "app.py")
        for root, dirs, files in os.walk(self.target_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_QA_DIRS and not d.endswith(".egg-info")]
            for wanted in entrypoint_names:
                if wanted in files:
                    f = wanted
                    main_py = os.path.join(root, f)
                    break
            if main_py:
                break

        if not main_py:
            self.log("[QA Build]: No main.py/bot.py/app.py found — checking for app entry point...")
            for root, dirs, files in os.walk(self.target_path):
                dirs[:] = [d for d in dirs if d not in IGNORED_QA_DIRS and not d.endswith(".egg-info")]
                for f in files:
                    if f.endswith(".py"):
                        with open(os.path.join(root, f), "r", encoding="utf-8", errors="replace") as fh:
                            c = fh.read()
                            if "FastAPI" in c or "uvicorn" in c:
                                main_py = os.path.join(root, f)
                                break
                if main_py:
                    break

        if main_py:
            try:
                # Quick syntax check of main entry point
                with open(main_py, "r", encoding="utf-8", errors="replace") as f:
                    compile(f.read(), os.path.basename(main_py), "exec")
                self.log(f"[QA Build]: {os.path.relpath(main_py, self.target_path)} passes syntax check.")
            except SyntaxError as e:
                errors.append(f"Syntax error in entry point {os.path.basename(main_py)}: {e}")
        else:
            self.log("[QA Build]: No Python entry point found. Skipping run test.")

        if errors:
            return False, errors, manual

        test_files = []
        for root, _fname, fpath in _walk_project_files(self.target_path):
            fname = os.path.basename(fpath)
            rel = os.path.relpath(fpath, self.target_path)
            if fname.startswith("test_") and fname.endswith(".py") or rel.startswith("tests" + os.sep) and fname.endswith(".py"):
                test_files.append(rel)
        if test_files:
            try:
                result = self.run_command(
                    [sys.executable, "-m", "pytest", "-q"],
                    cwd=self.target_path,
                    timeout=180,
                )
                if result.returncode != 0:
                    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                    tail = "\n".join(output.splitlines()[-40:])
                    errors.append(f"pytest failed ({len(test_files)} test files):\n{tail}")
                    self.log(f"[QA Build]: pytest failed on {len(test_files)} test file(s).")
                    return False, errors, manual
                self.log(f"[QA Build]: pytest passed ({len(test_files)} test file(s)).")
            except subprocess.TimeoutExpired:
                errors.append("pytest timed out (>180s)")
                self.log("[QA Build]: pytest timed out.")
                return False, errors, manual
            except Exception as e:
                errors.append(f"pytest could not run: {e}")
                self.log(f"[QA Build]: pytest could not run: {e}")
                return False, errors, manual
        self.log("[QA Build]: Project structure OK.")
        return True, [], []

    # ─── Stage 4: Delivery readiness ─────────────────────────────────────

    def stage_delivery_readiness(self):
        self.log("[QA Delivery]: Checking delivery readiness...")
        errors = []
        files = _project_files(self.target_path)
        normalized = {p.replace("\\", "/") for p in files}
        policies = set(self.policy_groups)

        has_python = "python" in policies and any(p.endswith(".py") for p in normalized)
        has_fastapi = False
        has_package_json = bool(policies.intersection({"node", "react_vite"})) and "package.json" in normalized
        root_readme = os.path.join(self.target_path, "README.md")

        if not os.path.isfile(root_readme):
            errors.append("Missing root README.md with install/run/test instructions")
            readme = ""
        else:
            readme = _read_text(root_readme)
            readme_lower = readme.lower()
            if not any(token in readme_lower for token in ("install", "установка", "pip install", "npm install")):
                errors.append("README.md is missing install instructions")
            if not any(token in readme_lower for token in ("run", "запуск", "uvicorn", "python", "npm run")):
                errors.append("README.md is missing run instructions")
            if "fastapi" in policies and has_python and "uvicorn" in readme_lower and "python -m uvicorn" not in readme_lower:
                errors.append("README.md should use 'python -m uvicorn ...' so it works when uvicorn.exe is not on PATH")

        source_stub_patterns = (
            "# main code here",
            "# test code here",
            "todo: implement",
            "not implemented",
            "pass  # TODO",
            "raise NotImplementedError",
        )
        for rel in normalized:
            if not rel.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css")):
                continue
            if rel.startswith("tests/") or rel.startswith("test_"):
                continue
            path = os.path.join(self.target_path, rel.replace("/", os.sep))
            try:
                content = _read_text(path)
            except Exception as e:
                errors.append(f"Cannot read source file {rel}: {e}")
                continue
            lower = content.lower()
            if "fastapi" in policies and "fastapi(" in lower:
                has_fastapi = True
            for pattern in source_stub_patterns:
                if pattern.lower() in lower:
                    errors.append(f"Stub/placeholder implementation found in {rel}: {pattern}")
                    break

        if "python" in policies and has_python and "requirements.txt" not in normalized:
            errors.append("Python project is missing root requirements.txt")
        if "fastapi" in policies and has_fastapi and "main.py" not in normalized and "app.py" not in normalized:
            errors.append("FastAPI project must expose a root entrypoint (main.py or app.py)")

        security_docs = []
        for rel in normalized:
            if rel.lower().endswith(("readme.md", "security_audit.md", "security.md")):
                security_docs.append(rel)
        if policies.intersection({"fastapi", "python"}):
            source_text = "\n".join(
                _read_text(os.path.join(self.target_path, rel.replace("/", os.sep)))
                for rel in normalized
                if rel.endswith(".py")
            )
            cors_enabled = "CORSMiddleware" in source_text or "add_middleware(" in source_text and "allow_origins" in source_text
            if cors_enabled:
                if 'allow_origins=["*"]' in source_text and "allow_credentials=True" in source_text:
                    errors.append("Unsafe CORS configuration: wildcard origins with credentials enabled")
            for rel in security_docs:
                doc = _read_text(os.path.join(self.target_path, rel.replace("/", os.sep))).lower()
                if "cors" in doc and any(phrase in doc for phrase in ("cors не включ", "cors is not", "no cors", "cors не установлен")) and cors_enabled:
                    errors.append(f"Documentation contradicts implementation: {rel} says CORS is disabled but code enables CORS")

        if has_package_json:
            try:
                with open(os.path.join(self.target_path, "package.json"), "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
                if "build" in scripts:
                    if not os.path.isdir(os.path.join(self.target_path, "node_modules")):
                        npm_install = self.run_command([_npm_command(), "install"], cwd=self.target_path, timeout=180)
                        if npm_install.returncode != 0:
                            tail = "\n".join(((npm_install.stdout or "") + "\n" + (npm_install.stderr or "")).splitlines()[-30:])
                            errors.append(f"npm install failed:\n{tail}")
                    if not any(e.startswith("npm install failed") for e in errors):
                        npm_build = self.run_command([_npm_command(), "run", "build"], cwd=self.target_path, timeout=180)
                        if npm_build.returncode != 0:
                            tail = "\n".join(((npm_build.stdout or "") + "\n" + (npm_build.stderr or "")).splitlines()[-40:])
                            errors.append(f"npm run build failed:\n{tail}")
                        else:
                            self.log("[QA Delivery]: npm run build passed.")
            except Exception as e:
                errors.append(f"package.json/build verification failed: {e}")

        if errors:
            self.log(f"[QA Delivery]: {len(errors)} issue(s) found.")
            return False, errors, []
        self.log("[QA Delivery]: Delivery readiness checks passed.")
        return True, [], []

    # ─── Stage 5: Profile-specific checks ────────────────────────────────

    def detect_profile(self):
        profiles = set(_project_profiles(self.project, self.target_path))
        if "fastapi" in profiles:
            return "fastapi"
        if "telegram_bot" in profiles:
            return "telegram_bot"
        if profiles.intersection({"react_frontend", "vite_frontend", "node_project", "node_backend"}):
            return "node_app"
        if "static_website" in profiles:
            return "static_web"
        if profiles.intersection({"python_application", "python_cli"}):
            return "python_app"
        files = set(p.replace("\\", "/") for p in _project_files(self.target_path))
        if "bot.py" in files and _has_any_file(self.target_path, (".env.example", "docker-compose.yml")):
            try:
                bot_text = _read_text(os.path.join(self.target_path, "bot.py"))
            except Exception:
                bot_text = ""
            if "aiogram" in bot_text or any(path.startswith("handlers/") for path in files):
                return "telegram_bot"
        if "package.json" in files:
            return "node_app"
        if "main.py" in files:
            return "python_app"
        return "generic"

    def stage_profile_checks(self):
        profile = self.detect_profile()
        self.log(f"[QA Profile]: Detected {profile}. Policy groups: {', '.join(self.policy_groups)}.")
        if "telegram" in self.policy_groups:
            return self._stage_telegram_bot_checks()
        return True, [], []

    def _stage_telegram_bot_checks(self):
        errors = []

        env_example = os.path.join(self.target_path, ".env.example")
        gitignore = os.path.join(self.target_path, ".gitignore")
        readme = os.path.join(self.target_path, "README.md")

        if not os.path.isfile(env_example):
            errors.append("Telegram bot is missing .env.example")
        else:
            env_text = _read_text(env_example)
            if "BOT_TOKEN=" not in env_text:
                errors.append(".env.example must document BOT_TOKEN")
            token_match = re.search(r"(?m)^BOT_TOKEN\s*=\s*(.+)$", env_text)
            if token_match:
                token_value = token_match.group(1).strip()
                if re.match(r"^\d{7,}:[A-Za-z0-9_-]{20,}$", token_value):
                    errors.append(".env.example contains a real-looking Telegram token")

        if not os.path.isfile(gitignore):
            errors.append("Telegram bot is missing .gitignore")
        elif ".env" not in _read_text(gitignore):
            errors.append(".gitignore must include .env")

        if os.path.isfile(readme):
            readme_text = _read_text(readme).lower()
            if "python bot.py" not in readme_text and "docker compose" not in readme_text:
                errors.append("README.md must include a real Telegram bot run command")
            if "docker-compose" in readme_text and "docker compose" not in readme_text:
                errors.append("README.md should use 'docker compose', not legacy-only 'docker-compose'")

        smoke_code = r'''
import asyncio
import tempfile
from pathlib import Path

from keyboards.inline import category_keyboard, joke_keyboard
from services.database import Database
from services.joke_service import JokeService
from utils.text import is_valid_joke, joke_hash

async def main():
    keys = []
    for row in category_keyboard().inline_keyboard:
        keys.extend(button.callback_data for button in row if button.callback_data)
    for row in joke_keyboard(joke_hash("quality smoke joke"), "dark").inline_keyboard:
        keys.extend(button.callback_data for button in row if button.callback_data)
    too_long = [key for key in keys if len(key.encode("utf-8")) > 64]
    assert not too_long, too_long

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "bot.db")
        await db.init()
        service = JokeService(db)
        dark_text, dark_hash = await service.get_joke(1001, category="dark")
        prog_text, prog_hash = await service.get_joke(1002, category="programmers")
        assert is_valid_joke(dark_text)
        assert is_valid_joke(prog_text)
        assert dark_text != prog_text
        await db.increment_stat(dark_hash, "likes", dark_text)
        top_text = await service.top()
        assert "Топ анекдотов" in top_text
        assert "Топ статистики" not in top_text

asyncio.run(main())
'''
        try:
            result = self.run_command(
                [sys.executable, "-c", smoke_code],
                cwd=self.target_path,
                timeout=120,
            )
            if result.returncode != 0:
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                tail = "\n".join(output.splitlines()[-40:])
                errors.append(f"Telegram bot smoke checks failed:\n{tail}")
            else:
                self.log("[QA Profile]: Telegram bot smoke checks passed.")
        except subprocess.TimeoutExpired:
            errors.append("Telegram bot smoke checks timed out")
        except Exception as e:
            errors.append(f"Telegram bot smoke checks could not run: {e}")

        if errors:
            self.log(f"[QA Profile]: {len(errors)} issue(s) found.")
            return False, errors, []
        return True, [], []

    # ─── Stage 6: Verify generated files are all non-empty ─────────────

    def stage_verify_files(self):
        self.log("[QA Verify]: Checking all generated files...")
        errors = []
        file_count = 0
        empty_files = []
        for root, fname, fpath in _walk_project_files(self.target_path):
                rel = os.path.relpath(fpath, self.target_path)
                size = os.path.getsize(fpath)
                file_count += 1
                # Skip __init__.py — these are intentional empty package markers
                if fname == "__init__.py":
                    continue
                if size == 0:
                    empty_files.append(rel)
                elif size < 20:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read().strip()
                    if len(content) < 10:
                        empty_files.append(rel)
        if empty_files:
            errors.append(f"Empty or near-empty files: {', '.join(empty_files)}")
        self.log(f"[QA Verify]: {file_count} files checked. {len(empty_files)} empty.")
        if errors:
            return False, errors, []
        return True, [], []

    def _failure_records(self, check, errors, round_num):
        records = []
        for message in errors:
            fingerprint = _failure_fingerprint(check, message)
            record = {
                "check": check,
                "message": _redact_secrets(message),
                "fingerprint": fingerprint,
                "files_likely_involved": _likely_files(message),
                "round": round_num,
            }
            info = self.failure_registry.setdefault(
                fingerprint,
                {
                    "fingerprint": fingerprint,
                    "error_signature": _normalize_failure_text(message),
                    "first_seen_round": round_num,
                    "last_seen_round": round_num,
                    "occurrence_count": 0,
                    "involved_checks": [],
                    "involved_files": [],
                    "previous_repair_attempts": [],
                },
            )
            info["last_seen_round"] = round_num
            info["occurrence_count"] += 1
            if check not in info["involved_checks"]:
                info["involved_checks"].append(check)
            for fname in record["files_likely_involved"]:
                if fname not in info["involved_files"]:
                    info["involved_files"].append(fname)
            records.append(record)
        return records

    def _upsert_qa_failure_issues(self, failures):
        if not failures:
            return []
        issues = self.project.setdefault("issues", [])
        updated = []
        by_fingerprint = {
            issue.get("evidence", {}).get("fingerprint"): issue
            for issue in issues
            if isinstance(issue, dict) and issue.get("source") == "qa_engine" and isinstance(issue.get("evidence"), dict)
        }
        for failure in failures:
            fingerprint = failure.get("fingerprint", "")
            registry_entry = self.failure_registry.get(fingerprint, {})
            repair_history = list(registry_entry.get("previous_repair_attempts", []))
            evidence = {
                "fingerprint": fingerprint,
                "error_signature": registry_entry.get("error_signature", _normalize_failure_text(failure.get("message", ""))),
                "failure": dict(failure),
                "failure_registry": dict(registry_entry),
                "repair_history": repair_history,
            }
            existing = by_fingerprint.get(fingerprint)
            if existing:
                existing["severity"] = _qa_issue_severity(failure.get("check", ""))
                existing["title"] = str(failure.get("message", "QA failure"))[:120]
                existing["evidence"] = evidence
                existing["reproduction"] = [failure.get("message", "")]
                existing["owner"] = "codex"
                existing["status"] = "open"
                existing["attempts"] = len(repair_history)
                existing["verification_method"] = failure.get("check", "")
                updated.append(existing)
                continue

            issue = Issue(
                id=_qa_issue_id(fingerprint),
                source="qa_engine",
                severity=_qa_issue_severity(failure.get("check", "")),
                requirement_id="",
                criterion_id="",
                title=str(failure.get("message", "QA failure"))[:120],
                evidence=evidence,
                reproduction=[failure.get("message", "")],
                owner="codex",
                status="open",
                attempts=len(repair_history),
                verification_method=failure.get("check", ""),
            ).to_dict()
            issues.append(issue)
            by_fingerprint[fingerprint] = issue
            updated.append(issue)
        if updated:
            persist_project_state(self.project, self.target_path)
        return updated

    def _close_fixed_qa_issues(self, comparison):
        fixed = set((comparison or {}).get("fixed", []))
        if not fixed:
            return []
        closed = []
        for issue in self.project.get("issues", []):
            if not isinstance(issue, dict) or issue.get("source") != "qa_engine":
                continue
            evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
            fingerprint = evidence.get("fingerprint")
            if fingerprint not in fixed or issue.get("status") == "closed":
                continue
            resolution = {
                "source": "qa_engine",
                "status": "passed",
                "summary": "QA verifier rerun no longer reproduced this failure fingerprint.",
                "round": comparison.get("round"),
                "fingerprint": fingerprint,
            }
            evidence["resolution"] = resolution
            issue["evidence"] = evidence
            issue["status"] = "closed"
            issue["attempts"] = len(evidence.get("repair_history", []))
            closed.append(issue)
        if closed:
            persist_project_state(self.project, self.target_path)
        return closed

    def _compare_rounds(self, round_num, current_failures, check_status):
        current = {item["fingerprint"]: item for item in current_failures}
        previous = self.previous_round_failures
        fixed = sorted(set(previous) - set(current))
        new = sorted(set(current) - set(previous))
        unchanged = sorted(set(previous) & set(current))
        regressions = []

        for check, passed in check_status.items():
            if not passed and self.previous_check_status.get(check) is True:
                self.log(f"[REGRESSION] Previously passing check now fails: {check}")
                regressions.append(check)
        for fp in fixed:
            prev = previous[fp]
            self.log(f"[FIXED] {prev.get('check')} issue resolved after repair: {fp}")
        for fp in unchanged:
            cur = current[fp]
            count = self.failure_registry.get(fp, {}).get("occurrence_count", 1)
            self.log(f"[UNCHANGED FAILURE] Same issue after repair {max(0, round_num - 1)}: {cur.get('check')} {fp} (seen {count}x)")
        for fp in new:
            if previous:
                cur = current[fp]
                self.log(f"[QA FAILURE] New failure detected: {cur.get('check')} {fp}")

        comparison = {
            "round": round_num,
            "fixed": fixed,
            "new_failures": new,
            "unchanged_failures": unchanged,
            "regressions": regressions,
        }
        self.previous_round_failures = current
        self.previous_check_status = dict(check_status)
        return comparison

    def _is_credential_blocker(self, failures):
        credential_names = [c.get("name", "") for c in self.project.get("project_spec", {}).get("required_credentials", [])]
        if not credential_names:
            return False
        joined = "\n".join(f.get("message", "") for f in failures).lower()
        credential_terms = ("api key", "apikey", "token", "secret", "credential", "unauthorized", "401", "403", "not set", "missing")
        return any(term in joined for term in credential_terms)

    def _build_repair_report(self, round_num, failures, comparison):
        relevant_criteria = []
        failed_criteria = []
        failure_text = "\n".join(f.get("message", "") for f in failures).lower()
        for criterion in self.project.get("acceptance_criteria", []):
            blob = f"{criterion.get('id')} {criterion.get('title')} {criterion.get('description')} {criterion.get('trace', '')}".lower()
            if criterion.get("status") == "failed":
                failed_criteria.append(criterion)
            if any(token and token in failure_text for token in re.findall(r"[a-zA-ZА-Яа-я0-9_/-]{4,}", blob)[:20]):
                relevant_criteria.append(criterion)
        if not relevant_criteria:
            relevant_criteria = self.project.get("acceptance_criteria", [])[:20]

        report = {
            "repair_round": round_num,
            "project_directory": self.target_path,
            "original_project_goal": self.project.get("project_spec", {}).get("project_goal") or self.project.get("title", ""),
            "project_specification": self.project.get("project_spec", {}),
            "detected_profiles": self.project.get("project_profiles") or self.project.get("project_spec", {}).get("project_profiles", []),
            "relevant_acceptance_criteria": relevant_criteria[:30],
            "failed_acceptance_criteria": failed_criteria[:30],
            "failed_checks": failures,
            "commands_executed": list(self.current_round_commands),
            "working_directories": sorted({cmd.get("working_directory", "") for cmd in self.current_round_commands if cmd.get("working_directory")}),
            "exit_codes": [cmd.get("exit_code") for cmd in self.current_round_commands],
            "stdout": _redact_secrets("\n".join(cmd.get("stdout", "") for cmd in self.current_round_commands))[-12000:],
            "stderr": _redact_secrets("\n".join(cmd.get("stderr", "") for cmd in self.current_round_commands))[-12000:],
            "files_likely_involved": sorted({fname for failure in failures for fname in failure.get("files_likely_involved", [])}),
            "previous_repair_history": list(self.repair_history),
            "failure_fingerprints": [self.failure_registry.get(f["fingerprint"], {}) for f in failures],
            "regression_comparison": comparison,
        }

        max_occurrence = max((self.failure_registry.get(f["fingerprint"], {}).get("occurrence_count", 1) for f in failures), default=1)
        if max_occurrence >= 3:
            report["repair_strategy_note"] = "This issue has survived multiple repair attempts. Do not repeat the previous approach. Reinspect the architecture and determine the root cause."
        elif max_occurrence >= 2:
            report["repair_strategy_note"] = "This issue survived a previous fix. Inspect why the prior approach did not resolve it before editing."
        else:
            report["repair_strategy_note"] = "First repair attempt for these failures. Fix root causes with the smallest correct changes."
        return report

    # ─── Main QA cycle ──────────────────────────────────────────────────

    def run(self):
        all_errors = []
        all_manual = []
        success = False
        self.project["_qa_active"] = True
        self.project["_repair_active"] = False

        self.set_state("verifying")
        gap_state, gap_manual = self._requirement_gap_blocker()
        if gap_state:
            self.project["_qa_active"] = False
            if gap_state == "needs_credentials":
                self.needs_credentials = True
            else:
                self.needs_human_input = True
            all_manual.extend(gap_manual)
            self.set_state(gap_state)
            self.log(f"[REQUIREMENT GAPS]: QA blocked by {gap_state.replace('_', ' ')}. OpenCode repair will not run for requirement gaps.")
            return {
                "success": False,
                "rounds_completed": 0,
                "total_errors": 0,
                "manual_steps": list(set(all_manual)),
                "errors": [],
                "round_history": self.round_history,
                "failure_registry": self.failure_registry,
                "repair_history": self.repair_history,
                "policy_groups": self.policy_groups,
                "needs_credentials": self.needs_credentials,
                "needs_human_input": self.needs_human_input,
            }
        if self.project.get("acceptance_criteria"):
            self.log(f"[ACCEPTANCE CRITERIA]: {len(self.project.get('acceptance_criteria', []))} criteria available for QA traceability.")
        if self.project.get("qa_plan"):
            levels = self.project.get("qa_plan", {}).get("levels", [])
            self.log(f"[QA PLAN]: {len(levels)} planned QA level(s) available.")

        total_rounds = self.max_repair_attempts + 1
        for round_num in range(1, total_rounds + 1):
            if self.project.get("cancel_requested") or self.project.get("status") == "cancelled":
                self.log("[QA]: Cancellation requested. QA loop stopped.")
                self.project["_qa_active"] = False
                self.project["_repair_active"] = False
                return {
                    "success": False,
                    "rounds_completed": round_num - 1,
                    "total_errors": len(all_errors),
                    "manual_steps": list(set(all_manual)),
                    "errors": all_errors,
                }

            self.current_round_commands = []
            self.log(f"\n{'='*50}")
            self.log(f"[QA ROUND {round_num}]: Starting full verification ({round_num}/{total_rounds})...")
            self.log(f"{'='*50}")

            round_errors = []
            round_failures = []
            check_status = {}

            # Stage 1: Syntax
            ok, syn_err = self.stage_check_syntax()
            check_status["syntax"] = ok
            if not ok:
                round_errors.extend(syn_err)
                all_errors.extend(syn_err)
                round_failures.extend(self._failure_records("syntax", syn_err, round_num))

            # Stage 2: Deps
            ok, dep_err, dep_manual = self.stage_install_deps()
            check_status["dependencies"] = ok
            if not ok:
                round_errors.extend(dep_err)
                all_errors.extend(dep_err)
                all_manual.extend(dep_manual)
                round_failures.extend(self._failure_records("dependencies", dep_err, round_num))

            # Stage 3: Build + run check (Windows-compatible, no Docker)
            ok, build_err, _ = self.stage_build_and_run()
            check_status["build_and_tests"] = ok
            if not ok:
                round_errors.extend(build_err)
                all_errors.extend(build_err)
                round_failures.extend(self._failure_records("build_and_tests", build_err, round_num))

            # Stage 4: Verify files
            ok, delivery_err, _ = self.stage_delivery_readiness()
            check_status["delivery_readiness"] = ok
            if not ok:
                round_errors.extend(delivery_err)
                all_errors.extend(delivery_err)
                round_failures.extend(self._failure_records("delivery_readiness", delivery_err, round_num))

            # Stage 5: Profile-specific checks
            ok, profile_err, _ = self.stage_profile_checks()
            check_status["profile_checks"] = ok
            if not ok:
                round_errors.extend(profile_err)
                all_errors.extend(profile_err)
                round_failures.extend(self._failure_records("profile_checks", profile_err, round_num))

            # Stage 6: Verify files
            ok, verify_err, _ = self.stage_verify_files()
            check_status["file_verification"] = ok
            if not ok:
                round_errors.extend(verify_err)
                all_errors.extend(verify_err)
                round_failures.extend(self._failure_records("file_verification", verify_err, round_num))

            comparison = self._compare_rounds(round_num, round_failures, check_status)
            self._close_fixed_qa_issues(comparison)
            self._upsert_qa_failure_issues(round_failures)
            self.round_history.append({
                "round": round_num,
                "success": not bool(round_errors),
                "failures": round_failures,
                "check_status": check_status,
                "comparison": comparison,
                "commands": list(self.current_round_commands),
            })

            if not round_errors:
                success = True
                self.log(f"\n[QA ROUND {round_num}]: ✅ ALL VERIFICATIONS PASSED!")
                break

            if self._is_credential_blocker(round_failures):
                self.needs_credentials = True
                self.set_state("needs_credentials")
                self.log("[QA FAILURE]: Missing or invalid external credential detected. OpenCode repair will not be retried for credentials.")
                all_manual.append("Provide required external credentials, then retry verification.")
                break

            if round_num <= self.max_repair_attempts:
                repair_report = self._build_repair_report(round_num, round_failures, comparison)
                self.log(f"\n[QA FAILURE]: {len(round_errors)} issue(s). Structured repair report created.")
                for err in round_errors[:10]:
                    self.log(f"[QA Error]: {err}")
                if self.project.get("cancel_requested") or self.project.get("status") == "cancelled":
                    self.log("[QA]: Cancellation requested before repair. Skipping fix attempt.")
                    break
                self.set_state("repairing")
                self.log(f"[OPENCODE FIX {round_num}]: Starting direct project repair.")
                self.project["_repair_active"] = True
                try:
                    fix_result = self._request_codex_fix(round_errors, repair_report)
                finally:
                    self.project["_repair_active"] = False
                repair_entry = {
                    "repair_round": round_num,
                    "success": bool(fix_result),
                    "fingerprints": [f.get("fingerprint") for f in round_failures],
                    "strategy_note": repair_report.get("repair_strategy_note", ""),
                }
                self.repair_history.append(repair_entry)
                for failure in round_failures:
                    info = self.failure_registry.get(failure["fingerprint"])
                    if info is not None:
                        info.setdefault("previous_repair_attempts", []).append(repair_entry)
                self._upsert_qa_failure_issues(round_failures)
                if not fix_result:
                    self.log("[QA]: Direct repair produced no changes. Aborting further attempts.")
                    break
                if fix_result.get(OPENCODE_FIX_APPLIED):
                    changed_count = fix_result.get("changed_files", "?")
                    self.log(f"[QA]: OpenCode modified {changed_count} file(s) on disk. Restarting full QA from the beginning.")
                    self.set_state("verifying")
                    continue
                self.log("[LEGACY JSON FALLBACK] Writing agent-provided file contents to disk.")
                for fname, content in fix_result.items():
                    if fname == OPENCODE_FIX_APPLIED:
                        continue
                    if not _is_safe_fix_path(fname):
                        self.log(f"[QA]: Ignored unsafe/cache fix path: {fname}")
                        continue
                    fpath = os.path.join(self.target_path, fname)
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(content)
                    self.log(f"[QA]: Fixed file written: {fname}")
                self.set_state("verifying")
            else:
                self.log(f"\n[QA]: Repair limit reached ({self.max_repair_attempts}). Some issues remain.")
                for err in round_errors[:20]:
                    self.log(f"[QA Remaining]: {err}")

        self.project["_qa_active"] = False
        self.project["_repair_active"] = False
        return {
            "success": success,
            "rounds_completed": round_num,
            "total_errors": len(all_errors),
            "manual_steps": list(set(all_manual)),
            "errors": all_errors,
            "round_history": self.round_history,
            "failure_registry": self.failure_registry,
            "repair_history": self.repair_history,
            "issues": self.project.get("issues", []),
            "policy_groups": self.policy_groups,
            "needs_credentials": self.needs_credentials,
            "needs_human_input": self.needs_human_input,
        }

    def _requirement_gap_blocker(self):
        spec = self.project.get("project_spec") if isinstance(self.project.get("project_spec"), dict) else {}
        gaps = spec.get("requirement_gaps", []) if isinstance(spec, dict) else []
        if not isinstance(gaps, list):
            return None, []
        assumptions = [gap for gap in gaps if isinstance(gap, dict) and gap.get("severity") not in ("blocker", "critical")]
        if assumptions:
            self.project["requirement_assumptions"] = assumptions
            self.log(f"[REQUIREMENT GAPS]: Continuing with {len(assumptions)} recorded non-critical assumption(s).")
        for category, state in (("missing_credential", "needs_credentials"), ("product_runtime_mismatch", "needs_human_input"), ("ambiguity", "needs_human_input")):
            blockers = [
                gap for gap in gaps
                if isinstance(gap, dict)
                and gap.get("category") == category
                and (category == "missing_credential" or gap.get("severity") in ("critical", "blocker"))
            ]
            if blockers:
                steps = [gap.get("suggested_question", "Resolve requirement gap before verification.") for gap in blockers]
                self.project["blocking_requirement_gaps"] = blockers
                return state, steps
        return None, []

    def _request_codex_fix(self, errors, repair_report=None):
        if self.project.get("cancel_requested") or self.project.get("status") == "cancelled":
            self.log("[QA]: Cancellation requested. Repair request aborted.")
            return None

        error_text = "\n".join(f"- {e}" for e in errors[:20])
        if len(errors) > 20:
            error_text += f"\n... and {len(errors) - 20} more issues"

        opencode_result = self._request_opencode_fix(errors, error_text, repair_report)
        if opencode_result:
            self.log(f"[QA OpenCode Fix] Direct repair applied — {opencode_result.get('changed_files', '?')} file(s) changed. Skipping legacy fallback.")
            return opencode_result

        self.log("[LEGACY JSON FALLBACK] OpenCode direct repair produced no meaningful disk changes. Attempting JSON-based repair.")

        prompt = CODEX_FIX_PROMPT.format(errors=error_text, policy_rules=policy_prompt_rules(self.policy_groups))
        # Retry up to 2 times on transient AI errors
        for attempt in range(2):
            try:
                raw = ask_studio_ai_with_history(
                    provider=self.provider,
                    model_name=self.model,
                    system_prompt="You are Codex, a code-fixing AI. Return ONLY valid JSON.",
                    chat_history=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                )
                parsed = parse_codex_json(raw)
                if parsed and "files" in parsed:
                    for fname in list(parsed["files"].keys()):
                        content = parsed["files"][fname]
                        if fname.endswith(".py"):
                            try:
                                import black
                                content = black.format_str(content, mode=black.Mode())
                            except Exception:
                                pass
                        parsed["files"][fname] = content.strip()
                    return parsed["files"]
            except Exception as e:
                if attempt == 0:
                    self.log(f"[QA Legacy Fix]: Retry after error: {e}")
                    continue
                self.log(f"[QA Legacy Fix]: Exception: {e}")
                return None
        self.log(f"[QA Legacy Fix]: Failed to parse fix JSON.")
        return None

    def _request_opencode_fix(self, errors, error_text, repair_report=None):
        """Run one repair attempt and always retain a safe, structured outcome."""
        started_at = datetime.now(timezone.utc)
        issue_summaries = []
        for item in errors or []:
            if isinstance(item, dict):
                issue_summaries.append({"id": str(item.get("id") or ""), "category": str(item.get("category") or item.get("source") or "")})
            else:
                issue_summaries.append({"id": "", "category": "qa_failure"})
        selected = [item for index, item in enumerate(issue_summaries) if not isinstance((errors or [])[index], dict) or (errors or [])[index].get("status", "open") == "open"]
        result = RepairAttemptResult({
            "attempt_id": f"repair-{uuid.uuid4().hex}", "success": False,
            "status": "issue_not_selected", "failure_stage": "issue_selection", "error_category": "",
            "issues_received": issue_summaries, "repairable_issues_selected": selected,
            "issue_ids": [item["id"] for item in selected if item["id"]],
            "prompt_built": False, "prompt_length": 0, "prompt_hash": "",
            "working_directory": os.path.abspath(self.target_path), "executable": "", "executable_found": False,
            "opencode_version": "", "transport": "opencode_cli", "command_shape": [], "model": self.model,
            "task_file_argument": "", "task_file_resolved_path": "", "task_file_exists": False,
            "subprocess_started": False, "started_at": started_at.isoformat(), "finished_at": "",
            "duration_seconds": 0.0, "timeout": False, "timeout_seconds": None, "exit_code": None,
            "stdout_length": 0, "stderr_length": 0, "stdout_summary": "", "stderr_summary": "",
            "exception_type": "", "exception_summary": "", "snapshot_before": {}, "snapshot_after": {},
            "filesystem_changes_detected": False, "changed_files": [], "meaningful_changes_detected": False,
            "textual_result_present": False, "diagnostics": {},
        })
        try:
            initial_snapshot = _snapshot_project_files(self.target_path)
            result["snapshot_before"] = initial_snapshot
            result["snapshot_after"] = initial_snapshot
        except Exception as exc:
            result["diagnostics"]["snapshot_error"] = _safe_repair_text(str(exc))

        def finish():
            finished = datetime.now(timezone.utc)
            result["finished_at"] = finished.isoformat()
            result["duration_seconds"] = round((finished - started_at).total_seconds(), 6)
            self.project.setdefault("repair_attempts", []).append(dict(result))
            try:
                persist_project_state(self.project, self.target_path)
            except Exception as exc:
                result["diagnostics"]["persistence_error"] = _safe_repair_text(str(exc))
            return result

        if not selected:
            self.log("[QA OpenCode Fix]: No repairable issues selected.")
            return finish()
        try:
            from opencode_bridge import get_bridge
        except Exception as e:
            self.log(f"[QA OpenCode Fix]: Bridge unavailable: {e}")
            result.update(status="subprocess_start_failed", failure_stage="bridge_import", error_category="bridge_unavailable", exception_type=type(e).__name__, exception_summary=_safe_repair_text(str(e)))
            return finish()

        criteria_lines = []
        for criterion in self.project.get("acceptance_criteria", [])[:30]:
            criteria_lines.append(
                f"- {criterion.get('id')}: {criterion.get('title')} | "
                f"Verify: {criterion.get('verification_method')} | Expected: {criterion.get('expected_result')}"
            )
        criteria_text = "\n".join(criteria_lines) if criteria_lines else "No acceptance criteria stored."

        try:
            report_json = json.dumps(repair_report or {}, ensure_ascii=False, indent=2)
            profile_rules = policy_prompt_rules(self.policy_groups)
            issue_report = (
            "You are repairing an existing software project. Inspect the actual files before changing anything. "
            "Fix the root cause of the failures. Do not remove required functionality. Do not weaken, delete, skip, or falsify tests only to obtain a passing result. "
            "Do not fake outputs. Do not remove difficult requirements. Make the smallest correct fix. Edit the real files directly. "
            "Perform local verification where practical.\n\n"
            "FreelancerStudio QA failed. Fix the project in place and write changes to disk.\n\n"
            f"Project: {self.project.get('name') or self.project_id}\n"
            f"Project path: {self.target_path}\n\n"
            "Structured repair report:\n"
            f"{report_json}\n\n"
            "Acceptance criteria that must remain satisfied:\n"
            f"{criteria_text}\n\n"
            "Failed checks:\n"
            f"{error_text}\n\n"
            "Requirements:\n"
            "- Use the existing project structure; do not create unrelated duplicate apps.\n"
            "- Fix root causes, not just tests.\n"
            "- Do not delete, weaken, skip, or falsify tests because they fail.\n"
            "- Preserve every required feature and explicit constraint from the structured specification.\n"
            "- Keep generated/cache/build artifacts out of fixes.\n"
            "- Ensure README/dependency/run/test instructions match the actual app.\n"
            "- Stop only when the product is runnable and the listed QA failures are resolved.\n"
            "\nProfile-specific rules selected for this project:\n"
                f"{profile_rules}"
            )
            result["prompt_built"] = True
            result["prompt_length"] = len(issue_report)
            result["prompt_hash"] = hashlib.sha256(issue_report.encode("utf-8")).hexdigest()
        except Exception as e:
            result.update(status="prompt_build_failed", failure_stage="prompt_build", error_category="prompt_serialization", exception_type=type(e).__name__, exception_summary=_safe_repair_text(str(e)))
            return finish()
        try:
            if self.project.get("cancel_requested") or self.project.get("status") == "cancelled":
                self.log("[QA OpenCode Fix]: Cancelled before OpenCode repair start.")
                result.update(status="issue_not_selected", failure_stage="cancelled", error_category="cancelled")
                return finish()
            bridge = get_bridge()
            executable = getattr(bridge, "_binary", None) or shutil.which("opencode.cmd") or shutil.which("opencode") or ""
            result["executable"] = _safe_repair_text(str(executable))
            result["executable_found"] = bool(executable)
            if not bridge.ensure_running(workdir=self.target_path):
                self.log("[QA OpenCode Fix]: OpenCode is unavailable or not authenticated.")
                result.update(status="executable_not_found" if not executable else "subprocess_start_failed", failure_stage="bridge_start", error_category="executable_not_found" if not executable else "bridge_unavailable")
                return finish()

            snapshot = _snapshot_project_files(self.target_path)
            result["snapshot_before"] = snapshot
            self.log(f"[QA OpenCode Fix] Snapshot captured: {len(snapshot)} relevant files")
            self.log("[QA OpenCode Fix]: Starting OpenCode repair task...")

            result_from_bridge = bridge.execute_fix_task(
                project_dir=self.target_path,
                issues=issue_report,
                log_callback=lambda msg: self.log(msg.replace("[Codex]", "[OpenCode]")),
            )

            after_snapshot = _snapshot_project_files(self.target_path)
            changed = _compare_snapshots(snapshot, after_snapshot)
            result["snapshot_after"] = after_snapshot
            result["changed_files"] = changed
            result["filesystem_changes_detected"] = bool(changed)
            result["meaningful_changes_detected"] = bool(changed)

            timed_out = bool(result_from_bridge.get("timed_out", False))
            session_id = result_from_bridge.get("session_id") or "?"
            success = bool(result_from_bridge.get("success", False))
            error = result_from_bridge.get("error")
            output = str(result_from_bridge.get("summary") or "")
            result.update({"timeout": timed_out, "timeout_seconds": result_from_bridge.get("timeout_seconds"), "exit_code": result_from_bridge.get("exit_code"), "subprocess_started": bool(result_from_bridge.get("subprocess_started", True)), "stdout_length": len(output), "stdout_summary": _safe_repair_text(output[-1000:]), "stderr_length": len(str(result_from_bridge.get("stderr") or "")), "stderr_summary": _safe_repair_text(str(result_from_bridge.get("stderr") or "")[-1000:]), "textual_result_present": bool(output), "task_file_argument": result_from_bridge.get("task_file_argument", ""), "task_file_resolved_path": _safe_repair_text(str(result_from_bridge.get("task_file_resolved_path") or "")), "task_file_exists": bool(result_from_bridge.get("task_file_exists", False)), "diagnostics": {"session_id": session_id, "command_shape": result_from_bridge.get("command_shape", [])}})
            result["command_shape"] = result["diagnostics"]["command_shape"]

            if changed:
                self.log(f"[QA OpenCode Fix] {len(changed)} project file(s) changed: {', '.join(changed[:10])}")
                if timed_out:
                    self.log("[QA OpenCode Fix] OpenCode exceeded the timeout, but project files changed. Running full QA to verify the actual repair.")
                elif not success and error:
                    self.log(f"[QA OpenCode Fix] OpenCode exited with error: {error}. Project files changed — running full QA to verify the actual repair.")
                else:
                    self.log(f"[QA OpenCode Fix]: OpenCode repair completed (session {session_id}). Files changed — running full QA to verify.")
                result.update(success=True, status="subprocess_zero_exit_changes_detected" if success else "subprocess_nonzero_exit", failure_stage="", error_category="" if success else "nonzero_exit", **{OPENCODE_FIX_APPLIED: True})
                return finish()
            else:
                if timed_out:
                    self.log("[QA OpenCode Fix] OpenCode timed out. No meaningful files changed. Repair attempt considered unsuccessful.")
                elif not success and error:
                    self.log(f"[QA OpenCode Fix]: OpenCode repair failed: {error}. No files changed.")
                else:
                    self.log("[QA OpenCode Fix]: OpenCode completed but no project files changed.")
                preflight_status = result_from_bridge.get("preflight_status")
                result.update(status="task_file_not_found" if preflight_status == "task_file_not_found" else ("subprocess_timeout" if timed_out else ("subprocess_nonzero_exit" if not success else "subprocess_zero_exit_no_changes")), failure_stage="preflight_path_validation" if preflight_status == "task_file_not_found" else "subprocess", error_category="task_file_not_found" if preflight_status == "task_file_not_found" else ("timeout" if timed_out else ("nonzero_exit" if not success else "no_source_changes")))
                return finish()
        except Exception as e:
            self.log(f"[QA OpenCode Fix]: Exception: {e}")
            result.update(status="exception", failure_stage="repair_invocation", error_category="exception", exception_type=type(e).__name__, exception_summary=_safe_repair_text(str(e)))
            return finish()
