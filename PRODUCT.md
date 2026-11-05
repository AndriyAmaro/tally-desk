# Product

## What it is

Tally Desk is an autonomous agent that decides on paper trades and can answer for every model call it makes. The trading agent is the reference workload; the product is the receipt layer under it: each call through the Otari gateway is captured with the gateway's own response headers and usage record, sealed into a hash-chained ledger of canonical-JSON dossiers, and re-derivable offline.

## Who it is for

- A reader or judge who wants to check a claim about what ran, without trusting the operator: `python3 -m verdict_desk verify` and `replay`, or the verifier page.
- A developer who wants to put the same receipt layer under another agent (`docs/bring-your-own-agent.md`).

## Scope today

- Roles: bull, bear, judge, citation checker and narrator, plus tool roles (search, fetch, code execution, MCP prices), each with its own model chain and end user budget.
- A closure gate that refuses a dossier it cannot stand behind and records the refusal.
- A source-drift seal, a seal API over receipts, a tamper-test page, an audit of the eight Otari resources, and an hourly GitHub Actions cycle.
- A guardrail service of its own (`services/guardrail/`) running in monitor mode.

## Not in scope

- Real money: every trade is on paper.
- Investment advice. The verdicts are a demonstration of a verifiable decision record, not a recommendation.
- Any LLM path outside Otari.

## Constraints

- Python standard library only; no install step.
- No secret in the repository; keys by environment variable.
- The ledger is append-only; its hashes and replays are the proof.
- Each live cycle spends Otari credit, so live cycles are run by the scheduled workflow or by the owner, not by tests.

## Where the detail lives

`README.md` (proofs and limits), `docs/architecture.md`, `docs/CASE_STUDY.md`, `docs/FEEDBACK_LOG.md`, `docs/HISTORY.md`.
