"""QA depth, level (c): does the app actually hold its own state?

The gates before this one check shape. `npm run build` proves it compiles, the smoke check
proves it renders and has controls, the visual check proves it matches the approved design,
the container proves it serves. An app can pass all four and still be a facade: every screen
renders beautifully, every control moves, and nothing is wired to anything.

That is not hypothetical. A delivered Focus Timer passed every gate above with a settings
screen whose duration control was `useState('25')` -- local component state the timer never
read and that reset on every navigation. The one test the pipeline asked for covered the
pure timer engine, which worked fine; nothing covered the wiring that did not.

So this gate changes a control, navigates away, comes back, and checks the value survived.
That is a property, not an opinion: either the DOM still holds what the user typed or it
does not. No model looks at the app and decides whether it "seems connected".

Deliberately still NOT covered: whether the feature is *correct*. This catches a facade,
not a wrong calculation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .docker_qa_runner import PLAYWRIGHT_IMAGE, PLAYWRIGHT_NPM_VERSION, run_qa_commands_in_docker
from .qa_runner import QAOutcome

_CHECK_FILENAME = "___freelancerstudio_state_check.mjs"
_PREVIEW_URL = "http://localhost:4173"
DEFAULT_STATE_CHECK_TIMEOUT_SECONDS = 240

# The one rule that decides whether a field is required to survive navigation. Kept as its
# own constant so the browser fixture below and the shipped check cannot drift apart: a
# rule verified in a fixture that is not the rule being shipped verifies nothing.
_TRANSIENT_INPUT_JS = """\n// A field whose value the app is *supposed* to drop on navigation. Two kinds: the
// empty-me-after-submit inputs of an add/create form, and a search or filter box.
// Without this the gate demanded that a half-typed new item and a stale search query
// survive navigation, which is not continuity -- it is a product nobody ordered. The
// reading-journal delivery of 2026-08-26 shows the price: to satisfy those four
// findings the repair wrote the draft book form and the search query into
// localStorage, so the app now reopens with a stale filter and a half-typed book.
//
// Deliberately narrow. A "Save"/"Apply"/"Update" form is still checked, because the
// defect this gate exists for -- a Focus Timer settings screen whose duration was
// component-local state -- lives in exactly such a form.
const isTransient = (node, label) => {
  const text = `${label} ${node.getAttribute('placeholder') || ''}`.toLowerCase();
  if (node.type === 'search' || /(search|filter|find|query)/.test(text)) return true;
  const form = node.closest('form');
  if (!form) return false;
  // type=button is included on purpose: a React add-form usually avoids a page reload
  // with `<button type="button" onClick={add}>`, and excluding those left exactly the
  // forms this rule is about unexempted. The label match below is what separates "Add"
  // from "Cancel", so nothing is lost by looking at every button but a reset.
  const submits = Array.from(form.querySelectorAll('button, input[type=submit]')).filter(
    (element) => element.type !== 'reset',
  );
  return submits.some((element) =>
    /^\\s*(add|create|new|post|insert|append)\\b/i.test(element.value || element.innerText || ''),
  );
};"""


_CHECK_SCRIPT = f"""import {{ chromium }} from 'playwright';

const TARGET_URL = {_PREVIEW_URL!r};

// Controls are driven through Playwright's own API rather than by assigning .value in the
// page: React's controlled inputs ignore a raw assignment (no synthetic event is emitted),
// so an in-page mutation would silently do nothing and every app would look broken.
async function readControls(page) {{
  return page.evaluate(() => {{
    {_TRANSIENT_INPUT_JS}
    const nodes = Array.from(document.querySelectorAll('input:not([type=hidden]), select, textarea'));
    return nodes
      .filter((node) => {{
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && !node.disabled && !node.readOnly;
      }})
      .map((node, index) => {{
        const label =
          node.getAttribute('aria-label')
          || (node.labels && node.labels[0] ? node.labels[0].innerText.trim() : '')
          || node.getAttribute('name')
          || node.id
          || `control ${{index + 1}}`;
        return {{
          index,
          key: `${{node.tagName.toLowerCase()}}:${{node.type || ''}}:${{label}}`,
          label: label.slice(0, 60),
          transient: isTransient(node, label),
          tag: node.tagName.toLowerCase(),
          type: node.type || '',
          value: node.type === 'checkbox' || node.type === 'radio' ? String(node.checked) : String(node.value ?? ''),
        }};
      }});
  }});
}}

async function mutate(page, control) {{
  const handles = await page.$$('input:not([type=hidden]), select, textarea');
  const handle = handles[control.index];
  if (!handle) return null;
  try {{
    if (control.type === 'checkbox' || control.type === 'radio') {{
      await handle.click();
    }} else if (control.tag === 'select') {{
      const options = await handle.$$eval('option', (list) => list.map((o) => o.value));
      const next = options.find((value) => value !== control.value);
      if (next === undefined) return null;
      await handle.selectOption(next);
    }} else if (control.type === 'number' || control.type === 'range') {{
      const current = Number(control.value);
      const next = Number.isFinite(current) ? String(current + 1) : '7';
      await handle.fill(next);
    }} else {{
      await handle.fill(`${{control.value}}zz`);
    }}
  }} catch {{
    return null;
  }}
  await page.waitForTimeout(150);
  const after = await readControls(page);
  const updated = after.find((item) => item.index === control.index);
  // Only a control that actually took the new value is worth re-checking later: one that
  // refused the edit is a different problem, and reporting it here would be misleading.
  return updated && updated.value !== control.value ? updated : null;
}}

// Hash routes are the common shape for a static `preview` build (no server rewrites),
// but a path route has to survive a real navigation, so both are handled.
async function goTo(page, route) {{
  if (route.startsWith('#')) {{
    await page.evaluate((target) => {{ window.location.hash = target.replace(/^#/, ''); }}, route);
  }} else {{
    await page.goto(`${{TARGET_URL}}${{route}}`, {{ waitUntil: 'load' }});
  }}
  await page.waitForTimeout(500);
}}

async function main() {{
  const browser = await chromium.launch();
  const page = await browser.newPage({{ viewport: {{ width: 1280, height: 900 }} }});

  let lastError = null;
  for (let attempt = 0; attempt < 15; attempt += 1) {{
    try {{
      await page.goto(TARGET_URL, {{ waitUntil: 'load', timeout: 20000 }});
      lastError = null;
      break;
    }} catch (err) {{
      lastError = err;
      await page.waitForTimeout(2000);
    }}
  }}
  if (lastError) {{
    await browser.close();
    console.error('STATE CONTINUITY CHECK FAILED: the preview server never accepted a connection.');
    console.error(String(lastError && lastError.message ? lastError.message : lastError));
    process.exit(1);
  }}
  await page.waitForTimeout(1200);

  // Every distinct in-app destination. Without at least two there is nothing to navigate
  // between, and this whole property is vacuous.
  const routes = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a[href]'))
      .map((a) => a.getAttribute('href'))
      .filter((href) => href && (href.startsWith('#') || href.startsWith('/')) && href !== '#' && !href.startsWith('#main'))
      .filter((href, index, all) => all.indexOf(href) === index),
  );

  if (routes.length < 2) {{
    console.log('STATE CONTINUITY CHECK SKIPPED: the app has fewer than two navigable screens.');
    await browser.close();
    process.exit(0);
  }}

  const failures = [];
  const checked = [];
  const skipped = [];

  for (const route of routes) {{
    await goTo(page, route);

    const controls = await readControls(page);
    if (!controls.length) continue;

    const elsewhere = routes.find((other) => other !== route);
    for (const control of controls.slice(0, 6)) {{
      if (control.transient) {{
        skipped.push(`${{route}} ${{control.label}}`);
        continue;
      }}
      const mutated = await mutate(page, control);
      if (!mutated) continue;

      // Leave and come back the way a person would.
      await goTo(page, elsewhere);
      await goTo(page, route);

      const back = await readControls(page);
      const same = back.find((item) => item.key === mutated.key) || back.find((item) => item.index === mutated.index);
      checked.push(`${{route}} ${{mutated.label}}`);
      if (!same) {{
        failures.push(`On ${{route}}, the control "${{mutated.label}}" disappeared after navigating away and back.`);
      }} else if (same.value !== mutated.value) {{
        failures.push(
          `On ${{route}}, "${{mutated.label}}" was set to "${{mutated.value}}" but reverted to "${{same.value}}" `
          + `after navigating away and back. The value is component-local state that is lost on unmount -- `
          + `lift it into shared state (or persist it) so the rest of the app sees it too.`
        );
      }}
    }}
  }}

  await browser.close();

  console.log(`Checked ${{checked.length}} control(s) across ${{routes.length}} screens for state continuity.`);
  // Printed rather than silent: a reader of qa_evidence.md has to be able to see what this
  // gate decided not to ask about, and to disagree with it.
  if (skipped.length > 0) {{
    console.log(`Not required to survive navigation (draft or search inputs): ${{skipped.join(', ')}}.`);
  }}
  if (failures.length > 0) {{
    console.error('STATE CONTINUITY CHECK FAILED:');
    for (const failure of failures.slice(0, 5)) console.error(`- ${{failure}}`);
    process.exit(1);
  }}
  console.log('STATE CONTINUITY CHECK PASSED');
  process.exit(0);
}}

main().catch((err) => {{
  console.error('STATE CONTINUITY CHECK FAILED: script error');
  console.error(String(err && err.stack ? err.stack : err));
  process.exit(1);
}});
"""

_SHELL_COMMAND = (
    "npm install --no-audit --no-fund >/dev/null 2>&1 && "
    f"npm install --no-save --no-audit --no-fund playwright@{PLAYWRIGHT_NPM_VERSION} >/dev/null 2>&1 && "
    # The bundle has to be the one the repair just wrote. `npm run preview` serves
    # whatever is in dist/, and dist/ is whatever the phase built before the repair loop
    # started -- so a repair that edited src/ was invisible here unless the model happened
    # to rebuild on its own. Measured on 2026-08-26: dist/assets built 17:05, the repair
    # edited src/styles/global.css at 17:20, and the gate at 17:21 returned its three
    # findings word for word, having loaded the 17:05 bundle. Two repair calls and 865
    # seconds bought nothing a browser could see.
    "if ! npm run build >/tmp/freelancerstudio-build.log 2>&1; then "
    "echo 'BUILD FAILED -- the last change left the project not compiling:'; "
    "tail -n 30 /tmp/freelancerstudio-build.log; exit 1; fi; "
    "(npm run preview >/tmp/freelancerstudio-preview.log 2>&1 &) && "
    "sleep 4 && "
    f"node {_CHECK_FILENAME}; "
    "STATUS=$?; "
    "pkill -f 'preview' >/dev/null 2>&1 || true; "
    "exit $STATUS"
)


# The cases the rule has to get right, as a page a browser can actually load. Written down
# here rather than in a comment because this rule is a judgement about product behaviour and
# the only honest way to check a DOM judgement is to put it in front of a DOM:
#
#   python -c "from order_workflow.state_continuity_check import build_transient_input_fixture as f; #              open('fixture.html','w',encoding='utf-8').write(f())"
#
# then open the file and call `__classify()` in the console. Verified this way on 2026-08-26:
# the add-book form's three fields and the search box are exempt, while a "Save" settings
# field and a standalone notes textarea are still required to survive navigation.
_FIXTURE_HTML = """<!doctype html><meta charset="utf-8"><title>state continuity rule fixture</title>
<h1>Which fields must survive navigation</h1>
<form>
  <label>Title <input name="title"></label>
  <label>Author <input name="author"></label>
  <label>Page count <input name="pages" type="number"></label>
  <button type="submit">Add book</button>
</form>
<label>Search by title or author <input name="q"></label>
<form>
  <label>Session length <input name="duration" type="number"></label>
  <button type="submit">Save</button>
</form>
<label>Notes <textarea name="notes"></textarea></label>
<form>
  <label>Nickname <input name="nick"></label>
  <button type="button">Add person</button>
</form>
<script>
%s

window.__classify = () =>
  Array.from(document.querySelectorAll('input:not([type=hidden]), select, textarea')).map((node) => {
    const label = (node.labels && node.labels[0] ? node.labels[0].innerText.trim() : '') || node.getAttribute('name') || '';
    return { label, transient: isTransient(node, label) };
  });
</script>
"""


def build_transient_input_fixture() -> str:
    """The fixture page carrying the very rule the check ships, not a copy of it."""
    return _FIXTURE_HTML % _TRANSIENT_INPUT_JS


def run_state_continuity_check_in_docker(
    qa_commands: tuple[str, ...],
    cwd: Path,
    *,
    timeout_seconds: int = DEFAULT_STATE_CHECK_TIMEOUT_SECONDS,
    docker_client_factory=None,
) -> QAOutcome:
    script_path = cwd / _CHECK_FILENAME
    try:
        script_path.write_text(_CHECK_SCRIPT, encoding="utf-8")
        return run_qa_commands_in_docker(
            (_SHELL_COMMAND,),
            cwd,
            image=PLAYWRIGHT_IMAGE,
            timeout_seconds=timeout_seconds,
            docker_client_factory=docker_client_factory,
        )
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass
