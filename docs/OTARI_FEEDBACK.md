# Feedback to Otari: written answers

The three feedback questions of MozFest Developers Wilding: Online, answered by the author of Tally Desk. Numbers come from the sealed ledger, dossiers 792 to 840; every friction has steps to reproduce in [`FEEDBACK_LOG.md`](FEEDBACK_LOG.md).

## 1. When were you most tempted to skip Otari and call the provider directly? What happened?

Three times. On the first day, slow completions were cut at about 30 seconds by an HTML 504 with no request id, and my retry then met `409 ... still in progress`. In my first ten dossiers, 7 of 167 calls ended on that 409.

On 2026-11-04, bull, bear and narrator answered at 15:33 UTC, were refused with `402 pricing_required` at 16:12, and answered again at 17:10. Same key, same code. Dossier 810 carries the request ids.

On 2026-11-05, two cycles (11:04 and 18:49 UTC) were refused with `403 budget_exceeded`, scope organization, before any model ran.

I stayed, because a direct call would have given up the receipts, the per-user budgets and the failover the project is about. I fixed my side instead: the client now waits on the same Idempotency-Key, each core role has backup models, and a closure gate refuses to seal a cycle whose analysts did not answer. Both refused cycles in November left nothing in the ledger, which is what I want from an audit trail.

## 2. What did you end up building yourself that Otari should've done for you?

- A guardrail service. The hosted gateway has no guardrails service, so every verdict came back `valid: null`. I deployed my own detector on Cloudflare Workers and mandated it as "your own guardrail" in the dashboard. Since dossier 817 Otari has evaluated it on 563 calls, and a live drill in block mode refused an injection with `403 guardrail_violation`.
- A receipt. Otari sends the facts as headers the hosted spec does not declare, and the model comes in three spellings. I rebuilt them as one sealed receipt per call.
- A retry and wait layer for the 504 and the 409: 267 retries, 248 stored answers replayed, in 1,125 receipts.
- A budget limit. A service key reads an end user's spend but not the limit, so USD 0.10 lives in my config.
- A role fallback. There is no per-request fallback list, so each role has its own chain of catalog ids, proven live by a drill.
- Model-level reconciliation. The usage record has cost, tokens and status but not the model.

## 3. If the rules hadn't required Otari, would you have continued using Otari? Why or why not?

Yes.

One key reached open-weight models from five vendors, and every answer said who served it. 1,125 calls in 49 cycles cost USD 2.03, a median of USD 0.04 a cycle. Otari moved 74 answers to a backup route, 31 of them real traffic I never had to handle. The budget refusal names the amount left and the estimate and refuses before any model runs. Retrying with the same key never billed twice that I could see. Bringing my own guardrail took one dashboard form, and from then on every call carried an evaluated verdict.

The docs were the slowest part: which credential reaches which route on the hosted service took me a day, so I sent a docs PR (mozilla-ai/otari #2894, merged). What I would still ask for: a cause in the error when a model that answered an hour earlier is refused as unpriced, the response headers in the hosted OpenAPI, and a guardrail service on the hosted tier so the next developer does not have to deploy one.
