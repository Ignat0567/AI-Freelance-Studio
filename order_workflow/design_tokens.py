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


MIN_TEXT_CONTRAST = 4.5


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    raw = hex_colour.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _blend(colour: str, towards: str, amount: float) -> str:
    """`colour` moved `amount` of the way to `towards`, channel by channel."""
    mixed = (round(a + (b - a) * amount) for a, b in zip(_rgb(colour), _rgb(towards)))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def accent_text_on(accent: str, grounds: tuple[str, ...], *, minimum: float = MIN_TEXT_CONTRAST) -> str:
    """The approved accent, moved just far enough to be legible *as text* on these grounds.

    Every style pack tells the build to use the accent for links; five of the nine ship a
    light accent that cannot carry text on their own background. `#0071e3` on `#f5f5f7` is
    4.31:1 against a 4.5:1 floor -- measured on the page, reported by the gate, and reported
    again on the next generation, because there was no way out of it: the tokens file says
    not to write colour literals, and the palette check counts the approved accent as one of
    the four colours that must be painted. So the build was told to use a colour, and then
    failed for using it.

    This is the same arithmetic as `readable_on`, in the other direction, and it belongs in
    the same place -- upstream of the build rather than inside a repair loop. The accent
    keeps its hue: it is darkened (or lightened) toward the end that has room, in small
    steps, and the first step that clears the floor on *both* grounds wins.
    """
    if all(contrast_ratio(accent, ground) >= minimum for ground in grounds):
        return accent
    hardest = min(grounds, key=lambda ground: contrast_ratio(accent, ground))
    direction = readable_on(hardest)
    steps = 50
    for step in range(1, steps + 1):
        candidate = _blend(accent, direction, step / steps)
        if all(contrast_ratio(candidate, ground) >= minimum for ground in grounds):
            return candidate
    # Both ends exhausted: the grounds themselves are mid-tone. The most legible colour
    # available is still better than the one that measured as failing.
    return direction


def _tokens(palette: ThemePalette) -> list[str]:
    return [
        f"  --color-background: {palette.background};",
        f"  --color-surface: {palette.surface};",
        f"  --color-text: {palette.text};",
        f"  --color-accent: {palette.accent};",
        # Not part of the approved palette -- derived from it. An accent is chosen to stand
        # out, which frequently makes it exactly the colour white text cannot sit on.
        f"  --color-on-accent: {readable_on(palette.accent)};",
        # Also derived: the accent as the colour of *text*, which is what "use the accent for
        # links" asks for and what several approved accents cannot do on their own background.
        f"  --color-accent-text: {accent_text_on(palette.accent, (palette.background, palette.surface))};",
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
        "   Two of the variables are derived rather than approved, and exist because an accent",
        "   chosen to stand out is rarely legible against either extreme: --color-on-accent is",
        "   what text sitting *on* the accent must be, and --color-accent-text is the accent",
        "   itself when it is the colour of text (links, eyebrows, figures) on the background or",
        "   a surface. Use --color-accent for fills, borders and glows; use --color-accent-text",
        "   the moment the accent becomes type.",
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
