"""Base Vite+React project scaffold shared by every generated cinematic website.

Everything here is static except `frontend/src/App.jsx`, which `materialize.py`
assembles from the caller's selected sections (import order = render order).
"""

from __future__ import annotations

import json

_BASE_DEPENDENCIES = {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
}
_BASE_DEV_DEPENDENCIES = {
    "@vitejs/plugin-react": "^4.3.1",
    "vite": "^5.3.1",
}

# Pinned to versions compatible with React 18 (the version this scaffold ships).
# @react-three/fiber's "latest" (v9) requires React 19 and breaks npm install.
_DEPENDENCY_VERSIONS = {
    "three": "^0.169.0",
    "@react-three/fiber": "^8.17.10",
    "gsap": "^3.12.5",
}

_VITE_CONFIG = "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n\nexport default defineConfig({ plugins: [react()] });\n"

_MAIN_JSX = (
    "import React from 'react';\n"
    "import { createRoot } from 'react-dom/client';\n"
    "import App from './App.jsx';\n"
    "import './tokens.css';\n"
    "import './index.css';\n\n"
    "createRoot(document.getElementById('root')).render(<App />);\n"
)

_INDEX_CSS = (
    "*{box-sizing:border-box;margin:0;padding:0}\n"
    "html{scroll-behavior:smooth}\n"
    "body{font-family:var(--font-body, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);"
    "background:var(--color-background, #ffffff);color:var(--color-text, #12141a)}\n"
    "h1,h2,h3,h4{font-family:var(--font-heading, inherit)}\n"
    "a{color:inherit}\n"
)


def index_html(project_title: str) -> str:
    safe_title = project_title.replace("<", "&lt;").replace(">", "&gt;") or "AI Freelance Studio"
    return f'<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>{safe_title}</title></head><body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>'


def package_json(project_name: str, extra_dependencies: tuple[str, ...]) -> str:
    dependencies = dict(_BASE_DEPENDENCIES)
    for dependency in extra_dependencies:
        dependencies.setdefault(dependency, _DEPENDENCY_VERSIONS.get(dependency, "latest"))
    payload = {
        "name": project_name,
        "version": "1.0.0",
        "private": True,
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "dependencies": dependencies,
        "devDependencies": dict(_BASE_DEV_DEPENDENCIES),
    }
    return json.dumps(payload, indent=2)


def app_jsx(imports: tuple[str, ...], renders: tuple[str, ...]) -> str:
    import_lines = "\n".join(imports)
    render_lines = "\n      ".join(renders)
    return (
        f"{import_lines}\n\n"
        "export default function App() {\n"
        "  return (\n"
        "    <main>\n"
        f"      {render_lines}\n"
        "    </main>\n"
        "  );\n"
        "}\n"
    )


def base_files(project_name: str, project_title: str, extra_dependencies: tuple[str, ...]) -> dict[str, str]:
    return {
        "frontend/package.json": package_json(project_name, extra_dependencies),
        "frontend/vite.config.js": _VITE_CONFIG,
        "frontend/index.html": index_html(project_title),
        "frontend/src/main.jsx": _MAIN_JSX,
        "frontend/src/index.css": _INDEX_CSS,
    }
