"""The b04 Tip Splitter of 2026-09-01 compiled a calculator in App.jsx and then mounted a
Hello-World stub from main.jsx on Vite 2. The smoke check never saw an input.
"""

from __future__ import annotations

import json
from pathlib import Path

from order_workflow.web_app_scaffold import (
    PLUGIN_REACT,
    PREVIEW_SCRIPT,
    REACT,
    VITE,
    looks_like_web_app_workspace,
    reconcile_web_app_workspace,
)


def test_a_python_bot_workspace_is_left_alone(tmp_path: Path):
    (tmp_path / "bot.py").write_text("print('hi')\n", encoding="utf-8")

    assert looks_like_web_app_workspace(tmp_path) is False
    assert reconcile_web_app_workspace(tmp_path) == ()
    assert list(tmp_path.iterdir()) == [tmp_path / "bot.py"]


def test_a_single_html_file_is_left_alone(tmp_path: Path):
    (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")

    assert looks_like_web_app_workspace(tmp_path) is False
    assert reconcile_web_app_workspace(tmp_path) == ()


def test_vite2_stub_entry_is_rewritten_to_mount_app(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "freelancerstudio",
                "scripts": {"build": "vite build", "preview": "vite preview --port 4173"},
                "dependencies": {"react": "^17.0.2", "react-dom": "^17.0.2"},
                "devDependencies": {"vite": "^2.6.4", "@vitejs/plugin-react": "^1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "vite.config.js").write_text(
        "import { defineConfig } from 'vite';\nexport default defineConfig({ server: { port: 4173 } });\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.jsx").write_text(
        "export default function App() { return <input id='total' />; }\n",
        encoding="utf-8",
    )
    (src / "main.jsx").write_text(
        "import React from 'react';\nimport ReactDOM from 'react-dom';\n"
        "function App() { return <h1>Hello, Freelancer Studio!</h1>; }\n"
        "ReactDOM.render(<App />, document.getElementById('root'));\n",
        encoding="utf-8",
    )

    changed = reconcile_web_app_workspace(tmp_path)

    assert "package.json" in changed
    assert "src/main.jsx" in changed
    assert "vite.config.js" in changed
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert package["type"] == "module"
    assert package["dependencies"]["react"] == REACT
    assert package["devDependencies"]["vite"] == VITE
    assert package["devDependencies"]["@vitejs/plugin-react"] == PLUGIN_REACT
    assert package["scripts"]["preview"] == PREVIEW_SCRIPT
    assert package["scripts"]["build"] == "vite build"
    main = (src / "main.jsx").read_text(encoding="utf-8")
    assert "from './App.jsx'" in main
    assert "createRoot" in main
    assert "Hello, Freelancer Studio" not in main
    assert (src / "App.jsx").read_text(encoding="utf-8").count("input") == 1
    vite = (tmp_path / "vite.config.js").read_text(encoding="utf-8")
    assert "port: 4173" in vite
    assert "127.0.0.1" in vite


def test_a_correct_scaffold_is_not_rewritten(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")
    first = set(reconcile_web_app_workspace(tmp_path))
    second = reconcile_web_app_workspace(tmp_path)

    assert "package.json" in first
    assert second == ()
