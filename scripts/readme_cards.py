#!/usr/bin/env python3
"""Draws the README cards (header, figures, link buttons) as self-contained SVG.

  python3 scripts/readme_cards.py        # rewrite the static cards: header and buttons

The figures card is drawn by `scripts/refresh_numbers.py` from the ledger, through `figures_svg`, so
no number on it is typed by hand. GitHub shows an SVG in a README as a plain image (no script, no web
font), so everything here is shapes and system fonts.
"""

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "readme"

FONT = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
INK, MUTED, FAINT = "#f4f5f8", "#a3a9b8", "#6b7385"
PANEL, EDGE = "#0b0d13", "#232838"
VIOLET, BLUE, AMBER, TEAL, ROSE = "#a78bfa", "#60a5fa", "#fbbf24", "#2dd4bf", "#fb7185"


def text_width(text: str, size: float, bold: bool = False) -> float:
    """A close estimate of rendered width, so chips and buttons fit their labels without a font file."""
    narrow, wide = set("iljtfr.,:;'| "), set("mwMW@")
    units = sum(0.31 if c in narrow else 0.9 if c in wide else 0.68 if c.isupper() or c.isdigit() else 0.6 for c in text)
    return units * size * (1.06 if bold else 1.0)


def _frame(width: int, height: int, body: str, glows: list[tuple[float, float, float, str]]) -> str:
    """A dark rounded panel with soft coloured light rising from its lower edge, like the readme-aura cards."""
    defs, lights = [], []
    for i, (cx, cy, r, colour) in enumerate(glows):
        defs.append(f'<radialGradient id="g{i}" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{colour}" stop-opacity="0.55"/>'
                    f'<stop offset="1" stop-color="{colour}" stop-opacity="0"/></radialGradient>')
        lights.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r * 0.42:.0f}" fill="url(#g{i})"/>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="{FONT}">
<defs>{''.join(defs)}<clipPath id="panel"><rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18"/></clipPath></defs>
<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18" fill="{PANEL}"/>
<g clip-path="url(#panel)">{''.join(lights)}</g>
<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18" fill="none" stroke="{EDGE}" stroke-width="1.5"/>
{body}
</svg>
"""


def _chip(x: float, y: float, label: str) -> tuple[str, float]:
    w = text_width(label, 12) + 22
    return (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="24" rx="7" fill="#151a26" stroke="#2c3346"/>'
            f'<text x="{x + w / 2:.1f}" y="{y + 16}" fill="#c7cbe0" font-size="12" text-anchor="middle">{escape(label)}</text>'), w


def header_svg() -> str:
    width, height = 860, 230
    # Scales of justice inside a ringed medallion: the evidence on one pan, the verdict on the other.
    emblem = f"""<defs><linearGradient id="ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{VIOLET}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient></defs>
<circle cx="104" cy="106" r="52" fill="#121726" stroke="url(#ring)" stroke-width="4"/>
<g transform="translate(0 10)"><g stroke="{INK}" stroke-width="3" stroke-linecap="round" fill="none">
<line x1="104" y1="64" x2="104" y2="126"/><line x1="90" y1="128" x2="118" y2="128"/><line x1="76" y1="74" x2="132" y2="74"/>
<line x1="76" y1="74" x2="66" y2="98"/><line x1="76" y1="74" x2="86" y2="98"/><line x1="132" y1="74" x2="122" y2="98"/><line x1="132" y1="74" x2="142" y2="98"/>
</g>
<path d="M63 98 h26 a13 9 0 0 1 -26 0z" fill="{AMBER}"/><path d="M119 98 h26 a13 9 0 0 1 -26 0z" fill="{TEAL}"/>
<circle cx="104" cy="62" r="4" fill="{INK}"/></g>"""
    chips, x = [], 186
    for label in ["Autonomous agents", "Chainlink CRE", "Sepolia testnet", "Bitcoin timestamps", "Sigstore log"]:
        svg, w = _chip(x, 146, label)
        chips.append(svg)
        x += w + 8
    body = f"""{emblem}
<text x="186" y="76" fill="{INK}" font-size="40" font-weight="700" letter-spacing="-0.5">Verdict Desk</text>
<text x="186" y="104" fill="{MUTED}" font-size="16">An autonomous agent makes a market decision.</text>
<text x="186" y="126" fill="{MUTED}" font-size="16">Verdict Desk turns it into <tspan fill="{INK}" font-weight="600">evidence a lawyer can file</tspan>.</text>
{''.join(chips)}
<text x="186" y="202" fill="{FAINT}" font-size="11" letter-spacing="1.2">BLI LEGAL TECH HACKATHON 2  ·  AUTONOMOUS AGENTS + CRE  ·  PRACTICE TRADES ONLY</text>"""
    return _frame(width, height, body, [(160, 235, 260, VIOLET), (470, 245, 240, BLUE), (760, 235, 220, "#7c3aed")])


def figures_svg(figures: list[tuple[str, str, str]], footnote: str) -> str:
    """One column per figure: the number large and coloured, a short label in small capitals under it."""
    width, height = 860, 150
    colours = [VIOLET, BLUE, AMBER, TEAL, ROSE]
    col = (width - 40) / len(figures)
    parts = []
    for i, (value, label, sub) in enumerate(figures):
        cx = 20 + col * i + col / 2
        if i:
            parts.append(f'<line x1="{20 + col * i:.1f}" y1="30" x2="{20 + col * i:.1f}" y2="104" stroke="{EDGE}"/>')
        parts.append(f'<text x="{cx:.1f}" y="66" fill="{colours[i % len(colours)]}" font-size="31" font-weight="700" text-anchor="middle">{escape(value)}</text>')
        parts.append(f'<text x="{cx:.1f}" y="88" fill="{MUTED}" font-size="10.5" font-weight="700" letter-spacing="1.3" text-anchor="middle">{escape(label.upper())}</text>')
        parts.append(f'<text x="{cx:.1f}" y="104" fill="{FAINT}" font-size="10.5" text-anchor="middle">{escape(sub)}</text>')
    parts.append(f'<text x="{width / 2}" y="134" fill="{FAINT}" font-size="11" text-anchor="middle">{escape(footnote)}</text>')
    return _frame(width, height, "\n".join(parts), [(150, 160, 210, VIOLET), (430, 168, 230, BLUE), (720, 160, 210, "#9333ea")])


ICONS = {
    # 16x16 glyphs drawn at the button's left edge
    "play": '<path d="M5 3.5 L13 8 L5 12.5 Z" fill="currentColor"/>',
    "chart": '<rect x="2" y="9" width="3" height="5" rx="1" fill="currentColor"/><rect x="6.5" y="5" width="3" height="9" rx="1" fill="currentColor"/><rect x="11" y="2" width="3" height="12" rx="1" fill="currentColor"/>',
    "check": '<path d="M2.5 8.5 L6.5 12.5 L13.5 3.5" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    "chat": '<path d="M2 3 h12 v8 h-7 l-3.5 3 v-3 h-1.5 z" fill="currentColor"/>',
    "scale": '<path d="M8 2 v12 M4 14 h8 M2.5 5 h11 M2.5 5 l-2 4.5 h4 z M13.5 5 l-2 4.5 h4 z" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linejoin="round"/>',
    "shield": '<path d="M8 1.5 L14 4 V8 C14 11.5 11 13.8 8 14.8 C5 13.8 2 11.5 2 8 V4 Z" fill="currentColor"/>',
}

BUTTONS = [
    ("dashboard", "Live dashboard", "chart", BLUE),
    ("video", "Watch the demo", "play", ROSE),
    ("judge", "Judge path", "check", TEAL),
    ("ask", "Ask the record", "chat", VIOLET),
    ("lawyer", "Evidence for a lawyer", "scale", AMBER),
    ("witnesses", "Witnesses", "shield", "#34d399"),
]


def button_svg(label: str, icon: str, colour: str) -> str:
    width = int(text_width(label, 14, bold=True) + 62)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="42" viewBox="0 0 {width} 42" font-family="{FONT}">
<defs><linearGradient id="edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{colour}" stop-opacity="0.9"/><stop offset="1" stop-color="{colour}" stop-opacity="0.25"/></linearGradient></defs>
<rect x="1.5" y="1.5" width="{width - 3}" height="39" rx="19.5" fill="{PANEL}" stroke="url(#edge)" stroke-width="2"/>
<g transform="translate(18 13)" color="{colour}">{ICONS[icon]}</g>
<text x="42" y="26.5" fill="{INK}" font-size="14" font-weight="600">{escape(label)}</text>
</svg>
"""


def write_static() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {OUT / "header.svg": header_svg()}
    for slug, label, icon, colour in BUTTONS:
        files[OUT / f"button-{slug}.svg"] = button_svg(label, icon, colour)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    return list(files)


if __name__ == "__main__":
    for path in write_static():
        print(f"wrote {path.relative_to(ROOT)}")
    sys.exit(0)
