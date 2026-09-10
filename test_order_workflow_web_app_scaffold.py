"""The b04 Tip Splitter of 2026-09-01 compiled a calculator in App.jsx and then mounted a
Hello-World stub from main.jsx on Vite 2. The smoke check never saw an input.
"""

from __future__ import annotations

import json
from pathlib import Path

from order_workflow.web_app_scaffold import (
    JSDOM,
    LINT_SCRIPT,
    PLUGIN_REACT,
    PREVIEW_SCRIPT,
    REACT,
    TEST_SCRIPT,
    TESTING_LIBRARY_DOM,
    TESTING_LIBRARY_JEST_DOM,
    TESTING_LIBRARY_REACT,
    VITE,
    VITEST,
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
    assert package["scripts"]["test"] == TEST_SCRIPT
    assert "--passWithNoTests" not in package["scripts"]["test"], (
        "a missing test must fail the gate -- core_feature's prompt already asks for one"
    )
    assert package["devDependencies"]["vitest"] == VITEST
    assert package["devDependencies"]["jsdom"] == JSDOM
    assert package["devDependencies"]["@testing-library/react"] == TESTING_LIBRARY_REACT
    assert package["devDependencies"]["@testing-library/jest-dom"] == TESTING_LIBRARY_JEST_DOM
    assert package["devDependencies"]["@testing-library/dom"] == TESTING_LIBRARY_DOM
    main = (src / "main.jsx").read_text(encoding="utf-8")
    assert "from './App.jsx'" in main
    assert "createRoot" in main
    assert "Hello, Freelancer Studio" not in main
    assert (src / "App.jsx").read_text(encoding="utf-8").count("input") == 1
    vite = (tmp_path / "vite.config.js").read_text(encoding="utf-8")
    assert "port: 4173" in vite
    assert "127.0.0.1" in vite
    assert "environment: 'jsdom'" in vite
    assert "./vitest.setup.js" in vite
    assert "include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}']" in vite
    assert "vitest.setup.js" in changed
    setup = (tmp_path / "vitest.setup.js").read_text(encoding="utf-8")
    assert "@testing-library/jest-dom/vitest" in setup


def test_a_correct_scaffold_is_not_rewritten(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")
    first = set(reconcile_web_app_workspace(tmp_path))
    second = reconcile_web_app_workspace(tmp_path)

    assert "package.json" in first
    assert "vitest.setup.js" in first
    assert second == ()


def test_a_model_authored_setup_file_is_extended_not_replaced(tmp_path: Path):
    """A model that already wrote its own vitest.setup.js (a mock server, extra matchers)
    must not have that work silently discarded just because jest-dom's import is missing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")
    (tmp_path / "vitest.setup.js").write_text("import './some-mock-server-setup.js';\n", encoding="utf-8")

    changed = reconcile_web_app_workspace(tmp_path)

    assert "vitest.setup.js" in changed
    setup = (tmp_path / "vitest.setup.js").read_text(encoding="utf-8")
    assert "./some-mock-server-setup.js" in setup
    assert "@testing-library/jest-dom/vitest" in setup

    # And now it is left alone: the model's line plus ours together already satisfy it.
    second = reconcile_web_app_workspace(tmp_path)
    assert "vitest.setup.js" not in second


def test_a_model_authored_setup_file_with_jest_dom_already_is_left_alone(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")
    original = "import '@testing-library/jest-dom';\nimport './extra-setup.js';\n"
    (tmp_path / "vitest.setup.js").write_text(original, encoding="utf-8")

    changed = reconcile_web_app_workspace(tmp_path)

    assert "vitest.setup.js" not in changed
    assert (tmp_path / "vitest.setup.js").read_text(encoding="utf-8") == original


def test_the_lint_gate_is_scaffolded_with_its_config_and_its_dependencies(tmp_path: Path):
    """`vite build` resolves no identifiers, so `setCoutn(count + 1)` in an onClick compiles,
    ships, and throws the first time a client presses the button. Verified end to end on
    2026-09-10: a component with that typo, an undefined variable and a conditional useState
    built clean with exit 0 and failed the lint gate with three findings."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")

    changed = reconcile_web_app_workspace(tmp_path)

    assert "eslint.config.js" in changed
    payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert payload["scripts"]["lint"] == LINT_SCRIPT
    for package in ("eslint", "globals", "eslint-plugin-react-hooks"):
        assert package in payload["devDependencies"], f"the gate cannot run without {package}"


def test_the_lint_config_exempts_generated_tests_from_undefined_globals(tmp_path: Path):
    """Without this block `no-undef` reports describe, it and expect in every generated test
    file, which is every core_feature phase that writes one. A gate that fires on working
    code costs a repair call each time, which is the opposite of why it exists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")

    reconcile_web_app_workspace(tmp_path)

    config = (tmp_path / "eslint.config.js").read_text(encoding="utf-8")
    assert "'src/**/*.{test,spec}.{js,jsx}'" in config
    for name in ("describe", "it", "expect", "vi"):
        assert f"{name}:" in config


def test_a_legacy_eslintrc_is_removed_rather_than_left_to_stop_the_gate(tmp_path: Path):
    """eslint 9 reads one flat config and refuses to start beside an .eslintrc*. A model that
    has seen a lot of pre-9 React writes one, and its presence turns the gate from "reports
    findings" into "cannot run" -- a failure nobody can act on."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")
    (tmp_path / ".eslintrc.json").write_text('{"extends": "eslint:recommended"}', encoding="utf-8")

    reconcile_web_app_workspace(tmp_path)

    assert not (tmp_path / ".eslintrc.json").exists()
    assert (tmp_path / "eslint.config.js").is_file()


def test_the_gate_reports_clean_rather_than_failing_when_there_is_nothing_to_lint(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <button>Go</button>; }\n", encoding="utf-8")

    reconcile_web_app_workspace(tmp_path)

    assert "--no-error-on-unmatched-pattern" in LINT_SCRIPT
