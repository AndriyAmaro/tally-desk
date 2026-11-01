# Otari map for Verdict Desk (phase F0)

Written 2026-11-01, before any account or key exists. Goal: for each of the eight Otari features, the exact way to trigger it on the HOSTED gateway (otari.ai), the proof it leaves, what it costs, and what is still unknown.

Evidence labels used everywhere below:

| Label | Meaning |
|---|---|
| [LIVE] | Checked today with plain unauthenticated GETs against the hosted API (no account, no spend). |
| [SPEC] | Read in the hosted OpenAPI document, version `0.35.0+85a3861`, served at `https://api.otari.ai/api/v1/openapi.json` (public). |
| [DOC] | Stated in the Otari docs on `main` (mozilla-ai/otari). The hosted build may be older than `main`, so every [DOC] item is a test for F1, not a fact. |
| [?] | Unknown. Only the logged-in account can answer. Listed in the last section. |

Sources: the docs pages routing, tools, mcp, guardrails, access-control, models, traces, hosted-providers, code-execution-protocol, api-reference, configuration, dashboard, provider-endpoints, quickstart, sdk-compatibility; the rendered pages https://otari.ai/ and https://otari.ai/devs-wilding; the hosted OpenAPI and public catalog.

## 1. Hosted basics

### 1.1 Endpoints and region

| Item | Value |
|---|---|
| OpenAI-compatible base (US) | `https://api.otari.ai/api/v1` [LIVE] (the dashboard bundle hardcodes `https://api.otari.ai`) |
| OpenAI-compatible base (EU) | `https://eu.api.otari.ai/api/v1` [LIVE] |
| Anthropic-compatible base | `https://api.otari.ai/api` (the SDK appends `/v1/messages`) [DOC] |
| Chat route | `POST {base}/chat/completions`; also `/messages`, `/responses` [SPEC] |
| Swagger / OpenAPI | `{base}/docs`, `{base}/openapi.json`, both public [LIVE] |
| Hosted version | `0.35.0+85a3861` at `/api/v1/health/readiness` [LIVE] |
| Regions | `GET /api/v1/regions` returns `us` and `eu` [LIVE]. An API key belongs to one region; the other region answers `421 misdirected_request` [DOC]. Ask Andri which region the account was created in and set the base URL to match. |
| Auth headers | Any one of `Authorization: Bearer <key>`, `Otari-Key: <key>`, `x-api-key: <key>` [DOC, SPEC] |
| Sign-up | Open sign-up, Google or GitHub OAuth, password and passkeys; terms at otari.ai/terms [LIVE from `/api/v1/bootstrap`] |
| Hackathon credit | "The first 200 people to sign up get $20 in tokens" (otari.ai/devs-wilding) [LIVE]. The wallet read "applies its one-time signup credit" [SPEC], so the credit may only appear after the first `GET /wallet` or first request [?]. |

`GET /api/v1/bootstrap` also lists the hosted surfaces: admin, agent_recommenders, budgets, health_checks, hosted_providers, keys, models, organization_guardrails, organization_providers, organization_usage, organizations, playground, pricing, routing, settings, tools, traces, usage, users, workspaces [LIVE]. Every one of the eight features has a surface on that list.

### 1.2 Model names

| Form | Example | Behavior |
|---|---|---|
| `provider:model` | `deepinfra:openai/gpt-oss-120b` | Pins one provider. No automatic fallback. [DOC] |
| catalog id | `openai/gpt-oss-120b` | Otari picks the provider and falls back across up to 5 offerings of the same model. [DOC] |
| `provider:catalog-id` | `nebius:openai/gpt-oss-120b` | Pins the provider, Otari spells the model id for it. [DOC] |
| alias or routing policy name | `vd-judge` | Stored in the workspace. Names cannot contain `:` or `/`. The response body `model` keeps this name. [DOC] |

Hosted provider ids today: US `bedrock, deepinfra, fireworks, mistral, nebius, ovhcloud, together`; EU is the same without `nebius` [LIVE].

### 1.3 Listing the catalog

| Call | Needs a key | Notes |
|---|---|---|
| `GET /api/v1/catalog/models?limit=1000&include_facets=true` | No [LIVE] | Public catalog. US: 160 models. EU: 117 models. Each entry has `open_weights`, `capabilities.tool_call`, `capabilities.structured_output`, `context_window`, `min_input_price_per_million`, `min_output_price_per_million`, `selectors`, `providers`. |
| `GET /api/v1/catalog/models/{id}` (URL-encode the slash) | No [LIVE] | Offerings of one model in fallback order (for `openai/gpt-oss-120b`: deepinfra, ovhcloud, fireworks, nebius, together). Prices are hidden for visitors. |
| `GET /api/v1/models` and `GET /api/v1/models?provider=x` | Yes [SPEC] | What this key may call, including aliases and routing policies. |
| `GET /api/v1/models/{id}` | Yes [SPEC] | One catalog id, alias or selector. |

### 1.4 Open-weight models the hosted catalog really offers [LIVE]

102 of 160 US catalog entries are flagged `open_weights: true` (about 80 are chat-capable text models). All candidates below have tool calling and structured output flags, and are present in both regions.

| Role (proposed) | Catalog id | Vendor | $/M in | $/M out | Context | Selectors (US) | EU providers |
|---|---|---|---|---|---|---|---|
| bull | `openai/gpt-oss-120b` | OpenAI | 0.037 | 0.17 | 131k | `deepinfra:openai/gpt-oss-120b`, `fireworks:accounts/fireworks/models/gpt-oss-120b`, `nebius:openai/gpt-oss-120b`, `ovhcloud:gpt-oss-120b`, `together:openai/gpt-oss-120b` | bedrock, deepinfra, fireworks, ovhcloud |
| bear | `nvidia/nemotron-3-super-120b-a12b` | NVIDIA | 0.15 | 0.65 | 262k | `bedrock:nvidia.nemotron-super-3-120b`, `deepinfra:nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`, `nebius:nvidia/nemotron-3-super-120b-a12b`, `together:nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16` | bedrock, deepinfra |
| judge | `deepseek/deepseek-v4-flash-0731` | DeepSeek | 0.06 | 0.18 | 1.05M | `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731`, `nebius:deepseek-ai/DeepSeek-V4-Flash-0731` | deepinfra |
| checker | `meta/llama-4-scout-17b` | Meta | 0.10 | 0.30 | 328k | `deepinfra:meta-llama/Llama-4-Scout-17B-16E-Instruct` | deepinfra |
| judge fallback / alt | `alibaba/qwen3.8-27b` | Alibaba | 0.20 | 2.50 | 262k | `deepinfra:Qwen/Qwen3.8-27B`, `nebius:Qwen/Qwen3.8-27B` | deepinfra |
| alt bear | `nvidia/nemotron-3-ultra-550b-a55b` | NVIDIA | 0.60 | 2.40 | 1.05M | `fireworks:accounts/fireworks/models/nemotron-3-ultra-nvfp4`, `nebius:nvidia/Nemotron-3-Ultra-550b-a55b` | fireworks |

Four vendors (OpenAI, NVIDIA, DeepSeek, Meta) in four distinct roles covers the "two or more open-weight models with different roles" block. The existing ledger already ranked gpt-oss-120b and nemotron-3-ultra at the top for citation-validator pass rate (`config/model_routing.json`), so those two are the safest picks for the roles that make claims. Final assignment must still pass the dual ruler (set A and frozen set B) before it becomes the champion.

The docs say Otari "routes to 40+ providers"; the hosted deployment today serves only the seven providers above. Do not plan on OpenAI, Anthropic or Google providers being available on the hosted credential.

### 1.5 Own provider key (BYOK, for example NVIDIA NIM) on hosted

- Mechanism: an organization owner or admin adds a provider key, `POST /api/v1/organizations/me/provider-keys` with `{provider, name, api_key, api_base, client_args}` [SPEC]. Adding a key pulls the provider's model list and every model arrives with a serving switch. Requests on it are paid by the organization's own upstream account, and the organization may set its own rate (0 is allowed) [DOC].
- `api_base` must be `https` and resolve to public addresses [DOC]. NIM's `https://integrate.api.nvidia.com/v1` fits.
- No `nvidia` provider name appears anywhere in the hosted spec. The route would be `provider: "openai"` plus the NIM `api_base` [?]. `GET /api/v1/providers/catalog` (key needed) lists the accepted provider ids.
- Side effects: such a key becomes the organization's preferred credential for that provider name, and its traffic still counts against budgets and rate limits (unlike "provider endpoints", which are operator-only and budget-exempt [DOC]).
- Verdict: not on the critical path. The hosted roster already offers 80-plus open-weight models. The only reason to do it is to move token spend off the $20 wallet and keep the wallet for tool calls. It means handing a NIM secret to a third party, which is Andri's decision (section 7, D5).

### 1.6 Who pays and what the $20 buys [DOC, SPEC]

Hosted models run on the deployment's own provider credentials. The deployment sets the price (organizations cannot override prices of hosted models) and charges the organization's wallet. Before a request the organization is "granted any credit it is owed", and with no funds the request is refused `402 insufficient_funds` and nothing is sent upstream. Tool calls (search, fetch, code execution) are charged to the same wallet. The wallet is readable at `GET /api/v1/wallet` (fields `balance`, `reserved`, `available`, strings in USD) and `GET /api/v1/wallet/transactions`, owners and admins only. A voucher is redeemed at `POST /api/v1/wallet/vouchers/redeem`; whether the hackathon credit arrives as a voucher or automatically is [?].

### 1.7 Two kinds of credential (this decides who can configure what)

| Credential | What it is | Can do |
|---|---|---|
| Inference API key (`tk-...`) | Created in the dashboard (`POST /api/v1/organizations/me/keys`); returned once | Chat, Messages, Responses, models, catalog, tools list, search, MCP tools and execute, `usage/requests/{id}`, traces of its own requests, and (service keys only) `/end-users` [DOC, SPEC] |
| Dashboard session (human sign-in, cookie or `access_token`) | Email plus password, passkey, or OAuth | Everything under `/organizations/me/*` and `/workspaces/{id}/*`: budgets, spend ceilings, routing policies, guardrail mandates and definitions, MCP server registry, provider keys, workspace tool policies, wallet. These need organization owner or admin. [DOC] |

Whether a plain API key is also accepted on the management routes is [?] (every operation in the spec lists the key schemes only, but the docs say "master key, and an organization owner or admin", and `/hosted-providers` answers "an API key gets 401"). Assume NOT until tested. Consequence: the panel work for features 2, 3, 4, 5 and 8 is done once by Andri in the dashboard (about 15 minutes, checklist in section 7) or by a session he explicitly hands over. After that, the app only needs the inference key. The video shows the panel anyway.

Dashboard-session detail worth knowing: sign-in with Google or GitHub leaves the account without a password, and `POST /api/v1/auth/session` only takes `email` and `password` (or the master key, which hosted users do not have). Scripted management calls would need Andri to set a password in Account settings. Not recommended: panel clicks are safer than handing a login to an agent.

## 2. What `llm.py` must read from Otari

`verdict_desk/llm.py` already works against Otari with no code change for the F1 smoke call:

```
MISTRAL_BASE_URL=https://api.otari.ai/api/v1      # or https://eu.api.otari.ai/api/v1
MISTRAL_MODEL=openai/gpt-oss-120b                  # catalog id, enables automatic fallback
MISTRAL_API_KEY=$OTARI_API_KEY
```

It posts `{base}/chat/completions` with `Authorization: Bearer`. For receipts it must also read the following. Everything in the right column of the first table is [DOC] unless marked; the hosted spec does not describe response headers at all, so each one is verified on the first F1 response (Q3).

### 2.1 Response headers (non-streaming; `llm.py` never streams)

| Header | Meaning | Maps to in the dossier |
|---|---|---|
| `Otari-Request-ID` | Id of the request; equals `request_group_id` in the usage log; key to the trace | `receipt.request_id` |
| `Otari-Provider` | Provider instance that served it (`deepinfra`) | with `Otari-Model`: `served_by = "<provider>:<model>"` |
| `Otari-Model` | Model id sent to that provider | same |
| `Otari-Attempt-Count` | Candidates actually called, including the winner | `receipt.attempt_count` |
| `Otari-Fallback` | `true` when a candidate other than the first served | `receipt.fallback` |
| `Otari-Response-Duration-Ms`, `Server-Timing` | Gateway plus provider time | `receipt.duration_ms`, `receipt.otari_ms`, `receipt.upstream_ms` |
| `Otari-Guardrails` | Guardrail verdict (monitor mode, non-streaming only). Format undocumented; store the raw string | `receipt.guardrails_raw` |
| `Otari-End-User-Budget` | Budget id the end user is on | `receipt.end_user_budget` |
| `Otari-Rate-Limit-Exceeded` | Report-only rate rules that were over | `receipt.rate_limit_reported` |
| `Otari-Container-Id` | Sandbox kept for reuse | `receipt.container_id` |
| `Otari-Idempotent-Replayed` | `true` when a retry returned a stored answer | `receipt.replayed` |

### 2.2 Response body

| Field | Meaning |
|---|---|
| `model` | Echoes what was SENT (alias, policy name or catalog id), not what served it. |
| `usage.prompt_tokens`, `usage.completion_tokens` | Tokens. Reasoning tokens bill as output. |
| `usage.cost_usd` | Six-decimal USD string, the amount written to the usage record, tool charges included. Absent when unpriced. |
| `usage.pricing_source` | `organization`, `deployment` or `defaults`. |
| `otari_tool_calls` | Top-level array of Otari's own web searches and fetches: `id`, `type`, `status`, `query` or `url`, `sources[{url,title,published_date}]`, `error`. Code execution and MCP are NOT reported here on Chat Completions. |
| `choices[0].message.content` | As today. |

### 2.3 Error headers (needed for visible refusals)

| Header | Meaning |
|---|---|
| `Otari-Error-Code` (also `code` in the JSON body) | `budget_exceeded` (403), `insufficient_funds` (402), `pricing_required` (402), `rate_limited` (429), `upstream_rate_limited` (429), `model_not_allowed` (403), `model_not_serving` (403), `provider_not_configured` (424), `provider_credential_rejected` (424), `all_candidates_rejected` (400), `end_user_budget_not_allowed` and `end_user_limit_reached` (403), `guardrail_violation` (403, guardrail in block mode) |
| `Otari-Budget-Scope` | Which budget refused: `user`, `organization`, `workspace`, `workspace_member`, `org_member`, `api_token` |
| `Retry-After` | Seconds until the refusing budget resets, when it resets |
| `Otari-Error-Provider`, `-Status`, `-Tag`, `-Retryable` | Safe summary of a provider failure |
| `x-should-retry: false` | Retrying cannot help |

### 2.4 Three gotchas in the current client

1. `_remember_model` reads the body `model`. With Otari that is the policy or catalog name, so the dossier would record `vd-judge` instead of the model that answered. Read `Otari-Provider` plus `Otari-Model`.
2. `_remember_route` expects `X-Verdict-Route` (a list of attempts). Otari sends counts, not the list. Keep the dossier `route` shape (`[{"model","status","ms"}]`) by filling it after the cycle from `GET /api/v1/usage?request_group_id=<Otari-Request-ID>` (rows carry `model`, `provider`, `status`, `status_code`, `attempt_position`, `attempt_count`, `selection_reason`, `policy_name`, `latency_ms`) [SPEC]. The policy hash (`X-Verdict-Policy`) has no Otari equivalent: record the sha256 of our own policy JSON and keep a copy in `config/otari_policies.json`.
3. `_http_error` flattens everything into `HTTP 403 ... detail`. Raise an `LLMError` subclass carrying `code`, `budget_scope` and `retry_after` so the app can show "refused by budget" as a first-class dossier event. Do not retry 402, 403 or 424, and honor `x-should-retry: false` (the current `RETRY_STATUS` already skips 402 and 403).

### 2.5 Request additions the client needs (a small `extra` hook, kept thread-local like `_served`)

| Need | Where |
|---|---|
| `user` (end user id, for feature 4), `metadata` or `Otari-Tags` (role, cycle, dossier), `session_label` | body, header |
| `Otari-Conversation-Id: vd-<cycle id>` so one decision cycle is one trace session | header [DOC] |
| `Otari-End-User-Budget` | header |
| `guardrails`, `tools`, `mcp_servers` or `mcp_server_ids`, `max_tool_iterations` | body |
| `reasoning_effort` (`low` saves tokens), `max_tokens` at most 1500 for analysts (the budget hold is priced on it) | body |
| `Idempotency-Key` (one per logical call) so a retry after a lost response is not billed twice | header [DOC] |
| `User-Agent: verdict-desk/1.0` so the trace "harness" reads right | header |

## 3. The eight features

Conventions: `$BASE` is the hosted base, `$KEY` the inference key. Management calls (marked ADMIN) need a dashboard owner or admin session unless Q5 shows a key works.

### 3.1 Several models in distinct roles

1. Trigger. Pure request: set `model` per role. No setup. Use the catalog id for automatic fallback, or the pinned selector when a test needs one provider.

```bash
curl -sS -D headers.txt "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Otari-Tags: role=bull,cycle=c-0001" \
  -d '{"model":"openai/gpt-oss-120b","max_tokens":600,"temperature":0,
       "messages":[{"role":"system","content":"..."},{"role":"user","content":"..."}]}'
```

2. Proof. `Otari-Provider` and `Otari-Model` per role; `usage.cost_usd`; `Otari-Request-ID`. Later: `GET /api/v1/usage/summary?group_by_tag=role` returns spend per role, and `GET /api/v1/usage/summary` lists `by_model`. Receipt per call in the dossier (section 4); an aggregate "models by role" table in the dossier header.
3. Cost. About $0.0024 per four-call cycle at the prices in 1.4 (section 5).
4. Risks. A candidate may be switched off in the hosted roster (then `404 model_not_found` or `424`). Mitigation: Q2 lists exactly what the key sees. Reasoning models spend tokens before answering (the client already handles this with `MAX_TOKENS`).

### 3.2 Routing and fallback

1. Trigger. Two layers.
   - Automatic, zero setup: send the catalog id (`openai/gpt-oss-120b`). When the first offering fails before answering, the next one serves; usage rows record `selection_reason` `catalog` then `on_failure` [DOC]. `GET /api/v1/organizations/me/automatic-fallbacks` lists, per model, the provider order and how many requests a backup served [SPEC].
   - Explicit policy (ADMIN): `POST /api/v1/organizations/me/routing-policies` with `{name, workspace_id, spec}`; or the dashboard Routing page, "Create policy", then "Fallback". The spec is the config.yml shape:

```json
{"name": "vd-judge", "workspace_id": "<WORKSPACE_ID>",
 "spec": {"select": [{"default": "deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731"}],
          "on_failure": ["nebius:deepseek-ai/DeepSeek-V4-Flash-0731", "deepinfra:Qwen/Qwen3.8-27B"]}}
```

   (EU has no nebius: use `deepinfra:Qwen/Qwen3.8-27B` and `deepinfra:openai/gpt-oss-120b` as backups.)
   - Forced-failure drill: a second policy `vd-judge-drill` whose first target is a model that does not exist (`deepinfra:drill/does-not-exist`) and whose `on_failure` is the real judge. The doc says a candidate that fails "whatever the provider's error" moves on, and a candidate with no credential fails the same way [DOC]. Drill requests use `model: "vd-judge-drill"`; production uses `vd-judge`.
   - Optional budget tie-in: a "Tier down on budget" policy, `select: [{when: {budget_used_pct: {gte: 80}}, target: "deepinfra:meta-llama/Llama-4-Scout-17B-16E-Instruct"}, {default: "deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731"}]`. It reads the tightest budget gating the request, so it links feature 2 to features 3 and 4 in one recibo.
   - Limits: at most 5 candidates per plan; targets must be concrete selectors (policies and aliases do not chain); only non-streaming failover is clean (and `llm.py` is non-streaming).
2. Proof. On the winning response: `Otari-Fallback: true`, `Otari-Attempt-Count: 2`, `Otari-Provider`, `Otari-Model`, body `model` = policy name. Full plan: `GET /api/v1/usage?request_group_id=<Otari-Request-ID>`; the failed attempt is an "absorbed" row with its `status_code` and `attempt_position` [DOC]. Trace: `GET /api/v1/traces/requests/{request_id}` shows routing attempts as spans [DOC]. Dossier stores the rows verbatim plus their sha256.
3. Cost. The failed attempt is not billed (absorbed rows have no settled cost). One extra attempt at most per drill.
4. Risks. [?] whether the org route accepts a hosted-provider target ("refuse a target the organization holds no provider access for" [DOC]; hosted providers serve every organization, so likely fine). [?] whether an unlisted model fails fast as 404, 424 or 400; all three move on per the docs. [?] a policy takes up to 30 seconds to reach other workers after a write.

### 3.3 Budgets and spend limits

1. Trigger (ADMIN once, then enforced on every request).
   - Budget objects: `POST /api/v1/organizations/me/budgets` with `{name, max_budget, token_limit, request_limit, reset_alignment|budget_duration_sec}`. `reset_alignment` is `calendar_day|calendar_week|calendar_month`; a budget with no period never resets, which makes a lifetime cap.
   - Ceilings: `POST /api/v1/organizations/me/spend-ceilings` with `{scope_type, scope_id, budget_id, provider_key_id?}`; `scope_type` is `organization|workspace|workspace_member|org_member|api_token`. `GET /api/v1/organizations/me/budgets` and `/spend-ceilings` report `current_spend`, `reserved_spend`, `current_tokens`, `current_requests`, `period_end`.
   - Suggested set: (a) organization ceiling, `max_budget` 15, no period, as the lifetime kill switch that leaves 5 USD of the 20 for video retakes; (b) api_token ceiling on the Verdict Desk key, calendar day, 1.00 USD; (c) a tiny budget `vd-drill-tiny` at 0.0005 USD for the refusal drill.
   - Per-cycle cap. Primitive limit: a request bills exactly one budget path (one `user`). Per-cycle cap therefore has two honest forms. Form A (recommended): client-side `CycleBudget` that sums `usage.cost_usd` per cycle, lowers `max_tokens` as the cycle spends, and aborts the cycle visibly; the server ceilings in (a) and (b) are the hard backstop, and the dossier records both numbers. Form B: bill each cycle to its own end user (`user: "cycle-<id>"`, budget `vd-cycle`, USD 0.05) and give up per-analyst budgets on that key. Pick A unless the video needs a server-enforced per-cycle refusal.
2. Proof. Enforcement refusal: HTTP 403, `Otari-Error-Code: budget_exceeded`, `Otari-Budget-Scope`, `Retry-After`, and a detail that reads either "has exceeded budget limit" or "has $X of budget remaining, less than this request's estimated cost of $Y" [DOC]. Cheap, deterministic refusal: send `max_tokens` 8000 to a budget of 0.0005: the up-front hold (prompt at roughly 4 characters per token, plus the whole `max_tokens`) exceeds the remainder, so the call is refused BEFORE any spend, and refused requests are not billed [DOC]. Record the refusal as a dossier event `otari.refusal`, plus `GET .../spend-ceilings` before and after, and `GET /api/v1/wallet` before and after a cycle.
3. Cost. Zero for refusals. Holds are released on settlement.
4. Risks. [?] whether a plain key can create budgets (Q5). Holds are priced on `max_tokens`: keep it realistic or the key's own ceiling will refuse healthy calls near the cap.

### 3.4 Budget per user

1. Trigger. One service key whose end users are the analysts. A service key is fixed at creation (`is_service_key` cannot be added later), so Andri creates the main app key as a service key from the start.
   - Create the budget first (3.3, `vd-analyst-day`: max_budget 0.25, request_limit 200, `calendar_day`).
   - ADMIN: `POST /api/v1/organizations/me/keys` with `{key_name:"verdict-desk", workspace_id, is_service_key:true, end_user_budget_id:"<vd-analyst-day id>", end_user_budget_ids:[...], max_end_users:50, expires_at:"2026-11-13T00:00:00Z"}`. The secret is shown once; it becomes `OTARI_API_KEY`.
   - Per request: body `"user": "analyst-bull"` (any string up to 256 characters). The end user is created on first use and gets the budget by copy. Optional header `Otari-End-User-Budget: <budget id>` picks another budget from the key's list, applied only when the end user is created.
   - An ordinary request with no `user` bills the key owner, so one key serves both modes.
2. Proof, readable with the service key itself (no session): `GET /api/v1/end-users?external_id=analyst-bull` returns `budget_id, spend, reserved, current_tokens, current_requests, next_budget_reset_at, blocked` [SPEC]; `GET /api/v1/end-user-budgets` returns users per budget. Refusal: 403 `budget_exceeded` with `Otari-Budget-Scope: user`. Also `end_user_limit_reached` and `end_user_budget_not_allowed`. Usage rows carry the user, so `GET /api/v1/usage/summary` has `by_user`.
3. Cost. Free. The mechanism is bookkeeping.
4. Risks. Spend of one request goes to one end user, so "analyst" must be the real human or agent id per request (the `bull`, `bear`, `judge` and `checker` roles are the natural end users). Send a stable id, never a per-request one (each new id creates an end user). `safety_identifier` is a separate field and is not billing.

### 3.5 Guardrails

1. Trigger. Request-level, no admin step, up to 8 entries:

```json
"guardrails": [{"profile": "prompt-injection", "mode": "monitor", "on_unavailable": "monitor", "on": ["input"]}]
```

   `mode: block` returns 403 `guardrail_violation` and never calls the model; a `block` guardrail that cannot be evaluated fails closed with 502 unless `on_unavailable: monitor`. Other layers (ADMIN): an organization mandate at `POST /api/v1/organizations/me/guardrails` (runs on every request of chosen workspaces; the stricter setting wins when layers overlap), organization guardrail definitions at `/organizations/me/guardrail-definitions` (Otari builds an any-guardrail vendor check itself), and `guardrails` inside a routing policy spec (input checks only).
   Three possible backends on hosted, in order of preference:
   a. The deployment's own guardrail service with the `prompt-injection` profile. Whether hosted has one configured is [?] (the profile list read is operator-only, so the only test is a real request).
   b. An organization definition built from the catalog (`GET /api/v1/tool-settings/guardrails/catalog`, readable by any key). The checks in it call third-party hosted APIs and need a vendor credential; `any_llm` is refused outright [DOC].
   c. Our own service: the request entry accepts a `url`; the service speaks `POST /validate {"profile","input_text","validate_kwargs"}` and answers `{"profile","result":{"valid","explanation","score"}}` [DOC]. `validate_kwargs` is forwarded, so the evidence index can ride along and our existing citation validator can run as the check. It must be publicly reachable; on hosted, loopback means the gateway's own host and is useless. The same public endpoint can also host the MCP tool of 3.8.
   Where the check runs: the spec lets an entry choose `on: ["input","output"]` [SPEC], but the docs describe input checks only [DOC]. The plan's "guardrail on the judge output" depends on `output` actually working [?]. Fallback design that does not depend on it: guard the INPUT of every analyst and judge call (evidence text that came from the web is untrusted, so a prompt-injection check there is a real control, not decoration).
2. Proof. `Otari-Guardrails` response header (monitor mode, non-streaming only; raw string stored as-is since the format is undocumented); a trace span for the guardrail check [DOC]; in block mode, the 403 with `Otari-Error-Code: guardrail_violation`. Drill: one benign request (valid) and one request carrying "Ignore all previous instructions and ..." in an evidence line (flagged), both recorded.
3. Cost. A guardrail call is not billed per request in the docs [?]. Model tokens unchanged.
4. Risks. If neither (a) nor (b) works, only (c) remains and it needs a public host (section 7, D4). This is the feature most likely to fail on hosted; keep code execution warm as its replacement (PLANO already does).

### 3.6 Web search and fetch

1. Trigger. Request-level; two shapes:
   - In the loop: add `"tools": [{"type": "otari_web_search", "max_uses": 2}]` (or `otari_web_fetch`). Otari runs the search, feeds results to the model, returns the final answer.
   - Direct: `POST $BASE/search` with `{"query":"...","max_results":5,"search_domain_filter":["reuters.com"]}` returns `results[{title,url,snippet,date}]` with no model involved. This is the cheapest, most deterministic evidence collector and fits the "evidence item with URL and time" receipt.
   Availability on hosted is [?]: `GET $BASE/tools` returns `available` per tool; `GET /api/v1/workspaces/{id}/web-search` reports `web_search_configured` and `default_enabled` [SPEC]. Fetch is "disabled by default" in the docs [DOC]. If the deployment has no search backend, the workspace can bring its own search key (`POST /api/v1/organizations/me/web-search-keys`: Exa, Tavily, Brave, Mojeek, DeepSeek) which needs an external account.
   Workspace policy (ADMIN, `PUT /workspaces/{id}/web-search`): `enabled`, `max_results`, `allowed_domains`, `blocked_domains`. Set `max_results` low (Exa bills more past 10 results).
2. Proof. Chat Completions: `otari_tool_calls` with `query`, `status`, `sources[{url,title,published_date}]`; add our own `fetched_at` from the response `Date` header. Usage rows carry a tool line (`<provider>:<instance>`, `builtin:builtin_fetch`, `org:<provider>` or `otari:web_search`) in `pricing_breakdown`; `GET /api/v1/usage?tool=web_search` filters them [SPEC, DOC]. Direct search: `Otari-Request-ID` header and the result list. Dossier item: `{"kind":"web","query","url","title","fetched_at","request_id","sha256_of_snippet"}`.
3. Cost. Rate per call is set by the operator and unknown to us [?] (docs cite provider list prices of roughly 0.00075 to 0.003 USD per call for DeepSeek, Tabstack and Mojeek, and Exa per search with extra per result past 10). Budget rule of thumb: at most 2 searches per cycle (`max_uses`), at most 10 calls per request is the deployment ceiling [DOC]. Failed tool calls are recorded but not charged [DOC]. With `require_pricing` on, an unpriced tool is refused `402 pricing_required` before the model runs.
4. Risks. Search and fetch results enter the model prompt, so fetched pages can steer later tool calls. Our app already treats news text as untrusted evidence; pair this with the input guardrail of 3.5. Chat Completions only reports web calls in `otari_tool_calls` if the hosted build has that field (it is absent from the 0.35.0 spec text; Q10 confirms).

### 3.7 Code execution

1. Trigger. Request-level: `"tools": [{"type": "otari_code_execution"}]`. The docs describe a hosted, pool-backed sandbox backend for otari.ai that speaks the same protocol as the open-source container [DOC, code-execution-protocol]. Availability and price on hosted are [?]: `GET $BASE/tools` and `GET /api/v1/workspaces/{id}/code-execution-policy` (`default_enabled`).
   Use Responses or Messages, not Chat Completions, because Chat Completions returns only the final text for code runs; the native result blocks (`code_interpreter_call` items on `/responses`, `server_tool_use` and `code_execution_tool_result` blocks on `/messages`) are the receipt. `Otari-Code-Execution: otari` pins Otari's sandbox. Limits: at most 25 loop iterations and 60 seconds per execution; one request shares one sandbox; `container: "auto"` keeps it across requests (`Otari-Container-Id`).
   Planned use: "re-derive the verdict in the sandbox". Send the frozen feature packet (JSON) and the pure-Python derivation function (a short file) in the prompt (files can also be uploaded and are seeded into the sandbox [DOC]), ask the model to run it and print the verdict JSON only. The sandbox probably has no network, so inputs must be inline.
2. Proof. The native blocks (code and stdout); `Otari-Request-ID`; a `otari:code_execution` line in `pricing_breakdown`; `GET /api/v1/usage?tool=code_execution`. Dossier item: `{"kind":"sandbox","stdout_sha256","local_replay_sha256","equal":true,"request_id"}`. The check that matters is equality with our local replay; the model is only a carrier, so a mismatch is recorded, not hidden.
3. Cost. Per-call price unknown [?]; plus about 2k to 4k tokens of prompt and code. Cap with `max_tool_iterations: 3` and one sandbox run per dossier.
4. Risks. Model might not run the code faithfully (temperature 0, "print only the JSON", compare to local replay). Sandbox may lack the stdlib features we use. The protocol doc says the grant and front door for the hosted pool "are not built yet" [DOC], so availability on hosted is the real question.

### 3.8 MCP

1. Trigger. Two integration modes.
   - Managed loop (request-level, no admin): `"mcp_servers": [{"name":"verdict-verify","url":"https://<public host>/mcp","purpose_hint":"...","allowed_tools":["verify_dossier","rederive_verdict"]}]` plus `"max_tool_iterations": 4` [SPEC]. `url` must be a streamable-HTTP MCP endpoint reachable FROM the gateway: there is inline support, but a public server is mandatory on hosted (private and reserved addresses are refused; loopback is the gateway's own host; `http` is rejected when a token is present). Otari does the model-tool-model loop.
   - Caller-orchestrated (ADMIN to register once): `POST /api/v1/workspaces/{workspace_id}/mcp-servers` stores the server (the response carries a connection `health` check with the tool list); then with the inference key: `GET /api/v1/mcp/servers/{id}/tools` and `POST /api/v1/mcp/execute` `{mcp_server_id, tool_name, arguments, server_revision, client_execution_id}`. No model in the loop, native MCP result returned, `Otari-Request-ID` header on success and error. Never auto-retry `execute` (a failure may already have run the tool).
   Where the server lives at zero cost is an open decision (section 7, D4). The tool is read-only compute (hash-chain verification and replay of a dossier posted in the arguments), so exposing it publicly is safe: no secrets, no side effects.
   Fallback if no public host: a public no-auth MCP server that the gateway can reach (for example a documentation MCP) satisfies "MCP used" but does not carry our story; treat it as the last resort. Reachability from the hosted gateway is [?].
2. Proof. Caller-orchestrated is the cleanest receipt: the tool list response plus the `execute` result plus the request id (registered server id and `server_revision` show it ran through Otari). Managed loop: Chat Completions hides MCP activity; on `/messages` with `anthropic-beta: mcp-client-2025-11-20`, streaming responses emit `mcp_tool_use` and `mcp_tool_result` blocks [DOC]; the trace holds the MCP connection and tool-call spans [DOC]. Dossier item: `{"kind":"mcp","server":"verdict-verify","tool":"verify_dossier","args_sha256","result_sha256","is_error":false,"request_id"}`.
3. Cost. MCP tool calls price under `otari:<tool>` rows if the operator set any (not stated) [?]; model tokens for the managed loop; execute path has no model cost at all.
4. Risks. Public host and its uptime during judging and recording; 30 second tool deadline, 1 MiB result cap, 256 KiB argument cap [DOC]: dossiers are small, fine. Tool names that collide with built-ins (`web_search`, `web_fetch`, `code_execution`) are refused with 400.

## 4. Receipt format (one per Otari call, sealed in the dossier)

```json
{
  "schema": "otari-receipt/1",
  "role": "judge",
  "request_id": "<Otari-Request-ID>",
  "sent_model": "vd-judge",
  "served_by": "deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731",
  "attempt_count": 2,
  "fallback": true,
  "duration_ms": 1840,
  "tokens": {"prompt": 2210, "completion": 640},
  "cost_usd": "0.000412",
  "pricing_source": "deployment",
  "end_user": "analyst-judge",
  "end_user_budget": "vd-analyst-day",
  "guardrails_raw": "<Otari-Guardrails header, verbatim>",
  "tool_calls": [{"type": "web_search", "query": "...", "status": "completed", "sources": [{"url": "..."}]}],
  "refusal": null,
  "session": "vd-c-0001",
  "captured_at": "2026-11-03T12:00:00Z"
}
```

Seal rules. The receipt is part of the dossier body, so the hash chain covers it and `verify` and `replay` keep working (replay never calls a model; text and receipt are frozen). After each cycle, one enrichment pass fetches and freezes `GET /api/v1/usage?request_group_id=<id>` and `GET /api/v1/traces/requests/<id>` (names, ids, timings, tokens and costs only; no prompt text is recorded unless a workspace turns on trace content, which stays off) under `otari_evidence`, with their sha256. Judges cannot call our key, so the frozen JSON plus the request ids and timestamps are what they see; the Otari engineers who judge can look the ids up on their side (assumption, say so in the video). `otari_audit` (F4) then counts features from receipts, never from config.

## 5. Keeping inside the $20

Estimates use the public hosted prices in 1.4 and a four-call cycle (bull 3k in and 1.5k out, bear 3k and 1.5k, judge 2k and 1.2k, checker 1.5k and 0.4k tokens).

| Item | Estimate |
|---|---|
| One decision cycle, model tokens, recommended roles | about 0.0024 USD |
| Same cycle, every call on `qwen3.8-27b` with 6k output tokens (reasoning-heavy worst case) | about 0.062 USD |
| Existing narrative extraction batches (10 batches, 4k in, 4k out, `qwen3.8-27b`) | about 0.11 USD per cycle |
| 50 cycles, models only | about 0.12 USD |
| 200 cycles, models only | about 0.48 USD |
| One budget hold (3k prompt plus `max_tokens` 1500, `qwen3.8-27b`) | about 0.0044 USD, released on settlement |
| Web search, fetch, code execution | unknown, set by the operator [?]; they, not tokens, are the real risk to the 20 USD |

Controls, in order of strength: (1) organization lifetime ceiling at 15 USD, no period; (2) key ceiling per calendar day; (3) `max_tokens` at most 1500 for analysts and `reasoning_effort: low` where the model accepts it; (4) `max_uses` at most 2 and `max_tool_iterations` at most 3 or 4 on every tool request; (5) cycles only on demand, never on an hourly schedule; (6) read `GET /wallet` before and after each batch and stop under 5 USD; (7) the first call of each tool is a single logged probe whose `usage.cost_usd` is written into this file before anything is looped; (8) `Idempotency-Key` on every non-streaming call so a lost response does not bill twice.

## 6. Friction already seen (seeds for `FEEDBACK_LOG.md`)

| # | Observation | Source |
|---|---|---|
| 1 | The hosted spec (0.35.0) does not describe response headers, and `otari_tool_calls` and `Otari-Provider`, `-Model`, `-Attempt-Count`, `-Fallback`, `Otari-Conversation-Id` are absent from it, while the docs on `main` rely on them. Either the hosted build is behind `main` or the spec omits them. | Spec text search versus docs |
| 2 | Guardrails doc says checks run on the input; the hosted `GuardrailConfig` has `on: ["input","output"]` with no description. | Spec versus guardrails.md |
| 3 | `Otari-Guardrails` header format is not documented anywhere; streaming does not propagate it. | demo README |
| 4 | For a tenant on the hosted service, "master key" instructions (MCP servers, guardrail mandates, provider keys) have no hosted equivalent in the docs; which credential reaches the management routes is not stated. | mcp.md, guardrails.md |
| 5 | The `SessionResponse` schema says the session token travels only in the cookie, while the public dashboard bundle stores an `access_token` in localStorage and sends it as the bearer credential. | Spec versus `otari.ai` bundle |
| 6 | A key created in one region answers 421 on the other; the dashboard and docs say little about picking a region at sign-up, and the EU catalog has 117 models against 160. | `/regions`, catalogs |
| 7 | Google or GitHub sign-up leaves no password, so scripted management with `POST /auth/session` is impossible. | Spec |
| 8 | Public catalog hides prices from visitors (`input_price_per_million: null` on offerings) while the listing shows minimum prices. | Catalog |

If a real reproducible bug shows up in F1, Andri opens the issue in mozilla-ai/otari (the submission asks for the link).

## 7. Decisions needed from Andri

| # | Decision | Default recommendation |
|---|---|---|
| D1 | Region of the account (US or EU) and tell us which. | US (160 models, includes nebius as a fallback provider), unless he already signed up in EU. |
| D2 | Create the main key as a SERVICE key, `verdict-desk`, expiry 2026-11-13. | Yes; it cannot be changed later. |
| D3 | Do the panel setup himself from a checklist (budgets, ceilings, routing policies, guardrail mandate if available, MCP server registration, tool policies): about 15 minutes. | Yes. Do not hand a login to an agent. |
| D4 | Public host for the MCP and guardrail endpoint at zero cost (free static or function host, or a tunnel from a machine he controls). | Needed only if Q12 or Q13 show hosted has no guardrail service or if we want our own MCP tool. Decide after F1. |
| D5 | BYOK with the team NIM key. | No, unless tool spend threatens the 20 USD. |

Panel checklist for D3, in order: (1) Spend and budgets: create `vd-analyst-day` (0.25 USD, 200 requests, calendar day), `vd-drill-tiny` (0.0005 USD), `vd-org-lifetime` (15 USD, no period) and attach the last as an organization ceiling; (2) API keys: create `verdict-desk` as a service key with `vd-analyst-day`, then attach a calendar-day ceiling to it; (3) Routing: create `vd-judge` (fallback) and `vd-judge-drill`; (4) Tools: confirm web search and code execution are enabled for the workspace; (5) Guardrails: mandate `prompt-injection` in monitor mode if the profile is offered; (6) MCP servers: register the verify server when D4 is settled; (7) copy the workspace id and key id into the F1 notes (ids are not secrets; the key secret goes only to the cloud environment as `OTARI_API_KEY`).

## 8. Order of attack (ordem de ataque), most certain first

| # | Feature | Certainty on hosted | Why | Needs | Switch if it fails |
|---|---|---|---|---|---|
| 1 | Several models in distinct roles | Very high | Public catalog shows 80-plus open-weight chat models with tools and JSON; plain key and request field only [LIVE] | Key only | None needed |
| 2 | Routing and fallback | High | Catalog-id fallback needs no setup; the routing surface and `/organizations/me/routing-policies` exist in the hosted spec [SPEC] | Panel for the policy and the drill | Catalog-id fallback alone, with the failure forced through an unlisted first target |
| 3 | Budgets and spend limits | High | Budgets, ceilings and wallet exist in the hosted spec; refusal is a documented 403 with headers [SPEC, DOC] | Panel once | Wallet `402` plus client-side cycle cap |
| 4 | Budget per user | High | Service key and end users are in the hosted key-creation schema; read-back uses the key itself [SPEC] | Panel for the key and budget | Tags plus `usage/summary` by `by_user` |
| 5 | Code execution | Medium | A hosted pool-backed sandbox is documented; policy route exists in the spec; price and enablement unknown [DOC, SPEC] | `GET /tools`, one probe | Falls to the reserve only if the probe fails |
| 6 | Web search or fetch | Medium | Policy routes and search-tool routes exist in the spec; whether the hosted deployment has a search backend is unknown; fetch is off by default in the docs | `GET /tools`, one probe; maybe an external search key | Direct `POST /search`; if no backend, drop to the reserve |
| 7 | Guardrails | Medium-low | Mechanism is request-level and simple, but the service behind a profile may not exist on hosted | A real request; maybe a public `/validate` host | Reserve: swap in code execution as a titular |
| 8 | MCP | Medium-low | Inline servers are supported, but a public server is mandatory and the hosted gateway must reach it | Public host (D4) or a reachable public server | Caller-orchestrated path with a stored server; else the reserve is lost |

Because items 5 and 6 are probably stronger than 7 and 8, F1 should settle the question and PLANO should be re-ranked before F3: the target is six features with receipts, and the strongest six at that point are 1 to 4 plus whichever two of 5, 6, 7 and 8 pass their probes.

## 9. Questions only the logged-in account answers (test in F1)

Each has the command and what the answer decides. Do the cheap ones first; stop at the first probe that costs more than 0.05 USD and record its price.

| # | Question | How to test | Decides |
|---|---|---|---|
| Q1 | Which region and base URL does the key belong to, and does a wrong-region call give 421? | `GET /api/v1/regions`; call `/models` on both bases | D1, the base URL in the environment |
| Q2 | What does `GET $BASE/models` list for this key, and do the four role models appear with the selectors in 1.4? | `curl -H "Authorization: Bearer $KEY" $BASE/models` and compare with the public catalog | Role assignment |
| Q3 | On a real response, which of `Otari-Request-ID`, `-Provider`, `-Model`, `-Attempt-Count`, `-Fallback`, `-Response-Duration-Ms`, `Server-Timing` and `usage.cost_usd` exist on 0.35.0? | One 50-token chat call with `curl -D -` | Whether section 2.1 is real or the client needs usage-log enrichment instead |
| Q4 | What is the wallet after sign-up? Is the 20 USD credit automatic, applied on the first wallet read, or a voucher? What does an empty wallet return? | `GET /wallet` (session); `POST /wallet/vouchers/redeem` only if a code was mailed | The real budget and the kill-switch numbers |
| Q5 | Does a plain `tk-` key work on the management routes? | `GET /organizations/me`, `/organizations/me/budgets`, `/organizations/me/routing-policies` with the key | Whether agents can configure, or Andri clicks |
| Q6 | Does the org routing route accept hosted-provider targets, and does a bogus first target move on to the real one with `Otari-Fallback: true`? | Create `vd-judge-drill`; call it once | Feature 2 drill design |
| Q7 | Does the service key create end users on first use, return the budget echo header, and refuse with the expected 403 shape? | Call with `user: analyst-bull`; read `/end-users?external_id=analyst-bull`; call with `max_tokens` 8000 against `vd-drill-tiny` | Features 3 and 4 |
| Q8 | Is an `api_token` ceiling refusal reported with `Otari-Budget-Scope: api_token`, and does the org lifetime ceiling stop calls? | Create ceilings; trip the tiny one | Kill switch |
| Q9 | Which tools are `available` on hosted, and what do the workspace policies say? | `GET $BASE/tools`; `GET /workspaces/{id}/web-search`; `GET /workspaces/{id}/code-execution-policy` | Order of attack items 5 and 6 |
| Q10 | Does in-loop search work, with what per-call price and does `otari_tool_calls` appear? Does direct `POST /search` work? | One `otari_web_search` call with `max_uses: 1`; one direct search; read `usage.cost_usd` and `pricing_breakdown` | Feature 6 and the 20 USD plan |
| Q11 | Does code execution run on hosted, on which route do the result blocks appear (Responses or Messages), what is the price, does the sandbox have network, what is the largest prompt it digests? | One probe: "print the SHA-256 of this JSON" on `/responses`, then `/messages` | Feature 7 |
| Q12 | Does the `prompt-injection` guardrail exist on hosted? What does `Otari-Guardrails` look like? Does `on: ["output"]` do anything? Which entries does `GET /tool-settings/guardrails/catalog` list? | Benign and hostile request with `mode: monitor`; same with `on: ["output"]`; then the catalog read | Feature 5 and whether D4 is needed |
| Q13 | Can the hosted gateway reach a public MCP URL, does inline `mcp_servers` work on Chat Completions, and what do the trace and the Messages beta blocks show? Does registering a server return `health: ok`? | Probe with a public no-auth MCP URL; later with ours; `GET /traces/requests/{id}` | Feature 8, D4 |
| Q14 | Is `GET /traces/requests/{id}` readable with the inference key, does `Otari-Conversation-Id` group a cycle into one session, and do spans show routing attempts, guardrail checks and tool calls? | After two calls with the same conversation id, read the trace | Receipt enrichment |
| Q15 | Can the inference key read `GET /api/v1/usage?request_group_id=...` and `GET /api/v1/usage/requests/{id}`? | Both calls with the key | How attempts and tool lines enter the dossier |
| Q16 | What rate limit does hosted enforce (429 and its `Otari-Rate-Limit-Rule`)? | Short burst of 5 calls | `MISTRAL_MIN_INTERVAL` |
| Q17 | Is `Idempotency-Key` honored (replay header)? | Same call twice with the same key | Retry safety |
| Q18 | Does `GET /providers/catalog` list a NIM-compatible provider, and would the org key accept NIM's `api_base`? | Read only; do not add the key without D5 | BYOK option |
| Q19 | Is the organization owner or admin role what the signed-up account holds, and does the account get a default workspace? | `GET /organizations/me`, `/workspaces` | Everything marked ADMIN |
