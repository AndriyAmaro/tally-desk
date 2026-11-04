# Case study: Tally Desk on Otari

## What it does

Tally Desk is an autonomous agent that decides on paper trades for BTC, ETH and SOL, about once an hour, and keeps a receipt for every model call it makes. Four open-weight models argue each decision in separate roles: a bull analyst, a bear analyst, a citation checker and a judge. A rules engine written as plain code turns the result into a verdict. The record of each cycle is sealed into a hash-chained ledger that anyone can verify and replay offline. No real order is ever placed.

The question it answers is simple: when the agent loses money, which model said what, and can you prove it? In the Otari era the simulated account closed 11 trades, 2 won and 9 lost, for a realized result of minus 500 on a 10,000 start (`python3 -m verdict_desk portfolio`). The agent is not good at trading. It is good at being asked why.

## What Otari made possible

- One key and one API reached open models from five vendors, so each role could be a different model and a call could be traced to the one that answered.
- The response headers (request id, provider, served model, attempt count, fallback flag) became the core of each receipt, so the audit does not rest on the app's own logs.
- Per-role end users with a budget gave each role its own daily limit, and Otari's spend state for each end user feeds the router that decides when the judge may use the larger model.
- Otari ran the tools: web search and fetch, a code sandbox that re-derives the verdict, and an MCP price check. Each is a call with its own request id.

## Five numbers, all computed from `ledger/ledger.jsonl`

| Number | Value |
|---|---|
| Otari-era dossiers and receipts | 21 dossiers (792 to 812), 433 receipts |
| Calls answered | 423 of 433; the other 10 are 7 HTTP 409 after five tries and 3 HTTP 402 `pricing_required` |
| Answers served by a backup route after the first attempt did not succeed | 43 (15 failover canary calls, 28 real calls) |
| Cost of the receipts | USD 0.736186 in all; median cycle USD 0.038622 |
| Receipts that needed more than one client try | 139, of which 132 came back as a stored answer (`Otari-Idempotent-Replayed`) |

## What was hard

- **Long calls and the edge.** A slow completion is cut at about 30 seconds with an HTML 504 and no request id. The retry then meets a 409 "still in progress". Seven narrator batches never got past it. The app now polls on the same Idempotency-Key.
- **Guardrails could not be proven.** A guardrail is attached to the judge's policy, but the hosted gateway has no guardrails service, so Otari reported `valid: null` on every call (since dossier 805 with a reason: `unavailable, not_configured`). It is marked NOT PROVEN and not counted.
- **A pricing refusal in the middle of a run.** At 16:12 UTC on 2026-11-04 the gateway refused the bull, bear and narrator models with HTTP 402 `pricing_required`, after answering the same models at 15:33. They answered again by 17:10. Dossier 810 was sealed with no argument on either side, because it was recorded before the closure gate and the role fallback chains existed (both were added that same afternoon).

## What we added

- Each core role lists fallback models. A refusal for a model-side reason moves to the next entry, and the receipt records why. This is tested against a fake gateway; no live call has used it yet, and the audit counts it apart from Otari's own failover.
- A closure gate runs nine checks before a dossier is sealed. If a hard check fails, for example a bull or bear call was refused, nothing is appended and the refusal goes to `ledger/closure-refusals.jsonl`. It has not refused a live cycle yet.
- The citation checker has a consequence from rules v7. In dossier 812 it marked one claim unsupported, the judge's lean rested on it, and the lean was voided.

## What we would ask of Otari

1. A guardrails service on the hosted gateway, and an error instead of a silent pass for an output-side guardrail. The header now says `unavailable, not_configured` (first seen in dossier 805), which helps, but nothing is evaluated.
2. A JSON error with a request id when the edge times out, a documented 30 second limit, and a wait instead of a 409 on a retried key.
3. A notice, or a clear cause in the error, when a model that answered an hour earlier is refused as unpriced.
4. A per-request list of fallback models, so an app can test failover from code.
5. A way for a service key to read a budget's name and limit, and the response headers declared in the OpenAPI document.

Every item has a reproduction in [`FEEDBACK_LOG.md`](FEEDBACK_LOG.md), except the pricing refusal, whose request ids are in dossier 810.
