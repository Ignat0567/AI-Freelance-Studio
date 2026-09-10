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
TEST_SCRIPT = "vitest run"
# --no-error-on-unmatched-pattern so the gate reports "clean" rather than "failed" for a
# workspace that has no src/*.jsx yet. A check must never fail because there was nothing
# to check; that is the vacuous pass qa_runner is careful about elsewhere, in reverse.
LINT_SCRIPT = "eslint src --no-error-on-unmatched-pattern"

# `npm test` runs inside the node:20-slim Docker image (docker_qa_runner.py), permanently --
# see project_vite8_frontend_upgrade_deferred. Every version below is pinned against that,
# not against "latest": vitest 5.x needs Vite >=6 (incompatible with VITE above); jsdom's own
# Node floor rose from a plain >=20 to >=20.19 (27.1.0) then to >=22 (30.0.0); jest-dom's rose
# straight to >=22 at 6.10.0/7.0.0. Bump any of these only after checking `npm view <pkg>@<new
# version> engines` against the image's actual node --version, not the registry's "latest".
VITEST = "^3.2.7"
JSDOM = "^27.0.1"
TESTING_LIBRARY_REACT = "^16.3.3"
TESTING_LIBRARY_JEST_DOM = "^6.9.1"
TESTING_LIBRARY_DOM = "^10.4.1"
# Same rule as the block above -- these are pinned against node:20-slim, not against
# "latest". Measured 2026-09-10: eslint 9.39.5 declares ^18.18 || ^20.9 || >=21.1, and the
# other two declare >=18, so all three run on the image. eslint 10, whenever it lands, is
# the one to check before bumping.
ESLINT = "^9.39.5"
ESLINT_GLOBALS = "^17.12.0"
ESLINT_PLUGIN_REACT_HOOKS = "^7.1.1"

_VITE_CONFIG = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.js'],
    include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'],
  },
});
"""

_VITEST_SETUP = """import '@testing-library/jest-dom/vitest';
"""

# Two rules, both measured on 2026-09-10 against a deliberately broken component, and both
# chosen because `vite build` passes the file anyway:
#
#   no-undef                     `setCoutn(count + 1)` in an onClick -- builds, ships, and
#                                throws the first time a client presses the button. esbuild
#                                does no identifier resolution, so the build cannot see it.
#   react-hooks/rules-of-hooks   a useState behind an `if` -- renders until the branch
#                                flips, then corrupts every hook after it.
#
# Nothing stylistic is enabled, on purpose. A finding here costs a repair call, so a rule
# that fires on working code converts straight into money; the same reasoning that keeps
# ruff.toml narrow at Studio's own root.
#
# The second block is not optional. `no-undef` with browser globals alone reports describe,
# it and expect in every generated test file, which would have failed every core_feature
# phase that wrote one -- i.e. all of them. Measured before shipping, not after.
#
# `<MissingWidget />` is NOT caught: no-undef does not resolve JSX component names, and
# catching it needs eslint-plugin-react for one rule. Left out deliberately -- an undefined
# component renders a blank page, which the visual and smoke gates already fail on.
_ESLINT_CONFIG = """import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

const testGlobals = {
  describe: 'readonly', it: 'readonly', test: 'readonly', expect: 'readonly',
  beforeAll: 'readonly', afterAll: 'readonly', beforeEach: 'readonly', afterEach: 'readonly',
  vi: 'readonly', suite: 'readonly',
};

export default [
  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'no-undef': 'error',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
  {
    files: ['src/**/*.{test,spec}.{js,jsx}'],
    languageOptions: { globals: testGlobals },
  },
];
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
    if _ensure_eslint_config(root):
        changed.append("eslint.config.js")
    if _ensure_vitest_setup(root):
        changed.append("vitest.setup.js")
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
            # No --passWithNoTests: `core_feature`'s own prompt already tells the model to
            # "wire the feature and its test". A missing test is a defect to repair, the same
            # bar the Claude Code/OpenCode-authored path has always had to clear -- the gap
            # measured live 2026-09-03 (b03/b04) was that nothing installed a test runner for
            # this write-only path, not that the requirement should be softer.
            "test": TEST_SCRIPT,
            "lint": LINT_SCRIPT,
        },
        "dependencies": {**dependencies, "react": REACT, "react-dom": REACT},
        "devDependencies": {
            **dev_dependencies,
            "vite": VITE,
            "@vitejs/plugin-react": PLUGIN_REACT,
            "vitest": VITEST,
            "jsdom": JSDOM,
            "@testing-library/react": TESTING_LIBRARY_REACT,
            "@testing-library/jest-dom": TESTING_LIBRARY_JEST_DOM,
            "@testing-library/dom": TESTING_LIBRARY_DOM,
            "eslint": ESLINT,
            "globals": ESLINT_GLOBALS,
            "eslint-plugin-react-hooks": ESLINT_PLUGIN_REACT_HOOKS,
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


def _ensure_eslint_config(root: Path) -> bool:
    path = root / "eslint.config.js"
    text = _ESLINT_CONFIG if _ESLINT_CONFIG.endswith("\n") else _ESLINT_CONFIG + "\n"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current == text:
        return False
    path.write_text(text, encoding="utf-8")
    # eslint 9 reads exactly one flat config and refuses to start if an .eslintrc* is also
    # present: a model that has seen a lot of pre-9 React will write one, and its presence
    # turns the gate from "reports findings" into "cannot run", which reads as a failure
    # nobody can act on. Same treatment vite.config.ts gets above.
    for stale_name in (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml"):
        stale = root / stale_name
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass
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


def _ensure_vitest_setup(root: Path) -> bool:
    """Register jest-dom's matchers on vitest's own `expect`, not a global-scope shim --
    the exact wiring `test: { environment: 'jsdom', setupFiles: [...] }` in vite.config.js
    points at. Only written if missing: a model-authored setup file (e.g. one that also
    configures MSW or a mock) is left alone as long as it already imports jest-dom/vitest."""
    path = root / "vitest.setup.js"
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if "@testing-library/jest-dom/vitest" in current or "@testing-library/jest-dom" in current:
            return False
    text = _VITEST_SETUP if _VITEST_SETUP.endswith("\n") else _VITEST_SETUP + "\n"
    if path.is_file():
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n" + text, encoding="utf-8")
    else:
        path.write_text(text, encoding="utf-8")
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
