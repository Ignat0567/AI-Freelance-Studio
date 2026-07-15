"""Architecture maintainability checks for generated projects."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from quality_profiles import architecture_policy_for_profile
from repair_scope import EXCLUDED_REPAIR_DIRS, EXCLUDED_REPAIR_EXTENSIONS, walk_repairable_files


SEVERITIES = ("info", "warning", "major", "critical")
SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql"}
SECRET_RE = re.compile(r"\b\d{7,}:[A-Za-z0-9_-]{20,}\b|sk-[A-Za-z0-9_-]{16,}|(?i:(api[_-]?key|token|secret|password)\s*=\s*(['\"])(?!replace_me|your_|example|changeme|<)[^'\"\r\n]{10,}\2)")


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _add(finding_list: list[dict[str, Any]], severity: str, code: str, title: str, **evidence: Any) -> None:
    finding_list.append({"severity": severity if severity in SEVERITIES else "warning", "code": code, "title": title, "evidence": evidence})


def _responsibilities(rel: str, text: str) -> list[str]:
    low = text.lower()
    checks = {
        "routes/controllers": r"@app\.|apirouter|express\(|router\.|urlpatterns|fetch\(",
        "models": r"class\s+\w+\(|sequelize|mongoose|sqlalchemy|dataclass",
        "schemas": r"basemodel|zod\.|yup\.|interface\s+\w+|type\s+\w+\s*=",
        "services/domain": r"calculate_|process_|workflow|business|status transition|total\s*=|price|booking|order",
        "repositories/data": r"sqlite|sqlalchemy|select\s+.+from|insert\s+into|localstorage|asyncstorage|prisma|repository",
        "authentication": r"login|logout|jwt|password|bcrypt|role|rbac|auth",
        "configuration": r"os\.getenv|process\.env|dotenv|settings|config",
        "ui/screens": r"<html|jsx|tsx|react|stylesheet|navigationcontainer|button|form|useeffect|usestate",
    }
    found = [name for name, pattern in checks.items() if re.search(pattern, low)]
    name = rel.replace("\\", "/").lower()
    if "/test" in name or name.startswith("test_") or name.endswith(".test.js") or name.endswith(".test.ts"):
        found.append("tests")
    return list(dict.fromkeys(found))


def _module_map(files: list[dict[str, Any]]) -> dict[str, list[str]]:
    areas = {
        "routes/controllers": ("route", "router", "controller", "api"),
        "models": ("model", "models", "entity"),
        "schemas": ("schema", "schemas", "dto"),
        "services/domain": ("service", "services", "domain"),
        "repositories/data": ("repository", "repositories", "dao", "data", "db"),
        "authentication": ("auth", "login", "session"),
        "configuration": ("config", "settings", ".env"),
        "tests": ("test", "tests", "spec"),
        "screens": ("screen", "screens", "page", "pages"),
        "navigation": ("navigation", "navigator", "router"),
        "components": ("component", "components"),
        "state/store": ("store", "state", "slice"),
        "theme": ("theme", "styles"),
        "localization": ("i18n", "locale", "localization"),
        "api client": ("client", "api"),
    }
    result = {area: [] for area in areas}
    for item in files:
        rel = item["path"].replace("\\", "/").lower()
        for area, tokens in areas.items():
            if any(token in rel for token in tokens):
                result[area].append(item["path"])
    return {key: value for key, value in result.items() if value}


def _project_text(project: dict[str, Any]) -> str:
    spec = project.get("project_spec") if isinstance(project.get("project_spec"), dict) else {}
    values = [project.get("title", ""), project.get("description", ""), project.get("original_request", ""), spec.get("original_user_request", ""), " ".join(project.get("project_profiles", []) or spec.get("project_profiles", []))]
    return "\n".join(str(value) for value in values if value).lower()


def _needs_database(files: list[dict[str, Any]]) -> bool:
    return any(re.search(r"sqlite|sqlalchemy|postgres|mysql|prisma|mongoose|sequelize|create\s+table", item.get("text", "").lower()) for item in files)


def _has_migrations(root: str, files: list[dict[str, Any]]) -> bool:
    names = {item["path"].replace("\\", "/").lower() for item in files}
    return any("migration" in name or name.startswith("alembic/") or name.startswith("prisma/migrations/") or name.endswith("schema.prisma") for name in names) or os.path.isdir(os.path.join(root, "migrations"))


def analyze_architecture(project: dict[str, Any], root: str, quality_profile: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = quality_profile or str(project.get("quality_profile") or project.get("project_spec", {}).get("quality_profile") or "strict_mvp")
    rules = {**architecture_policy_for_profile(profile), **(policy or {})}
    files: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    ignored_dirs = EXCLUDED_REPAIR_DIRS | {".freelancerstudio", "local_backups", "generated_projects"}
    ignored_exts = EXCLUDED_REPAIR_EXTENSIONS
    for rel, path in walk_repairable_files(root, SOURCE_EXTS):
        if any(part in ignored_dirs for part in rel.replace("\\", "/").split("/")) or Path(rel).suffix.lower() in ignored_exts:
            continue
        text = _read(str(path))
        if not text.strip():
            continue
        responsibilities = _responsibilities(rel, text)
        item = {"path": rel, "line_count": len(text.splitlines()), "responsibilities": responsibilities, "responsibility_count": len(responsibilities), "text": text}
        files.append(item)

    large_files = [{key: item[key] for key in ("path", "line_count", "responsibilities", "responsibility_count")} for item in files if item["line_count"] > int(rules.get("max_recommended_file_lines") or 0)]
    for item in large_files:
        _add(findings, "warning", "large_file", f"{item['path']} is larger than recommended", file=item["path"], line_count=item["line_count"], responsibilities=item["responsibilities"])

    module_map = _module_map(files)
    project_text = _project_text(project)
    has_backend = any(token in project_text for token in ("fastapi", "backend", "api")) or any("routes/controllers" in item["responsibilities"] for item in files)
    has_mobile = any(token in project_text for token in ("android", "ios", "mobile", "react native", "expo")) or any("react-native" in item.get("text", "").lower() for item in files)
    has_manager = any(token in project_text for token in ("manager", "admin dashboard", "management ui"))
    expected = []
    if has_backend:
        expected.extend(["routes/controllers", "schemas", "services/domain", "repositories/data", "configuration", "tests"])
    if has_mobile:
        expected.extend(["screens", "navigation", "api client", "state/store", "components"])
    if has_manager:
        expected.extend(["screens", "components", "api client", "authentication", "state/store"])
    expected = list(dict.fromkeys(expected))
    missing = [area for area in expected if area not in module_map]

    source_count = len([item for item in files if Path(item["path"]).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"}])
    if rules.get("separation_of_concerns_required") and source_count >= 3 and len(missing) >= max(3, len(expected) // 2):
        _add(findings, "major", "missing_separation", "Expected architecture areas are not meaningfully separated", missing_separation=missing, expected_areas=expected, module_map=module_map)

    for item in files:
        mixed_backend = {"routes/controllers", "schemas", "services/domain", "repositories/data"}.issubset(set(item["responsibilities"]))
        mixed_ui = "ui/screens" in item["responsibilities"] and len(set(item["responsibilities"]) & {"repositories/data", "services/domain", "authentication"}) >= 2
        above_blocking = bool(rules.get("max_blocking_file_lines")) and item["line_count"] > int(rules["max_blocking_file_lines"])
        if rules.get("separation_of_concerns_required") and (mixed_backend or mixed_ui) and (above_blocking or item["responsibility_count"] >= 5):
            _add(findings, "critical", "mixed_critical_responsibilities", "Mandatory application logic is concentrated in one untestable file", file=item["path"], line_count=item["line_count"], responsibilities=item["responsibilities"])
        if rules.get("no_single_file_multiplatform_application") and "routes/controllers" in item["responsibilities"] and "ui/screens" in item["responsibilities"]:
            _add(findings, "critical", "single_file_multiplatform_application", "Backend and UI/platform behavior are implemented in one file", file=item["path"], responsibilities=item["responsibilities"])
        if SECRET_RE.search(item.get("text", "")):
            _add(findings, "critical", "hardcoded_secret", "Secret-like value is hardcoded in source", file=item["path"])
        if rules.get("no_hardcoded_demo_data_in_production_paths") and re.search(r"(?i)(demo|sample|mock)(users|orders|data)?\s*=|const\s+\w*(demo|sample|mock)\w*\s*=", item.get("text", "")):
            _add(findings, "major", "hardcoded_demo_data", "Hardcoded demo data appears in a production path", file=item["path"])

    mandatory_ui = has_manager or any(target in (project.get("quality_settings", {}).get("required_targets") or []) for target in ("web", "manager_web", "android", "ios"))
    if rules.get("no_placeholder_main_ui") and mandatory_ui:
        ui_files = [item for item in files if Path(item["path"]).name.lower() in {"index.html", "app.jsx", "app.tsx", "app.js", "app.ts"} or "pages/" in item["path"].replace("\\", "/").lower()]
        for item in ui_files:
            text = item.get("text", "").lower()
            if re.search(r"coming soon|lorem ipsum|placeholder|todo: implement|static mock", text) and not re.search(r"<form|<button|onclick|fetch\(|axios\.|navigation|route", text):
                _add(findings, "critical", "placeholder_main_ui", "Mandatory UI is a static placeholder", file=item["path"])

    migrations_present = _has_migrations(root, files)
    if rules.get("migrations_required") and _needs_database(files) and not migrations_present:
        _add(findings, "critical", "missing_migration_strategy", "Database-backed production candidate has no migration path", migration_status="missing")

    blocking = [finding for finding in findings if finding["severity"] == "critical" or (profile in {"production_candidate", "production"} and finding["severity"] == "major")]
    public_files = [{key: item[key] for key in ("path", "line_count", "responsibilities", "responsibility_count")} for item in files]
    return {
        "status": "failed" if blocking else "passed",
        "quality_profile": profile,
        "policy": rules,
        "large_files": large_files,
        "files": public_files,
        "responsibility_count": sum(item["responsibility_count"] for item in files),
        "module_map": module_map,
        "missing_separation": missing,
        "migration_status": "present" if migrations_present else "missing" if _needs_database(files) else "not_applicable",
        "placeholder_status": "failed" if any(f["code"] == "placeholder_main_ui" for f in findings) else "passed",
        "findings": findings,
        "blocking_findings": blocking,
    }
