"""Shared drawing helpers for the README cards: a text-width estimate and the 16x16 glyphs the buttons use.

The cards themselves are drawn by `scripts/otari_cards.py`. GitHub shows an SVG in a README as a plain
image (no script, no web font), so everything drawn with these helpers is shapes and system fonts.
"""

from html import escape  # noqa: F401  (re-exported: otari_cards uses rc.escape)


def text_width(text: str, size: float, bold: bool = False) -> float:
    """A close estimate of rendered width, so chips and buttons fit their labels without a font file."""
    narrow, wide = set("iljtfr.,:;'| "), set("mwMW@")
    units = sum(0.31 if c in narrow else 0.9 if c in wide else 0.68 if c.isupper() or c.isdigit() else 0.6 for c in text)
    return units * size * (1.06 if bold else 1.0)


ICONS = {
    # 16x16 glyphs drawn at the button's left edge
    "play": '<path d="M5 3.5 L13 8 L5 12.5 Z" fill="currentColor"/>',
    "chart": '<rect x="2" y="9" width="3" height="5" rx="1" fill="currentColor"/><rect x="6.5" y="5" width="3" height="9" rx="1" fill="currentColor"/><rect x="11" y="2" width="3" height="12" rx="1" fill="currentColor"/>',
    "check": '<path d="M2.5 8.5 L6.5 12.5 L13.5 3.5" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "chat": '<path d="M2 3 h12 v8 h-7 l-3.5 3 v-3 h-1.5 z" fill="currentColor"/>',
    "shield": '<path d="M8 1.5 L14 4 V8 C14 11.5 11 13.8 8 14.8 C5 13.8 2 11.5 2 8 V4 Z" fill="currentColor"/>',
}
