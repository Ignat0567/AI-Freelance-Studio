"""Put the approved palette in the workspace as a file, before the coding CLI runs.

Across the archived live runs the visual gate rejected work for painting framework defaults
instead of the approved colours -- one run measured 1 of 4 approved colours on the page (25%)
-- and each rejection costs a repair call, which is a full coding-CLI invocation.

Every attempt to fix that so far has been an attempt to say it better in the prompt: name the
palette, then name what the gate will measure. That is the weaker instrument. A prompt asks a
model to reproduce four hex values correctly from prose; a file in the workspace is simply
there, and `var(--color-accent)` cannot drift from the approved accent because it is not a
copy of it.

So this turns three of the gate's four checks from things to verify into things that hold by
construction:

  * palette adherence -- the tokens are the palette, and the prompt points at them
  * dark mode repaint -- the media query and the ground rule are already written
  * contrast          -- the approved pairs are computed once, by Elena, not re-derived per run

What it deliberately does not do is guess. With no structured concept there is no reliable
mapping from a list of scraped hex values onto background/surface/text/accent, and getting
`text` wrong would author a contrast failure rather than prevent one. No concept, no file --
the same rule the gate itself follows when it has no colour direction to check against.
"""

from __future__ import annotations

from pathlib import Path

from .models import ElenaDesignConcept, ThemePalette

# Root of the workspace, not src/: the coding CLI scaffolds the project itself and chooses
# its own layout, so anything written under an assumed source directory is either in the
# wrong place or in the way. The workspace is already non-empty when the CLI starts (the
# phase prompt and an isolated .git are written there first), so one more root file changes
# nothing about how scaffolding behaves.
DESIGN_TOKENS_FILENAME = "design-tokens.css"


def _channel(value: int) -> float:
    fraction = value / 255
    return fraction / 12.92 if fraction <= 0.04045 else ((fraction + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_colour: str) -> float:
    """WCAG 2.x relative luminance. The same formula the visual gate runs in the browser --
    reimplemented here rather than shared because that copy is JavaScript embedded in a
    page script, and this one has to run while the page does not exist yet."""
    raw = hex_colour.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((_relative_luminance(foreground), _relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(background: str, *, candidates: tuple[str, ...] = ("#ffffff", "#111111")) -> str:
    """The candidate text colour with the most contrast against this background.

    Exists because of a measured failure, not a hypothetical one: on the 2026-08-17
    b06-reading-journal run both ui_shell repair attempts -- 254 seconds, 20% of the whole
    run -- were spent on one defect, white text at 2.53:1 on the approved dark-theme accent.
    The accent was correct and the gate was right; nobody had worked out what text colour
    that accent can actually carry. That is arithmetic, and it belongs upstream of the build
    rather than in a repair loop.
    """
    return max(candidates, key=lambda candidate: contrast_ratio(candidate, background))


def _tokens(palette: ThemePalette) -> list[str]:
    return [
        f"  --color-background: {palette.background};",
        f"  --color-surface: {palette.surface};",
        f"  --color-text: {palette.text};",
        f"  --color-accent: {palette.accent};",
        # Not part of the approved palette -- derived from it. An accent is chosen to stand
        # out, which frequently makes it exactly the colour white text cannot sit on.
        f"  --color-on-accent: {readable_on(palette.accent)};",
    ]


def build_design_tokens_css(concept: ElenaDesignConcept | None) -> str | None:
    """The approved light and dark palettes as CSS custom properties, or None."""
    if concept is None:
        return None
    # No emptiness check: ThemePalette's fields are ShortText (min_length=1, whitespace
    # stripped), so a concept carrying a blank colour cannot be constructed in the first place.
    light, dark = concept.light_theme, concept.dark_theme
    lines = [
        "/* Approved design tokens for this project.",
        "   Written by AI Freelance Studio from the design concept the client approved, before",
        "   the build started. These four colours are the project's palette -- use the variables,",
        "   do not introduce new colour literals.",
        "",
        "   Three states on purpose. A generated app that adds its own light/dark control writes",
        "   its choice onto the root element, and that choice has to win over the system",
        "   preference -- while a project with no control still follows the system. Defining",
        "   only :root and the media query leaves those two mechanisms fighting: on 2026-08-27",
        "   the reading journal shipped a theme toggle, and the visual gate found near-white",
        "   text on a white surface because half the page had switched and half had not. */",
        ":root {",
        *_tokens(light),
        "}",
        "",
        "/* System preference, unless the app has explicitly asked for light. */",
        "@media (prefers-color-scheme: dark) {",
        "  :root:not([data-theme=\"light\"]) {",
        *[f"  {line}" for line in _tokens(dark)],
        "  }",
        "}",
        "",
        "/* The app's own choice, whatever the system says. Set data-theme on <html> from a",
        "   theme control; leave it unset to follow the system. */",
        ":root[data-theme=\"dark\"] {",
        *_tokens(dark),
        "}",
        "",
        ":root[data-theme=\"light\"] {",
        *_tokens(light),
        "}",
        "",
        "/* The ground is painted from the tokens, so dark mode actually repaints rather than",
        "   staying light -- which is one of the things the visual check measures. */",
        "html,",
        "body {",
        "  background-color: var(--color-background);",
        "  color: var(--color-text);",
        "}",
        "",
    ]
    return "\n".join(lines)


def write_design_tokens(workspace_path: Path, concept: ElenaDesignConcept | None) -> str | None:
    """Write the tokens file into the workspace. Returns its name, or None if not written.

    A failure to write is not a reason to fail the phase: the palette is still stated in the
    prompt and still measured by the gate afterwards, so the run degrades to exactly the
    behaviour it had before this existed.
    """
    css = build_design_tokens_css(concept)
    if css is None:
        return None
    try:
        (Path(workspace_path) / DESIGN_TOKENS_FILENAME).write_text(css, encoding="utf-8")
    except OSError:
        return None
    return DESIGN_TOKENS_FILENAME
