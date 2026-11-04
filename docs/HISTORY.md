# Lineage

Verdict Desk on Otari did not start from an empty folder. It began as a snapshot of an earlier project and it keeps that project's engine. This page says which parts came from where, so nobody has to guess.

## The earlier project (frozen)

The earlier project, [AndriyAmaro/verdict-desk](https://github.com/AndriyAmaro/verdict-desk), was written for a legal-tech hackathon and pitched as evidence a lawyer could file. This repository was imported from it at commit `c65bab9`, and the original stays exactly as it was. The pages written for that pitch, the printable exhibits, the self-hosted model gateway and the research tools stay in that frozen repository and are not carried here. That pitch is not the product described in this repository.

The first commit of the Otari era is [`2bdfe40`](https://github.com/AndriyAmaro/verdict-desk-otari/commit/2bdfe40cfdd124f9997dbb617f797870630bcf1b): every model call goes through the gateway, one open-weight model per role, one receipt per call.

## What carried over

| Part | Where it lives | What it does |
|---|---|---|
| Hash chain | `verdict_desk/dossier.py`, `verdict_desk/canonical.py`, `ledger/ledger.jsonl` | One append-only JSONL file. Each dossier holds the SHA-256 of the one before it, taken over canonical JSON that Python and the browser produce identically. |
| Replay | `dossier.replay` | Recomputes the features and the verdict from the evidence frozen in the dossier and compares them with what was recorded. |
| Citation validator | `verdict_desk/citations.py` | An analyst claim stands only if it cites an evidence id that exists and every number in it appears in the cited item. |
| Witnesses | `ledger/timestamps/` (OpenTimestamps), `ledger/rekor/` (Sigstore), `cre/` (Chainlink CRE anchor on Sepolia) | Copies of the chain head that the operator cannot quietly rewrite. The Sigstore bundles stop at dossier 791 and the Sepolia anchor belongs to the earlier era. OpenTimestamps stamps continue. |
| Tamper battery | `verdict_desk/battery.py` | Attacks a copy of the ledger (edit, delete, swap, forge, truncate, re-hash) and counts what the verifier misses. |
| Evidence pages | `web/verify.html`, `web/witnesses.html`, `web/dashboard.html` | In-browser verification of the chain. These stayed in place. The pages written for lawyers stay in the frozen repository. |

The ledger is a single chain. Dossiers 1 to 791 were recorded before Otari. The Otari era starts at dossier 792, whose `prev_hash` is the hash of 791, so the chain was continued and not restarted.

## What is new in the Otari era

- **A receipt per gateway call.** `llm.parse_receipt` seals an `otari-receipt/1` for every model or tool call: request id, the model asked for and the model that answered, provider, attempts, fallback, tokens, cost, how the call stopped, and the end user billed.
- **Nine roles on open-weight models.** Bull, bear, judge, citation checker and narrator, plus four tool roles (searcher, fetcher, sandbox, MCP), all in `config/otari.json`. Each role is billed to its own end user.
- **Seven of eight Otari features proven inside the cycle.** Several open-weight models in distinct roles, routing and fallback, budgets, budget per user, web search and fetch, code execution and MCP are read back from sealed receipts. Guardrails are attached, but Otari never reported a result, so that row is NOT PROVEN and not claimed.
- **Rules v7, a checker gate.** A claim the citation checker marks unsupported voids a judge lean that rests on it. The marks are frozen in the dossier and the replay recomputes the outcome.
- **A cloud runner.** The workflow `otari-cycle` runs `tools/otari_runner.py` from GitHub Actions and pushes each new dossier, with its OpenTimestamps stamp, to the `otari` branch.
- **A call-by-call page.** [`web/cycle.html`](../web/cycle.html) shows one cycle as a timeline of receipts.
- **An audit against Otari.** `python3 -m verdict_desk otari-audit --reconcile` proves each resource from the ledger and compares every receipt with Otari's own usage record by request id.
- **A feedback log.** [`docs/FEEDBACK_LOG.md`](FEEDBACK_LOG.md) records each snag met with the gateway, with steps to reproduce.

## Where to go next

[`architecture.md`](architecture.md) describes the code as it is now, including what a second agent would need to get receipts and a sealed, replayable dossier. The [README](../README.md) is the front door.
