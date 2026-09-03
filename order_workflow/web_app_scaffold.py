"""Keep a generated Vite+React app on the toolchain the smoke check actually opens.

The local coder can only write, never delete, and a 14B model freely invents Vite 2 plus a
Hello-World `src/main.jsx` that never imports `App.jsx`. The functional smoke check then
hits `http://localhost:4173` -- Vite 2's preview default is 5000 -- and, when a server
finally appears, sees no inputs because the entry file rendered an h1. Measured on
2026-09-01, b04 Tip Splitter: five repairs, `qa_failed`, App.jsx already had the calculator.

This is not a new gate. It rewrites only scaffold files (package.json, vite.config, the
entry mount) after every write so the existing preview/smoke/visual checks have a server
and a real App to load.
"""

from __future__ import annotations

import json
from pathlib import Path

REACT = "^18.3.1"
VITE = "^5.3.1"
PLUGIN_REACT = "^4.3.1"
PREVIEW_SCRIPT = "vite preview --host 127.0.0.1 --port 4173"
BUILD_SCRIPT = "vite build"

_VITE_CONFIG = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
});
"""

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="{entry}"></script>
  </body>
</html>
"""

_MAIN_JSX = """import {{ StrictMode }} from 'react';
import {{ createRoot }} from 'react-dom/client';
import App from '{app_import}';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>
);
"""


def looks_like_web_app_workspace(cwd: Path) -> bool:
    root = Path(cwd)
    return any(
        (root / name).is_file()
        for name in (
            "package.json",
            "vite.config.js",
            "vite.config.ts",
            "src/App.jsx",
            "src/App.tsx",
            "src/main.jsx",
            "src/main.tsx",
        )
    )


def reconcile_web_app_workspace(cwd: Path) -> tuple[str, ...]:
    """Pin Vite 5 + React 18 and mount App.jsx. No-op on bots and single-file pages."""
    root = Path(cwd)
    if not looks_like_web_app_workspace(root):
        return ()
    changed: list[str] = []
    app = _app_component(root)
    entry_rel = "src/main.tsx" if app and app.suffix == ".tsx" else "src/main.jsx"
    if _ensure_package_json(root):
        changed.append("package.json")
    if _ensure_vite_config(root):
        changed.append("vite.config.js")
    if _ensure_index_html(root, entry=f"/{entry_rel}"):
        changed.append("index.html")
    if app is not None and _ensure_main_entry(root, app=app, entry_rel=entry_rel):
        changed.append(entry_rel)
    return tuple(changed)


def _app_component(root: Path) -> Path | None:
    for name in ("src/App.jsx", "src/App.tsx"):
        path = root / name
        if path.is_file():
            return path
    return None


def _ensure_package_json(root: Path) -> bool:
    path = root / "package.json"
    payload: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            payload = {}
    scripts = dict(payload.get("scripts") or {})
    dependencies = dict(payload.get("dependencies") or {})
    dev_dependencies = dict(payload.get("devDependencies") or {})
    desired = {
        "name": str(payload.get("name") or "app"),
        "private": True,
        "version": str(payload.get("version") or "1.0.0"),
        "type": "module",
        "scripts": {
            **scripts,
            "build": BUILD_SCRIPT,
            "preview": PREVIEW_SCRIPT,
        },
        "dependencies": {**dependencies, "react": REACT, "react-dom": REACT},
        "devDependencies": {
            **dev_dependencies,
            "vite": VITE,
            "@vitejs/plugin-react": PLUGIN_REACT,
        },
    }
    if "dev" not in desired["scripts"]:
        desired["scripts"]["dev"] = "vite"
    text = json.dumps(desired, indent=2) + "\n"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _ensure_vite_config(root: Path) -> bool:
    path = root / "vite.config.js"
    text = _VITE_CONFIG if _VITE_CONFIG.endswith("\n") else _VITE_CONFIG + "\n"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current == text:
        return False
    path.write_text(text, encoding="utf-8")
    stale = root / "vite.config.ts"
    if stale.is_file() and stale != path:
        try:
            stale.unlink()
        except OSError:
            pass
    return True


def _ensure_index_html(root: Path, *, entry: str) -> bool:
    path = root / "index.html"
    wanted = _INDEX_HTML.format(entry=entry)
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if 'id="root"' in current and entry in current and "type=\"module\"" in current:
            return False
    path.write_text(wanted if wanted.endswith("\n") else wanted + "\n", encoding="utf-8")
    return True


def _ensure_main_entry(root: Path, *, app: Path, entry_rel: str) -> bool:
    path = root / entry_rel
    app_import = "./App.tsx" if app.suffix == ".tsx" else "./App.jsx"
    wanted = _MAIN_JSX.format(app_import=app_import)
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        mounts_app = "from './App" in current.replace('"', "'") or 'from "./App' in current
        uses_create_root = "createRoot" in current
        stub = "Hello, Freelancer Studio" in current or "Hello, FreelancerStudio" in current
        if mounts_app and uses_create_root and not stub:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(wanted if wanted.endswith("\n") else wanted + "\n", encoding="utf-8")
    return True
