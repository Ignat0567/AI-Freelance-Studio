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

import time
from pathlib import Path

from .docker_qa_runner import PLAYWRIGHT_IMAGE, PLAYWRIGHT_NPM_VERSION, run_qa_commands_in_docker
from .phase_prompts import STATIC_PAGE_ALLOWED_HOSTS, STATIC_PAGE_FILENAME, STATIC_PAGE_MAX_BYTES
from .qa_runner import QACommandResult, QAOutcome

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


def _delivered_files(cwd: Path) -> list[Path]:
    files = []
    for path in sorted(cwd.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(cwd)
        if any(part in _IGNORED_NAMES for part in relative.parts):
            continue
        if any(relative.parts[-1].startswith(prefix) for prefix in _IGNORED_PREFIXES):
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


_CHECK_SCRIPT = f"""import {{ chromium }} from 'playwright';

const TARGET_URL = 'http://localhost:{_PORT}/{STATIC_PAGE_FILENAME}';
const ALLOWED_HOSTS = {list(ALLOWED_ASSET_HOSTS)!r};
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
  const de = document.documentElement;
  const overflow = de.scrollWidth - de.clientWidth;
  const smallTargets = [];
  const selector = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';
  for (const node of document.querySelectorAll(selector)) {{
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (rect.width < 1 || style.visibility === 'hidden' || style.display === 'none') continue;
    if (rect.width < 24 || rect.height < 24) {{
      smallTargets.push({{ tag: node.tagName.toLowerCase(), w: Math.round(rect.width), h: Math.round(rect.height) }});
    }}
  }}
  return {{ overflow, smallTargets: smallTargets.slice(0, 6) }};
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
  let lastError = null;
  for (let attempt = 0; attempt < 15; attempt += 1) {{
    try {{
      await page.goto(TARGET_URL, {{ waitUntil: 'load', timeout: NAV_TIMEOUT_MS }});
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
  if (!canvas) {{
    failures.push('The page has no <canvas> element, so nothing is being rendered.');
  }} else {{
    if (canvas.w < 2 || canvas.h < 2) failures.push(`The canvas is ${{canvas.w}}x${{canvas.h}}px -- it is not sized to be visible.`);
    if (canvas.context === 'none') failures.push('The canvas has no working WebGL context (getContext returned null).');
  }}
  if (first.drawCalls === 0) {{
    failures.push('Zero WebGL draw calls: a canvas exists but the scene never drew anything. Check that the renderer runs and the geometry is added to the scene.');
  }}
  if (second.drawCalls <= first.drawCalls) {{
    failures.push('Draw calls stopped after the first frames -- the page rendered once and froze. Keep a requestAnimationFrame loop running so the scene stays alive.');
  }}
  if (fps < MIN_FPS) {{
    failures.push(`The animation loop ran at ${{fps.toFixed(1)}} fps (needs at least ${{MIN_FPS}} even on software rendering). Reduce per-frame work: fewer draw calls, smaller geometry, cheaper materials.`);
  }}
  const foreign = hosts.filter((host) => !ALLOWED_HOSTS.includes(host));
  if (foreign.length > 0) {{
    failures.push(`Assets loaded from hosts that are not allowed: ${{foreign.join(', ')}}. Allowed: ${{ALLOWED_HOSTS.join(', ')}} -- everything else must be inline or a data: URI.`);
  }}
  if (unbacked.length > 0) {{
    failures.push(`Text sits directly over the 3D scene with no backing surface or text shadow: "${{unbacked.join('", "')}}". Contrast over a moving scene is not measurable -- put it on a frosted/solid panel or give it a text shadow.`);
  }}
  if (desktop.overflow > 1) failures.push(`The page scrolls horizontally at 1280px (overflowing by ${{desktop.overflow}}px).`);
  if (tablet.overflow > 1) failures.push(`The page scrolls horizontally at 768px (overflowing by ${{tablet.overflow}}px). Make the layout fit a tablet.`);
  if (tablet.smallTargets.length > 0) {{
    const listed = tablet.smallTargets.map((t) => `${{t.tag}} ${{t.w}}x${{t.h}}px`).join(', ');
    failures.push(`Tap targets under 24x24px at 768px: ${{listed}}. Enlarge them.`);
  }}
  if (pageErrors.length > 0) failures.push(`Uncaught page error(s): ${{pageErrors.slice(0, 4).join(' | ')}}`);
  if (consoleErrors.length > 0) failures.push(`Console error(s): ${{consoleErrors.slice(0, 4).join(' | ')}}`);

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
        check_path.write_text(_CHECK_SCRIPT, encoding="utf-8")
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
