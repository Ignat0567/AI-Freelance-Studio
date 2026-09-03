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
from order_workflow.complexity import describe_phase_complexity
from order_workflow.models import ElenaDesignConcept, ProductType, SUPPORTED_PRODUCT_TYPES, ThemePalette
from order_workflow.phase_prompts import (
    STATIC_PAGE_ALLOWED_HOSTS,
    STATIC_PAGE_FILENAME,
    STATIC_PAGE_MAX_BYTES,
    build_static_page_prompt,
)
from order_workflow.static_page_check import (
    ALLOWED_ASSET_HOSTS,
    finish_static_page_background,
    inspect_static_page_files,
    reconcile_static_page_workspace,
    resolve_static_page_background,
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


def test_a_webgl_scene_routes_to_the_stronger_model_for_a_real_reason():
    # Regression on the routing *justification*, not just the choice: the first live run of
    # this product type reported "3 project-specific technical constraints" -- three lines
    # the pipeline had injected into the brief about its own single-file/CDN rules. Right
    # model, invented reason. The graphics keywords carry the actual signal.
    complexity, reason = describe_phase_complexity(
        _brief(), focus_text="A Three.js scene with volumetric fog and a custom shader"
    )

    assert complexity == "complex"
    assert "three.js" in reason
    assert "constraints" not in reason


def test_the_static_page_brief_does_not_inject_pipeline_rules_as_project_constraints():
    from order_workflow.complexity import substantive_technical_constraints

    brief = _brief(technical_constraints=())

    assert substantive_technical_constraints(brief) == ()


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


def test_an_unstyled_form_fails_the_website_shape_floor(tmp_path):
    (tmp_path / STATIC_PAGE_FILENAME).write_text(
        "<!doctype html><html><body><input id='total'><button>10%</button></body></html>",
        encoding="utf-8",
    )

    failures = inspect_static_page_files(tmp_path)

    assert any("heading" in item.lower() for item in failures)
    assert any("css" in item.lower() for item in failures)


def test_a_designed_website_file_passes_the_shape_floor(tmp_path):
    (tmp_path / STATIC_PAGE_FILENAME).write_text(
        "<!doctype html><html><head><style>body{margin:0}button{padding:12px 20px}</style></head>"
        "<body><h1>Harbour Bakery</h1><button>See today's loaves</button></body></html>",
        encoding="utf-8",
    )

    assert inspect_static_page_files(tmp_path) == []


def test_a_webgl_canvas_page_is_not_held_to_the_website_shape_floor(tmp_path):
    _page(tmp_path)

    assert inspect_static_page_files(tmp_path) == []


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


def test_the_gate_waits_for_its_own_server_instead_of_trusting_a_sleep(tmp_path):
    """The static server is backgrounded by the shell command that launches the check, so a
    slow start would read as a broken page. The visual and state-continuity gates already
    retried; the smoke gate did not and it cost a wasted coding-CLI call on a live run
    (2026-08-17). Fixed in all four rather than in the one that happened to be caught."""
    _page(tmp_path)
    captured = {}

    def _fake_docker(*_args, **_kwargs):
        captured["script"] = (tmp_path / "___freelancerstudio_static_check.mjs").read_text(encoding="utf-8")
        raise RuntimeError("stop here: the script has been captured")

    with pytest.raises(RuntimeError):
        run_static_page_check_in_docker((), tmp_path, docker_client_factory=_fake_docker)

    script = captured["script"]
    assert "for (let attempt = 0; attempt < 15" in script
    assert "lastError" in script
    assert "the local server never accepted a connection" in script


def test_the_static_page_delivery_tells_the_client_to_open_the_file(tmp_path):
    """Caught by the first real Aurora delivery: the report handed a client
    `npm install && npm run preview` for a single self-contained HTML file."""
    from pathlib import Path

    source = Path("order_workflow/phased_adapter.py").read_text(encoding="utf-8")
    static_block = source.split("class StaticPageExecutionAdapter")[1]

    assert "run_instruction=" in static_block
    assert "Open `index.html`" in static_block
    assert "nothing to install" in static_block


def test_a_single_file_page_hands_back_the_proof_its_criterion_asks_for():
    """MVP_ACCEPTANCE criterion 2 wants a screenshot and an HTTP 200 in every delivered
    folder. The static-page path builds no container, so on 2026-08-27 its folder had
    neither, and its report answered that question with "container packaging was not part of
    this run" -- a statement rather than evidence. The check was already serving the page
    over HTTP and already had it open in a browser."""
    from order_workflow.static_page_check import _CHECK_SCRIPT
    from order_workflow.visual_check import SCREENSHOT_FILENAME

    assert SCREENSHOT_FILENAME in _CHECK_SCRIPT
    assert "page.screenshot(" in _CHECK_SCRIPT
    assert "Served over HTTP: ${httpStatus}" in _CHECK_SCRIPT
    # Taken before the viewport is reshaped for the tablet measurement, or the delivery photo
    # would show a layout no client asked about.
    assert _CHECK_SCRIPT.index("page.screenshot(") < _CHECK_SCRIPT.index("setViewportSize({ width: 768")


def test_a_failed_screenshot_cannot_fail_the_static_page_gate():
    # Documentation must never be able to fail a page that rendered correctly.
    from order_workflow.static_page_check import _CHECK_SCRIPT

    after = _CHECK_SCRIPT[_CHECK_SCRIPT.index("page.screenshot("):]
    assert after.index("catch") < after.index("STATIC PAGE CHECK PASSED")
    assert "screenshot skipped: " in _CHECK_SCRIPT


# --- the workspace the gate itself creates ---------------------------------------------


def test_a_windows_npm_junction_cannot_crash_the_gate(tmp_path, monkeypatch):
    """Live b02 of 2026-08-28: the page was built, its QA ran, a repair ran, and then the
    execution died with

        OSError: [WinError 1920] ... node_modules\.bin\playwright

    npm on Windows writes `node_modules/.bin` entries as NTFS junction points, and `stat()`
    raises on them instead of reporting a type. The gate installs Playwright into the
    workspace itself, so it creates the very entry that killed it. The same defect cost a run
    on 2026-08-10 in `scan_meaningful_generated_artifacts`, where it was guarded; this walk
    was the copy that was missed.
    """
    from order_workflow.static_page_check import _delivered_files

    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    junction = tmp_path / "node_modules" / ".bin"
    junction.mkdir(parents=True)
    (junction / "playwright").write_text("shim", encoding="utf-8")

    real_is_file = Path.is_file

    def exploding_is_file(self):
        if ".bin" in self.parts:
            raise OSError(1920, "the file cannot be accessed by the system")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", exploding_is_file)

    assert _delivered_files(tmp_path) == [Path("index.html")]


def test_reconcile_survives_a_windows_npm_junction_on_is_dir(tmp_path, monkeypatch):
    """The same junction that `_delivered_files` already skipped still killed reconcile:
    `_remove_empty_dirs` called `is_dir()` *before* the node_modules name filter, and
    WinError 1920 aborted the repair loop of 2026-09-01 after Grok had returned.
    """
    from order_workflow.static_page_check import reconcile_static_page_workspace

    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    junction = tmp_path / "node_modules" / ".bin"
    junction.mkdir(parents=True)
    (junction / "playwright").write_text("shim", encoding="utf-8")

    real_is_dir = Path.is_dir

    def exploding_is_dir(self):
        if ".bin" in self.parts:
            raise OSError(1920, "the file cannot be accessed by the system")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", exploding_is_dir)
    reconcile_static_page_workspace(tmp_path)
    assert (tmp_path / "index.html").is_file()


def test_an_entry_that_cannot_answer_is_not_a_file(tmp_path, monkeypatch):
    from order_workflow.workspace import is_regular_file

    target = tmp_path / "whatever"

    def exploding(self):
        raise OSError(1920, "the file cannot be accessed by the system")

    monkeypatch.setattr(Path, "is_file", exploding)

    assert is_regular_file(target) is False


def test_the_tap_target_rule_is_the_one_the_visual_gate_uses():
    """This gate carried its own copy, written before WCAG 2.5.8's exceptions were applied to
    the other one. It cost b02 a repair on 2026-08-28: "Tap targets under 24x24px at 768px: a
    43x16px, a 32x16px, a 49x16px" -- three inline links in a sentence, all of them fine."""
    from order_workflow.browser_rules import TAP_TARGET_MESSAGE_JS, TAP_TARGET_RULE_JS
    from order_workflow.static_page_check import _CHECK_SCRIPT
    from order_workflow.visual_check import _build_script, ExpectedPalette

    visual = _build_script(ExpectedPalette(background="#0b0f1a", colors=("#0b0f1a",)))

    for script in (_CHECK_SCRIPT, visual):
        assert TAP_TARGET_RULE_JS.strip() in script
        assert TAP_TARGET_MESSAGE_JS.strip() in script
        assert "Tap targets under 24x24px" not in script  # the nameless list both printed


def test_reconcile_promotes_the_richest_html_and_drops_sibling_sources(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><h1>Hello, World!</h1></body></html>",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.html").write_text(
        "<!doctype html><html><head><style>body{margin:0}</style></head>"
        "<body><h1>Mini Card</h1><p>Local Studio test</p><button>Email</button></body></html>",
        encoding="utf-8",
    )
    (src / "main.js").write_text("console.log('leftover');\n", encoding="utf-8")

    removed = reconcile_static_page_workspace(tmp_path)

    assert inspect_static_page_files(tmp_path) == []
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Mini Card" in page
    assert "Email" in page
    assert not (src / "index.html").exists()
    assert not (src / "main.js").exists()
    assert any(item.replace("\\", "/").endswith("main.js") for item in removed)


def test_reconcile_inlines_external_script_into_the_page(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><style>body{margin:0}button{padding:12px 20px}</style></head>"
        "<body><h1>Mini Card</h1><button>Email</button>"
        "<script src=\"main.js\"></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "main.js").write_text("document.querySelector('button').onclick = () => {};\n", encoding="utf-8")

    reconcile_static_page_workspace(tmp_path)

    assert inspect_static_page_files(tmp_path) == []
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "document.querySelector('button')" in page
    assert 'src="main.js"' not in page
    assert not (tmp_path / "main.js").exists()


def test_non_cinematic_prompt_does_not_require_webgl():
    prompt = build_static_page_prompt(
        _brief(
            goal="A single HTML page showing the name Mini Card and a button labelled Email.",
            core_features=("View Mini Card, read Local Studio test, and click Email",),
            acceptance_criteria=("The name, sentence and button are visible.",),
        ),
        _handoff(
            goal="Build the Mini Card page.",
            context_summary="One self-contained HTML card.",
            requirements=("Show Mini Card", "Show Local Studio test", "Email button"),
        ),
    )
    lowered = prompt.lower()
    assert "working webgl context" not in lowered
    assert "issue draw calls" not in lowered
    assert STATIC_PAGE_FILENAME in prompt
    assert "not a raw unstyled form" in lowered
    assert "<section>" in prompt
    assert "designed buttons" in lowered


def test_non_webgl_check_script_still_screenshots_and_skips_draw_calls(tmp_path):
    _page(tmp_path)
    captured = {}

    def _fake_docker(*_args, **_kwargs):
        captured["script"] = (tmp_path / "___freelancerstudio_static_check.mjs").read_text(encoding="utf-8")
        raise RuntimeError("captured")

    with pytest.raises(RuntimeError):
        run_static_page_check_in_docker((), tmp_path, docker_client_factory=_fake_docker, require_webgl=False)

    script = captured["script"]
    assert "page.screenshot(" in script
    assert "pageerror" in script
    assert "Zero WebGL draw calls" not in script
    assert "The page has no <canvas>" not in script
    assert "websiteShape" in script
    assert "designed website" in script


def test_reconcile_inlines_background_image_and_drops_the_sibling(tmp_path):
    (tmp_path / "elena_background.webp").write_bytes(b"RIFF....WEBPFAKE")
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><style>body{margin:0}button{padding:12px 20px}</style></head>"
        "<body><h1>Mini Card</h1><button>Email</button>"
        '<img src="elena_background.webp" alt=""></body></html>',
        encoding="utf-8",
    )

    reconcile_static_page_workspace(tmp_path)

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "data:image/webp;base64," in page
    assert not (tmp_path / "elena_background.webp").exists()
    assert inspect_static_page_files(tmp_path) == []


def test_finish_static_page_background_adds_cover_css(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head></head><body><h1>Mini Card</h1><button>Email</button></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "elena_background.webp").write_bytes(b"RIFF....WEBPFAKE")

    finish_static_page_background(tmp_path)
    reconcile_static_page_workspace(tmp_path)

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "fs-elena-cover" in page
    assert "data:image/webp;base64," in page
    assert inspect_static_page_files(tmp_path) == []


def test_resolve_static_page_background_from_env(tmp_path, monkeypatch):
    plate = tmp_path / "shore.webp"
    plate.write_bytes(b"x")
    monkeypatch.setenv("FREELANCERSTUDIO_STATIC_PAGE_BACKGROUND", str(plate))

    found = resolve_static_page_background("no path in the text")

    assert found == plate.resolve()


def test_elena_static_page_prompt_asks_to_animate_the_plate():
    palette = ThemePalette(background="#16324a", surface="#fff8ec", text="#132033", accent="#1e6f8a")
    concept = ElenaDesignConcept(
        visual_direction="Living client plate: slow camera drift, breathing light, water glints; frosted card for copy.",
        layout="Full-viewport animated background with a centered frosted content card.",
        screens=("Single living page",),
        components=("Animated background plate", "Frosted content card"),
        light_theme=palette,
        dark_theme=palette,
    )
    prompt = build_static_page_prompt(
        _brief(
            goal="A single HTML page showing the name Mini Card.",
            core_features=("View Mini Card and click Email",),
            acceptance_criteria=("The name, sentence and button are visible.",),
            elena_design_choice=ElenaDesignChoice.SHOW_ELENA_CONCEPT,
            elena_design_concept=concept,
        ),
        _handoff(
            goal="Build the Mini Card page.",
            context_summary="One self-contained HTML card over Elena's living plate.",
            requirements=("Show Mini Card", "Show Local Studio test", "Email button"),
            design_preview_summary=("If elena_background.webp is in the workspace, use it as a full-viewport living background.",),
        ),
    )
    lowered = prompt.lower()
    assert "elena" in lowered
    assert "elena_background.webp" in lowered
    assert "ken burns" in lowered
    assert "working webgl context" not in lowered


def test_cinematic_prompt_forbids_a_cube_and_asks_for_this_brief_s_world():
    prompt = build_static_page_prompt(_brief(), _handoff()).lower()
    assert "rotating cube" in prompt
    assert "this brief's subject" in prompt
    assert "overlay editorial chrome" in prompt
    assert "stock particle network" in prompt


def test_elena_static_concept_follows_the_order_subject():
    from order_workflow.brief_service import _elena_placeholder

    climate = _elena_placeholder(
        ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        product_type=ProductType.STATIC_PAGE,
        described="A cinematic WebGL nature world for alethia climate-tech. Moss, ferns, fog.",
        title="Alethia Carbon Intelligence",
    )
    bakery = _elena_placeholder(
        ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        product_type=ProductType.STATIC_PAGE,
        described="Harbour Bakery: warm bread, morning light, harbour fog outside the windows.",
        title="Harbour Bakery",
    )
    card = _elena_placeholder(
        ElenaDesignChoice.SHOW_ELENA_CONCEPT,
        product_type=ProductType.STATIC_PAGE,
        described="A single HTML page showing the name Mini Card and a button labelled Email.",
        title="Mini Card",
    )
    assert climate is not None and bakery is not None and card is not None
    assert "alethia" in climate.visual_direction.casefold()
    assert "harbour bakery" in bakery.visual_direction.casefold()
    assert climate.visual_direction != bakery.visual_direction
    assert "centered frosted content card" not in climate.layout.casefold()
    assert "centered frosted content card" not in bakery.layout.casefold()
    assert "overlay" in climate.layout.casefold()
    assert "living client plate" in card.visual_direction.casefold()
    assert "centered frosted content card" in card.layout.casefold()
