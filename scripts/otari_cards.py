#!/usr/bin/env python3
"""Draws the README cards of the Otari entry as self-contained SVG (shapes and system fonts only).

  python3 scripts/otari_cards.py           # rewrite the header, the buttons and the figures card
  python3 scripts/otari_cards.py --check   # exit 1 if the figures card does not match the ledger

The header and the buttons are static. The figures card is computed from ledger/ledger.jsonl and the drill
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
GREEN = "#34d399"
BUTTONS = [
    ("cycle", "Call-by-call view", "play", rc.TEAL),
    ("audit", "Proofs table", "check", GREEN),
    ("feedback", "Feedback to Otari", "chat", rc.AMBER),
]


def header_svg() -> str:
    width, height = 860, 230
    # A hash chain of four sealed blocks runs into a receipt slip: every call ends as a receipt in the chain.
    blocks = "".join(
        f'<rect x="{30 + i * 32}" y="{98 - (i % 2) * 14}" width="28" height="28" rx="6" fill="#0f1a1f" stroke="{rc.TEAL if i < 3 else rc.AMBER}" stroke-width="2.5"/>'
        for i in range(4))
    links = "".join(f'<line x1="{58 + i * 32}" y1="{112 - (i % 2) * 14}" x2="{62 + i * 32}" y2="{98 + ((i + 1) % 2) * 14 + 14}" stroke="{rc.MUTED}" stroke-width="2"/>' for i in range(3))
    slip = (f'<path d="M178 52 h52 v92 l-7.4 -6 l-6.5 6 l-6.5 -6 l-6.5 6 l-6.5 -6 l-6.5 6 l-6.5 -6 z" fill="#151a26" stroke="{rc.AMBER}" stroke-width="2.5" stroke-linejoin="round"/>'
            + "".join(f'<line x1="188" y1="{70 + k * 16}" x2="{220 - (k % 2) * 10}" y2="{70 + k * 16}" stroke="{rc.MUTED}" stroke-width="2.5" stroke-linecap="round"/>' for k in range(4)))
    emblem = f'<g transform="translate(-6 8)">{blocks}{links}<line x1="150" y1="106" x2="178" y2="106" stroke="{rc.AMBER}" stroke-width="2.5"/>{slip}</g>'
    chips, x = [], 262
    for label in ["Open-weight only", "A receipt per call", "Hash-chained ledger", "Paper trades only"]:
        svg, w = rc._chip(x, 146, label)
        chips.append(svg)
        x += w + 8
    body = f"""{emblem}
<text x="262" y="76" fill="{rc.INK}" font-size="38" font-weight="700" letter-spacing="-0.5">Verdict Desk on Otari</text>
<text x="262" y="104" fill="{rc.MUTED}" font-size="16">A paper-trading agent. Every model call goes through Otari.</text>
<text x="262" y="126" fill="{rc.MUTED}" font-size="16">Each call leaves a receipt that <tspan fill="{rc.INK}" font-weight="600">anyone can re-verify offline</tspan>.</text>
{''.join(chips)}
<text x="262" y="202" fill="{rc.FAINT}" font-size="11" letter-spacing="1.2">OTARI GATEWAY  ·  OPEN-WEIGHT MODELS  ·  BRANCH otari  ·  NO REAL-MONEY ORDERS</text>"""
    return rc._frame(width, height, body, [(120, 240, 240, rc.TEAL), (480, 250, 260, "#0ea5a0"), (780, 240, 200, rc.AMBER)])


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
        (str(len(answering)), "provider:model routes", "that answered, all open weight"),
        (f"{proven}/{len(rows)}", "resources proven", "guardrails not proven"),
        (str(backup), "backup-route answers", "canary plus real traffic"),
    ]


def figures_svg(ledger: list[dict], evidence: list[dict] | None = None) -> str:
    head = otari_report.era(ledger)[-1]["seq"]
    return rc.figures_svg(figures(ledger, evidence), f"As of dossier {head} · every figure counted from ledger/ledger.jsonl by scripts/otari_cards.py")


def load() -> tuple[list[dict], list[dict]]:
    ledger = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return ledger, otari_report.read_evidence()


def write_static() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {HEADER: header_svg()}
    for slug, label, icon, colour in BUTTONS:
        files[OUT / f"otari-button-{slug}.svg"] = rc.button_svg(label, icon, colour)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    return list(files)


def refresh_figures(check: bool = False) -> bool:
    """Redraws the figures card; returns True when it was already current."""
    ledger, evidence = load()
    new = figures_svg(ledger, evidence)
    current = FIGURES.read_text(encoding="utf-8") if FIGURES.exists() else None
    if not check and new != current:
        FIGURES.write_text(new, encoding="utf-8")
    return new == current


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        ok = refresh_figures(check=True)
        print("figures card is current" if ok else "figures card is out of date: run scripts/otari_cards.py")
        sys.exit(0 if ok else 1)
    for path in write_static():
        print(f"wrote {path.relative_to(ROOT)}")
    refresh_figures()
    print(f"wrote {FIGURES.relative_to(ROOT)}")
