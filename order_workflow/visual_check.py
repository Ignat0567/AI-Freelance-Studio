"""QA depth, level (b): did the built page actually adopt the design it was given?

`npm run build` proves it compiles. The functional smoke check proves it renders and is
alive. Neither notices that the design system specified a near-black ground with a soft
blue accent and the coding CLI shipped default white with browser-blue links -- the gap
this module closes, and the one the pipeline's own docs called its largest.

Every assertion here is measured, not judged:

  * palette adherence -- computed colors actually painted on the page, compared to the
    approved palette's hex values in RGB space
  * WCAG AA contrast  -- computed text colour against its effective background, a number
  * mobile overflow   -- scrollWidth > clientWidth at 375px, a boolean
  * dark-mode response -- does the page repaint under prefers-color-scheme: dark

No model looks at the page and reports whether it is pretty. A model asked to grade its
own output grades generously, and shares every blind spot with the model that produced it;
a contrast ratio does not. The output is a list of exact deltas ("--surface painted
#FFFFFF, approved palette says #182236"), which is what makes the repair prompt that
follows surgical instead of "make it look better".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .docker_qa_runner import PLAYWRIGHT_IMAGE, PLAYWRIGHT_NPM_VERSION, run_qa_commands_in_docker
from .models import ElenaDesignConcept
from .qa_runner import QAOutcome

_VISUAL_CHECK_FILENAME = "___freelancerstudio_visual_check.mjs"
# Ships with the project rather than being cleaned up like the script above: MVP_ACCEPTANCE's
# second criterion asks the delivered folder to prove the thing runs, and a stranger reads a
# picture faster than an HTTP status line.
SCREENSHOT_FILENAME = "delivery_screenshot.png"
_PREVIEW_URL = "http://localhost:4173"
DEFAULT_VISUAL_CHECK_TIMEOUT_SECONDS = 240

# Two colours count as "the same intended colour" within this Euclidean RGB distance.
# Loose enough to survive a designer-ish nudge or an alpha composite, tight enough that a
# different hue never passes for the approved one.
COLOR_MATCH_TOLERANCE = 26

# Below this share of the approved palette actually appearing on the page, the build has
# not adopted the design -- it has its own.
MIN_PALETTE_COVERAGE = 0.5

# WCAG 2.1 AA for body text. Large text is allowed 3.0 and the script applies that itself.
MIN_CONTRAST_NORMAL = 4.5
MIN_CONTRAST_LARGE = 3.0

_HEX_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


@dataclass(frozen=True, slots=True)
class ExpectedPalette:
    """The approved colours, flattened out of whatever the brief carries."""

    background: str
    colors: tuple[str, ...]

    def as_json(self) -> str:
        return json.dumps({"background": self.background, "colors": list(self.colors)})


def _normalise_hex(value: str) -> str:
    text = value.strip().lower()
    if len(text) == 4:  # #abc -> #aabbcc
        return "#" + "".join(char * 2 for char in text[1:])
    return text


def palette_from_concept(concept: ElenaDesignConcept | None, *, style_spec: str = "") -> ExpectedPalette | None:
    """Flatten the approved design into a list of hex colours to look for.

    Prefers the structured ThemePalette on the Elena concept; falls back to scraping hex
    literals out of the style-pack prose, which is where the nine style packs keep their
    real values. Returns None when the brief carries no colour direction at all -- there is
    then nothing to check adherence against, and the gate skips rather than inventing a
    standard the coding CLI was never told about.
    """
    if concept is not None:
        light = concept.light_theme
        values = [light.background, light.surface, light.text, light.accent]
        colors = tuple(dict.fromkeys(_normalise_hex(v) for v in values if isinstance(v, str) and v.strip()))
        if colors:
            return ExpectedPalette(background=_normalise_hex(light.background), colors=colors)

    scraped = tuple(dict.fromkeys(_normalise_hex(m) for m in _HEX_PATTERN.findall(style_spec or "")))
    if not scraped:
        return None
    return ExpectedPalette(background=scraped[0], colors=scraped[:8])


def _build_script(palette: ExpectedPalette, screenshot_name: str = SCREENSHOT_FILENAME) -> str:
    return f"""import {{ chromium }} from 'playwright';

const TARGET_URL = {_PREVIEW_URL!r};
const EXPECTED = {palette.as_json()};
const SCREENSHOT_PATH = {screenshot_name!r};
const TOLERANCE = {COLOR_MATCH_TOLERANCE};
const MIN_COVERAGE = {MIN_PALETTE_COVERAGE};
const MIN_CONTRAST_NORMAL = {MIN_CONTRAST_NORMAL};
const MIN_CONTRAST_LARGE = {MIN_CONTRAST_LARGE};

function hexToRgb(hex) {{
  const clean = hex.replace('#', '');
  return [parseInt(clean.slice(0, 2), 16), parseInt(clean.slice(2, 4), 16), parseInt(clean.slice(4, 6), 16)];
}}
function rgbToHex([r, g, b]) {{
  return '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
}}
function distance(a, b) {{
  return Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2);
}}

// Everything below runs in the page: colours have to be read as the browser actually
// painted them, not as they were authored. Thresholds are inlined rather than referenced
// -- this function body is serialised and evaluated in the browser context, where none of
// the Node-scope constants above exist.
const PAGE_PROBE = () => {{
  const MIN_CONTRAST_NORMAL = {MIN_CONTRAST_NORMAL};
  const MIN_CONTRAST_LARGE = {MIN_CONTRAST_LARGE};
  function parse(color) {{
    const m = color.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((p) => parseFloat(p.trim()));
    if (parts.length >= 4 && parts[3] === 0) return null;   // fully transparent
    return [parts[0], parts[1], parts[2]];
  }}
  function effectiveBackground(node) {{
    let current = node;
    while (current && current !== document.documentElement) {{
      const bg = parse(getComputedStyle(current).backgroundColor);
      if (bg) return bg;
      current = current.parentElement;
    }}
    const rootBg = parse(getComputedStyle(document.documentElement).backgroundColor);
    return rootBg || [255, 255, 255];
  }}
  function luminance([r, g, b]) {{
    const chan = [r, g, b].map((v) => {{
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    }});
    return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2];
  }}
  function contrast(fg, bg) {{
    const l1 = luminance(fg), l2 = luminance(bg);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }}

  const painted = new Map();
  const contrastFailures = [];
  const targets = [];

  const bodyBg = effectiveBackground(document.body);

  for (const node of document.querySelectorAll('*')) {{
    const rect = node.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    const style = getComputedStyle(node);
    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) === 0) continue;

    const area = rect.width * rect.height;
    const bg = parse(style.backgroundColor);
    if (bg) {{
      const key = bg.join(',');
      painted.set(key, (painted.get(key) || 0) + area);
    }}

    // Only nodes holding their own text are candidates for a contrast reading.
    const ownText = Array.from(node.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join('')
      .trim();
    if (ownText.length > 1) {{
      const fg = parse(style.color);
      if (fg) {{
        const key = fg.join(',');
        painted.set(key, (painted.get(key) || 0) + area * 0.25);
        const size = parseFloat(style.fontSize) || 16;
        const weight = parseInt(style.fontWeight, 10) || 400;
        const isLarge = size >= 24 || (size >= 18.66 && weight >= 700);
        const ratio = contrast(fg, effectiveBackground(node));
        const floor = isLarge ? MIN_CONTRAST_LARGE : MIN_CONTRAST_NORMAL;
        if (ratio < floor) {{
          contrastFailures.push({{
            text: ownText.slice(0, 40),
            ratio: Math.round(ratio * 100) / 100,
            required: floor,
            fg: parse(style.color),
            bg: effectiveBackground(node),
            fontSize: Math.round(size),
          }});
        }}
      }}
    }}

    // Collected whole, not filtered to the undersized ones: whether a small target is a
    // defect depends on what is next to it, which cannot be known one node at a time.
    if (node.matches('a[href], button, [role="button"], input:not([type="hidden"]), select')) {{
      targets.push({{ node, rect }});
    }}
  }}

  // --- layout defects -------------------------------------------------------
  // Only the objectively wrong ones. "Badly composed" is not measurable and is left to a
  // human; "this text sits on top of that text" and "this label is silently cut in half"
  // are, and both are what "elements hang wrong" usually turns out to be.
  // A finding has to say *which* element. "<div> is cut off by 185px" names one of several
  // hundred divs: on 2026-08-27 two of three findings read that way, and the repair spent 25
  // turns and $0.89 editing a theme toggle unrelated to any of them. Own text first, because
  // that is what a reader of the report recognises; otherwise the selector that would find it,
  // with the surrounding text as a landmark.
  function describe(node) {{
    const text = (node.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 32);
    if (text) return `"${{text}}"`;
    const tag = node.tagName.toLowerCase();
    const id = node.id ? `#${{node.id}}` : '';
    const classes = (typeof node.className === 'string' ? node.className : '')
      .trim().split(/\\s+/).filter(Boolean).slice(0, 2).map((name) => `.${{name}}`).join('');
    const named = node.getAttribute('data-testid') || node.getAttribute('aria-label') || '';
    const parent = node.parentElement && node.parentElement !== document.body ? node.parentElement : null;
    const parentText = ((parent && parent.innerText) || '').trim().replace(/\\s+/g, ' ').slice(0, 24);
    const landmark = parentText ? ` inside "${{parentText}}"` : '';
    return `<${{tag}}${{id}}${{classes}}${{named ? `[${{named}}]` : ''}}>${{landmark}}`;
  }}

  const textBoxes = [];
  const clippedNodes = [];
  const pastViewportNodes = [];
  const viewportWidth = document.documentElement.clientWidth;

  for (const node of document.querySelectorAll('*')) {{
    const rect = node.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) continue;
    const style = getComputedStyle(node);
    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) === 0) continue;

    // Content silently cut off: the box clips its own overflow and has more to show, with
    // no ellipsis to signal it. A scrollable region is excluded -- there the overflow is
    // reachable rather than lost.
    const clips = style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflow === 'clip' || style.overflowX === 'clip';
    const scrollable = style.overflowY === 'auto' || style.overflowY === 'scroll' || style.overflowX === 'auto' || style.overflowX === 'scroll';
    // ...and only where there is content to lose. Seven generations in a row were charged
    // a repair for a decorative background layer: an aria-hidden, pointer-events-none
    // wrapper holding three empty divs, styled with position fixed, inset 0 and overflow
    // clip, whose children are blurred colour blobs deliberately larger than the viewport
    // and drifting. The clipping is the design, not a defect, and there is no "end of the
    // content" left invisible because there is no content. A box holding text or a picture
    // is still checked, which is every case this rule was written for.
    const holdsContent = (node.innerText || '').trim().length > 0 || node.querySelector('img, svg, canvas, video, picture, iframe') !== null;
    if (clips && !scrollable && holdsContent && style.textOverflow !== 'ellipsis' && node.scrollWidth > node.clientWidth + 2 && node.clientWidth > 0) {{
      clippedNodes.push({{ node, what: describe(node), lost: node.scrollWidth - node.clientWidth }});
    }}

    // Static only. An absolutely or fixed positioned box sitting outside the viewport is a
    // placement decision, not a flow bug -- the skip link every accessible page starts with
    // lives at left:-9999px on purpose, and failing it would send the repair loop to delete
    // an accessibility feature. A box laid out by normal flow has no such excuse.
    if (style.position === 'static' && (rect.left < -1 || rect.right > viewportWidth + 1)) {{
      pastViewportNodes.push({{ node, what: describe(node), overhang: Math.round(Math.max(-rect.left, rect.right - viewportWidth)) }});
    }}

    // Own text only, and only statically positioned boxes: absolute/fixed layering is a
    // design choice (badges, tooltips, modals), not a bug.
    const ownText = Array.from(node.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join('').trim();
    if (ownText.length > 1 && style.position === 'static' && textBoxes.length < 160) {{
      textBoxes.push({{ node, rect, label: describe(node) }});
    }}
  }}

  // WCAG 2.5.8 target size (minimum), including the two exceptions it is written with.
  // Every recorded finding of this rule named an <a> 102-271px wide and 18-23px tall -- text
  // links sized by their own type, not controls too small to hit -- and "Enlarge them" asks
  // for the one thing that would break the sentence or the list they sit in.
  //
  //   Inline:  a link inside a run of text is sized by that text.
  //   Spacing: an undersized target whose 24px circle reaches no other target is still
  //            reachable without hitting something else, which is what the floor is for.
  //
  // What is left is the case the rule was written for: small controls crowded together.
  const smallTargets = [];
  for (const target of targets) {{
    const rect = target.rect;
    if (rect.width >= 24 && rect.height >= 24) continue;

    const parent = target.node.parentElement;
    const parentOwnText = parent
      ? Array.from(parent.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join('')
      : '';
    if (parentOwnText.length > 1) continue;   // inline, in a sentence

    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    let neighbour = null;
    for (const other of targets) {{
      if (other === target) continue;
      if (other.node.contains(target.node) || target.node.contains(other.node)) continue;
      const box = other.rect;
      if (box.width < 24 || box.height < 24) {{
        // Two circles: they intersect when their centres are closer than one diameter.
        const dx = cx - (box.left + box.width / 2);
        const dy = cy - (box.top + box.height / 2);
        if (Math.sqrt(dx * dx + dy * dy) < 24) {{ neighbour = other; break; }}
      }} else {{
        // A circle and a full-size target: the nearest point of its box inside the radius.
        const px = Math.max(box.left, Math.min(cx, box.right));
        const py = Math.max(box.top, Math.min(cy, box.bottom));
        if (Math.sqrt((cx - px) ** 2 + (cy - py) ** 2) < 12) {{ neighbour = other; break; }}
      }}
    }}
    if (!neighbour) continue;

    smallTargets.push({{
      tag: target.node.tagName.toLowerCase(),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
      what: describe(target.node),
      near: describe(neighbour.node),
    }});
  }}

  // One defect, one finding. A container that hangs past the viewport drags every child
  // with it, and each of them satisfies the same condition: on 2026-08-26 the reading
  // journal reported "Finished 2 Finished books, sorta", "Finished 2", "2" and "Finished
  // books, sortable by colu" -- one table and three of its own descendants, filling four of
  // the eight slots the repair prompt had. Moving the outermost box back inside brings the
  // rest with it, so only the outermost is worth saying. The node refs stay behind here:
  // this result is serialised out of the page, and a DOM node cannot cross that boundary.
  function outermost(entries) {{
    return entries.filter((entry) => !entries.some((other) => other !== entry && other.node.contains(entry.node)));
  }}
  const clipped = outermost(clippedNodes).map(({{ what, lost }}) => ({{ what, lost }}));
  const pastViewport = outermost(pastViewportNodes).map(({{ what, overhang }}) => ({{ what, overhang }}));

  const overlaps = [];
  // One pair of labels, one finding. A table header whose cells are wrapped in divs reports
  // the same collision from every combination of the wrappers: on 2026-08-28 "PAGES" and
  // "STATUS" overlapping was five of the seven findings in one repair prompt -- three at
  // 1280px and two at 375px -- for one defect. Ancestor pairs were already skipped; these
  // are siblings that describe themselves identically.
  const overlapKeys = new Set();
  for (let i = 0; i < textBoxes.length && overlaps.length < 6; i += 1) {{
    for (let j = i + 1; j < textBoxes.length && overlaps.length < 6; j += 1) {{
      const a = textBoxes[i];
      const b = textBoxes[j];
      if (a.node.contains(b.node) || b.node.contains(a.node)) continue;
      const key = [a.label, b.label].sort().join(' || ');
      if (overlapKeys.has(key)) continue;
      const width = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
      const height = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
      if (width <= 1 || height <= 1) continue;
      const smaller = Math.min(a.rect.width * a.rect.height, b.rect.width * b.rect.height);
      // A quarter of the smaller box: brushing borders are normal, half-covered text is not.
      if (smaller > 0 && (width * height) / smaller > 0.25) {{
        overlapKeys.add(key);
        overlaps.push({{ a: a.label, b: b.label, percent: Math.round(((width * height) / smaller) * 100) }});
      }}
    }}
  }}

  return {{
    bodyBg,
    painted: Array.from(painted.entries()).map(([k, area]) => ({{ rgb: k.split(',').map(Number), area }})),
    contrastFailures: contrastFailures.sort((a, b) => a.ratio - b.ratio).slice(0, 8),
    smallTargets: smallTargets.slice(0, 6),
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    clipped: clipped.slice(0, 4),
    pastViewport: pastViewport.slice(0, 4),
    overlaps,
  }};
}};

async function main() {{
  const browser = await chromium.launch();
  const page = await browser.newPage({{ viewport: {{ width: 1280, height: 900 }} }});

  // The preview server is started in the background by the shell command that launches
  // this script, so it may not be listening yet. Without this retry a slow start reports
  // as ERR_CONNECTION_REFUSED, the gate fails, and the repair loop then asks the coding
  // CLI to fix a design problem that does not exist -- burning both its attempts.
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
    console.error('VISUAL CHECK FAILED: the preview server never accepted a connection.');
    console.error(String(lastError && lastError.message ? lastError.message : lastError));
    process.exit(1);
  }}
  await page.waitForTimeout(1200);

  const desktop = await page.evaluate(PAGE_PROBE);

  // Taken here and nowhere else: the page has settled, and this is the only moment it is in
  // the state a client would recognise -- the next three lines shrink it to a phone and
  // repaint it dark to take measurements, which would make a misleading delivery shot.
  // Wrapped because a failed screenshot must never fail the gate: this is documentation, and
  // a run that painted the right colours and rendered correctly has passed whether or not a
  // PNG could be written.
  try {{
    await page.screenshot({{ path: SCREENSHOT_PATH, fullPage: false }});
  }} catch (err) {{
    console.error('screenshot skipped: ' + String(err && err.message ? err.message : err));
  }}

  await page.setViewportSize({{ width: 375, height: 812 }});
  await page.waitForTimeout(600);
  const mobile = await page.evaluate(PAGE_PROBE);

  await page.emulateMedia({{ colorScheme: 'dark' }});
  await page.waitForTimeout(600);
  const dark = await page.evaluate(PAGE_PROBE);

  await browser.close();

  const failures = [];
  const notes = [];

  // --- 1. palette adherence -------------------------------------------------
  const expected = EXPECTED.colors.map(hexToRgb);
  const paintedSorted = desktop.painted.sort((a, b) => b.area - a.area);
  const matched = [];
  const missing = [];
  EXPECTED.colors.forEach((hex, i) => {{
    const target = expected[i];
    const hit = paintedSorted.find((p) => distance(p.rgb, target) <= TOLERANCE);
    (hit ? matched : missing).push(hex);
  }});
  const coverage = matched.length / EXPECTED.colors.length;
  notes.push(`Palette: ${{matched.length}}/${{EXPECTED.colors.length}} approved colours painted (${{Math.round(coverage * 100)}}%).`);

  const bgTarget = hexToRgb(EXPECTED.background);
  const bgActual = desktop.bodyBg;
  const bgDelta = distance(bgActual, bgTarget);
  if (bgDelta > TOLERANCE) {{
    failures.push(
      `Page background is ${{rgbToHex(bgActual)}} but the approved palette specifies ${{EXPECTED.background}}. ` +
      `Apply the approved background colour to the page ground.`
    );
  }}
  if (coverage < MIN_COVERAGE) {{
    const topPainted = paintedSorted.slice(0, 5).map((p) => rgbToHex(p.rgb)).join(', ');
    failures.push(
      `Only ${{Math.round(coverage * 100)}}% of the approved palette appears on the page. ` +
      `Missing: ${{missing.join(', ')}}. Largest colours actually painted: ${{topPainted}}. ` +
      `Use the approved palette instead of framework defaults.`
    );
  }}

  // --- 2. WCAG AA contrast --------------------------------------------------
  // Grouped by colour pair, not listed per element: one muted token reused across a dozen
  // labels is one fix, and a repair prompt listing it a dozen times buries the other
  // findings under duplicates.
  function groupContrast(entries) {{
    const groups = new Map();
    for (const f of entries) {{
      const key = `${{rgbToHex(f.fg)}}|${{rgbToHex(f.bg)}}`;
      if (!groups.has(key)) groups.set(key, {{ ...f, count: 0, samples: [] }});
      const g = groups.get(key);
      g.count += 1;
      if (g.samples.length < 3) g.samples.push(f.text);
      if (f.ratio < g.ratio) g.ratio = f.ratio;
    }}
    return Array.from(groups.values()).sort((a, b) => a.ratio - b.ratio);
  }}

  for (const g of groupContrast(desktop.contrastFailures).slice(0, 4)) {{
    const where = g.count === 1 ? `text "${{g.samples[0]}}"` : `${{g.count}} text elements (e.g. "${{g.samples.join('", "')}}")`;
    failures.push(
      `Contrast ${{g.ratio}}:1 (needs ${{g.required}}:1) -- ${{rgbToHex(g.fg)}} on ${{rgbToHex(g.bg)}}, affecting ${{where}}. ` +
      `Darken this text colour or lighten its background until it clears ${{g.required}}:1.`
    );
  }}

  // --- 3. mobile layout -----------------------------------------------------
  if (mobile.overflow > 1) {{
    failures.push(
      `The page scrolls horizontally at 375px wide (overflowing by ${{mobile.overflow}}px). ` +
      `Make the layout fit a phone viewport -- no fixed widths wider than the screen.`
    );
  }}
  for (const t of mobile.smallTargets.slice(0, 3)) {{
    failures.push(
      `At phone width, ${{t.what}} is a ${{t.w}}x${{t.h}}px tap target -- under 24x24px, and its ` +
      `24px target circle overlaps ${{t.near}}. Enlarge it (padding counts) or space the two further apart.`
    );
  }}

  // --- 3b. layout ------------------------------------------------------------
  // Reported per width, because a layout that is fine at 1280 and broken at 375 is the
  // usual shape of "the elements hang wrong" -- and the reverse happens too.
  for (const [label, probe] of [['desktop (1280px)', desktop], ['phone (375px)', mobile]]) {{
    for (const overlap of probe.overlaps) {{
      failures.push(
        `On ${{label}}, ${{overlap.a}} and ${{overlap.b}} overlap by ${{overlap.percent}}% of the smaller box. `
        + `Two statically positioned text elements are sitting on top of each other -- fix the spacing or the container width.`
      );
    }}
    for (const item of probe.clipped) {{
      failures.push(
        `On ${{label}}, ${{item.what}} is cut off by ${{item.lost}}px: its container hides the overflow with no ellipsis, `
        + `so the end of the content is silently invisible. Let it wrap, widen the container, or add text-overflow: ellipsis.`
      );
    }}
    for (const item of probe.pastViewport) {{
      failures.push(
        `On ${{label}}, ${{item.what}} extends ${{item.overhang}}px outside the viewport. Keep it inside the visible area.`
      );
    }}
  }}

  // --- 4. dark mode ---------------------------------------------------------
  const darkChanged = distance(dark.bodyBg, desktop.bodyBg) > TOLERANCE;
  notes.push(darkChanged
    ? `Dark mode: page repaints under prefers-color-scheme: dark (${{rgbToHex(desktop.bodyBg)}} -> ${{rgbToHex(dark.bodyBg)}}).`
    : `Dark mode: page does not repaint under prefers-color-scheme: dark.`);
  // Only meaningful once the page has actually repainted. A page that ignores the media
  // query reports its light-mode contrast here too, and reporting the same defect twice
  // just pads the repair prompt.
  if (darkChanged && dark.contrastFailures.length > 0) {{
    const worst = groupContrast(dark.contrastFailures)[0];
    failures.push(
      `In dark mode, contrast drops to ${{worst.ratio}}:1 (${{rgbToHex(worst.fg)}} on ${{rgbToHex(worst.bg)}}), ` +
      `affecting e.g. "${{worst.samples[0]}}". Fix the dark palette on its own terms, do not only invert the light one.`
    );
  }}

  for (const note of notes) console.log(note);

  if (failures.length > 0) {{
    console.error('VISUAL CHECK FAILED:');
    for (const failure of failures) console.error(`- ${{failure}}`);
    process.exit(1);
  }}
  console.log('VISUAL CHECK PASSED');
  process.exit(0);
}}

main().catch((err) => {{
  console.error('VISUAL CHECK FAILED: script error');
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
    f"node {_VISUAL_CHECK_FILENAME}; "
    "STATUS=$?; "
    "pkill -f 'preview' >/dev/null 2>&1 || true; "
    "exit $STATUS"
)


def run_visual_check_in_docker(
    qa_commands: tuple[str, ...],
    cwd: Path,
    *,
    palette: ExpectedPalette,
    timeout_seconds: int = DEFAULT_VISUAL_CHECK_TIMEOUT_SECONDS,
    docker_client_factory=None,
) -> QAOutcome:
    script_path = cwd / _VISUAL_CHECK_FILENAME
    try:
        script_path.write_text(_build_script(palette), encoding="utf-8")
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


def build_visual_check_runner(palette: ExpectedPalette | None) -> Callable[[tuple[str, ...], Path], QAOutcome] | None:
    """Bind the approved palette into the `(qa_commands, cwd) -> QAOutcome` shape the
    per-phase QA/repair machinery already expects. Returns None when the brief carries no
    colour direction, so the caller can skip the gate instead of asserting a standard
    nobody specified.
    """
    if palette is None:
        return None

    def _runner(qa_commands: tuple[str, ...], cwd: Path) -> QAOutcome:
        return run_visual_check_in_docker(qa_commands, cwd, palette=palette)

    return _runner
