# Otari feedback log

Every real snag met while putting Verdict Desk behind the Otari gateway, with the symptom, the steps to reproduce and what would have helped. It is the raw material for the written feedback and the feedback video. Entries are added as they happen; nothing here is a guess. The hosted gateway was version `0.35.0+85a3861` on 2026-11-01. In the commands, `$OTARI_API_KEY` is an ordinary (non-service) inference key; the key itself never appears in this file.

Status words: **open** (still reproduces), **worked around** (our client handles it), **fixed** (retested after a change on your side).

## Entries

### 1. A slow model answers 504 from CloudFront with an HTML page and no Otari request id (open, worked around)

- **Note.** `config/otari.json` (whose sha256 is sealed in dossier 792, so it is not edited afterwards) cites this entry as "entry 2" by mistake; it is entry 1.
- **Symptom.** `meta/llama-4-scout-17b` through the catalog id. Four of five one-word calls on 2026-11-01 ended after 30.3 s with `HTTP 504`, `content-type: text/html`, a CloudFront "The request could not be satisfied" page, no `Otari-Request-ID` and no `Otari-Error-Code`. The fifth call succeeded with `Otari-Response-Duration-Ms: 23246` for a two-token answer (`otari-provider: deepinfra`). The other candidates we tried answered the same prompt in 0.3 to 2.4 s.
- **Reproduce.**
  ```bash
  curl -sS -m 90 -o /dev/null -w '%{http_code} %{time_total}\n' https://otari.ai/api/v1/chat/completions \
    -H "Otari-Key: $OTARI_API_KEY" -H 'Content-Type: application/json' \
    -d '{"model":"meta/llama-4-scout-17b","max_tokens":64,"messages":[{"role":"user","content":"Reply with the single word ok."}]}'
  ```
  Expected on a bad day: `504 30.4`.
- **Why it hurts.** The caller cannot tell a dead upstream from a cut connection, cannot look the request up (no id), and cannot tell whether it was billed. For long reasoning completions on healthy models the same 30 s ceiling would apply to non-streaming calls, which the docs do not state.
- **Suggestion.** Return a JSON error from the gateway (for example `upstream_timeout`, with `Otari-Request-ID`) before the edge gives up; document the 30 s non-streaming ceiling; let the automatic fallback move on when the first offering is that slow (here the catalog lists a single provider, so there was nothing to fall back to); show a latency hint next to single-provider catalog entries.
- **Our handling.** The client retries 504 with the same `Idempotency-Key` (and the follow-up 409, entry 7) and records `client_attempts` in the receipt. The role was moved to `google/gemma-3-27b-it` (1.2 to 1.4 s), and llama is listed under `rejected` in `config/otari.json` with this reason.

### 2. The `user` field with an ordinary key is refused with a message that does not name the cause (open, worked around)

- **Symptom.** Sending the documented per-user field `"user": "bull"` with a key that is not a service key returns `HTTP 403 {"detail":"'user' field does not match the authenticated API key's user"}`, with an `Otari-Request-ID` but no `Otari-Error-Code` header. A request without `user` succeeds.
- **Reproduce.**
  ```bash
  curl -sS -i https://otari.ai/api/v1/chat/completions -H "Otari-Key: $OTARI_API_KEY" -H 'Content-Type: application/json' \
    -d '{"model":"openai/gpt-oss-120b","max_tokens":8,"user":"bull","messages":[{"role":"user","content":"ok"}]}'
  ```
- **Why it hurts.** Per-user budgets are one of the headline features, and the first thing a developer tries is the `user` field. The text says "does not match", which reads like a typo in the id, not like "this key cannot name end users".
- **Suggestion.** A stable `Otari-Error-Code` such as `end_user_requires_service_key`, and a detail that says "end users need a service key; this key is not one". A pointer from the key-creation screen to the fact that `is_service_key` cannot be changed later would save a key rotation.
- **Our handling.** `config/otari.json` has `send_user: false`; the role name goes in the `user` field only after `send_user` (or `OTARI_SEND_USER=1`) is switched on for a service key.

### 3. `usage.cost_usd` is rounded to six decimals, which erases the cost of small calls (open, worked around)

- **Symptom.** One-word calls report `cost_usd` of `"0.000001"`, `"0.000002"`, `"0.000003"`, `"0.000005"`, `"0.000009"`. `deepseek/deepseek-v4-flash-0731` returned `estimated_cost: 1.0200000000000002e-06` next to `cost_usd: "0.000001"`; `nvidia/nemotron-3-super-120b-a12b` (served by bedrock) returned `cost_usd` only. The summed receipts of a cycle can drift from the real spend by up to half a micro-dollar per call.
- **Reproduce.** Any call with `max_tokens` 64 and a one-word prompt on the models above; read `usage` in the body.
- **Suggestion.** Always return the unrounded amount (or integer micro-dollars) for every provider, and document how `estimated_cost` differs from `cost_usd`.
- **Our handling.** The receipt keeps the string exactly as sent; no sum of receipts is presented as exact.

### 4. `usage.completion_tokens_details.reasoning_tokens` is present for some models only (open)

- **Symptom.** Reasoning models are billed for thinking tokens as output, but the usage block of `openai/gpt-oss-120b` (42 completion tokens for the word "ok") has no `completion_tokens_details`, while `deepseek/deepseek-v4-flash-0731` has `{"reasoning_tokens": 0}`. A caller cannot see how much of a bill was thinking.
- **Reproduce.** Same one-word call on both models; compare `usage`.
- **Suggestion.** Include `completion_tokens_details.reasoning_tokens` for every model flagged `capabilities.reasoning: true` in the catalog.

### 5. A reasoning model can return an empty `content` and still bill the tokens (open, worked around)

- **Symptom.** `alibaba/qwen-3.5-35b-a3b` with `max_tokens: 20`: `finish_reason: "length"`, `content: ""`, and the thinking in `message.reasoning` and `message.reasoning_content`. Standard behavior, but the 200 status gives no hint.
- **Reproduce.** `{"model":"alibaba/qwen-3.5-35b-a3b","max_tokens":20,"messages":[{"role":"user","content":"say ok"}]}`.
- **Suggestion.** An optional response header (for example `Otari-Finish-Reason: length`) or a catalog field with the typical thinking budget per reasoning model, so callers size `max_tokens` before the first bill.
- **Our handling.** The client turns an empty `content` into an error that names the `finish_reason` and the role, and the role config can set its own `max_tokens`.

### 6. `Otari-Model` is the provider's own spelling, the body `model` echoes the request, and neither is the catalog id (open, worked around)

- **Symptom.** Asking for `nvidia/nemotron-3-super-120b-a12b` answers with `otari-provider: bedrock`, `otari-model: nvidia.nemotron-super-3-120b`; asking for `deepseek/deepseek-v4-flash-0731` answers with `otari-model: deepseek-ai/DeepSeek-V4-Flash-0731`. The body `model` repeats what was sent.
- **Why it hurts.** An audit trail that records the served model has to map three spellings. The docs describe the headers but the hosted OpenAPI (`0.35.0`) does not list any response header at all.
- **Suggestion.** Add `Otari-Catalog-Model` (the catalog id that was resolved) next to `Otari-Model`, and declare the response headers in the OpenAPI document so SDK generators and readers see them.
- **Our handling.** The receipt stores `sent_model`, `provider`, `served_model` and a joined `served_by`, so nothing is inferred later.

### 7. After an edge 504 the retry meets `409 Idempotency-Key is still in progress`, and the tail latency sits right under the 30 s ceiling (open, worked around)

- **Symptom.** First full cycle on 2026-11-01 (nine parallel narrative extraction batches on `alibaba/qwen3-235b-a22b-instruct-2507`, `max_tokens` 6000): four batches failed with `HTTP 409 {"detail":"A request with this Idempotency-Key is still in progress. Retry with the same key to get its result."}`. The sequence was an edge timeout, then our retry with the same `Idempotency-Key`, then 409 because the gateway was still finishing the first attempt. A 409 is not in any usual retry list, so a standard client gives up on a request that is about to succeed. In the clean second run the nine batches took 5.0 to 29.8 s (`Otari-Response-Duration-Ms`), and the first run of `openai/gpt-oss-120b` for the bull analyst took 27.0 s, so 30 s is not a remote ceiling for ordinary reasoning calls.
- **Reproduce.** Send a long non-streaming completion that needs more than 30 s (for example `max_tokens` 6000 on a 10-headline extraction prompt, nine at once), then repeat it with the same `Idempotency-Key` after the first 504.
- **Why it hurts.** The 409 text does tell the caller what to do, which is good, but it only appears after a timeout whose own page does not name the key or the request (entry 1), and the status code suggests a client error.
- **Suggestion.** Answer 409 here with `Retry-After`; better, hold the second request open until the first finishes (the key already identifies it); and consider `202` or a documented polling route for completions longer than the edge allows. Document the non-streaming ceiling next to the idempotency section.
- **Our handling.** The client retries that exact 409 with a 5 s growing wait on the same key; the stored reply then comes back with `Otari-Idempotent-Replayed: true`, which the receipt records. In the final cycle one batch needed three tries (`client_attempts: 3`) and all nine succeeded.

### 8. Three credential headers all work, and the docs could say which one to prefer (note)

- Measured on 2026-11-01: `Otari-Key: <key>`, `Authorization: Bearer <key>` and `x-api-key: <key>` each answered HTTP 200 on `/chat/completions`.
- We use `Otari-Key` because it cannot be confused with a provider key in a shared environment, and `Authorization: Bearer` stays one config line away (`auth_header` in `config/otari.json`). A sentence in the quickstart on which header the team recommends, and whether the others stay supported, would remove a guess.

### 9. Request fields and headers that worked on the first try (what to keep)

- `Idempotency-Key`: the second call with the same key came back in 0.27 s with `Otari-Idempotent-Replayed: true` and the same `Otari-Request-ID`, with no second charge visible in `usage`. It makes retrying a 504 safe, which is exactly where we needed it (entries 1 and 7).
- `Otari-Tags: role=bull` and `Otari-Conversation-Id` were accepted without error. We could not see them anywhere with an inference key, so we do not yet know whether they are visible in the dashboard or in `usage/summary` (to check in F2 with the panel).

## Process notes

- **A discarded test cycle.** The first full cycle on 2026-11-01 (BTCUSDT, local, `decide-v6`) appended a dossier 792 whose narrative had four of nine extraction batches failed by the 409 of entry 7 (its bull, bear, checker and judge requests were `11c4541b-8211-409b-ad47-d5828ce95e80`, `d061214b-c8d4-4278-b366-8b0d9a884176`, `4a7a1a13-2ece-429b-8b74-f194886eef6a` and `fb534b12-ebae-41cb-9666-d5f87c0d2a17`). It was never committed, pushed, stamped or published: the three ledger files were restored from git, the client was fixed to retry that 409, and the cycle was run again. The dossier 792 in this repository is the second run. The failed run is mentioned here so that nobody wonders about the missing first attempt; the ledger is append-only once it is committed, and this one never was.
- **The legacy `route` field of dossier 792.** That dossier was sealed with a `route` list built from the receipt (and `routing_policy` empty) on each of bull, bear and judge. Otari does not send such a list, so the field was dropped for every later dossier (review finding F1.1). Dossier 792 is not rewritten; treat its `route` as derived from the receipt, which is the record of what Otari reported.

## Carried from the F0 reading of the spec and docs (not re-tested yet)

| # | Observation | Status |
|---|---|---|
| a | Hosted spec `0.35.0` does not describe response headers; the live replies do carry `Otari-Request-ID`, `-Provider`, `-Model`, `-Attempt-Count`, `-Fallback`, `-Response-Duration-Ms` (checked today), so the spec lags. `otari_tool_calls` is still unchecked. | partly confirmed |
| b | The guardrails doc says checks run on the input; the hosted `GuardrailConfig` accepts `on: ["input","output"]` with no description. | not tested (needs F3) |
| c | The `Otari-Guardrails` header format is undocumented, and streaming does not propagate it. | not tested (needs F3) |
| d | For a tenant on the hosted service, the "master key" instructions (MCP servers, guardrail mandates, provider keys) have no hosted equivalent in the docs; which credential reaches the management routes is not stated. Today an ordinary key gets 403 "This endpoint requires a service key" on `GET /api/v1/end-users` and 401 on the budget routes. | partly confirmed |
| e | A key created in one region answers 421 on the other; the dashboard and docs say little about picking a region at sign-up. | not tested |
| f | Google or GitHub sign-up leaves no password, so scripted management through `POST /auth/session` is impossible. | not tested |
| g | The public catalog hides offering prices from visitors while the listing shows minimum prices. | confirmed (catalog read today) |
