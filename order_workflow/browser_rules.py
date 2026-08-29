"""Rules two browser gates both need, written once.

The visual check (React builds) and the static-page check (one self-contained HTML file) run
in different containers against different products, and both ask some of the same questions.
Where the question is the same, a second copy of the answer is a second thing to get wrong:
the tap-target rule was fixed in the visual check on 2026-08-28 -- WCAG 2.5.8's inline and
spacing exceptions, and a finding that names the element -- and the copy in the static-page
check went on reporting every text link. It cost a repair on b02 the same night:

    Tap targets under 24x24px at 768px: a 43x16px, a 32x16px, a 49x16px. Enlarge them.

Three inline links in a sentence, all of them fine.

These snippets are JavaScript source, injected into a page-evaluated function by both gates.
They are plain strings rather than f-strings: the braces are JS, and the modules that embed
them format their own scripts.
"""

from __future__ import annotations


# WCAG 2.5.8 target size (minimum), including the two exceptions it is written with:
#
#   Inline   a target inside a run of text is sized by that text.
#   Spacing  an undersized target whose 24px circle reaches no other target is still
#            reachable without hitting something else, which is what the floor is for.
#
# What is left is the case the floor was written for: small controls crowded together. The
# finding names the target and what it collides with, because "a 131x21px" is not something a
# repair can act on -- and because crowding, not size alone, is the defect.
TAP_TARGET_RULE_JS = """
const TAP_TARGET_SELECTOR = 'a[href], button, [role="button"], [role="link"], [role="tab"], input:not([type="hidden"]), select, textarea';

function describeTapTarget(node) {
  const text = (node.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 32);
  if (text) return `"${text}"`;
  const tag = node.tagName.toLowerCase();
  const id = node.id ? `#${node.id}` : '';
  const classes = (typeof node.className === 'string' ? node.className : '')
    .trim().split(/\\s+/).filter(Boolean).slice(0, 2).map((name) => `.${name}`).join('');
  const named = node.getAttribute('data-testid') || node.getAttribute('aria-label') || '';
  return `<${tag}${id}${classes}${named ? `[${named}]` : ''}>`;
}

function smallTapTargets() {
  const targets = [];
  for (const node of document.querySelectorAll(TAP_TARGET_SELECTOR)) {
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (rect.width < 1 || style.visibility === 'hidden' || style.display === 'none') continue;
    targets.push({ node, rect });
  }

  const found = [];
  for (const target of targets) {
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
    for (const other of targets) {
      if (other === target) continue;
      if (other.node.contains(target.node) || target.node.contains(other.node)) continue;
      const box = other.rect;
      if (box.width < 24 || box.height < 24) {
        // Two circles: they intersect when their centres are closer than one diameter.
        const dx = cx - (box.left + box.width / 2);
        const dy = cy - (box.top + box.height / 2);
        if (Math.sqrt(dx * dx + dy * dy) < 24) { neighbour = other; break; }
      } else {
        // A circle and a full-size target: the nearest point of its box inside the radius.
        const px = Math.max(box.left, Math.min(cx, box.right));
        const py = Math.max(box.top, Math.min(cy, box.bottom));
        if (Math.sqrt((cx - px) ** 2 + (cy - py) ** 2) < 12) { neighbour = other; break; }
      }
    }
    if (!neighbour) continue;

    found.push({
      tag: target.node.tagName.toLowerCase(),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
      what: describeTapTarget(target.node),
      near: describeTapTarget(neighbour.node),
    });
  }
  return found;
}
"""


# The Node half: both gates compose their report outside the page, so the sentence lives here
# rather than in the rule above, and the two reports read identically to a client.
TAP_TARGET_MESSAGE_JS = """
function tapTargetFailure(widthLabel, t) {
  return `At ${widthLabel}, ${t.what} is a ${t.w}x${t.h}px tap target -- under 24x24px, and its `
    + `24px target circle overlaps ${t.near}. Enlarge it (padding counts) or space the two further apart.`;
}
"""
