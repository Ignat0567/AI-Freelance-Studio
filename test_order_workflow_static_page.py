"""ProductType.STATIC_PAGE: the prompt, the file-shape gate, and the adapter wiring.

The browser half of the gate (draw calls, frame rate, overflow) needs Docker and a real
Playwright run, so it is covered by asserting the script the container executes contains the
measurements it claims to make -- the same approach the other Playwright gates' unit tests
take. The file-shape half runs entirely in Python and is tested for real.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from order_workflow import AgentHandoff, ElenaDesignChoice, ProjectBrief, RecommendedStack
from order_workflow.models import ProductType, SUPPORTED_PRODUCT_TYPES
from order_workflow.phase_prompts import (
    STATIC_PAGE_ALLOWED_HOSTS,
    STATIC_PAGE_FILENAME,
    STATIC_PAGE_MAX_BYTES,
    build_static_page_prompt,
)
from order_workflow.static_page_check import (
    ALLOWED_ASSET_HOSTS,
    inspect_static_page_files,
    run_static_page_check_in_docker,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def _brief(**changes) -> ProjectBrief:
    values = {
        "id": "brief_static",
        "order_id": "order_static",
        "product_type": ProductType.STATIC_PAGE,
        "goal": "A living, breathing nature world: mossy branches floating in volumetric fog.",
        "target_users": ("Single local user",),
        "core_features": ("An orbiting 3D scene with a butterfly that lands on a branch",),
        "acceptance_criteria": ("The scene renders and keeps animating.",),
        "recommended_stack": RecommendedStack(frontend="Single-file HTML + inline JS (no framework, no build)", backend="None", storage="None"),
        "elena_design_choice": ElenaDesignChoice.PROCEED_DIRECTLY,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProjectBrief(**values)


def _handoff(**changes) -> AgentHandoff:
    values = {
        "id": "handoff_static",
        "order_id": "order_static",
        "brief_id": "brief_static",
        "source_agent": "alex",
        "target_agent": "codex",
        "goal": "Build the approved single-file nature scene.",
        "context_summary": "One self-contained page, delivered as a single file.",
        "requirements": ("Hero headline and supporting copy", "Frosted glass cards", "Butterfly that flies off on hover"),
        "acceptance_criteria": ("The scene renders and keeps animating.",),
        "created_at": NOW,
    }
    values.update(changes)
    return AgentHandoff(**values)


def _page(tmp_path: Path, body: str = "<canvas></canvas>") -> Path:
    page = tmp_path / STATIC_PAGE_FILENAME
    page.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
    return page


def test_static_page_is_an_accepted_product_type():
    assert ProductType.STATIC_PAGE in SUPPORTED_PRODUCT_TYPES


def test_prompt_forbids_the_npm_project_shape_the_web_app_pipeline_assumes():
    prompt = build_static_page_prompt(_brief(), _handoff())

    lowered = prompt.lower()
    assert "no build step" in lowered
    assert "package.json" in lowered
    assert STATIC_PAGE_FILENAME in prompt
    # The web-app pipeline's rules must not leak in: there is no preview server to ask for
    # and no framework to forbid a database in.
    assert "npm run preview" not in lowered
    assert "react" in lowered  # only as the thing it is NOT
    assert "this is not a react/vite project" in lowered


def test_prompt_states_the_gate_s_own_measurements():
    prompt = build_static_page_prompt(_brief(), _handoff())

    assert "webgl" in prompt.lower()
    assert "draw calls" in prompt.lower()
    assert "requestanimationframe" in prompt.lower()
    assert "768px" in prompt
    assert "24x24px" in prompt
    for host in STATIC_PAGE_ALLOWED_HOSTS:
        assert host in prompt


def test_prompt_leaves_the_art_direction_to_the_client():
    # The inversion that makes this product type possible: enforcing Elena's palette on a
    # deliberately moody scene would make the order contradict itself.
    prompt = build_static_page_prompt(_brief(), _handoff())

    assert "authoritative" in prompt.lower()
    assert "do not substitute a different palette" in prompt.lower()


def test_prompt_carries_the_requirements_and_client_additions():
    prompt = build_static_page_prompt(_brief(), _handoff(), additions="  Keep   the fog subtle. ")

    assert "Butterfly that flies off on hover" in prompt
    assert "Keep the fog subtle." in prompt
    # Client notes are placed before the strict rules, which then declare precedence.
    assert prompt.index("Keep the fog subtle.") < prompt.index("Strict rules for this phase")


def test_allowed_hosts_cannot_drift_between_the_prompt_and_the_gate():
    assert ALLOWED_ASSET_HOSTS == STATIC_PAGE_ALLOWED_HOSTS


def test_file_shape_accepts_a_single_page(tmp_path):
    _page(tmp_path)

    assert inspect_static_page_files(tmp_path) == []


def test_file_shape_rejects_a_missing_page(tmp_path):
    (tmp_path / "scene.html").write_text("<html></html>", encoding="utf-8")

    failures = inspect_static_page_files(tmp_path)

    assert len(failures) == 1
    assert STATIC_PAGE_FILENAME in failures[0]


def test_file_shape_rejects_sibling_sources_and_build_config(tmp_path):
    _page(tmp_path)
    (tmp_path / "scene.js").write_text("export const x = 1;", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "vite.config.js").write_text("export default {}", encoding="utf-8")

    report = " ".join(inspect_static_page_files(tmp_path))

    assert "scene.js" in report
    assert "styles.css" in report
    assert "vite.config.js" in report


def test_file_shape_rejects_a_second_html_file(tmp_path):
    _page(tmp_path)
    (tmp_path / "about.html").write_text("<html></html>", encoding="utf-8")

    report = " ".join(inspect_static_page_files(tmp_path))

    assert "exactly one HTML file" in report
    assert "about.html" in report


def test_file_shape_rejects_an_oversized_page(tmp_path):
    page = tmp_path / STATIC_PAGE_FILENAME
    page.write_text("<html>" + ("x" * (STATIC_PAGE_MAX_BYTES + 10)) + "</html>", encoding="utf-8")

    report = " ".join(inspect_static_page_files(tmp_path))

    assert "over the" in report and "KB limit" in report


def test_file_shape_ignores_the_pipeline_s_own_footprint(tmp_path):
    # node_modules from the gate's own Playwright install, checkpoints, prompt records and
    # delivery docs are not the client's deliverable -- counting them would fail every run.
    _page(tmp_path)
    (tmp_path / "node_modules" / "playwright").mkdir(parents=True)
    (tmp_path / "node_modules" / "playwright" / "index.js").write_text("module.exports={}", encoding="utf-8")
    (tmp_path / "___freelancerstudio_static_check.mjs").write_text("//", encoding="utf-8")
    (tmp_path / "execution_prompt_static_page_build.md").write_text("prompt", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")

    assert inspect_static_page_files(tmp_path) == []


def test_file_shape_failure_short_circuits_before_docker(tmp_path):
    # No page at all: the runner must report it without a container ever being created, so
    # the cheapest certain failure costs milliseconds rather than an image pull.
    def _explode(*_args, **_kwargs):
        raise AssertionError("Docker must not be touched when the file shape already failed")

    outcome = run_static_page_check_in_docker((), tmp_path, docker_client_factory=_explode)

    assert outcome.passed is False
    assert STATIC_PAGE_FILENAME in outcome.failure_summary()


def test_the_container_script_measures_what_the_module_claims(tmp_path):
    # The browser half cannot run in a unit test, so assert the script handed to the
    # container actually contains each measurement this gate advertises.
    _page(tmp_path)
    captured = {}

    def _fake_docker(*_args, **_kwargs):
        captured["script"] = (tmp_path / "___freelancerstudio_static_check.mjs").read_text(encoding="utf-8")
        captured["server"] = (tmp_path / "___freelancerstudio_static_server.mjs").read_text(encoding="utf-8")
        raise RuntimeError("stop here: the script has been captured")

    with pytest.raises(RuntimeError):
        run_static_page_check_in_docker((), tmp_path, docker_client_factory=_fake_docker)

    script = captured["script"]
    assert "drawArrays" in script and "drawElements" in script  # draw-call instrumentation
    assert "requestAnimationFrame" in script  # frame counting
    assert "addInitScript" in script  # installed before the page's own script runs
    assert "webgl2" in script
    assert "scrollWidth - de.clientWidth" in script
    assert "768" in script
    assert "pageerror" in script
    assert "swiftshader" in script  # software GL, or WebGL is unavailable in the container
    # The page is served over http rather than opened as file://, or every CDN import fails
    # for a reason unrelated to the page being correct.
    assert "http://localhost:4173" in script
    assert "createServer" in captured["server"]


def test_the_gate_does_not_read_pixels(tmp_path):
    # Deliberate: a WebGL canvas created without preserveDrawingBuffer reads back blank
    # after compositing, so a pixel check would fail on perfectly good pages. If someone
    # adds one later, this test should make them explain why.
    _page(tmp_path)
    captured = {}

    def _fake_docker(*_args, **_kwargs):
        captured["script"] = (tmp_path / "___freelancerstudio_static_check.mjs").read_text(encoding="utf-8")
        raise RuntimeError("captured")

    with pytest.raises(RuntimeError):
        run_static_page_check_in_docker((), tmp_path, docker_client_factory=_fake_docker)

    assert "toDataURL" not in captured["script"]
    assert "readPixels" not in captured["script"]
