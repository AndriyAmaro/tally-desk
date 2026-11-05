# Onboarding

## What you need

- Python 3 (the standard library is enough; observed with 3.14). Nothing to install.
- Node, only to run the browser-code tests (observed with 24; no version is pinned).
- Chromium, only for `scripts/check_pages.sh`.
- An Otari key (`OTARI_API_KEY`), only to run a live cycle, a drill or `otari-audit --reconcile`. Reading, verifying and testing need no key. Never paste a key into a file or a chat; export it in your own shell.

## First ten minutes, no key

```bash
python3 -m verdict_desk verify          # recomputes every hash and link of the chain
python3 -m verdict_desk replay          # re-derives each verdict from its frozen evidence
python3 -m verdict_desk otari-audit     # the eight Otari resources and how each is proven
env -u OTARI_API_KEY python3 -m unittest discover -s tests -q
node --test tests/*.test.js
python3 -m http.server                  # then open http://localhost:8000/web/cycle.html
```

## Where things are

| Path | What it holds |
|---|---|
| `verdict_desk/` | the Python package: dossier and ledger, the Otari adapter (`llm.py`), receipts and seal, closure gate, audit |
| `ledger/` | the append-only hash chain, closure refusals, witnesses |
| `web/` | the static pages and the browser verifier |
| `config/` | the Otari roles, fallback chains, budgets and guardrail switch (`otari.json`) |
| `services/guardrail/` | the guardrail Cloudflare Worker (its tests are `tests/guardrail_*.test.js` and `tests/test_guardrail_*.py`) |
| `tests/` | Python and JavaScript tests; they never spend on Otari |
| `scripts/` | README block and card generators, the page smoke check |
| `docs/` | architecture, case study, feedback log, history, evidence |

## Rules that save time

- The tests read the live ledger. After a new dossier lands, the fixtures that need a fixed ledger pin it; if a count in a doc changes, run `scripts/refresh_otari_readme.py` and `scripts/otari_cards.py`.
- Do not run a live cycle to try a change. The scheduled workflow does that, and each cycle spends credit.
- The ledger is append-only; if `verify` fails, something was edited.

More: `AGENTS.md` (rules for an agent or a contributor), `docs/quality.md` (checks), `docs/architecture.md`.
