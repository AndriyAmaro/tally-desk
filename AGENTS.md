# Tally Desk

A paper-trading agent whose every model call goes through the Otari gateway and leaves a receipt that anyone can re-verify offline.

## Product and stack

- Product and scope: `PRODUCT.md`. Structure and decisions: `docs/architecture.md`.
- Stack: Python 3 standard library only (package `verdict_desk`, no install step); browser code in plain JavaScript under `web/`; a Cloudflare Worker in `services/guardrail/`. Observed on 2026-11-05 with Python 3.14 and Node 24.
- Setup, tool coverage and evidence: `docs/setup.md`. Checks: `docs/quality.md`. First run: `docs/onboarding.md`.

## Canonical commands

- Install: none.
- Python tests (no network, no key): `env -u OTARI_API_KEY python3 -m unittest discover -s tests -q`
- Browser-code tests: `node --test tests/*.test.js` (always the glob; `node --test tests/` reports a false failure).
- Verify the ledger: `python3 -m verdict_desk verify && python3 -m verdict_desk replay`
- Audit the eight Otari resources: `python3 -m verdict_desk otari-audit`
- Regenerate the README blocks, the numbers table of `docs/CASE_STUDY.md` and the cards after a number changes (each live cycle does it): `python3 scripts/refresh_otari_readme.py` and `python3 scripts/otari_cards.py`
- Page smoke check (needs Chromium): `bash scripts/check_pages.sh`
- Lint, types, build: unavailable by design (no root package manifest, no compiler step; `services/guardrail/` has its own `package.json` for the Worker, and its tests are `tests/guardrail_*.test.js` and `tests/test_guardrail_*.py`).
- Verify foundation: the Python tests, the browser-code tests and `verify` plus `replay`, all green.

## Working agreements

- Read the code and this file before editing. Change only what the request needs; state assumptions that affect behavior.
- A fix needs a reproduction and a regression test when feasible. Do not weaken a test, a gate or a check to make it pass.
- The ledger (`ledger/ledger.jsonl`) is append-only and hash-chained. Never edit, reorder or rewrite a dossier; `verify` and `replay` must keep passing.
- Every model call goes through Otari (`verdict_desk/llm.py`). Do not add a direct provider path.
- No secret in code, commits or logs. Keys come from environment variables (`OTARI_API_KEY`) or the host's secret store; never print them.
- Do not spend Otari credit to try something: no live cycle (`decide-v7`), no drill, no workflow dispatch, unless the owner asks.
- Do not edit `.github/workflows/otari-cycle.yml`. A failed cycle is diagnosed in the code, not the workflow.
- Keep plans, notes and personal data out of the repository: it becomes public at submission time.
- Do not turn an assumption into a fact or a pending integration into a simulated result. Say what was not run.
- Publishing, messaging third parties, costs and destructive actions need the owner's explicit say-so.

## Documentation ownership

`PRODUCT.md` owns scope; `docs/architecture.md`, structure and decisions; `docs/design.md`, the web interface; `docs/onboarding.md`, preparation; `docs/quality.md`, checks; `docs/setup.md`, tool evidence. `README.md` and the numbers table of `docs/CASE_STUDY.md` carry generated blocks between `<!-- otari:... -->` markers: regenerate them with the scripts above, do not hand-edit them. Revise the responsible document together with the change.

## Delegation and review

Prefer direct work or a deterministic script. Delegate only for broad, independent reading or a genuinely parallel track; one writer per file, and the coordinator owns the ledger and every Git operation. Review a nontrivial change against the exact diff and its callers and tests, and record what was not checked.
