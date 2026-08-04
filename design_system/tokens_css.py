from __future__ import annotations

from .models import ColorPalette, DesignTokens


def _palette_declarations(palette: ColorPalette) -> str:
    return (
        f"  --color-primary: {palette.primary};\n"
        f"  --color-secondary: {palette.secondary};\n"
        f"  --color-accent: {palette.accent};\n"
        f"  --color-background: {palette.background};\n"
        f"  --color-surface: {palette.surface};\n"
        f"  --color-text: {palette.text};\n"
        f"  --color-muted: {palette.muted};\n"
    )


def render_tokens_css(tokens: DesignTokens) -> str:
    pairing = tokens.font_pairing
    return (
        f"@import url('{pairing.google_fonts_import_url}');\n\n"
        ":root {\n"
        f"{_palette_declarations(tokens.light)}"
        f"  --font-heading: {pairing.heading_family};\n"
        f"  --font-body: {pairing.body_family};\n"
        "}\n\n"
        "@media (prefers-color-scheme: dark) {\n"
        "  :root {\n"
        f"{_palette_declarations(tokens.dark)}"
        "  }\n"
        "}\n"
    )
