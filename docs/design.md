# Design

Scope: the static pages in `web/` (home, cycle, verify, dashboard, lab, narrative, witnesses, ask). There is no application UI beyond them.

## Principles

- Every page renders from the files in the repository (`web/` and `ledger/`), with no server and no third-party request. Fonts are served from `web/fonts` (SIL Open Font License) and the system fallbacks keep a page readable offline.
- A page shows what the ledger says and lets the reader recompute it; it never states a number the page cannot trace to a dossier or a receipt.
- Plain language first, the technical term beside it. The verifier explains each failure in words.

## Tokens and theme

`web/theme.css` is the single source, in three layers: primitives (`--p-*`), semantic tokens (`--color-*`, `--text-*`, `--space-*`, `--radius-*`, `--elev-*`, switched per theme) and components built only from semantic tokens. Dark is the default; the light theme follows `prefers-color-scheme`. Older pages are restyled through the legacy aliases at the end of the token block. A component uses semantic tokens, never a primitive or a raw color.

## Accessibility and checks

- Statuses carry a word as well as a color (`PASS`, `NOT PROVEN`, `refused`) in the audit and the closure views. A full keyboard and contrast review of every page has not been done; `theme.css` defines one `:focus-visible` rule, and that is all this document claims.
- `scripts/check_pages.sh` renders each page in headless Chromium and asserts on the DOM, because a broken page still answers HTTP 200. It needs a Chromium binary and was not part of the 2026-11-05 setup run.
- The shared header and footer of every page are written by `scripts/site_shell.py`; `python3 scripts/site_shell.py --check` fails on a stale page, and `tests/test_site.py` runs it.
