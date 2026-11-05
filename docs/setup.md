# Setup and evidence

## Source and context

- Date: 2026-11-05. Toolkit: `https://github.com/soumatheusgomes/vibe-coding-toolkit`, revision `21b71b6` ("docs: add opt-in security audit and remediation workflow"), read from one shallow clone kept outside the repository. Every guide and template used comes from that revision.
- Procedure: prompt 12 of the toolkit (adopt, update and verify in an existing project), applied once, on a branch (`toolkit-setup`) created from `otari`.
- Product: Tally Desk, Python standard library plus plain JavaScript pages, no install step. See `PRODUCT.md`.
- Client: Claude Code with the session's own model and authentication; nothing was changed in the client, the machine or any account. No subagent was used: the work was small enough to do directly.

## Decisions and sources

- Keep the project's stack and commands. Nothing was migrated or installed; examples for Next.js, Drizzle and PostgreSQL in the toolkit do not apply.
- Keep `README.md` and `docs/architecture.md` as the canonical documents (the toolkit maps them to the same names). Add the missing ones: `PRODUCT.md`, `AGENTS.md`, `CLAUDE.md` (imports `AGENTS.md`), `docs/design.md`, `docs/onboarding.md`, `docs/quality.md`, `docs/setup.md`.
- Existing evidence and history documents (`docs/CASE_STUDY.md`, `docs/FEEDBACK_LOG.md`, `docs/HISTORY.md`, `docs/bring-your-own-agent.md`, `docs/evidence/`) stay where they are.
- No Git hook, push blocker or branch requirement was added; the repository already has one scheduled workflow, which this setup does not touch.
- Revisit when: a root package manifest appears (add lint and types), a UI framework is adopted, or the repository stops being a single Python package.

## Components

Every row of the toolkit catalog (`docs/tools/README.md`) was assessed. "Present" means the client showed it in the session; none of them was exercised by this setup.

| Component | Decision | Scope | State and evidence |
|---|---|---|---|
| Superpowers | Reuse the installed plugin (brainstorming and verification skills) | Client | Present; used for the design stage of this setup |
| Subagents (02) | Policy written in `AGENTS.md`; none dispatched | Project | Documented only |
| RTK (03) | Reuse; no change | Client | Output filtering was seen active in the session; not configured by this setup |
| Ponytail (04), Caveman (05) | Not adopted; reply style stays the owner's choice | Client | Not installed by this setup |
| ESLint/Biome (06) | Inapplicable | n/a | No root package manifest; Python and plain JS checked by tests |
| Graphify (07) | Pending | Project | Skill present in the client; no graph generated. A build needs exclusions (`data/`, `ledger/`, `node_modules/`, `services/guardrail/.wrangler/`) and an authorized extraction mode |
| Obsidian memory (08) | Inapplicable | n/a | No external vault is needed |
| Claude memory (09) | Reuse the client's memory plus versioned docs | Client | Client memory is local and not versioned; durable decisions live in `docs/` and `AGENTS.md` |
| Hooks (10) | None added | n/a | Not needed; the existing checks are explicit commands |
| agent-browser (11), Chrome DevTools MCP (14) | Pending | Client | A browser exists in the client; page rendering is checked by `scripts/check_pages.sh` (Chromium), not run here |
| Context7 (12) | Pending | Client | Not observed in the session; the project uses no library with a moving API (standard library only) |
| Anthropic skills (13) | Reuse what is present; nothing added | Client | Not needed |
| Codex (15) | Inapplicable | n/a | Claude Code is the client; `AGENTS.md` is readable by Codex as well |
| Vercel skills (16), Next.js loop and bundle (17), shadcn (21) | Inapplicable | n/a | No Next.js, React or shadcn |
| PostgreSQL (18) | Inapplicable | n/a | No database |
| GitHub CI repair (19) | Reuse on demand | Client | The workflow is not edited by agents; a failed cycle is diagnosed in the code |
| Security review (20, prompt 13) | Adopted as a reusable, read-only procedure | Project docs | Invocation recorded in `docs/quality.md`; not yet run |
| Sentry MCP (22) | Inapplicable | n/a | No Sentry service |
| Harness2 (23, prompt 15) | Native method recorded; no candidate comparison | Project docs | No observed failure to compare; see below |

## Documentation created

`PRODUCT.md`, `AGENTS.md`, `CLAUDE.md`, `docs/design.md`, `docs/onboarding.md`, `docs/quality.md` and this file are new and written from the repository as observed. `README.md` and `docs/architecture.md` are unchanged and canonical. No document describes a planned capability as implemented.

## Checks

| Item | State | Evidence |
|---|---|---|
| Python tests, before this setup | Validated | OK on commit `f9a59b1` (2026-11-05) |
| Browser-code tests, before this setup | Validated | 164 of 164 passing on the same commit |
| `verify` and `replay` | Validated | Chain intact, 818 dossiers; replay matches |
| Tests after adding the new documents | Validated | Python suite OK, browser-code tests 164 of 164, `verify` and `replay` pass, on the branch with the new files (2026-11-05) |
| Page rendering (`scripts/check_pages.sh`) | Not run | Needs Chromium |
| Security review (prompt 13) | Not run | Adoption only |
| Link check of the new documents | Validated by reading | Relative paths checked against the tree |

No resource was left running. The shallow clone of the toolkit lives in the session's scratch directory and is not part of the repository.

## Memory and harness evidence

- Memory: the client's native memory is local to the machine and not versioned. The versioned sources are `AGENTS.md` (rules and commands), `PRODUCT.md` (scope) and `docs/` (decisions). A fact in client memory that conflicts with the code is rechecked against the code and retired. Retrieval in a clean context was not observed.
- Harness: the native adaptation of the toolkit's harness method is the set of explicit commands in `AGENTS.md` and `docs/quality.md` plus the reusable security-review prompt. The toolkit's disposable behavioral cases (prompt 14) were not run: this setup did not observe a failure to compare, and a passing case would not show cross-client reliability.

## Pending work and next delivery

- Owner decisions: whether to merge `toolkit-setup` into `otari`; whether to install Graphify, Context7 and a browser MCP on the machine (each is a machine-level change).
- Next useful checks: run `bash scripts/check_pages.sh` where Chromium exists; run the security-review prompt in repository mode and keep the report private; run the harness cases of prompt 14 after the next change to `AGENTS.md`.
- Repeat this setup by re-running prompt 12 against the then-current toolkit revision; it preserves the files above and revises their content.
