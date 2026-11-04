#!/usr/bin/env python3
"""Writes the shared header (navigation) and footer into every page of web/, so all pages carry the same shell.

  python3 scripts/site_shell.py           # rewrite the shell of every page
  python3 scripts/site_shell.py --check   # exit 1 if a page carries a stale or missing shell

A page keeps its shell between <!-- shell:header:start --> / <!-- shell:header:end --> and <!-- shell:footer:start --> /
<!-- shell:footer:end -->. A page that has no markers yet gets them once: its old <nav> is removed, the header is put on top, the
content is wrapped in <main id="content"> and the footer closes the page. No page logic is touched. Every link the shell writes is relative
to the page.
"""

import posixpath
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
REPO = "https://github.com/AndriyAmaro/tally-desk"
NAME, NAME_SUB = "Tally Desk", "on Otari"  # THE product name, in one place: the site shell and the README cards (scripts/otari_cards.py PRODUCT_NAME) both read it
PRIMARY = [("index.html", "Overview"), ("cycle.html", "Cycle"), ("verify.html", "Verify")]
AUDIT = (REPO + "/tree/otari#proofs-resource-by-resource", "Audit")
# Chain tools and views of the reference workload: they stay in place and read the same ledger.
MORE = [("dashboard.html", "Chain dashboard"), ("witnesses.html", "Witnesses"), ("ask.html", "Ask the record"), ("lab.html", "Lab"),
        ("narrative.html", "Market voices")]
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230d1017'/%3E"
           "%3Crect x='4' y='13' width='8' height='8' rx='2' fill='none' stroke='%232dd4bf' stroke-width='2.4'/%3E%3Crect x='11' y='7' width='8' height='8' rx='2' fill='none' stroke='%232dd4bf' stroke-width='2.4'/%3E"
           "%3Cpath d='M21 5h7v20l-1.75-1.3-1.75 1.3-1.75-1.3L21 25z' fill='none' stroke='%23fbbf24' stroke-width='2.4' stroke-linejoin='round'/%3E%3C/svg%3E")
MARK = ('<svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round">'
        '<rect class="a" x="2.5" y="10" width="6.5" height="6.5" rx="1.7"/><rect class="a" x="8.5" y="5" width="6.5" height="6.5" rx="1.7"/>'
        '<path class="b" d="M17.5 3.5h6v18.5l-1.5-1.1-1.5 1.1-1.5-1.1-1.5 1.1z"/></svg>')


def rel(target: str, page: str) -> str:
    """The link from `page` to `target`, both given relative to web/."""
    return posixpath.relpath(target, posixpath.dirname(page) or ".")


def menu(summary: str, items: list, page: str) -> str:
    def link(href, label):
        cur = ' aria-current="page"' if href == page else ""
        return f'<a href="{rel(href, page)}"{cur}>{escape(label)}</a>'
    has = " has-current" if any(h == page for h, _ in items) else ""
    return f'<details class="more{has}"><summary>{escape(summary)}</summary><ul>{"".join(f"<li>{link(h, l)}</li>" for h, l in items)}</ul></details>'


def header(page: str) -> str:
    def link(href, label):
        cur = ' aria-current="page"' if href == page else ""
        return f'<a href="{rel(href, page)}"{cur}>{escape(label)}</a>'
    primary = "".join(f"<li>{link(h, l)}</li>" for h, l in PRIMARY) + f'<li><a href="{AUDIT[0]}" rel="noopener">{AUDIT[1]}</a></li>'
    return (f'<!-- shell:header:start -->\n<a class="skip-link" href="#content">Skip to content</a>\n'
            f'<header class="site-header"><nav class="site-nav" aria-label="Primary">'
            f'<a class="brand" href="{rel("index.html", page)}"{" aria-current=\"page\"" if page == "index.html" else ""}>{MARK}<span>{escape(NAME)}</span><span class="brand-sub">{escape(NAME_SUB)}</span></a>'
            f'<ul class="nav-links">{primary}</ul>'
            f'{menu("More", MORE, page)}</nav></header>\n'
            f'<!-- shell:header:end -->')


def footer() -> str:
    return ('<!-- shell:footer:start -->\n<footer class="site-footer">'
            f'<p>{escape(NAME)} {escape(NAME_SUB)} decides on paper trades only. No real-money order is ever placed.</p>'
            f'<ul><li><a href="{REPO}/tree/otari" rel="noopener">Source (branch otari)</a></li>'
            f'<li><a href="{REPO}/blob/otari/docs/FEEDBACK_LOG.md" rel="noopener">Feedback to Otari</a></li>'
            f'<li><a href="{REPO}/tree/otari#proofs-resource-by-resource" rel="noopener">Proofs table</a></li>'
            f'<li><a href="{REPO}/blob/otari/LICENSE" rel="noopener">MIT license</a></li></ul>'
            '<p>Built by Andri Amaro with the help of an AI coding agent (Claude Code). The AI assistance is disclosed here on purpose.</p></footer>\n'
            '<!-- shell:footer:end -->')


HEAD_RE = re.compile(r"<!-- shell:header:start -->.*?<!-- shell:header:end -->", re.S)
FOOT_RE = re.compile(r"<!-- shell:footer:start -->.*?<!-- shell:footer:end -->", re.S)
ICON_RE = re.compile(r'<link rel="icon"[^>]*>')


def render(page: str, html: str) -> str:
    if not HEAD_RE.search(html):  # first time: a page without markers
        html = re.sub(r"<nav\b.*?</nav>\s*", "", html, count=1, flags=re.S)
        html = re.sub(r"<body[^>]*>", lambda m: m.group(0) + "\n<!-- shell:header:start --><!-- shell:header:end -->\n<main id=\"content\">", html, count=1)
        html = html.replace("</body>", "</main>\n<!-- shell:footer:start --><!-- shell:footer:end -->\n</body>", 1)
    html = HEAD_RE.sub(lambda _: header(page), html, count=1)
    html = FOOT_RE.sub(lambda _: footer(), html, count=1)
    icon = f'<link rel="icon" href="{FAVICON}">'
    html = ICON_RE.sub(lambda _: icon, html, count=1) if ICON_RE.search(html) else html.replace("</head>", icon + "\n</head>", 1)
    return html


def main(argv=None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    stale = []
    for path in sorted(WEB.glob("**/*.html")):
        name = path.relative_to(WEB).as_posix()
        old = path.read_text(encoding="utf-8")
        new = render(name, old)
        if new != old:
            stale.append(name)
            if not check:
                path.write_text(new, encoding="utf-8")
    if check:
        print("every page carries the current shell" if not stale else "stale shell: " + ", ".join(stale) + " (run scripts/site_shell.py)")
        return 1 if stale else 0
    print("rewrote the shell of: " + (", ".join(stale) or "nothing, already current"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
