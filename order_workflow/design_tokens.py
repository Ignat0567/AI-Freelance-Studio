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


def _tokens(palette: ThemePalette) -> list[str]:
    return [
        f"  --color-background: {palette.background};",
        f"  --color-surface: {palette.surface};",
        f"  --color-text: {palette.text};",
        f"  --color-accent: {palette.accent};",
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
        "   do not introduce new colour literals. */",
        ":root {",
        *_tokens(light),
        "}",
        "",
        "@media (prefers-color-scheme: dark) {",
        "  :root {",
        *[f"  {line}" for line in _tokens(dark)],
        "  }",
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
