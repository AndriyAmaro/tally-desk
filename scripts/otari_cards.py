#!/usr/bin/env python3
"""Draws the README cards of the Otari entry as self-contained SVG (shapes and system fonts only, dark by default and light on request).

  python3 scripts/otari_cards.py           # rewrite the header, the buttons and the figures card
  python3 scripts/otari_cards.py --check   # exit 1 if the figures card does not match the ledger

The header and the buttons are static. The figures card (and web/otari-figures.json, its twin for the site) is computed from ledger/ledger.jsonl and the drill
evidence through verdict_desk.otari_report, so no number on it is typed by hand; scripts/refresh_otari_readme.py
redraws it on every cycle together with the README blocks. Shared drawing helpers come from readme_cards.py.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import readme_cards as rc  # noqa: E402
from verdict_desk import otari_report  # noqa: E402

OUT = ROOT / "docs" / "readme"
HEADER = OUT / "otari-header.svg"
FIGURES = OUT / "otari-figures.svg"
FIGURES_JSON = ROOT / "web" / "otari-figures.json"
FLOW = OUT / "otari-flow.svg"
FEATURES = OUT / "otari-features.svg"
ICON_DIR = OUT / "icons"
# (slug, label, icon, style): the first button is the primary action, the others are quiet
PRODUCT_NAME = "Verdict Desk on Otari"  # the product name on the header card; scripts/site_shell.py holds the one the site shell shows

BUTTONS = [
    ("cycle", "Call-by-call view", "play", "primary"),
    ("verify", "Verify a record", "check", "quiet"),
    ("audit", "Proofs table", "shield", "quiet"),
    ("feedback", "Feedback to Otari", "chat", "quiet"),
]

# The cards follow the site: dark by default, light when the reader's system asks for it (an SVG shown as an image honors prefers-color-scheme).
FONT = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
STYLE = """<style>
:root{--bg:#0d1017;--panel:#12161f;--bd:#262d3b;--tx:#e7ebf2;--mu:#a9b2c2;--su:#8a94a6;--ac:#2dd4bf;--onac:#03201d;--am:#fbbf24;--bull:#34d399;--bear:#fb7185;--chk:#a78bfa;--glow:#2dd4bf;--glowo:.13}
@media (prefers-color-scheme:light){:root{--bg:#ffffff;--panel:#f6f7fa;--bd:#dfe3ea;--tx:#0b1020;--mu:#475166;--su:#586377;--ac:#0f766e;--onac:#ffffff;--am:#b45309;--bull:#047857;--bear:#be123c;--chk:#6d28d9;--glow:#0f766e;--glowo:.07}}
.bg{fill:var(--bg)}.pn{fill:var(--panel)}.bd{stroke:var(--bd);fill:none}.ln{stroke:var(--bd)}
.tx{fill:var(--tx)}.mu{fill:var(--mu)}.su{fill:var(--su)}.ac{fill:var(--ac)}.am{fill:var(--am)}.onac{fill:var(--onac)}
.sa{stroke:var(--ac);fill:none}.sm{stroke:var(--am);fill:none}.fb{fill:var(--bull)}.fr{fill:var(--bear)}.fj{fill:var(--am)}.fc{fill:var(--chk)}
.sk{fill:var(--bd)}
</style>"""


def _svg(width: int, height: int, body: str, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="{FONT}">\n'
            f'{STYLE}\n<defs>{defs}</defs>\n{body}\n</svg>\n')


def _text(x, y, label, cls, size, weight=400, anchor="start", spacing=None, mono=False):
    extra = f' letter-spacing="{spacing}"' if spacing is not None else ""
    fam = f' font-family="{MONO}"' if mono else ""
    return f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{extra}{fam}>{rc.escape(label)}</text>'


def _mark(x, y, size):
    """The brand mark: two linked blocks of the chain running into a receipt slip."""
    k = size / 26
    return (f'<g transform="translate({x} {y}) scale({k:.3f})" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round">'
            '<rect class="sa" x="2.5" y="10" width="6.5" height="6.5" rx="1.7"/><rect class="sa" x="8.5" y="5" width="6.5" height="6.5" rx="1.7"/>'
            '<path class="sm" d="M17.5 3.5h6v18.5l-1.5-1.1-1.5 1.1-1.5-1.1-1.5 1.1z"/></g>')


def _chip(x, y, label):
    w = rc.text_width(label, 12) + 24
    return (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="26" rx="13" class="pn"/><rect x="{x + .5:.1f}" y="{y + .5}" width="{w - 1:.1f}" height="25" rx="12.5" class="bd"/>'
            + _text(f"{x + w / 2:.1f}", y + 17.5, label, "mu", 12, 500, "middle")), w


def _slip(x, y):
    """A receipt slip: one row per role, each with its color and the lines a receipt carries. No figure on it, it only shows the idea."""
    w, h = 196, 190
    rows = [("bull", "fb"), ("bear", "fr"), ("judge", "fj"), ("checker", "fc")]
    step = w / 14
    zig = "".join(f" l-{step:.2f} {'-7' if i % 2 == 0 else '7'}" for i in range(14))
    outline = f"M{x} {y} h{w} v{h}{zig} z"
    out = [f'<path d="{outline}" class="pn"/>', f'<path d="{outline}" class="bd" stroke-width="1.2"/>',
           _text(x + 18, y + 30, "receipt", "mu", 12, 500, mono=True)]
    for i, (role, cls) in enumerate(rows):
        ry = y + 52 + i * 31
        out.append(f'<circle cx="{x + 22}" cy="{ry}" r="4.5" class="{cls}"/>' + _text(x + 34, ry + 4, role, "tx", 11.5, 500, mono=True)
                   + f'<rect x="{x + 100}" y="{ry - 6}" width="{[58, 42, 66, 50][i]}" height="4" rx="2" class="sk"/><rect x="{x + 100}" y="{ry + 2}" width="{[40, 54, 30, 44][i]}" height="4" rx="2" class="sk"/>')
    out.append(f'<line x1="{x + 18}" y1="{y + 172 - 16}" x2="{x + w - 18}" y2="{y + 172 - 16}" class="ln" stroke-dasharray="3 4"/>')
    out.append(f'<rect x="{x + 18}" y="{y + 160}" width="{w - 36}" height="5" rx="2.5" class="sk"/>')
    return "".join(out)


def header_svg() -> str:
    """The cover: brand, a two-line promise, what it is in one breath, four chips and a receipt slip, on a dotted field with two soft glows."""
    width, height = 860, 300
    chips, x = [], 40
    for label in ["Open-weight only", "A receipt per call", "Hash-chained ledger", "Paper trades only"]:
        svg, w = _chip(x, 238, label)
        chips.append(svg)
        x += w + 8
    defs = ('<radialGradient id="g1" cx="0" cy="0" r="1" gradientUnits="objectBoundingBox"><stop offset="0" style="stop-color:var(--glow);stop-opacity:var(--glowo)"/><stop offset="1" style="stop-color:var(--glow);stop-opacity:0"/></radialGradient>'
            '<radialGradient id="g2" cx="1" cy="1" r="1" gradientUnits="objectBoundingBox"><stop offset="0" style="stop-color:var(--am);stop-opacity:.10"/><stop offset="1" style="stop-color:var(--am);stop-opacity:0"/></radialGradient>'
            '<linearGradient id="edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" style="stop-color:var(--ac)"/><stop offset="1" style="stop-color:var(--am)"/></linearGradient>'
            '<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" style="fill:var(--bd)"/></pattern>'
            f'<clipPath id="clip"><rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18"/></clipPath>')
    body = f"""<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18" class="bg"/>
<g clip-path="url(#clip)"><rect width="{width}" height="{height}" fill="url(#dots)" opacity=".55"/><ellipse cx="140" cy="10" rx="460" ry="280" fill="url(#g1)"/><ellipse cx="780" cy="300" rx="380" ry="220" fill="url(#g2)"/><rect x="0" y="0" width="{width}" height="4" fill="url(#edge)"/></g>
<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18" class="bd" stroke-width="1.5"/>
{_mark(40, 34, 30)}
{_text(82, 56, PRODUCT_NAME, "mu", 15, 600, spacing="-0.1")}
{_text(40, 122, "Every model call,", "tx", 46, 700, spacing="-1.6")}
{_text(40, 172, "on the record.", "ac", 46, 700, spacing="-1.6")}
{_text(40, 204, "A paper-trading agent on the Otari gateway and open-weight models.", "mu", 14.5)}
{_text(40, 224, "Each call leaves a receipt anyone can re-verify offline.", "mu", 14.5)}
{''.join(chips)}
{_slip(618, 46)}"""
    return _svg(width, height, body, defs)


# Line icons on a 24 grid (stroke only, drawn with currentColor), used by the section markers and the feature cards.
LINE_ICONS = {
    "layers": '<path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
    "route": '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H15a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h6.5"/>',
    "gauge": '<path d="M4 18a9 9 0 1 1 16 0"/><path d="m12 14 4-5"/><circle cx="12" cy="14" r="1.3"/>',
    "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14.2c2.4.3 4 2.2 4 4.8"/>',
    "shield": '<path d="M12 3 4.5 6v5.5c0 4.4 3.1 7.8 7.5 9.5 4.4-1.7 7.5-5.1 7.5-9.5V6L12 3Z"/>',
    "shield-check": '<path d="M12 3 4.5 6v5.5c0 4.4 3.1 7.8 7.5 9.5 4.4-1.7 7.5-5.1 7.5-9.5V6L12 3Z"/><path d="m9 12 2 2 4-4"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.7 3.9 5.7 3.9 9S14.6 18.3 12 21c-2.6-2.7-3.9-5.7-3.9-9S9.4 5.7 12 3Z"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7.5 9.5 3 2.5-3 2.5"/><path d="M13 15h3.5"/>',
    "plug": '<path d="M9 3v4M15 3v4"/><path d="M6 7h12v4a6 6 0 0 1-12 0V7Z"/><path d="M12 17v4"/>',
    "workflow": '<rect x="3" y="3" width="8" height="8" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect x="13" y="13" width="8" height="8" rx="2"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
    "rocket": '<path d="M5 19c.5-3 2-4.5 4-5"/><path d="M14 4c3.5.3 6 2.8 6.3 6.3-1.4 3.1-4.2 5.9-8.3 7.7l-5.5-5.5C8.3 8.4 11 5.6 14 4Z"/><circle cx="15" cy="9" r="1.4"/><path d="M9 14l-3 .5L4.5 17M10 15l-.5 3L7 19.5"/>',
    "file-check": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/><path d="m9.5 14 2 2 3.5-4"/>',
    "bars": '<path d="M5 20v-7M12 20V5M19 20V9"/>',
    "message": '<path d="M4 5h16v11H9l-5 4V5Z"/><path d="M8 9h8M8 12.5h5"/>',
    "book": '<path d="M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Z"/><path d="M5 17a3 3 0 0 1 3-3h10"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
}
# README section -> icon. Each one is a small tile drawn above its heading, so the heading text (and its link anchor) stays plain.
SECTION_ICONS = {
    "how": "workflow", "models": "cpu", "score": "target", "start": "rocket", "verify": "shield-check", "proofs": "file-check",
    "board": "bars", "learn": "message", "details": "book", "about": "info", "why": "layers",
}
# audit row number -> (icon, short name for the card)
FEATURE_CARDS = {1: ("layers", "Models in roles"), 2: ("route", "Routing, fallback"), 3: ("gauge", "Budgets"), 4: ("users", "Budget per user"),
                 5: ("shield", "Guardrails"), 6: ("globe", "Search and fetch"), 7: ("terminal", "Code execution"), 8: ("plug", "MCP")}


def _line_icon(name: str, x: float, y: float, size: float, color: str = "var(--ac)") -> str:
    k = size / 24
    return (f'<g transform="translate({x} {y}) scale({k:.3f})" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{LINE_ICONS[name]}</g>')


def section_icon_svg(icon: str) -> str:
    """A 44 px tile: a tinted rounded square with the line icon in the accent color."""
    body = ('<rect x="1" y="1" width="42" height="42" rx="12" class="pn"/><rect x="1" y="1" width="42" height="42" rx="12" class="bd" stroke-width="1.2"/>'
            '<rect x="1" y="1" width="42" height="42" rx="12" fill="var(--ac)" fill-opacity=".08"/>' + _line_icon(icon, 10, 10, 24))
    return _svg(44, 44, body)


def features_svg(ledger: list[dict], evidence: list[dict] | None = None) -> str:
    """The eight Otari resources as cards: icon, name, status and the dossier that proves it. Status and dossier come from the audit of the ledger."""
    rows = {r["n"]: r for r in otari_report.audit(ledger, evidence)}
    width, margin, gap, cw, ch = 860, 16, 14, 193, 96
    top = 54
    height = top + 2 * ch + gap + margin
    parts = [f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bg"/><rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bd" stroke-width="1.5"/>',
             _text(margin + 8, 33, "Eight Otari resources, each one checked against the ledger", "tx", 14, 650, spacing="-0.1")]
    for i, n in enumerate(range(1, 9)):
        r = rows[n]
        icon, name = FEATURE_CARDS[n]
        x, y = margin + (i % 4) * (cw + gap), top + (i // 4) * (ch + gap)
        ok = r["status"] == "PASS"
        tone = "var(--ac)" if ok else "var(--am)"
        label = "PASS" if ok else "NOT PROVEN"
        pill_w = rc.text_width(label, 10.5, bold=True) + 20
        sub = f"dossier {r['dossier']}" if r["dossier"] else "no dossier"
        parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="12" class="pn"/><rect x="{x + .5}" y="{y + .5}" width="{cw - 1}" height="{ch - 1}" rx="11.5" class="bd" stroke-width="1"/>')
        parts.append(f'<rect x="{x + 14}" y="{y + 14}" width="36" height="36" rx="10" fill="{tone}" fill-opacity=".12"/>' + _line_icon(icon, x + 20, y + 20, 24, tone))
        parts.append(_text(x + 60, y + 29, name, "tx", 13, 650, spacing="-0.1"))
        parts.append(_text(x + 60, y + 46, sub, "su", 11, 400, mono=True))
        parts.append(f'<rect x="{x + 14}" y="{y + 62}" width="{pill_w:.1f}" height="22" rx="11" fill="{tone}" fill-opacity=".14"/>'
                     + _text(f"{x + 14 + pill_w / 2:.1f}", y + 77, label, "ac" if ok else "am", 10.5, 700, "middle", spacing="0.4"))
        if ok:
            parts.append(_text(x + 14 + pill_w + 8, y + 77, "sealed" if r["source"] == "sealed" else "drill only", "su", 11))
    return _svg(width, height, "\n".join(parts))


# One cycle, step by step. Each step names what it does, and a chip says whether it goes through Otari (and which feature) or runs in our own code.
FLOW_STEPS = [
    ("1", "Evidence", ["Market data and news:", "Binance, OKX, GDELT, RSS,", "Telegram previews, frozen."], [("otari", "Web search and fetch")]),
    ("2", "Bull and bear", ["Two analysts on two", "open-weight models argue", "from the same evidence."], [("otari", "Routing, fallback"), ("otari", "Budget per user")]),
    ("3", "Citation checker", ["A third model re-reads", "each claim against the", "evidence it cites."], [("otari", "Routing, fallback")]),
    ("4", "Judge", ["Weighs only the claims", "that were validated.", "Guardrails: NOT PROVEN."], [("otari", "Routing policy"), ("otari", "Budgets")]),
    ("5", "Rules engine", ["Deterministic code turns", "the judge's lean into a", "paper-trade verdict."], [("local", "Our code, no model")]),
    ("6", "Re-derive and check", ["The verdict is re-run in a", "sandbox, and prices are", "read through MCP."], [("otari", "Code execution"), ("otari", "MCP")]),
    ("7", "Sealed dossier", ["Evidence, verdict and a", "receipt per call, hashed", "into the ledger chain."], [("local", "Hash chain, replayable")]),
    ("8", "Outside witnesses", ["OpenTimestamps each cycle.", "Sigstore head lookup.", "Sepolia: earlier era only."], [("local", "Third parties")]),
]


def flow_svg() -> str:
    """The cycle as two rows of four steps. Static: the words come from FLOW_STEPS, there is no figure on it."""
    width, bw, bh, gap, left = 860, 182, 178, 30, 21
    rows_y = (62, 290)
    parts = [f'<rect x="1" y="1" width="{width - 2}" height="514" rx="16" class="bg"/><rect x="1" y="1" width="{width - 2}" height="514" rx="16" class="bd" stroke-width="1.5"/>',
             _mark(22, 18, 24), _text(54, 36, "One cycle, end to end", "tx", 15, 650, spacing="-0.1"),
             _text(width - 22, 36, "Every model call and tool call below leaves a receipt", "su", 12, 400, "end")]
    centers = []
    for i, (num, title, lines, chips) in enumerate(FLOW_STEPS):
        col, row = i % 4, i // 4
        x, y = left + col * (bw + gap), rows_y[row]
        centers.append((x, y))
        parts.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="12" class="pn"/><rect x="{x + .5}" y="{y + .5}" width="{bw - 1}" height="{bh - 1}" rx="11.5" class="bd" stroke-width="1.2"/>')
        parts.append(f'<circle cx="{x + 20}" cy="{y + 24}" r="11" class="ac"/>' + _text(x + 20, y + 28.5, num, "onac", 12, 700, "middle"))
        parts.append(_text(x + 38, y + 29, title, "tx", 14, 650))
        for k, line in enumerate(lines):
            parts.append(_text(x + 16, y + 58 + k * 17, line, "mu", 11.5))
        cy = y + bh - 16 - 27 * (len(chips) - 1)
        for kind, label in chips:
            w = rc.text_width(label, 11, bold=True) + 18
            if kind == "otari":
                parts.append(f'<rect x="{x + 14}" y="{cy - 15}" width="{w:.1f}" height="21" rx="10.5" class="ac" style="fill-opacity:.16"/><rect x="{x + 14.5}" y="{cy - 14.5}" width="{w - 1:.1f}" height="20" rx="10" class="sa" stroke-width="1"/>'
                             + _text(f"{x + 14 + w / 2:.1f}", cy, label, "ac", 11, 600, "middle"))
            else:
                parts.append(f'<rect x="{x + 14}" y="{cy - 15}" width="{w:.1f}" height="21" rx="10.5" class="bd" stroke-width="1" stroke-dasharray="3 3"/>'
                             + _text(f"{x + 14 + w / 2:.1f}", cy, label, "su", 11, 500, "middle"))
            cy += 27
    # arrows: along each row, and one elbow from step 4 down and back to step 5
    for row in range(2):
        y = rows_y[row] + bh / 2
        for col in range(3):
            x1 = left + col * (bw + gap) + bw
            parts.append(f'<path d="M{x1 + 3} {y} H{x1 + gap - 5}" class="sa" stroke-width="1.6"/><path d="M{x1 + gap - 9} {y - 4} L{x1 + gap - 4} {y} L{x1 + gap - 9} {y + 4}" class="sa" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')
    x4, y4 = centers[3][0] + bw / 2, rows_y[0] + bh
    x5, y5 = centers[4][0] + bw / 2, rows_y[1]
    mid = (y4 + y5) / 2
    parts.append(f'<path d="M{x4} {y4 + 2} V{mid} H{x5} V{y5 - 7}" class="sa" stroke-width="1.6" stroke-linejoin="round"/><path d="M{x5 - 4} {y5 - 9} L{x5} {y5 - 4} L{x5 + 4} {y5 - 9}" class="sa" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>')
    ly = 496
    parts.append(f'<rect x="22" y="{ly - 13}" width="16" height="16" rx="8" class="ac" style="fill-opacity:.16"/><rect x="22.5" y="{ly - 12.5}" width="15" height="15" rx="7.5" class="sa" stroke-width="1"/>'
                 + _text(46, ly, "Goes through the Otari gateway, and the feature it exercises", "mu", 11.5)
                 + f'<rect x="438" y="{ly - 13}" width="16" height="16" rx="8" class="bd" stroke-width="1" stroke-dasharray="3 3"/>'
                 + _text(462, ly, "Runs in our own code or outside parties, not through Otari", "mu", 11.5))
    return _svg(width, 516, "\n".join(parts))


def figures(ledger: list[dict], evidence: list[dict] | None = None) -> list[tuple[str, str, str]]:
    """The five figures of the card, each counted from the ledger (and the drill file for the audit)."""
    dossiers = otari_report.era(ledger)
    calls = sum(len(otari_report.calls_of(d)) for d in dossiers)
    board = otari_report.board(ledger)
    answering = [r for r in board if r["ok"] and not r["model"].startswith("(no answer)")]
    backup = sum(r["fallbacks"] for r in board)
    rows = otari_report.audit(ledger, evidence)
    proven = sum(1 for r in rows if r["status"] == "PASS")
    first, last = dossiers[0]["seq"], dossiers[-1]["seq"]
    return [
        (str(len(dossiers)), "Otari-era dossiers", f"{first} to {last}"),
        (str(calls), "sealed receipts", "one per model or tool call"),
        (str(len(answering)), "provider:model routes", "answered, all open weight"),
        (f"{proven}/{len(rows)}", "resources proven", (" and ".join(r["resource"].lower() for r in rows if r["status"] != "PASS") + " not proven") if proven < len(rows) else "all from sealed receipts or drills"),
        (str(backup), "backup-route answers", "canary plus real traffic"),
    ]


def figures_svg(ledger: list[dict], evidence: list[dict] | None = None) -> str:
    """Five figures side by side: the number large, its label under it, a short qualifier. Counted from the ledger, never typed."""
    items = figures(ledger, evidence)
    head = otari_report.era(ledger)[-1]["seq"]
    width, height = 860, 148
    col = (width - 32) / len(items)
    parts = [f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bg"/><rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bd" stroke-width="1.5"/>']
    for i, (value, label, sub) in enumerate(items):
        cx = 16 + col * i + col / 2
        if i:
            parts.append(f'<line x1="{16 + col * i:.1f}" y1="26" x2="{16 + col * i:.1f}" y2="98" class="ln"/>')
        parts.append(_text(f"{cx:.1f}", 66, value, "tx", 34, 650, "middle", spacing="-1"))
        parts.append(_text(f"{cx:.1f}", 86, label, "mu", 12.5, 500, "middle"))
        parts.append(_text(f"{cx:.1f}", 103, sub, "am" if "not proven" in sub else "su", 11, 500 if "not proven" in sub else 400, "middle"))
    parts.append(f'<line x1="16" y1="116" x2="{width - 16}" y2="116" class="ln"/>')
    parts.append(_text(width / 2, 135, f"As of dossier {head}. Every figure is counted from ledger/ledger.jsonl by scripts/otari_cards.py.", "su", 11, 400, "middle"))
    return _svg(width, height, "\n".join(parts))


def button_svg(label: str, icon: str, style: str) -> str:
    width = int(rc.text_width(label, 14, bold=True) + 58)
    primary = style == "primary"
    box = (f'<rect x=".75" y=".75" width="{width - 1.5}" height="38.5" rx="9" class="ac"/>' if primary else
           f'<rect x=".75" y=".75" width="{width - 1.5}" height="38.5" rx="9" class="pn"/><rect x=".75" y=".75" width="{width - 1.5}" height="38.5" rx="9" class="bd" stroke-width="1.5"/>')
    glyph = f'<g transform="translate(16 12)" style="color:var({"--onac" if primary else "--ac"})">{rc.ICONS[icon]}</g>'
    return _svg(width, 40, box + glyph + _text(40, 25, label, "onac" if primary else "tx", 14, 600))


def figures_payload(ledger: list[dict], evidence: list[dict] | None = None) -> dict:
    """What web/index.html cannot count in the browser: the audit of the eight resources, counted by otari_report from the ledger and the drill file."""
    rows = otari_report.audit(ledger, evidence)
    return {
        "asof": otari_report.era(ledger)[-1]["seq"],
        "source": "scripts/otari_cards.py, from ledger/ledger.jsonl and docs/evidence/otari-drills.jsonl",
        "figures": [{"value": v, "label": label, "sub": sub} for v, label, sub in figures(ledger, evidence)],
        "proven": sum(1 for r in rows if r["status"] == "PASS"),
        "total": len(rows),
        "resources": [{"n": r["n"], "resource": r["resource"], "status": r["status"], "source": r["source"], "dossier": r["dossier"]} for r in rows],
    }


def load() -> tuple[list[dict], list[dict]]:
    ledger = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return ledger, otari_report.read_evidence()


def write_static() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {HEADER: header_svg(), FLOW: flow_svg()}
    for slug, label, icon, style in BUTTONS:
        files[OUT / f"otari-button-{slug}.svg"] = button_svg(label, icon, style)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for slug, icon in SECTION_ICONS.items():
        files[ICON_DIR / f"{slug}.svg"] = section_icon_svg(icon)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    return list(files)


def refresh_figures(check: bool = False) -> bool:
    """Redraws the figures card and its JSON twin for the site; returns True when both were already current."""
    ledger, evidence = load()
    wanted = {FIGURES: figures_svg(ledger, evidence), FEATURES: features_svg(ledger, evidence), FIGURES_JSON: json.dumps(figures_payload(ledger, evidence), indent=2) + "\n"}
    current = True
    for path, text in wanted.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            current = False
            if not check:
                path.write_text(text, encoding="utf-8")
    return current


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        ok = refresh_figures(check=True)
        print("figures card is current" if ok else "figures card is out of date: run scripts/otari_cards.py")
        sys.exit(0 if ok else 1)
    for path in write_static():
        print(f"wrote {path.relative_to(ROOT)}")
    refresh_figures()
    print(f"wrote {FIGURES.relative_to(ROOT)}, {FEATURES.relative_to(ROOT)} and {FIGURES_JSON.relative_to(ROOT)}")
