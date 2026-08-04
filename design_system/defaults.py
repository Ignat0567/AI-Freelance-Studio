from __future__ import annotations

from .fonts import FONT_LIBRARY
from .models import ColorPalette, DesignTokens

DEFAULT_TOKENS = DesignTokens(
    light=ColorPalette(
        primary="#2952E3",
        secondary="#5B6472",
        accent="#F2A93B",
        background="#F7F8FA",
        surface="#FFFFFF",
        text="#12141A",
        muted="#8A93A3",
    ),
    dark=ColorPalette(
        primary="#7C93FF",
        secondary="#9AA3B2",
        accent="#F2A93B",
        background="#0B0D12",
        surface="#161923",
        text="#F2F4F8",
        muted="#6B7383",
    ),
    font_pairing=FONT_LIBRARY[0],
)
