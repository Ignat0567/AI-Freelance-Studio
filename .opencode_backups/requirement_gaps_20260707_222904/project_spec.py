import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


ACCEPTANCE_EVIDENCE_FIELDS = (
    "criterion_id",
    "verifier",
    "method",
    "command",
    "exit_code",
    "status",
    "timestamp",
    "summary",
    "artifacts",
)
ACCEPTANCE_EVIDENCE_HISTORY_KEY = "acceptance_evidence"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_acceptance_evidence(criterion_id: str, status: str, evidence: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(evidence or {})
    normalized = {
        "criterion_id": source.get("criterion_id", criterion_id),
        "verifier": source.get("verifier", source.get("source", "unknown")),
        "method": source.get("method", source.get("verification_method", "")),
        "command": source.get("command"),
        "exit_code": source.get("exit_code"),
        "status": source.get("status", status),
        "timestamp": source.get("timestamp", _utc_timestamp()),
        "summary": source.get("summary", ""),
        "artifacts": source.get("artifacts", []),
    }
    for key, value in source.items():
        normalized.setdefault(key, value)
    if str(normalized["status"]).lower() in ("pass", "passed") and not _has_acceptance_evidence(normalized):
        raise ValueError("Passed acceptance evidence requires a summary, command, exit code, or artifacts")
    return normalized


def _has_acceptance_evidence(evidence: dict[str, Any]) -> bool:
    if evidence.get("summary"):
        return True
    if evidence.get("command"):
        return True
    if evidence.get("exit_code") is not None:
        return True
    return bool(evidence.get("artifacts"))


def ensure_acceptance_evidence_history(project: dict) -> dict[str, list[dict[str, Any]]]:
    history = project.get(ACCEPTANCE_EVIDENCE_HISTORY_KEY)
    if not isinstance(history, dict):
        history = {}
        project[ACCEPTANCE_EVIDENCE_HISTORY_KEY] = history

    for criterion in project.get("acceptance_criteria", []):
        criterion_id = criterion.get("id")
        if not criterion_id:
            continue
        entries = history.setdefault(criterion_id, [])
        legacy_entries = criterion.get("evidence", [])
        if isinstance(legacy_entries, list) and legacy_entries is not entries:
            for entry in legacy_entries:
                if isinstance(entry, dict) and entry not in entries:
                    entries.append(entry)
        criterion["evidence"] = entries
    return history


IGNORED_QA_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    "__pycache__",
}


def _text(project: dict) -> str:
    parts = [project.get("title", ""), project.get("description", "")]
    for msg in project.get("chat_history", []):
        if msg.get("role") in ("user", "assistant"):
            parts.append(msg.get("content", ""))
    return "\n".join(p for p in parts if p)


def _original_request(project: dict) -> str:
    return project.get("description") or "\n".join(
        msg.get("content", "") for msg in project.get("chat_history", []) if msg.get("role") == "user"
    ) or project.get("title", "")


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"(?:\r?\n|[.;])", text or "")
    return [c.strip(" -\t") for c in chunks if len(c.strip()) >= 8]


def _feature_phrases(text: str) -> list[str]:
    features = []
    patterns = (
        r"(?:must|should|needs? to|allow(?:s)?|can|create|build|implement|add|support|include)\s+([^\n.;]+)",
        r"(?:нужно|должен|должна|должно|добавь|создай|сделай|поддержи)\s+([^\n.;]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            phrase = match.group(1).strip(" .,:;-\t")
            if len(phrase) >= 4:
                features.append(phrase)
    if not features:
        features = _sentences(text)[:6]
    return _dedupe(features)[:12]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        key = str(item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _stable_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index:03d}"


def _requirement_title(description: str) -> str:
    title = str(description or "").strip().splitlines()[0]
    return title[:72] or "Requirement"


def requirements_from_features(features: list[str]) -> list[dict[str, Any]]:
    unique_features = _dedupe([str(feature).strip() for feature in features if str(feature).strip()])
    return [
        {
            "id": _stable_id("REQ", index),
            "parent_id": None,
            "title": _requirement_title(feature),
            "description": feature,
            "priority": "high",
            "source": "user_requirement",
            "dependencies": [],
            "status": "pending",
            "mandatory": True,
        }
        for index, feature in enumerate(unique_features, start=1)
    ]


def _user_requirements(features: list[str]) -> list[dict[str, Any]]:
    return requirements_from_features(features)


def requirement_dependency_errors(requirements: list[dict[str, Any]]) -> list[str]:
    ids = {str(req.get("id")) for req in requirements if isinstance(req, dict) and req.get("id")}
    errors = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        req_id = str(requirement.get("id") or "")
        dependencies = requirement.get("dependencies", [])
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list):
            errors.append(f"{req_id}: dependencies must be a list")
            continue
        for dependency_id in dependencies:
            dependency_id = str(dependency_id)
            if dependency_id == req_id:
                errors.append(f"{req_id}: dependency cannot reference itself")
            elif dependency_id not in ids:
                errors.append(f"{req_id}: unknown dependency {dependency_id}")
    return errors


def requirement_graph(project_spec: dict) -> list[dict[str, Any]]:
    requirements = project_spec.get("requirements", [])
    return requirements if isinstance(requirements, list) else []


def mandatory_user_requirements(project_spec: dict) -> list[dict[str, Any]]:
    requirements = project_spec.get("requirements", [])
    if not isinstance(requirements, list):
        return []
    mandatory = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        if requirement.get("source") != "user_requirement":
            continue
        if requirement.get("mandatory", True) and requirement.get("priority", "high") in ("critical", "high"):
            mandatory.append(requirement)
    return mandatory


def orphan_mandatory_requirements(project_spec: dict, acceptance_criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linked_requirement_ids = set()
    for criterion in acceptance_criteria:
        ids = criterion.get("requirement_ids", [])
        if isinstance(ids, str):
            ids = [ids]
        if isinstance(ids, list):
            linked_requirement_ids.update(str(req_id) for req_id in ids if req_id)
    return [req for req in mandatory_user_requirements(project_spec) if req.get("id") not in linked_requirement_ids]


def _credential_requirements(text: str) -> list[dict[str, str]]:
    lower = (text or "").lower()
    credentials = []
    candidates = [
        ("TELEGRAM_BOT_TOKEN", ("telegram", "bot"), "Telegram Bot API token"),
        ("OPENAI_API_KEY", ("openai",), "OpenAI API key"),
        ("ANTHROPIC_API_KEY", ("anthropic", "claude"), "Anthropic API key"),
        ("STRIPE_API_KEY", ("stripe", "payment"), "Stripe API key"),
        ("DISCORD_TOKEN", ("discord", "bot"), "Discord bot token"),
        ("DATABASE_URL", ("postgres", "mysql", "database url"), "Database connection string"),
        ("SMTP_PASSWORD", ("smtp", "email", "mail"), "SMTP credentials"),
    ]
    for env_name, keywords, description in candidates:
        if any(k in lower for k in keywords):
            credentials.append({"name": env_name, "description": description, "required": "true"})
    if any(k in lower for k in ("api key", "apikey", "token", "secret", "oauth")) and not credentials:
        credentials.append({"name": "EXTERNAL_SERVICE_CREDENTIAL", "description": "Credential required by requested external service", "required": "true"})
    return credentials


def detect_project_profiles(project_spec: dict | None = None, project_path: str | None = None, text: str = "") -> list[str]:
    blob = "\n".join(
        str(x)
        for x in [
            text,
            (project_spec or {}).get("original_user_request", ""),
            (project_spec or {}).get("project_goal", ""),
            " ".join((project_spec or {}).get("required_features", [])),
            " ".join((project_spec or {}).get("technology_requirements", [])),
        ]
    ).lower()

    files = set()
    if project_path and os.path.isdir(project_path):
        for root, dirs, names in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_QA_DIRS]
            for name in names:
                rel = os.path.relpath(os.path.join(root, name), project_path).replace(os.sep, "/")
                files.add(rel.lower())

    profiles = []
    has_file = lambda name: name.lower() in files
    has_suffix = lambda suffix: any(f.endswith(suffix) for f in files)

    if any(k in blob for k in ("fastapi", "uvicorn")) or has_file("main.py") and any("fastapi" in _safe_read(project_path, f) for f in files if f.endswith(".py")):
        profiles.append("fastapi")
        profiles.append("REST_API")
    if any(k in blob for k in ("telegram", "aiogram")) or has_file("bot.py") or any(f.startswith("handlers/") for f in files):
        profiles.append("telegram_bot")
    if any(k in blob for k in ("react", "jsx")) or any(f.endswith((".jsx", ".tsx")) for f in files):
        profiles.append("react_frontend")
    if any(k in blob for k in ("vite",)) or has_file("vite.config.js") or has_file("vite.config.ts"):
        profiles.append("vite_frontend")
    if any(k in blob for k in ("node", "express", "npm")) or has_file("package.json"):
        profiles.append("node_backend" if "express" in blob else "node_project")
    if any(k in blob for k in ("static website", "landing page", "html")) or has_file("index.html") or has_suffix(".html"):
        profiles.append("static_website")
    if any(k in blob for k in ("database", "sqlite", "postgres", "mysql", "sqlalchemy")):
        profiles.append("database_application")
    if any(k in blob for k in ("ai ", "llm", "openai", "anthropic", "nvidia", "model")):
        profiles.append("AI_application")
    if any(k in blob for k in ("cli", "command line", "terminal")):
        profiles.append("python_cli")
    if any(k in blob for k in ("python", "fastapi", "django", "flask", "aiogram")) or has_suffix(".py"):
        profiles.append("python_application")
    if not profiles:
        profiles.append("generic")
    return _dedupe(profiles)


def _safe_read(project_path: str | None, rel: str) -> str:
    if not project_path:
        return ""
    try:
        path = Path(project_path) / rel
        if path.is_file() and path.stat().st_size < 250_000:
            return path.read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return ""
    return ""


def build_project_spec(project: dict, existing_path: str | None = None) -> dict[str, Any]:
    source_text = _text(project)
    original = _original_request(project)
    features = _feature_phrases(source_text)
    credentials = _credential_requirements(source_text)
    profiles = detect_project_profiles(text=source_text, project_path=existing_path)

    technology = []
    lower = source_text.lower()
    for token, label in (
        ("fastapi", "FastAPI"),
        ("react", "React"),
        ("vite", "Vite"),
        ("telegram", "Telegram Bot API"),
        ("aiogram", "aiogram"),
        ("sqlite", "SQLite"),
        ("postgres", "PostgreSQL"),
        ("docker", "Docker"),
    ):
        if token in lower:
            technology.append(label)

    expected_entrypoint = "main.py"
    if "telegram_bot" in profiles:
        expected_entrypoint = "bot.py"
    elif "vite_frontend" in profiles or "react_frontend" in profiles:
        expected_entrypoint = "package.json"

    install_method = "Document exact dependency installation in README.md"
    run_method = "Document exact run command in README.md"
    test_method = "Run configured automated tests when present"
    if "fastapi" in profiles:
        install_method = "python -m pip install -r requirements.txt"
        run_method = "python -m uvicorn main:app --host 127.0.0.1 --port <free-port>"
        test_method = "python -m pytest -q"
    elif "telegram_bot" in profiles:
        install_method = "python -m pip install -r requirements.txt"
        run_method = "python bot.py"
        test_method = "python -m pytest -q when tests exist; otherwise run import/smoke checks"
    elif "vite_frontend" in profiles:
        install_method = "npm install"
        run_method = "npm run dev or npm run preview"
        test_method = "npm run build and configured npm test when present"

    return {
        "original_user_request": original,
        "project_goal": project.get("title") or (features[0] if features else "Generated project"),
        "project_type": profiles[0],
        "project_profiles": profiles,
        "intended_users": _infer_users(source_text),
        "requirements": _user_requirements(features),
        "required_features": features,
        "explicit_constraints": _infer_constraints(source_text),
        "inferred_requirements": _inferred_requirements(profiles, credentials),
        "technology_requirements": _dedupe(technology),
        "external_services": _external_services(source_text, credentials),
        "required_credentials": credentials,
        "expected_entrypoint": expected_entrypoint,
        "installation_method": install_method,
        "run_method": run_method,
        "test_method": test_method,
        "delivery_artifacts": _delivery_artifacts(profiles, credentials),
        "risks": _risks(profiles, credentials),
        "unknowns": _unknowns(source_text, credentials),
        "assumptions": _assumptions(profiles),
    }


def _infer_users(text: str) -> list[str]:
    lower = text.lower()
    users = []
    if "admin" in lower:
        users.append("administrator")
    if "telegram" in lower:
        users.append("telegram users")
    if "api" in lower:
        users.append("API consumers")
    if "dashboard" in lower or "frontend" in lower or "website" in lower:
        users.append("web users")
    return users or ["end users"]


def _infer_constraints(text: str) -> list[str]:
    constraints = []
    for sentence in _sentences(text):
        lower = sentence.lower()
        if any(k in lower for k in ("must", "must not", "no ", "without", "only", "долж", "нельзя", "только", "без ")):
            constraints.append(sentence)
    return _dedupe(constraints)[:10]


def _inferred_requirements(profiles: list[str], credentials: list[dict[str, str]]) -> list[str]:
    reqs = ["Root README.md with install, run, and test instructions", "No placeholder or TODO-only implementation"]
    if any(p in profiles for p in ("python_application", "fastapi", "telegram_bot")):
        reqs.append("Python dependency file must match imports")
    if any(p in profiles for p in ("node_project", "react_frontend", "vite_frontend")):
        reqs.append("Node dependency file and build script must be valid")
    if credentials:
        reqs.append("Credential requirements must be documented in .env.example without real secrets")
    return reqs


def _external_services(text: str, credentials: list[dict[str, str]]) -> list[str]:
    services = []
    lower = text.lower()
    for token, label in (("telegram", "Telegram"), ("openai", "OpenAI"), ("anthropic", "Anthropic"), ("stripe", "Stripe"), ("discord", "Discord")):
        if token in lower:
            services.append(label)
    services.extend(c["description"] for c in credentials if c.get("description"))
    return _dedupe(services)


def _delivery_artifacts(profiles: list[str], credentials: list[dict[str, str]]) -> list[str]:
    artifacts = ["README.md"]
    if any(p in profiles for p in ("python_application", "fastapi", "telegram_bot")):
        artifacts.append("requirements.txt")
    if any(p in profiles for p in ("node_project", "react_frontend", "vite_frontend")):
        artifacts.append("package.json")
    if credentials:
        artifacts.extend([".env.example", ".gitignore"])
    if "static_website" in profiles:
        artifacts.append("index.html or frontend/index.html")
    return _dedupe(artifacts)


def _risks(profiles: list[str], credentials: list[dict[str, str]]) -> list[str]:
    risks = []
    if credentials:
        risks.append("Project depends on external credentials that cannot be validated without user input")
    if "telegram_bot" in profiles:
        risks.append("Telegram network behavior cannot be fully smoke-tested without a real bot token")
    if "AI_application" in profiles:
        risks.append("AI provider behavior may vary by model/provider availability")
    return risks or ["User-specific acceptance behavior still requires project-specific QA evidence"]


def _unknowns(text: str, credentials: list[dict[str, str]]) -> list[str]:
    unknowns = []
    if not text.strip():
        unknowns.append("No detailed original request was provided")
    if credentials:
        unknowns.append("Real production credentials are not available during generation")
    return unknowns


def _assumptions(profiles: list[str]) -> list[str]:
    assumptions = ["Generated project should run locally from its project root"]
    if "fastapi" in profiles:
        assumptions.append("FastAPI app exposes an ASGI app object named app")
    if "vite_frontend" in profiles:
        assumptions.append("Vite build is the production frontend readiness check")
    return assumptions


def generate_acceptance_criteria(project_spec: dict) -> list[dict[str, Any]]:
    criteria = []

    def add(title: str, description: str, priority: str, source: str, method: str, expected: str, trace: str = "", requirement_ids: list[str] | None = None):
        criteria.append({
            "id": _stable_id("AC", len(criteria) + 1),
            "title": title,
            "description": description,
            "priority": priority,
            "source": source,
            "verification_method": method,
            "expected_result": expected,
            "requirement_ids": requirement_ids or [],
            "status": "pending",
            "evidence": [],
            "trace": trace,
        })

    add("Project has delivery documentation", "Root documentation explains installation, run, and test commands.", "high", "system_safety", "file_check", "README.md exists and contains install/run/test guidance")
    add("Project has no placeholder implementation", "Required functionality is implemented, not replaced by TODO/pass/stub code.", "high", "system_safety", "static_scan", "No blocking placeholder patterns in source files")

    requirements = mandatory_user_requirements(project_spec)
    if not requirements:
        requirements = _user_requirements(project_spec.get("required_features", []))
    for requirement in requirements:
        feature = str(requirement.get("description", ""))
        add(
            f"Feature: {feature[:72]}",
            f"Requested feature must be present and testable: {feature}",
            "high",
            "user_requirement",
            "feature_trace_static_or_smoke",
            "Implementation contains behavior matching the requested feature and no TODO-only substitute",
            trace=feature,
            requirement_ids=[requirement["id"]],
        )

    profiles = project_spec.get("project_profiles", [])
    if "fastapi" in profiles:
        add("FastAPI application imports", "FastAPI app module imports successfully.", "high", "project_profile", "python_import", "Expected ASGI app can be imported")
        add("FastAPI runtime starts", "Application starts with python -m uvicorn on a free local port.", "high", "project_profile", "runtime_smoke", "Process stays up long enough to serve HTTP")
    if "telegram_bot" in profiles:
        add("Telegram bot configuration is safe", "Bot documents token requirements without committing a real token.", "critical", "project_profile", "file_and_secret_check", ".env.example documents BOT_TOKEN and contains no real-looking token")
        add("Telegram command handlers smoke test", "Core bot commands and callback data are importable and safe.", "high", "project_profile", "telegram_smoke", "/start/key commands import without exception; callback_data <= 64 bytes")
    if "vite_frontend" in profiles or "react_frontend" in profiles:
        add("Frontend production build succeeds", "Frontend builds in production mode using configured npm scripts.", "high", "project_profile", "command", "npm install and npm run build exit with code 0")
    if "static_website" in profiles:
        add("Static website entry page exists", "Static website has an entry HTML file and local references resolve.", "high", "project_profile", "static_asset_check", "index.html exists and referenced local files are present")
    if project_spec.get("required_credentials"):
        add("Credentials are documented safely", "All required credentials are represented in docs/env examples without real secrets.", "critical", "inferred_requirement", "secret_scan", ".env.example exists, .gitignore excludes .env, and no real secrets are committed")

    return criteria


def generate_qa_plan(project_spec: dict, profiles: list[str], acceptance_criteria: list[dict[str, Any]]) -> dict[str, Any]:
    levels = []

    def level(level_id: str, title: str, checks: list[str]):
        levels.append({"level": level_id, "title": title, "checks": checks, "evidence": []})

    level("A", "Delivery readiness", [
        "README/install/run/test instructions",
        "dependency files",
        "expected entrypoint",
        ".env.example and .gitignore when credentials are required",
        "secret scan and placeholder scan",
    ])
    level("B", "Static checks", ["Python syntax/import/compileall when Python is present", "Node build/type checks when configured"])
    level("C", "Dependencies", ["Python requirements verification", "Node npm install/build when package.json exists"])
    level("D", "Automated tests", ["pytest/unittest/npm test/vitest/jest when configured", "capture command, cwd, exit code, output, duration"])
    level("E", "Runtime smoke tests", ["start runnable projects on a free port", "verify expected endpoint/page", "stop only owned process"])

    profile_checks = []
    if "telegram_bot" in profiles:
        profile_checks.extend(["Telegram imports", "handler registration", "/start", "callback length", "database behavior", "token safety"])
    if "fastapi" in profiles:
        profile_checks.extend(["FastAPI import", "application startup", "important endpoints", "invalid input behavior"])
    if "vite_frontend" in profiles or "react_frontend" in profiles:
        profile_checks.extend(["npm install", "production build", "expected frontend files"])
    if "static_website" in profiles:
        profile_checks.extend(["entry HTML", "referenced local resources", "broken internal paths"])
    if not profile_checks:
        profile_checks.append("generic project smoke/readiness checks")
    level("F", "Profile-specific tests", profile_checks)

    return {
        "profiles": profiles,
        "levels": levels,
        "acceptance_criteria_ids": [c["id"] for c in acceptance_criteria],
        "orphan_mandatory_requirement_ids": [r["id"] for r in orphan_mandatory_requirements(project_spec, acceptance_criteria)],
        "expected_entrypoint": project_spec.get("expected_entrypoint", ""),
        "install_command": project_spec.get("installation_method", ""),
        "run_command": project_spec.get("run_method", ""),
        "test_command": project_spec.get("test_method", ""),
    }


def ensure_project_spec_bundle(project: dict, project_path: str | None = None) -> dict[str, Any]:
    spec = build_project_spec(project, existing_path=project_path)
    profiles = detect_project_profiles(spec, project_path=project_path)
    spec["project_profiles"] = profiles
    spec["project_type"] = profiles[0] if profiles else "generic"
    criteria = generate_acceptance_criteria(spec)
    qa_plan = generate_qa_plan(spec, profiles, criteria)
    bundle = {
        "project_spec": spec,
        "project_profiles": profiles,
        "acceptance_criteria": criteria,
        "qa_plan": qa_plan,
        "traceability": {"orphan_mandatory_requirement_ids": qa_plan["orphan_mandatory_requirement_ids"]},
    }
    project.update(bundle)
    ensure_acceptance_evidence_history(project)
    return bundle


def acceptance_summary(project: dict) -> str:
    criteria = project.get("acceptance_criteria", [])
    lines = []
    for c in criteria:
        reqs = ",".join(c.get("requirement_ids", [])) or "none"
        lines.append(f"{c.get('id')}: {c.get('title')} | REQs: {reqs} | Verify: {c.get('verification_method')} | Expected: {c.get('expected_result')}")
    return "\n".join(lines)


def record_acceptance_evidence(project: dict, criterion_id: str, status: str, evidence: dict[str, Any]) -> None:
    history = ensure_acceptance_evidence_history(project)
    for criterion in project.get("acceptance_criteria", []):
        if criterion.get("id") == criterion_id:
            criterion["status"] = status
            entry = normalize_acceptance_evidence(criterion_id, status, evidence)
            history.setdefault(criterion_id, []).append(entry)
            criterion["evidence"] = history[criterion_id]
            return
