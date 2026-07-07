import os
import json
import re
import time
from ai_utils import ask_studio_ai_with_history

BATCH_SIZE = 4  # Files per AI call (LEGACY — one-by-one generation is preferred)

_AI_RULES = (
    "CRITICAL RULES — VIOLATING ANY WILL CAUSE PROJECT REJECTION:\n"
    "1. EVERY listed file must be generated with COMPLETE, production-ready code — no stubs, no TODOs, no placeholders\n"
    "2. dependencies: if the spec mentions a database, include the driver (psycopg2-binary for PostgreSQL sync, asyncpg for PostgreSQL async, aiosqlite for SQLite async). requirements.txt MUST have ALL needed packages\n"
    "3. async consistency: if using SQLAlchemy, decide sync OR async — NEVER mix sync engine with async queries.\n"
    "4. frontend completeness: if spec mentions React/Vue/Angular/frontend/dashboard — create COMPLETE frontend with package.json (type=module), index.html, src/main.jsx, src/App.jsx, tailwind.config.js, postcss.config.js, vite.config.js\n"
    "5. ESM configs: when using ESM config files (tailwind.config.js, postcss.config.js, vite.config.js), package.json MUST have '\"type\": \"module\"'\n"
    "6. env vars: every os.getenv() call MUST provide a default value. Include python-dotenv in requirements and load_dotenv() in entry point\n"
    "7. test-model alignment: tests MUST use exact field names/types from actual models/schemas — verify before writing assertions\n"
    "8. endpoint testing: tests MUST only test endpoints that exist in actual API code — cross-reference router definitions\n"
    "9. imports: import names MUST exactly match the exports of the dependency file — verify exports before importing\n"
    "10. Include config files (Dockerfile, docker-compose.yml, .env.example, README.md)\n"
    "11. Include tests (conftest.py + test files, all runnable)\n"
    "12. Maximum 30 files total\n"
    "13. Frontend projects MUST use Tailwind CSS with PostCSS (tailwind.config.js + postcss.config.js)\n"
    "14. In Jinja2 templates use DIRECT paths: /static/style.css, NOT {{ url_for('static', filename='...') }}\n"
    "15. PyMuPDF: import as 'import fitz' (not 'import PyMuPDF'). Use fitz.open(stream=bytes, filetype='pdf') NOT fitz.open('filename.pdf').\n"
    "16. uvicorn.run() must NOT have debug=True.\n"
    "17. In requirements.txt: 'python-dotenv' NOT 'dotenv', 'PyMuPDF' NOT 'fitz'.\n"
    "18. Do NOT add numpy, pandas, scipy, matplotlib, sklearn to requirements.txt.\n"
    "19. Container names in docker-compose.yml must NOT start with a digit.\n"
    "20. ALL file paths use forward slash (/) not backslash (\\).\n"
    "21. Optional[X] imports: use ONLY from typing import Optional.\n"
    "22. For React backend: return JSON only (NO Jinja2 templates), enable CORS middleware.\n"
)

STRUCTURE_PROMPT = (
    "You are Codex, a senior software architect. Analyze this project specification and "
    "design a complete directory tree with all files needed for a production-grade application.\n\n"
    "For each file provide:\n"
    "- path: relative path from project root\n"
    "- purpose: what this file does\n"
    "- depends_on: list of other files this file imports from (use their paths)\n"
    "- exports: key classes, functions, or types this file defines\n\n"
    f"{_AI_RULES}\n"
    "Return ONLY valid JSON:\n"
    '{"files": [{"path": "backend/app/main.py", "purpose": "...", "depends_on": ["..."], "exports": ["..."]}, ...]}'
)

BATCH_PROMPT_TEMPLATE = (
    "You are Codex, an expert developer. Generate the following files for this project.\n\n"
    "PROJECT SPECIFICATION:\n{spec}\n\n"
    "FILES TO GENERATE IN THIS BATCH:\n{batch_files}\n\n"
    "PROJECT STRUCTURE (all files in project):\n{file_tree}\n\n"
    "ALREADY GENERATED FILES (use these exact exports for imports):\n{already_generated}\n\n"
    f"{_AI_RULES}\n"
    "- Write COMPLETE, production-ready code (no placeholders, no TODOs, no pass statements, no stub functions)\n"
    "- Use ONLY ESM syntax (import/export) for JS/TS files — NEVER mix with CommonJS (require/module.exports)\n"
    "- For Python files, use ONLY modern imports (import x / from x import y), never __import__ or exec\n"
    "- Include all imports, type hints (Python) / JSDoc (JS), docstrings\n"
    "- Each file must import from the exact paths shown in ALREADY GENERATED\n"
    "- Files in this batch can import from each other freely\n"
    "Return ONLY valid JSON with file paths as keys and file contents as values.\n"
    'Example: {{"backend/app/config.py": "from pydantic_settings import BaseSettings\\nclass Settings(BaseSettings):\\n  ...", "backend/app/database.py": "..."}}\n'
    'Replace the example paths with the actual file paths. Escape newlines as \\\\n in JSON values.'
)


def _topological_levels(file_tree):
    """Group files by dependency level using topological sort."""
    path_map = {f["path"]: f for f in file_tree}
    levels = []
    remaining = set(f["path"] for f in file_tree)
    generated = set()

    while remaining:
        level = []
        for path in list(remaining):
            deps = set(path_map[path].get("depends_on", []))
            if deps.issubset(generated):
                level.append(path)
        if not level:
            # Circular or missing deps — add remaining as last level
            level = list(remaining)
        levels.append(level)
        generated.update(level)
        remaining -= set(level)
    return levels


def _batch_files(file_tree, batch_size=BATCH_SIZE):
    """Split topologically-sorted files into batches."""
    levels = _topological_levels(file_tree)
    batches = []
    current = []
    for level in levels:
        current.extend(level)
        while len(current) >= batch_size:
            batches.append(current[:batch_size])
            current = current[batch_size:]
    if current:
        batches.append(current)
    return batches


def plan_project_structure(spec_text, provider, model, temperature):
    raw = ask_studio_ai_with_history(
        provider=provider, model_name=model,
        system_prompt=STRUCTURE_PROMPT,
        chat_history=[{"role": "user", "content": spec_text}],
        temperature=temperature, max_tokens=4096,
    )
    cleaned = _clean_json(raw)
    try:
        data = json.loads(cleaned)
        files = data.get("files", [])
        if files and isinstance(files, list):
            valid = []
            for f in files:
                if isinstance(f, dict) and "path" in f:
                    f.setdefault("purpose", "")
                    f.setdefault("depends_on", [])
                    f.setdefault("exports", [])
                    valid.append(f)
            if valid:
                return valid
    except json.JSONDecodeError:
        pass
    m = re.search(r'\[.*?\{.*"path".*\}.*?\]', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    print("[Codex Plan]: Fallback to default structure")
    return _default_structure(spec_text)


def _default_structure(spec_text):
    spec_lower = spec_text.lower()
    files = [
        {"path": "backend/app/main.py", "purpose": "FastAPI app entry point with CORS, middleware, router mounting", "depends_on": ["backend/app/routers/users.py", "backend/app/routers/tasks.py", "backend/app/database.py", "backend/app/config.py"], "exports": ["app"]},
        {"path": "backend/app/config.py", "purpose": "Configuration via pydantic-settings / env vars", "depends_on": [], "exports": ["settings"]},
        {"path": "backend/app/models.py", "purpose": "SQLAlchemy ORM models", "depends_on": ["backend/app/database.py"], "exports": ["User", "Task", "Project"]},
        {"path": "backend/app/schemas.py", "purpose": "Pydantic request/response schemas", "depends_on": [], "exports": ["UserCreate", "UserRead", "TaskCreate", "TaskRead", "ProjectCreate"]},
        {"path": "backend/app/database.py", "purpose": "Database engine, session, Base", "depends_on": ["backend/app/config.py"], "exports": ["engine", "SessionLocal", "Base", "get_db"]},
        {"path": "backend/app/auth.py", "purpose": "JWT creation, password hashing, dependency", "depends_on": ["backend/app/models.py", "backend/app/config.py", "backend/app/database.py"], "exports": ["create_access_token", "get_current_user", "hash_password", "verify_password"]},
        {"path": "backend/app/routers/__init__.py", "purpose": "Router package marker", "depends_on": [], "exports": []},
        {"path": "backend/app/routers/users.py", "purpose": "User CRUD + profile endpoints", "depends_on": ["backend/app/models.py", "backend/app/schemas.py", "backend/app/auth.py", "backend/app/database.py"], "exports": ["router"]},
        {"path": "backend/app/routers/tasks.py", "purpose": "Task CRUD with pagination + filters", "depends_on": ["backend/app/models.py", "backend/app/schemas.py", "backend/app/auth.py", "backend/app/database.py"], "exports": ["router"]},
        {"path": "backend/tests/conftest.py", "purpose": "pytest fixtures: test client, test db", "depends_on": ["backend/app/main.py", "backend/app/database.py", "backend/app/models.py"], "exports": ["client", "test_db"]},
        {"path": "backend/tests/test_auth.py", "purpose": "Auth endpoint tests", "depends_on": ["backend/tests/conftest.py"], "exports": []},
        {"path": "backend/tests/test_tasks.py", "purpose": "Task CRUD tests", "depends_on": ["backend/tests/conftest.py"], "exports": []},
        {"path": "backend/requirements.txt", "purpose": "Python dependencies (fastapi, uvicorn, pydantic, sqlalchemy, python-dotenv, psycopg2-binary, pytest, pytest-cov, httpx)", "depends_on": [], "exports": []},
        {"path": "backend/Dockerfile", "purpose": "Docker image for backend", "depends_on": [], "exports": []},
        {"path": "docker-compose.yml", "purpose": "Multi-service Docker setup", "depends_on": [], "exports": []},
        {"path": ".env.example", "purpose": "Environment variables template with realistic defaults for all vars", "depends_on": [], "exports": []},
        {"path": "README.md", "purpose": "Project documentation with commands that match actual project structure (pip install, uvicorn, npm commands only if frontend exists)", "depends_on": [], "exports": []},
    ]
    if any(kw in spec_lower for kw in ["react", "frontend", "vue", "angular"]):
        files.extend([
            {"path": "frontend/package.json", "purpose": "NPM dependencies and scripts with \"type\": \"module\" for ESM configs (react, react-dom, vite, @vitejs/plugin-react, tailwindcss, postcss, autoprefixer)", "depends_on": [], "exports": []},
            {"path": "frontend/vite.config.js", "purpose": "Vite build config with proxy", "depends_on": [], "exports": []},
            {"path": "frontend/tailwind.config.js", "purpose": "TailwindCSS config", "depends_on": [], "exports": []},
            {"path": "frontend/index.html", "purpose": "HTML shell", "depends_on": [], "exports": []},
            {"path": "frontend/src/main.jsx", "purpose": "React entry point", "depends_on": ["frontend/src/App.jsx"], "exports": []},
            {"path": "frontend/src/App.jsx", "purpose": "Root component with router + theme provider", "depends_on": ["frontend/src/pages/Dashboard.jsx", "frontend/src/pages/Login.jsx", "frontend/src/components/Layout.jsx"], "exports": ["App"]},
            {"path": "frontend/src/api/client.js", "purpose": "Axios/fetch API client", "depends_on": [], "exports": ["api"]},
            {"path": "frontend/src/pages/Login.jsx", "purpose": "Login + register page", "depends_on": ["frontend/src/api/client.js"], "exports": ["LoginPage"]},
            {"path": "frontend/src/pages/Dashboard.jsx", "purpose": "Dashboard with charts + stats", "depends_on": ["frontend/src/api/client.js"], "exports": ["Dashboard"]},
            {"path": "frontend/src/components/Layout.jsx", "purpose": "Sidebar + header + dark mode toggle", "depends_on": [], "exports": ["Layout"]},
            {"path": "frontend/Dockerfile", "purpose": "Nginx-served frontend Docker image", "depends_on": [], "exports": []},
        ])
    return files


def _build_batch_prompt(batch_paths, file_tree, spec_text, generated):
    path_map = {f["path"]: f for f in file_tree}
    batch_lines = []
    for path in batch_paths:
        f = path_map.get(path, {})
        batch_lines.append(f"  {path} — {f.get('purpose', '')}  exports: {', '.join(f.get('exports', []))}")
    batch_files_str = "\n".join(batch_lines)

    tree_summary = "\n".join(f["path"] for f in file_tree)

    already_lines = []
    for path, content in generated.items():
        f = path_map.get(path, {})
        exports = f.get("exports", [])
        if exports:
            short = content[:150].replace("\n", "\\n")
            already_lines.append(f"  {path}  exports=[{', '.join(exports)}]  snippet=\"{short}\"")
    already_str = "\n".join(already_lines) or "None yet (first batch)."

    return BATCH_PROMPT_TEMPLATE.format(
        spec=spec_text[:3000],
        batch_files=batch_files_str,
        file_tree=tree_summary,
        already_generated=already_str,
    )


def _parse_batch_response(raw, batch_paths):
    cleaned = _clean_json(raw)

    strategies = [
        lambda d: json.loads(d),
        lambda d: json.loads(d.replace('\\\\n', '\\n')),
        lambda d: json.loads(d.replace('\\n', '\n')),
    ]

    for strategy in strategies:
        try:
            data = strategy(cleaned)
            if not isinstance(data, dict):
                continue
            result = {}
            for path in batch_paths:
                content = None
                if path in data and isinstance(data[path], str):
                    content = data[path]
                elif "files" in data and path in data["files"] and isinstance(data["files"][path], str):
                    content = data["files"][path]
                if content:
                    # Normalize escaped newlines: \\n (literal backslash-n) → actual newline
                    if "\\n" in content:
                        content = content.replace("\\n", "\n")
                    result[path] = content
            if result:
                return result
        except (json.JSONDecodeError, TypeError):
            continue

    # Regex fallback per file
    result = {}
    for path in batch_paths:
        escaped = re.escape(path)
        m = re.search(rf'"{escaped}"\s*:\s*"([^"]*\\[^"]*|[^"]*)"', raw, re.DOTALL)
        if not m:
            m = re.search(rf"'?'{escaped}'?'\s*:\s*'([^']*)'", raw, re.DOTALL)
        if m:
            content = m.group(1).replace('\\n', '\n').replace('\\"', '"').replace("\\\\", "\\")
            result[path] = content
    return result


def _clean_json(raw):
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif raw.startswith("```"):
        raw = raw.split("```")[1].split("```")[0].strip()
    return raw


def run_ai_development_cycle(project_title, chat_history, provider, model_name, temperature, progress_callback=None):
    print(f"[Codex Dev]: Starting batch multi-file generation for '{project_title}'...")

    spec_text = "\n".join(
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in chat_history
        if isinstance(msg, dict) and "role" in msg and "content" in msg
    ) or project_title

    # Phase 1: Structure
    print("[Codex Dev]: Phase 1/3 — Planning project structure...")
    if progress_callback: progress_callback("Planning project structure")
    file_tree = plan_project_structure(spec_text, provider, model_name, temperature)
    print(f"[Codex Dev]: {len(file_tree)} files planned")

    # Group into batches
    batches = _batch_files(file_tree)
    print(f"[Codex Dev]: Phase 2/3 — {len(batches)} batches of ~{BATCH_SIZE} files each")

    generated = {}
    total_errors = 0

    for batch_idx, batch_paths in enumerate(batches):
        task = f"Generating batch {batch_idx+1}/{len(batches)}: {batch_paths[0].split('/')[-1] if batch_paths else ''}"
        print(f"  Batch {batch_idx+1}/{len(batches)}: {', '.join(batch_paths)}")
        if progress_callback: progress_callback(task)
        prompt = _build_batch_prompt(batch_paths, file_tree, spec_text, generated)

        try:
            raw = ask_studio_ai_with_history(
                provider=provider, model_name=model_name,
                system_prompt="You are Codex. Return ONLY valid JSON.",
                chat_history=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=8192,
            )
            batch_result = _parse_batch_response(raw, batch_paths)
            ok = 0
            for path in batch_paths:
                if path in batch_result and batch_result[path].strip():
                    generated[path] = batch_result[path].strip()
                    ok += 1
                else:
                    total_errors += 1
            print(f"    -> {ok}/{len(batch_paths)} files OK")
        except Exception as e:
            print(f"    -> FAIL: {e}")
            total_errors += len(batch_paths)

        time.sleep(0.3)

    # Retry failed files individually
    if total_errors > 0:
        print(f"[Codex Dev]: Retrying {total_errors} failed files individually...")
        path_map = {f["path"]: f for f in file_tree}
        for idx, path in enumerate([p for p in path_map if p not in generated]):
            if progress_callback: progress_callback(f"Retrying {path.split('/')[-1]} ({idx+1}/{total_errors})")
            _gen_single(path, path_map[path], file_tree, spec_text, generated, provider, model_name, temperature)

    result = {"files": {}}
    for path, content in generated.items():
        result["files"][path] = content

    if not result["files"]:
        return {"error": True, "message": "No files were generated"}

    print(f"[Codex Dev]: Done — {len(result['files'])} files in {len(batches)} batches")
    if progress_callback: progress_callback("Code generation complete")
    return result


def _gen_single(path, file_spec, file_tree, spec_text, generated, provider, model, temperature):
    deps = file_spec.get("depends_on", [])
    sibling_lines = []
    for dep_path in deps:
        if dep_path in generated:
            f = next((x for x in file_tree if x["path"] == dep_path), None)
            exports = f.get("exports", []) if f else []
            sibling_lines.append(f"--- {dep_path} exports: {', '.join(exports)} ---")
            sibling_lines.append(generated[dep_path][:300])
    sibling_context = "\n".join(sibling_lines) or "No sibling files."
    tree_summary = "\n".join(f["path"] for f in file_tree)

    prompt = (
        f"You are Codex. Generate the file '{path}' for this project.\n\n"
        f"PROJECT SPEC:\n{spec_text[:2500]}\n\n"
        f"PURPOSE: {file_spec.get('purpose', '')}\n\n"
        f"PROJECT TREE:\n{tree_summary}\n\n"
        f"AVAILABLE IMPORTS:\n{sibling_context}\n\n"
        f"{_AI_RULES}\n"
        "- Write COMPLETE code — no placeholders, no TODOs, no pass statements\n"
        f"Return ONLY the file content as a raw string. No JSON, no markdown."
    )
    try:
        raw = ask_studio_ai_with_history(
            provider=provider, model_name=model,
            system_prompt="You are Codex. Generate ONLY file content, no JSON.",
            chat_history=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=8192,
        )
        content = _clean_single(raw)
        if content:
            generated[path] = content
            print(f"    Retry {path}: OK ({len(content)} chars)")
        else:
            print(f"    Retry {path}: EMPTY response")
    except Exception as e:
        print(f"    Retry {path}: FAIL {e}")


def _clean_single(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw
