"""The gate for ProductType.STATIC_PAGE: is the delivered single file actually a living page?

The web-app gates cannot be reused here and it is worth being precise about why, because
"it's just a smaller web app" is the assumption this module exists to reject:

  * `npm run build` proves nothing about a file with no build step.
  * The visual gate measures painted colour off DOM computed styles. A WebGL page paints
    inside a <canvas>; the moss, the fog and the branches are pixels no computed style knows
    about, so palette coverage would read as "almost nothing painted" on a page that is in
    fact fully rendered. It also enforces Elena's palette, which for a creative order
    contradicts the art direction the client actually asked for.
  * The smoke check wants `npm run preview` and counts DOM interactive elements. A scene
    whose entire interaction is mouse-driven canvas orbiting has few of those and needs none.

So this gate asks the questions that *are* answerable about such a page, and answers them
with programs rather than opinions:

  * file shape -- exactly one .html, no sibling sources, no build config, under a size cap
    (checked in Python, before any container starts: it is the cheapest failure available)
  * every asset host is on the allowlist the prompt promised (no surprise CDN, no remote image)
  * a canvas exists and holds a working WebGL context
  * the scene actually draws: WebGL draw calls are counted by instrumenting the context
    before the page's own script runs. Counting draw calls rather than reading pixels is
    deliberate -- a WebGL canvas created without `preserveDrawingBuffer` reads back blank
    after compositing, so a pixel check would fail on perfectly good pages.
  * the page is alive: requestAnimationFrame callbacks and draw calls both keep increasing
    between two samples, so a single static frame does not pass as an animation
  * no uncaught or console errors, no horizontal overflow at 1280/768, tap targets >= 24px
  * text over the scene has a backing surface -- contrast against a moving 3D background is
    not measurable, so the gate requires the page not to depend on it

What this gate does NOT judge: whether the scene is beautiful, whether the fog reads as
cinematic, whether the butterfly is charming. Same honest line as the rest of the pipeline --
mechanical questions answered completely, taste left visibly alone.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path

from .docker_qa_runner import PLAYWRIGHT_IMAGE, PLAYWRIGHT_NPM_VERSION, run_qa_commands_in_docker
from .phase_prompts import STATIC_PAGE_ALLOWED_HOSTS, STATIC_PAGE_FILENAME, STATIC_PAGE_MAX_BYTES
from .browser_rules import TAP_TARGET_MESSAGE_JS, TAP_TARGET_RULE_JS
from .qa_runner import QACommandResult, QAOutcome
from .visual_check import SCREENSHOT_FILENAME
from .workspace import is_regular_file

_SERVER_FILENAME = "___freelancerstudio_static_server.mjs"
_CHECK_FILENAME = "___freelancerstudio_static_check.mjs"
_PORT = 4173
DEFAULT_STATIC_PAGE_CHECK_TIMEOUT_SECONDS = 240

# Must stay in step with phase_prompts.STATIC_PAGE_ALLOWED_HOSTS -- the prompt states this
# list as a contract and this gate is what enforces it. Imported rather than duplicated so
# they cannot drift apart.
ALLOWED_ASSET_HOSTS: tuple[str, ...] = STATIC_PAGE_ALLOWED_HOSTS

# Files this pipeline itself writes into the workspace, plus what npm leaves behind when the
# gate installs Playwright. Excluded from the "exactly one file" rule because the client is
# not delivering them -- counting them would fail every run for the gate's own footprint.
_IGNORED_NAMES = frozenset({"node_modules", ".git", "package.json", "package-lock.json", "README.md", "ARCHITECTURE.md", "delivery_report.md", "generated_project_summary.json"})
_IGNORED_PREFIXES = ("___freelancerstudio", ".freelancerstudio-checkpoint", "execution_prompt_", "claude_code_raw_output")

# Low on purpose: WebGL in the QA container runs on SwiftShader (software rasterisation), so
# a page that holds a comfortable 60fps on the client's GPU can legitimately measure in the
# teens here. The floor exists to catch a page that does not animate at all, or one whose
# per-frame work is so heavy it would stutter on real hardware too -- not to grade smoothness.
MIN_FPS = 10

_HTML_SUFFIXES = {".html", ".htm"}
_SOURCE_SUFFIXES = {".js", ".mjs", ".css", ".ts", ".jsx", ".tsx"}
_MEDIA_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".avif"}
_BUILD_CONFIG_NAMES = {"vite.config.js", "vite.config.ts", "webpack.config.js", "tsconfig.json", "index.js"}
_STUB_MARKERS = ("hello, world", "hello world")
ELENA_BACKGROUND_STEM = "elena_background"
STATIC_PAGE_BACKGROUND_ENV = "FREELANCERSTUDIO_STATIC_PAGE_BACKGROUND"
_WINDOWS_MEDIA = re.compile(
    r'(?P<path>[A-Za-z]:\\(?:[^<>"|*?]+)\.(?:webp|png|jpe?g))',
    re.IGNORECASE,
)
_SCRIPT_SRC = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*['"]([^'"]+)['"][^>]*>\s*</script>""",
    re.IGNORECASE,
)
_IMG_SRC = re.compile(
    r"""(<img\b[^>]*\bsrc\s*=\s*['"])([^'"]+)(['"][^>]*>)""",
    re.IGNORECASE,
)
_CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.IGNORECASE)
_REMOTE_SRC_PREFIXES = ("http://", "https://", "//", "data:", "blob:")


def _delivered_files(cwd: Path) -> list[Path]:
    files = []
    for path in sorted(cwd.rglob("*")):
        relative = path.relative_to(cwd)
        # Name filters first: they answer without touching the filesystem, and the paths they
        # exclude include the one that cannot be stat-ed -- node_modules/.bin, which this gate
        # creates itself by installing Playwright into the workspace.
        if any(part in _IGNORED_NAMES for part in relative.parts):
            continue
        if any(relative.parts[-1].startswith(prefix) for prefix in _IGNORED_PREFIXES):
            continue
        if not is_regular_file(path):
            continue
        files.append(relative)
    return files


def inspect_static_page_files(cwd: Path) -> list[str]:
    """The file-shape half of the gate, with no browser and no container involved.

    Separate from the browser half so the cheapest possible failure -- "you wrote four files
    and a package.json" -- costs milliseconds instead of a Playwright image pull, and so it
    is unit-testable without Docker.
    """
    failures: list[str] = []
    files = _delivered_files(cwd)
    page = cwd / STATIC_PAGE_FILENAME

    if not page.is_file():
        failures.append(f"There is no {STATIC_PAGE_FILENAME} at the project root. The whole deliverable must be that one file.")
        return failures

    html_files = [item for item in files if item.suffix.lower() in {".html", ".htm"}]
    if len(html_files) > 1:
        listed = ", ".join(str(item) for item in html_files)
        failures.append(f"Expected exactly one HTML file but found {len(html_files)}: {listed}. Inline everything into {STATIC_PAGE_FILENAME}.")

    sources = [item for item in files if item.suffix.lower() in {".js", ".mjs", ".css", ".ts", ".jsx", ".tsx"}]
    if sources:
        listed = ", ".join(str(item) for item in sources)
        failures.append(f"Found source files beside the page: {listed}. This deliverable is one self-contained file -- move that code inline into {STATIC_PAGE_FILENAME}.")

    build_config = [item for item in files if item.name in {"vite.config.js", "vite.config.ts", "webpack.config.js", "tsconfig.json", "index.js"}]
    if build_config:
        listed = ", ".join(str(item) for item in build_config)
        failures.append(f"Found build configuration ({listed}). A static page has no build step; delete it.")

    size = page.stat().st_size
    if size > STATIC_PAGE_MAX_BYTES:
        failures.append(f"{STATIC_PAGE_FILENAME} is {size // 1000} KB, over the {STATIC_PAGE_MAX_BYTES // 1000} KB limit. Reduce inline data or generate textures procedurally instead of embedding them.")

    return failures


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _html_score(text: str) -> int:
    lowered = text.lower()
    score = len(text)
    if any(marker in lowered for marker in _STUB_MARKERS):
        score -= 50_000
    if "<style" in lowered:
        score += 400
    if "<button" in lowered:
        score += 400
    if "<h1" in lowered or "<h2" in lowered:
        score += 100
    return score


def _resolve_workspace_asset(cwd: Path, html_origin: Path, src: str) -> Path | None:
    cleaned = src.strip().split("?", 1)[0].split("#", 1)[0].lstrip("./").replace("\\", "/")
    if not cleaned or ":" in cleaned.split("/")[0]:
        return None
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    candidates = [cwd.joinpath(*parts), html_origin.parent.joinpath(*parts)]
    name = parts[-1]
    if name:
        for path in cwd.rglob(name):
            relative = path.relative_to(cwd)
            if any(part in _IGNORED_NAMES for part in relative.parts):
                continue
            if any(relative.parts[-1].startswith(prefix) for prefix in _IGNORED_PREFIXES):
                continue
            if is_regular_file(path):
                candidates.append(path)
    root = cwd.resolve()
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _inline_external_scripts(cwd: Path, html_origin: Path, html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        src = match.group(1).strip()
        if src.lower().startswith(_REMOTE_SRC_PREFIXES):
            return match.group(0)
        asset = _resolve_workspace_asset(cwd, html_origin, src)
        if asset is None:
            return match.group(0)
        body = _read_text(asset)
        return f"<script>\n{body}\n</script>"

    return _SCRIPT_SRC.sub(replace, html)


def _data_uri_for(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/webp" if suffix == ".webp" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _inline_local_media(cwd: Path, html_origin: Path, html: str) -> str:
    def img_replace(match: re.Match[str]) -> str:
        src = match.group(2).strip()
        if src.lower().startswith(_REMOTE_SRC_PREFIXES):
            return match.group(0)
        asset = _resolve_workspace_asset(cwd, html_origin, src)
        if asset is None or asset.suffix.lower() not in _MEDIA_SUFFIXES:
            return match.group(0)
        return f"{match.group(1)}{_data_uri_for(asset)}{match.group(3)}"

    def css_replace(match: re.Match[str]) -> str:
        src = match.group(1).strip()
        if src.lower().startswith(_REMOTE_SRC_PREFIXES):
            return match.group(0)
        asset = _resolve_workspace_asset(cwd, html_origin, src)
        if asset is None or asset.suffix.lower() not in _MEDIA_SUFFIXES:
            return match.group(0)
        return f"url('{_data_uri_for(asset)}')"

    return _CSS_URL.sub(css_replace, _IMG_SRC.sub(img_replace, html))


def resolve_static_page_background(text: str = "", *, environ: dict[str, str] | None = None) -> Path | None:
    env = environ if environ is not None else os.environ
    candidates: list[Path] = []
    raw = str(env.get(STATIC_PAGE_BACKGROUND_ENV) or "").strip().strip('"')
    if raw:
        candidates.append(Path(raw))
    for match in _WINDOWS_MEDIA.finditer(text or ""):
        candidates.append(Path(match.group("path")))
    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def stage_static_page_background(cwd: Path, plate: Path) -> Path:
    dest = Path(cwd) / f"{ELENA_BACKGROUND_STEM}{plate.suffix.lower() or '.webp'}"
    shutil.copy2(plate, dest)
    return dest


def _inject_living_background(html: str, filename: str) -> str:
    if filename in html or "data:image/" in html:
        return html
    scene = (
        f'<div class="fs-elena-scene" aria-hidden="true"><img src="{filename}" alt=""></div>'
        "<style>.fs-elena-scene{position:fixed;inset:0;overflow:hidden;z-index:0}"
        ".fs-elena-scene img{position:absolute;inset:-8%;width:116%;height:116%;object-fit:cover;"
        "animation:fs-elena-drift 32s ease-in-out infinite alternate}"
        "@keyframes fs-elena-drift{from{transform:translate3d(-3%,-1%,0) scale(1.02)}"
        "to{transform:translate3d(2%,2%,0) scale(1.1)}}"
        "@media (prefers-reduced-motion:reduce){.fs-elena-scene img{animation:none}}"
        "body{margin:0}.fs-elena-fg{position:relative;z-index:1}</style>"
    )
    lowered = html.lower()
    body_at = lowered.find("<body")
    if body_at == -1:
        return scene + html
    gt = html.find(">", body_at)
    if gt == -1:
        return scene + html
    return html[: gt + 1] + scene + html[gt + 1 :]


_ELENA_COVER_CSS = """/* fs-elena-cover */
img.background, .fs-elena-scene img, img[src^="data:image"] {
  position: fixed; inset: -8%; width: 116%; height: 116%; max-width: none;
  object-fit: cover; z-index: 0; transform-origin: 50% 50%;
}
@keyframes kenburns {
  from { transform: scale(1.02) translate3d(-2%, -1%, 0); }
  to { transform: scale(1.1) translate3d(2%, 2%, 0); }
}
.content-card, .card, .fs-elena-fg { position: relative; z-index: 1; }
html, body { margin: 0; min-height: 100%; overflow: hidden; }
"""


def _ensure_cover_css(html: str) -> str:
    if "fs-elena-cover" in html:
        return html
    snippet = f"<style>{_ELENA_COVER_CSS}</style>"
    close = html.lower().rfind("</head>")
    if close == -1:
        return snippet + html
    return html[:close] + snippet + html[close:]


def finish_static_page_background(cwd: Path, filename: str = f"{ELENA_BACKGROUND_STEM}.webp") -> None:
    page = Path(cwd) / STATIC_PAGE_FILENAME
    plate = Path(cwd) / filename
    if not page.is_file():
        return
    html = _read_text(page)
    if plate.is_file():
        html = _inject_living_background(html, filename)
    html = _ensure_cover_css(html)
    page.write_text(html if html.endswith("\n") else html + "\n", encoding="utf-8")


def _remove_empty_dirs(cwd: Path) -> None:
    dirs = sorted((path for path in cwd.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True)
    for path in dirs:
        relative = path.relative_to(cwd)
        if any(part in _IGNORED_NAMES for part in relative.parts):
            continue
        try:
            path.rmdir()
        except OSError:
            continue


def reconcile_static_page_workspace(cwd: Path) -> tuple[str, ...]:
    """Promote the richest HTML to root index.html and delete sibling sources.

    Coding backends used here can only write, never delete. A repair that emits
    src/index.html plus src/main.js would otherwise fail inspect_static_page_files
    on every later attempt.
    """
    root = Path(cwd)
    files = _delivered_files(root)
    html_relatives = [item for item in files if item.suffix.lower() in _HTML_SUFFIXES]
    if not html_relatives:
        return ()

    scored = []
    for relative in html_relatives:
        path = root / relative
        scored.append((_html_score(_read_text(path)), relative, path))
    scored.sort(key=lambda item: (-item[0], 0 if item[1].as_posix() == STATIC_PAGE_FILENAME else 1))
    _best_score, best_relative, best_path = scored[0]
    html = _inline_local_media(root, best_path, _inline_external_scripts(root, best_path, _read_text(best_path)))
    target = root / STATIC_PAGE_FILENAME
    target.write_text(html if html.endswith("\n") else html + "\n", encoding="utf-8")

    removed: list[str] = []
    keep = {STATIC_PAGE_FILENAME}
    for relative in _delivered_files(root):
        name = relative.as_posix()
        if name in keep:
            continue
        suffix = relative.suffix.lower()
        if suffix in _HTML_SUFFIXES or suffix in _SOURCE_SUFFIXES or suffix in _MEDIA_SUFFIXES or relative.name in _BUILD_CONFIG_NAMES:
            path = root / relative
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(name)
    _remove_empty_dirs(root)
    return tuple(removed)


_SERVER_SCRIPT = f"""import http from 'node:http';
import {{ readFile }} from 'node:fs/promises';
import {{ extname, join, normalize }} from 'node:path';

// Minimal static server: the page must be fetched over http, not file://, or Chromium
// refuses cross-origin module scripts from a null origin and every CDN import fails for a
// reason that has nothing to do with the page being wrong.
const TYPES = {{
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
}};

http.createServer(async (req, res) => {{
  try {{
    const raw = decodeURIComponent((req.url || '/').split('?')[0]);
    const relative = normalize(raw === '/' ? '/{STATIC_PAGE_FILENAME}' : raw).replace(/^([/\\\\])+/, '');
    const body = await readFile(join(process.cwd(), relative));
    res.writeHead(200, {{ 'Content-Type': TYPES[extname(relative).toLowerCase()] || 'application/octet-stream' }});
    res.end(body);
  }} catch {{
    res.writeHead(404);
    res.end('not found');
  }}
}}).listen({_PORT});
"""


def _build_check_script(*, require_webgl: bool = True) -> str:
    webgl_failures = ""
    if require_webgl:
        webgl_failures = """  if (!canvas) {
    failures.push('The page has no <canvas> element, so nothing is being rendered.');
  } else {
    if (canvas.w < 2 || canvas.h < 2) failures.push(`The canvas is ${canvas.w}x${canvas.h}px -- it is not sized to be visible.`);
    if (canvas.context === 'none') failures.push('The canvas has no working WebGL context (getContext returned null).');
  }
  if (first.drawCalls === 0) {
    failures.push('Zero WebGL draw calls: a canvas exists but the scene never drew anything. Check that the renderer runs and the geometry is added to the scene.');
  }
  if (second.drawCalls <= first.drawCalls) {
    failures.push('Draw calls stopped after the first frames -- the page rendered once and froze. Keep a requestAnimationFrame loop running so the scene stays alive.');
  }
  if (fps < MIN_FPS) {
    failures.push(`The animation loop ran at ${fps.toFixed(1)} fps (needs at least ${MIN_FPS} even on software rendering). Reduce per-frame work: fewer draw calls, smaller geometry, cheaper materials.`);
  }
  if (unbacked.length > 0) {
    failures.push(`Text sits directly over the 3D scene with no backing surface or text shadow: "${unbacked.join('", "')}". Contrast over a moving scene is not measurable -- put it on a frosted/solid panel or give it a text shadow.`);
  }
"""
    return f"""import {{ chromium }} from 'playwright';

const TARGET_URL = 'http://localhost:{_PORT}/{STATIC_PAGE_FILENAME}';
const SCREENSHOT_PATH = {SCREENSHOT_FILENAME!r};
const ALLOWED_HOSTS = {list(ALLOWED_ASSET_HOSTS)!r};
{TAP_TARGET_MESSAGE_JS}
const MIN_FPS = {MIN_FPS};
const NAV_TIMEOUT_MS = 30000;

const consoleErrors = [];
const pageErrors = [];

// Installed before any page script runs: wrap getContext so every WebGL context this page
// creates is instrumented, and count draw calls and animation frames on the way through.
// This is the measurement the whole gate turns on -- a canvas element proves nothing, a
// canvas that has issued 40k draw calls and is still issuing them proves the scene is real.
const INSTRUMENT = `
  window.__fsStats = {{ drawCalls: 0, frames: 0, contexts: 0, glLost: false }};
  const realGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {{
    const ctx = realGetContext.call(this, kind, ...rest);
    if (ctx && /webgl/i.test(String(kind))) {{
      window.__fsStats.contexts += 1;
      for (const method of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {{
        if (typeof ctx[method] !== 'function') continue;
        const real = ctx[method].bind(ctx);
        ctx[method] = (...args) => {{ window.__fsStats.drawCalls += 1; return real(...args); }};
      }}
    }}
    return ctx;
  }};
  const realRaf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => realRaf((t) => {{ window.__fsStats.frames += 1; return cb(t); }});
`;

function overflowAndTargets() {{
  {TAP_TARGET_RULE_JS}
  const de = document.documentElement;
  const overflow = de.scrollWidth - de.clientWidth;
  // The same rule the visual gate applies, from the same source. Its own copy of this,
  // written before WCAG 2.5.8's exceptions were, reported every text link on the page: the
  // b02 order of 2026-08-28 spent a repair on "a 43x16px, a 32x16px, a 49x16px" -- three
  // inline links in a sentence.
  return {{ overflow, smallTargets: smallTapTargets().slice(0, 6) }};
}}

function unbackedTextOverScene() {{
  // Text whose effective background is transparent all the way up, sitting on top of a
  // canvas: its contrast depends on whatever the scene happens to be painting behind it at
  // that instant, which is not a measurable property. A frosted panel, a solid card or a
  // text shadow all make it measurable again, so any of those satisfies this.
  const canvases = Array.from(document.querySelectorAll('canvas')).map((c) => c.getBoundingClientRect());
  if (canvases.length === 0) return [];
  const offenders = [];
  for (const node of document.querySelectorAll('*')) {{
    const own = Array.from(node.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join('').trim();
    if (own.length < 2) continue;
    const rect = node.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const overCanvas = canvases.some((c) => rect.left < c.right && rect.right > c.left && rect.top < c.bottom && rect.bottom > c.top);
    if (!overCanvas) continue;
    const style = getComputedStyle(node);
    if (style.textShadow && style.textShadow !== 'none') continue;
    let backed = false;
    let current = node;
    while (current && current !== document.documentElement) {{
      const bg = getComputedStyle(current).backgroundColor;
      const parts = String(bg).match(/[\\d.]+/g);
      if (parts && (parts.length < 4 || parseFloat(parts[3]) > 0.15)) {{ backed = true; break; }}
      current = current.parentElement;
    }}
    if (!backed) offenders.push(own.slice(0, 40));
  }}
  return offenders.slice(0, 5);
}}

function assetHosts() {{
  const out = [];
  for (const node of document.querySelectorAll('script[src], link[href], img[src], source[src], image')) {{
    const raw = node.getAttribute('src') || node.getAttribute('href') || node.getAttribute('xlink:href') || '';
    if (!raw || raw.startsWith('data:') || raw.startsWith('blob:') || raw.startsWith('#')) continue;
    try {{
      const url = new URL(raw, document.baseURI);
      if (url.protocol !== 'http:' && url.protocol !== 'https:') continue;
      if (url.hostname === location.hostname) continue;
      out.push(url.hostname);
    }} catch {{ /* an unparseable reference is reported by the console-error check instead */ }}
  }}
  return Array.from(new Set(out));
}}

async function main() {{
  const browser = await chromium.launch({{ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] }});
  const page = await browser.newPage({{ viewport: {{ width: 1280, height: 800 }} }});
  await page.addInitScript(INSTRUMENT);
  page.on('console', (msg) => {{ if (msg.type() === 'error') consoleErrors.push(msg.text()); }});
  page.on('pageerror', (err) => {{ pageErrors.push(String(err)); }});

  // Same race the other three browser gates guard against: the static server is backgrounded
  // by the shell command that launches this script, so a slow start would otherwise read as
  // a broken page and send the repair loop after code that is fine. Cheaper to wait here
  // than to spend a coding-CLI call discovering the server simply was not up yet.
  // The criterion a delivery is judged by asks for proof that it runs: a screenshot and an
  // HTTP 200. A single-file page builds no container, so until 2026-08-27 its folder carried
  // neither -- its delivery report answered that question with "container packaging was not
  // part of this run", which is a statement rather than evidence. This check already serves
  // the page over HTTP and already has it open in a real browser; both were simply not
  // recorded.
  let httpStatus = 0;
  let lastError = null;
  for (let attempt = 0; attempt < 15; attempt += 1) {{
    try {{
      const response = await page.goto(TARGET_URL, {{ waitUntil: 'load', timeout: NAV_TIMEOUT_MS }});
      httpStatus = response ? response.status() : 0;
      lastError = null;
      break;
    }} catch (err) {{
      lastError = err;
      await page.waitForTimeout(2000);
    }}
  }}
  if (lastError) {{
    await browser.close();
    console.error('STATIC PAGE CHECK FAILED: the local server never accepted a connection.');
    console.error(String(lastError && lastError.message ? lastError.message : lastError));
    process.exit(1);
  }}
  await page.waitForTimeout(1500);

  // Here and nowhere else: the scene has settled and the viewport is still the one a client
  // would recognise -- the tablet measurement below reshapes it. Wrapped because a delivery
  // photograph must never be able to fail a gate the page has otherwise passed.
  try {{
    await page.screenshot({{ path: SCREENSHOT_PATH, fullPage: false }});
  }} catch (err) {{
    console.error('screenshot skipped: ' + String(err && err.message ? err.message : err));
  }}

  const first = await page.evaluate(() => ({{ ...window.__fsStats }}));
  const canvas = await page.evaluate(() => {{
    const node = document.querySelector('canvas');
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    let context = 'none';
    try {{ context = node.getContext('webgl2') ? 'webgl2' : (node.getContext('webgl') ? 'webgl' : 'none'); }} catch {{ context = 'none'; }}
    return {{ w: Math.round(rect.width), h: Math.round(rect.height), context }};
  }});
  const hosts = await page.evaluate(assetHosts);
  const unbacked = await page.evaluate(unbackedTextOverScene);
  const desktop = await page.evaluate(overflowAndTargets);

  const startedAt = Date.now();
  await page.waitForTimeout(1500);
  const second = await page.evaluate(() => ({{ ...window.__fsStats }}));
  const elapsedSeconds = (Date.now() - startedAt) / 1000;
  const fps = (second.frames - first.frames) / elapsedSeconds;

  await page.setViewportSize({{ width: 768, height: 1024 }});
  await page.waitForTimeout(600);
  const tablet = await page.evaluate(overflowAndTargets);

  await browser.close();

  const failures = [];
{webgl_failures}
  const foreign = hosts.filter((host) => !ALLOWED_HOSTS.includes(host));
  if (foreign.length > 0) {{
    failures.push(`Assets loaded from hosts that are not allowed: ${{foreign.join(', ')}}. Allowed: ${{ALLOWED_HOSTS.join(', ')}} -- everything else must be inline or a data: URI.`);
  }}
  if (desktop.overflow > 1) failures.push(`The page scrolls horizontally at 1280px (overflowing by ${{desktop.overflow}}px).`);
  if (tablet.overflow > 1) failures.push(`The page scrolls horizontally at 768px (overflowing by ${{tablet.overflow}}px). Make the layout fit a tablet.`);
  for (const t of tablet.smallTargets.slice(0, 3)) {{
    failures.push(tapTargetFailure('768px', t));
  }}
  if (pageErrors.length > 0) failures.push(`Uncaught page error(s): ${{pageErrors.slice(0, 4).join(' | ')}}`);
  if (consoleErrors.length > 0) failures.push(`Console error(s): ${{consoleErrors.slice(0, 4).join(' | ')}}`);

  console.log(`Served over HTTP: ${{httpStatus}} from the check's own local server.`);
  console.log(`Canvas: ${{canvas ? `${{canvas.w}}x${{canvas.h}} ${{canvas.context}}` : 'absent'}}.`);
  console.log(`Draw calls: ${{first.drawCalls}} in the first 1.5s, ${{second.drawCalls}} total. Frames: ${{second.frames}} (${{fps.toFixed(1)}} fps).`);
  if (failures.length > 0) {{
    console.error('STATIC PAGE CHECK FAILED:');
    for (const failure of failures) console.error(`- ${{failure}}`);
    process.exit(1);
  }}
  console.log('STATIC PAGE CHECK PASSED');
  process.exit(0);
}}

main().catch((err) => {{
  console.error('STATIC PAGE CHECK FAILED: script error');
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
}});
"""


_CHECK_SCRIPT = _build_check_script(require_webgl=True)


_SHELL_COMMAND = (
    f"npm install --no-save --no-package-lock --no-audit --no-fund playwright@{PLAYWRIGHT_NPM_VERSION} >/dev/null 2>&1 && "
    f"(node {_SERVER_FILENAME} >/tmp/freelancerstudio-static-server.log 2>&1 &) && "
    "sleep 2 && "
    f"node {_CHECK_FILENAME}; "
    "STATUS=$?; "
    f"pkill -f {_SERVER_FILENAME} >/dev/null 2>&1 || true; "
    "exit $STATUS"
)


def run_static_page_check_in_docker(
    qa_commands: tuple[str, ...],
    cwd: Path,
    *,
    timeout_seconds: int = DEFAULT_STATIC_PAGE_CHECK_TIMEOUT_SECONDS,
    docker_client_factory=None,
    require_webgl: bool = True,
) -> QAOutcome:
    """`Callable[[tuple[str, ...], Path], QAOutcome]`, so it drops into run_qa_repair_loop
    exactly like the web-app gates do. `qa_commands` is a human-readable label only; what
    gets checked is fixed here, not caller-supplied.

    File-shape failures short-circuit before Docker is touched: they are certain, instant,
    and the most common way a model misreads "one self-contained file".
    """
    started = time.perf_counter()
    file_failures = inspect_static_page_files(cwd)
    if file_failures:
        report = "STATIC PAGE CHECK FAILED:\n" + "\n".join(f"- {item}" for item in file_failures)
        return QAOutcome(
            passed=False,
            results=(
                QACommandResult(
                    command="static page file shape",
                    exit_code=1,
                    stdout_tail=report,
                    stderr_tail="",
                    duration=time.perf_counter() - started,
                ),
            ),
        )

    server_path = cwd / _SERVER_FILENAME
    check_path = cwd / _CHECK_FILENAME
    try:
        server_path.write_text(_SERVER_SCRIPT, encoding="utf-8")
        check_path.write_text(_build_check_script(require_webgl=require_webgl), encoding="utf-8")
        return run_qa_commands_in_docker(
            (_SHELL_COMMAND,),
            cwd,
            image=PLAYWRIGHT_IMAGE,
            timeout_seconds=timeout_seconds,
            docker_client_factory=docker_client_factory,
        )
    finally:
        for path in (server_path, check_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
