# Quality

## Commands

| Command | What it proves |
|---|---|
| `env -u OTARI_API_KEY python3 -m unittest discover -s tests -q` | the Python suite: the chain against edit, delete, swap, forge and truncate; receipts; the closure gate; fallbacks; the audit; the generated README blocks. No network, no key, no Otari spend |
| `node --test tests/*.test.js` | the browser code: verifier parity with Python, the cycle page, stats, the guardrail detector and worker. Use the glob; `node --test tests/` reports a false failure |
| `python3 -m verdict_desk verify` | every hash and link of `ledger/ledger.jsonl` |
| `python3 -m verdict_desk replay` | each recorded verdict re-derived from its frozen evidence |
| `python3 -m verdict_desk otari-audit` | the eight Otari resources, each marked proven from sealed receipts, from a drill, or not proven |
| `python3 scripts/site_shell.py --check` | every page of `web/` carries the current shared shell (also run by `tests/test_site.py`) |
| `bash scripts/check_pages.sh` | the pages render in headless Chromium (needs a Chromium binary; not part of the combined check) |

Combined check before a commit: the two test commands, then `verify` and `replay`. Run a single test module (`python3 -m unittest tests.test_role_fallbacks -q`) while iterating, and the full suites before committing. Python and JavaScript each take a few minutes on a busy machine; run them one after the other, not together.

## Baseline rules

- The ledger is append-only. A change that makes `verify` or `replay` fail is a bug in the change.
- A new behavior or a fixed bug comes with a test that fails without it. A test that only repeats the code is not worth adding.
- Fixtures that need a fixed ledger pin it (`PRE_GUARD` in `tests/test_otari_report.py` is the ledger before the guardrail appears); a test never assumes the newest dossier looks like an older one.
- The fixtures that run a cycle set `OTARI_GUARDRAILS=0` (`tests/test_otari_tools.py`, `tests/test_guardrail_app.py`) so the live config switch does not change their result.
- A gate is never loosened to make a check pass (closure checks, audit rows, the guardrail rules).
- No test spends on Otari: no live cycle, no drill, no workflow dispatch.
- Generated blocks (README and the numbers table of `docs/CASE_STUDY.md` between `<!-- otari:... -->`, cards in `docs/readme/`) come from `scripts/refresh_otari_readme.py` and `scripts/otari_cards.py`; a test fails when they are stale.
- Lint and types do not apply: there is no root package manifest and no compiler step. This is a choice, not a gap to fill with a tool the code does not need.

## Testing strategy

Behavior and failure paths first: a refused call, a model-side refusal against a budget refusal (`tests/test_role_fallbacks.py`), a tampered ledger, a drifted source, a guardrail that did not run. Real-ledger tests read the committed ledger, so they also guard the docs against numbers that went stale. Browser-only code is covered by `node --test` and, for rendering, by `scripts/check_pages.sh`, which was not run in the 2026-11-05 setup.

## Review after implementation or fixes

Select the scope from the actual diff and its callers: requirements and behavior; regressions and tests; security and data; configuration and integrations; the pages in `web/`. Record the exact revision range, the findings with location and a concrete failure, and what was not checked. Add a round only for new evidence or unresolved risk; fixes do not reset a requested count. A missing independent review is a stated limitation.

## Security review

Reusable and read-only by default: the Prompt block of `docs/prompts/13-security-review.md` in the vibe-coding-toolkit (`https://github.com/soumatheusgomes/vibe-coding-toolkit`, revision `21b71b6` on 2026-11-05). It supports a repository baseline and a precise diff, writes a private redacted report outside the repository and changes nothing. Invoke it by asking the agent to read the file at that revision and run its Prompt block in this repository, repository mode, or diff mode with an explicit base and head. Keep the report out of Git: findings are published only by the owner. Not yet run on this repository; a review with no findings is not a security guarantee.

Project-specific trust boundaries to trace: the Otari key and the guardrail token (environment and Worker secret only), model output reaching a page (`web/` renders ledger text), the guardrail Worker's input handling, and the hourly workflow's use of the repository secret.

## Dependencies and delivery

There are no third-party Python dependencies. `services/guardrail/package.json` belongs to the Worker alone. The hourly cycle commits the ledger from CI; delivery of the site and any publication are the owner's decision.
