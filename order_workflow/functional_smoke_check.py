"""QA depth, level (a): does the generated app actually boot and render, not just compile?

`npm run build` / `npm test` (the existing per-phase QA commands) only prove the code
compiles. A project can pass both and still be a blank white screen or crash on first
render -- neither of those is caught anywhere today. This module adds one more, cheap
check: launch a real (headless) browser against the built app and confirm it renders
visible content, has at least one usable interactive element, and produced no console/
page errors.

Deliberately NOT covered here (see the design discussion this shipped from): AI-authored
acceptance-criteria scenarios, or visual/design-fidelity judging. Both are real, separate,
more expensive features -- this module only proves the app is alive, not that it is correct.
"""

from __future__ import annotations

from pathlib import Path

from .docker_qa_runner import run_qa_commands_in_docker
from .qa_runner import QAOutcome

# Pinned together on purpose: the npm `playwright` package version installed inside the
# container must match the browsers already baked into this image (PLAYWRIGHT_BROWSERS_PATH),
# or Playwright tries to download browsers at QA time instead of reusing the cached ones.
_PLAYWRIGHT_IMAGE = "mcr.microsoft.com/playwright:v1.48.0-jammy"
_PLAYWRIGHT_NPM_VERSION = "1.48.0"

_SMOKE_CHECK_FILENAME = "___freelancerstudio_smoke_check.mjs"
_PREVIEW_URL = "http://localhost:4173"
DEFAULT_SMOKE_CHECK_TIMEOUT_SECONDS = 180

_SMOKE_CHECK_SCRIPT = f"""import {{ chromium }} from 'playwright';

const TARGET_URL = {_PREVIEW_URL!r};
const NAV_TIMEOUT_MS = 15000;

const consoleErrors = [];
const pageErrors = [];

async function main() {{
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on('console', (msg) => {{
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  }});
  page.on('pageerror', (err) => {{
    pageErrors.push(String(err));
  }});

  await page.goto(TARGET_URL, {{ waitUntil: 'load', timeout: NAV_TIMEOUT_MS }});
  await page.waitForTimeout(1000);

  const bodyText = await page.evaluate(() => (document.body ? document.body.innerText.trim() : ''));
  const interactiveCount = await page.evaluate(() => {{
    const nodes = document.querySelectorAll('a[href], button, input, select, textarea, [role="button"], [role="link"]');
    let visible = 0;
    for (const node of nodes) {{
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none') visible += 1;
    }}
    return visible;
  }});

  await browser.close();

  const failures = [];
  if (bodyText.length === 0) failures.push('The page rendered no visible text content.');
  if (interactiveCount === 0) failures.push('No visible interactive elements (links, buttons, inputs) were found.');
  if (pageErrors.length > 0) failures.push(`Uncaught page error(s): ${{pageErrors.slice(0, 5).join(' | ')}}`);
  if (consoleErrors.length > 0) failures.push(`Console error(s): ${{consoleErrors.slice(0, 5).join(' | ')}}`);

  if (failures.length > 0) {{
    console.error('FUNCTIONAL SMOKE CHECK FAILED:');
    for (const failure of failures) console.error(`- ${{failure}}`);
    process.exit(1);
  }}

  console.log('FUNCTIONAL SMOKE CHECK PASSED');
  console.log(`Rendered text length: ${{bodyText.length}}`);
  console.log(`Visible interactive elements: ${{interactiveCount}}`);
  process.exit(0);
}}

main().catch((err) => {{
  console.error('FUNCTIONAL SMOKE CHECK FAILED: script error');
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
}});
"""

_SHELL_COMMAND = (
    "npm install --no-audit --no-fund >/dev/null 2>&1 && "
    f"npm install --no-save --no-audit --no-fund playwright@{_PLAYWRIGHT_NPM_VERSION} >/dev/null 2>&1 && "
    "(npm run preview >/tmp/freelancerstudio-preview.log 2>&1 &) && "
    "sleep 4 && "
    f"node {_SMOKE_CHECK_FILENAME}; "
    "STATUS=$?; "
    "pkill -f 'preview' >/dev/null 2>&1 || true; "
    "exit $STATUS"
)


def run_functional_smoke_check_in_docker(
    qa_commands: tuple[str, ...],
    cwd: Path,
    *,
    timeout_seconds: int = DEFAULT_SMOKE_CHECK_TIMEOUT_SECONDS,
    docker_client_factory=None,
) -> QAOutcome:
    """Matches the `Callable[[tuple[str, ...], Path], QAOutcome]` shape run_qa_repair_loop
    already expects, so it drops straight into the existing per-phase QA/repair machinery
    as a second qa_runner -- `qa_commands` here is just a human-readable label for logging,
    the actual check is fixed (see module docstring), not caller-supplied like the build/
    test commands are.

    Requires the project to expose `npm run preview` serving the production build on the
    default Vite preview port (4173) -- the ui_shell phase prompt now asks for this
    explicitly. A project without that script fails this check with a clear, actionable
    error rather than hanging (the preview command backgrounds instantly either way;
    the failure shows up as "no visible content" once navigation still times out or hits
    a non-listening port).
    """
    script_path = cwd / _SMOKE_CHECK_FILENAME
    try:
        script_path.write_text(_SMOKE_CHECK_SCRIPT, encoding="utf-8")
        return run_qa_commands_in_docker(
            (_SHELL_COMMAND,),
            cwd,
            image=_PLAYWRIGHT_IMAGE,
            timeout_seconds=timeout_seconds,
            docker_client_factory=docker_client_factory,
        )
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass
