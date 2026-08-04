from __future__ import annotations

from .models import FontPairing

# Curated, not AI-invented: an AI-authored font name has a real chance of not
# existing on Google Fonts and silently degrading to a generic system font,
# defeating the entire point of a design system. Closed vocabulary guarantees
# every generated site actually loads real, distinct type.
FONT_LIBRARY: tuple[FontPairing, ...] = (
    FontPairing(
        slug="modern-sans",
        heading_family="'Space Grotesk', sans-serif",
        body_family="'Inter', sans-serif",
        google_fonts_import_url="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500&display=swap",
    ),
    FontPairing(
        slug="editorial-serif",
        heading_family="'Fraunces', serif",
        body_family="'Inter', sans-serif",
        google_fonts_import_url="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=Inter:wght@400;500&display=swap",
    ),
    FontPairing(
        slug="technical-mono-accent",
        heading_family="'Sora', sans-serif",
        body_family="'Source Sans 3', sans-serif",
        google_fonts_import_url="https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=Source+Sans+3:wght@400;500&display=swap",
    ),
)


def get_font_pairing(slug: str) -> FontPairing | None:
    for pairing in FONT_LIBRARY:
        if pairing.slug == slug:
            return pairing
    return None
