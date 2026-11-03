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
# (slug, label, icon, style): the first button is the primary action, the others are quiet
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
    width, height = 860, 250
    chips, x = [], 40
    for label in ["Open-weight only", "A receipt per call", "Hash-chained ledger", "Paper trades only"]:
        svg, w = _chip(x, 188, label)
        chips.append(svg)
        x += w + 8
    defs = '<radialGradient id="glow" cx="0" cy="0" r="1" gradientUnits="objectBoundingBox"><stop offset="0" style="stop-color:var(--glow);stop-opacity:var(--glowo)"/><stop offset="1" style="stop-color:var(--glow);stop-opacity:0"/></radialGradient><clipPath id="clip"><rect x="1" y="1" width="858" height="248" rx="16"/></clipPath>'
    body = f"""<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bg"/>
<g clip-path="url(#clip)"><ellipse cx="120" cy="20" rx="420" ry="260" fill="url(#glow)"/></g>
<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="16" class="bd" stroke-width="1.5"/>
{_mark(38, 30, 28)}
{_text(76, 50, "Verdict Desk on Otari", "mu", 15, 600, spacing="-0.1")}
{_text(40, 112, "Every model call, on the record.", "tx", 38, 650, spacing="-1.2")}
{_text(40, 148, "A paper-trading agent. Every call goes through the Otari gateway,", "mu", 15)}
{_text(40, 169, "on open-weight models, and leaves a receipt anyone can re-verify offline.", "mu", 15)}
{''.join(chips)}
{_slip(628, 28)}"""
    return _svg(width, height, body, defs)


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
    files = {HEADER: header_svg()}
    for slug, label, icon, style in BUTTONS:
        files[OUT / f"otari-button-{slug}.svg"] = button_svg(label, icon, style)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    return list(files)


def refresh_figures(check: bool = False) -> bool:
    """Redraws the figures card and its JSON twin for the site; returns True when both were already current."""
    ledger, evidence = load()
    wanted = {FIGURES: figures_svg(ledger, evidence), FIGURES_JSON: json.dumps(figures_payload(ledger, evidence), indent=2) + "\n"}
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
    print(f"wrote {FIGURES.relative_to(ROOT)} and {FIGURES_JSON.relative_to(ROOT)}")
