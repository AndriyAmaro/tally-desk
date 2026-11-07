<p align="center">
  <a href="web/cycle.html"><img src="docs/readme/otari-header.svg" width="100%" alt="Tally Desk on Otari: a paper-trading agent whose every model call goes through the Otari gateway and leaves a receipt that anyone can re-verify offline"></a>
</p>
<p align="center">
  <img src="docs/readme/otari-figures.svg" width="100%" alt="Figures counted from the ledger: Otari-era dossiers, sealed receipts, provider and model routes that answered, resources proven, backup-route answers">
</p>
<p align="center">
  <img src="docs/readme/otari-features.svg" width="100%" alt="The eight Otari resources with the status the audit gives each one: all eight proven from sealed receipts">
</p>
<p align="center">
  <a href="web/cycle.html"><img src="docs/readme/otari-button-cycle.svg" height="40" alt="Call-by-call view"></a>
  <a href="web/verify.html"><img src="docs/readme/otari-button-verify.svg" height="40" alt="Verify a record"></a>
  <a href="#proofs-resource-by-resource"><img src="docs/readme/otari-button-audit.svg" height="40" alt="Proofs table"></a>
  <a href="docs/FEEDBACK_LOG.md"><img src="docs/readme/otari-button-feedback.svg" height="40" alt="Feedback to Otari"></a>
</p>

**Tally Desk decides on paper trades and answers for every model call it makes.** It is an autonomous trading agent that runs on [Otari](https://otari.ai), the gateway that sits between an app and its model providers. Each of its four decision roles (a bull analyst, a bear analyst, a citation checker and a judge) runs on a different open-weight model, and every call, to a model or to a tool, leaves a receipt: who asked, which model answered through which provider, what it cost, how it stopped. The receipts are sealed into a hash-chained ledger that anyone can verify and replay offline. No real-money order is ever placed.

The trading agent is the reference workload. What the project really builds is a tamper-evident receipt layer for an agent that calls models through a gateway: the receipts, the hash chain, the replay and the verifier know nothing about markets. [`docs/architecture.md`](docs/architecture.md) splits the code into four parts and says, function by function, what a second agent would plug in.

<img src="docs/readme/icons/why.svg" width="44" height="44" alt="">

## Why it matters

An agent that acts alone has to be able to answer for each thing it asked a model to do. Logs written by the agent itself are not an answer, because the agent wrote them. Here the gateway's own response headers and usage records are captured on every call, the verdict is recomputed from frozen evidence by plain code, and the whole record is checked by a script or a browser tab with nothing to trust but a hash. A judge, an auditor or a stranger can read one cycle call by call and confirm that the model it names is the model that answered.

<img src="docs/readme/icons/how.svg" width="44" height="44" alt="">

## How it works

<p align="center">
  <img src="docs/readme/otari-flow.svg" width="100%" alt="One cycle in eight steps: evidence, bull and bear analysts, citation checker, judge, rules engine, sandbox re-derivation and MCP price check, sealed dossier in the hash chain, outside witnesses. Chips mark which steps go through which Otari feature.">
</p>

1. **Evidence** is gathered from public market data and news, then frozen. Otari's web search and fetch tools add one sourced page.
2. **Bull and bear** argue from the same evidence, each on its own open-weight model.
3. **The citation checker** re-reads every claim against the evidence it cites. From rules v7 its marks have a consequence for the verdict (see the details below).
4. **The judge** weighs only the claims that were validated. Every call that reads outside text is screened by a guardrail service of our own, mandated in the Otari dashboard (details below), and Otari reports its verdict in each receipt.
5. **The rules engine** is ordinary deterministic code. It turns the judge's lean into a paper-trade verdict, and `replay` recomputes it from the frozen evidence.
6. **Two independent checks** run through Otari: the verdict is re-derived in Otari's code sandbox and compared with the local run, and prices are read through an MCP server and compared with the market data.
7. **The sealed dossier** holds the evidence, the verdict and one receipt per call, linked by hash to the one before it.
8. **Outside witnesses** let a reader check the head of the chain without trusting the operator: OpenTimestamps (Bitcoin) on each cycle, and the public Sigstore log for the heads published up to dossier 791.

Three safeguards sit around those steps, each with its own receipts or sealed fields:

- **Role fallback chains.** If the gateway refuses the model a role asked for for a model-side reason (HTTP 402 `pricing_required`, an unknown or unavailable model, a 5xx that outlived the retries), the app asks the next open-weight model in that role's list in `config/otari.json`, the last one a DeepSeek model. A budget refusal or the cycle cap never moves on. The receipt records which entry answered and why the earlier ones were skipped, and the audit counts these apart from Otari's own provider failover.
- **The closure gate.** Before a dossier is appended, nine deterministic checks inspect it: both analysts answered, every call has a receipt, the evidence is the evidence collected, the verdict replays, and so on. If a hard check fails, nothing is appended, the refusal is written to `ledger/closure-refusals.jsonl`, and the next cycle goes on. The outcome is sealed in the dossier and `replay` recomputes it.
- **The source drift seal.** Each cycle reads again one web page that an earlier dossier cited and records whether the passage it froze has changed. It is a freshness receipt that no rule reads, so it cannot move a verdict.

<img src="docs/readme/icons/score.svg" width="44" height="44" alt="">

## Score map

| Rubric line | What we did | Where to verify |
|---|---|---|
| Open-weight models (30) | Four roles (bull, bear, judge, citation checker) each answer on a different open-weight model, and every call in the Otari era went to an open-weight model | [Proofs](#proofs-resource-by-resource) row 1, [model scoreboard](#open-model-scoreboard), [models and roles](#models-and-roles) |
| Otari features, 10 each, max 6 (60) | Six features proven from inside the app: routing and fallback, budgets, budget per user, web search and fetch, code execution, MCP | [Proofs](#proofs-resource-by-resource) rows 2, 3, 4, 6, 7, 8, each with request ids |
| Guardrails | **Proven.** Our own prompt-injection screen ([`services/guardrail/`](services/guardrail/README.md), on Cloudflare Workers) is mandated in the Otari dashboard in monitor mode, and Otari evaluates it on every call that reads outside text. A live drill in block mode served a normal headline and refused an injection with a 403 | [Proofs](#proofs-resource-by-resource) row 5 |
| Working app (5) | One command records a cycle through Otari; `verify` and `replay` re-check the whole chain offline | [Quickstart](#quickstart) and [`web/cycle.html`](web/cycle.html) |
| Our feedback to Otari | Every real snag met with the gateway, with symptom, steps to reproduce and status | [`docs/FEEDBACK_LOG.md`](docs/FEEDBACK_LOG.md) |

Receipts are also checked against Otari's own usage log with free GET calls. The latest run, [`docs/evidence/otari-reconcile-798-801.txt`](docs/evidence/otari-reconcile-798-801.txt), compared 54 request ids and found no divergence in status, tokens, cost or usage rows. The 20 ids of dossier 801 belong to the key of the scheduled job and can only be checked with that key.

<img src="docs/readme/icons/models.svg" width="44" height="44" alt="">

## Models and roles

Each role is billed to its own end user and asked for one open-weight catalog id. The "Answered by" column is not the config: it is counted from the sealed receipts, so a route that really answered, and the provider that carried it, shows up whatever the config says.

<!-- otari:models:start -->
**Bull analyst** &nbsp;|&nbsp; argues the long case

- Asked for (open weights): `openai/gpt-oss-120b` (OpenAI)
- Answered by, from the receipts: `deepinfra:openai/gpt-oss-120b` (32), `ovhcloud:gpt-oss-120b` (2)
- Why this model: an open-weight 120B model from a different vendor than the bear, so the two sides do not echo one model

**Bear analyst** &nbsp;|&nbsp; argues the short case

- Asked for (open weights): `nvidia/nemotron-3-super-120b-a12b` (NVIDIA)
- Answered by, from the receipts: `bedrock:nvidia.nemotron-super-3-120b` (34)
- Why this model: an open-weight 120B model from another vendor, so a claim's side can be traced to one model

**Judge** &nbsp;|&nbsp; weighs the validated claims

- Asked for (open weights): `deepseek/deepseek-v4-flash-0731` (DeepSeek)
- Answered by, from the receipts: `deepinfra:deepseek-ai/DeepSeek-V4-Pro` (23), `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731` (11)
- Why this model: a fast model by default; the router may take the larger route of the same family only while the judge's end user is under 80 percent of its daily budget

**Citation checker** &nbsp;|&nbsp; re-reads each claim against the evidence it cites

- Asked for (open weights): `google/gemma-3-27b-it` (Google)
- Answered by, from the receipts: `nebius:google/gemma-3-27b-it` (34)
- Why this model: small on purpose, since it only compares a claim with its source; a Llama 4 route was tried first and dropped for 504s (feedback log, entry 1)

**Narrator** &nbsp;|&nbsp; reads news headlines in batches

- Asked for (open weights): `alibaba/qwen3-235b-a22b-instruct-2507` (Alibaba)
- Answered by, from the receipts: `deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507` (432), `bedrock:qwen.qwen3-235b-a22b-2507-v1:0` (27)
- Why this model: a large open-weight model for the highest-volume reading job, sent as a plain catalog id so Otari's own provider failover applies

**Tool drivers** &nbsp;|&nbsp; search, fetch, sandbox, MCP

- Asked for (open weights): `deepseek/deepseek-v4-flash-0731` (DeepSeek)
- Answered by, from the receipts: `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731` (146)
- Why this model: the tool does the work and the model only drives it, so a small fast model is enough
<!-- otari:models:end -->

<img src="docs/readme/icons/start.svg" width="44" height="44" alt="">

## Quickstart

```bash
export OTARI_API_KEY=...   # from the Otari dashboard; never commit it

# 1. record one cycle, every call through Otari
VERDICT_DESK_APPEND_PUBLIC=1 python3 -m verdict_desk decide-v7 BTCUSDT --config config/voices.json

# 2. the chain is intact, and every verdict is re-derived offline
python3 -m verdict_desk verify && python3 -m verdict_desk replay

# 3. the eight resources, and each receipt against Otari's usage log
python3 -m verdict_desk otari-audit --reconcile
```

Python 3 and its standard library are enough; there is no install step. To see a cycle rendered, run `python3 -m http.server` in the repository root and open `http://localhost:8000/web/cycle.html`.

<img src="docs/readme/icons/verify.svg" width="44" height="44" alt="">

## Verify without trusting us

You need no key, no account and none of our word for the first four.

- **Offline, from a clone:** `python3 -m verdict_desk verify` recomputes every hash and link of the chain, and `python3 -m verdict_desk replay` re-derives each verdict from its frozen evidence. An edited or forged dossier fails one of the two.
- **In your browser:** [`web/verify.html`](web/verify.html) takes a dossier, recomputes its hash, checks its link in the chain and re-derives the verdict, with JavaScript that is tested against the Python implementation.
- **Try to cheat it:** [`web/verify.html?real=1#cheat`](web/verify.html?real=1#cheat) loads the newest dossier and lets you change one thing in an in-memory copy: a call's cost, the verdict, a sentence of a claim, or the link to the previous record.
  The same three checks run again and name which one failed, with the recomputed hash beside the stored one; Undo puts the original back and it verifies again. Nothing is uploaded or saved.
- **Call by call:** [`web/cycle.html`](web/cycle.html) shows one cycle as a timeline of receipts: the model asked for and the model that answered, provider, cost, latency, fallback, request id, budget headroom and who saw each prompt. [`web/index.html`](web/index.html) is the landing page.
- **Against Otari:** `python3 -m verdict_desk otari-audit --reconcile` compares each receipt with Otari's own usage record by request id (free GET calls, a key is required).

The pages are static: serve the repository as shown in the quickstart to see them rendered.

<img src="docs/readme/icons/proofs.svg" width="44" height="44" alt="">

## Proofs, resource by resource

<!-- otari:proofs:start -->
| # | Resource | Status | Dossier |
|---|---|---|---|
| 1 | Several open-weight models in distinct roles | **PASS** | 826 |
| 2 | Routing and fallback | **PASS** | 822 |
| 3 | Budgets and spend limits | **PASS** | 826 |
| 4 | Budget per user | **PASS** | 826 |
| 5 | Guardrails | **PASS** | 826 |
| 6 | Web search and fetch | **PASS** | 820 |
| 7 | Code execution | **PASS** | 826 |
| 8 | MCP | **PASS** | 826 |

**8 resources: 8 proven from sealed receipts + 0 from drills (not sealed) + 0 NOT PROVEN.**

#### 1. Several open-weight models in distinct roles

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> bull, bear, judge and checker each answered on a different open model: bull openai/gpt-oss-120b, bear nvidia.nemotron-super-3-120b, judge deepseek-ai/DeepSeek-V4-Pro, checker google/gemma-3-27b-it

Request ids:

- `2aecc954-4c13-48d7-9d54-096dc6dedea8`
- `3ba742ad-100e-4be9-8af9-5f2bea8fee39`
- `26cf3592-cf34-4e20-94c8-1bb27b957557`
- `41a0a35d-9df6-4a83-b790-1dfe4b84228e`

Code: `config/otari.json`, `llm.py`

#### 2. Routing and fallback

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 822

> 29 real calls were answered by a backup route after the first attempt did not succeed (Otari-Fallback true, attempt count 2), in dossiers 800, 801, 802, 805, 822; newest: a bull call sent as openai/gpt-oss-120b (the plain catalog id, no routing policy of ours, so this is Otari's own provider failover and not the vd-judge policy) was answered on its second attempt by ovhcloud:gpt-oss-120b. Besides, the failover canary of dossier 826 is the deliberate test of the same route: policy vd-judge-drill served by deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731 after 2 attempts (Otari-Fallback true). All sealed in the ledger

Request ids:

- `8eca518a-c683-4486-b77e-c887fba81e85`
- `08856bea-76c3-4819-808d-d6ea75ca619d`
- `298e0bd8-2976-46d8-91a1-52512e5d44ed`
- `1dd7daa2-c2ea-4c8f-81b7-aec04e383a71` (drill, not sealed)

Code: `otari_canary.py`

#### 3. Budgets and spend limits

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> budget-aware routing: Otari's spend for the judge's end user (USD 0.003463 of USD 0.10) was read and sealed, and the router allowed the hard route on it; per-cycle caps sealed too. Otari's own 403 budget_exceeded refusal is shown by the drill only (not sealed)

Request ids:

- `26cf3592-cf34-4e20-94c8-1bb27b957557`
- `d9b44c76-c18e-4f75-8ed1-4a5781e0d8e7` (drill, not sealed)

Code: `llm.read_headroom`, `llm.route_decision`

#### 4. Budget per user

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> 10 of the 10 end users found sit on the vd-analyst budget; 10 of 10 asked were found (vd-bull, vd-bear, vd-judge, vd-checker, vd-narrator, vd-searcher, vd-sandbox, vd-mcp, vd-fetcher, vd-canary) (read back from Otari, sealed in the dossier); Otari echoed the budget on the receipts

Request ids:

- `f7493042-d6ca-4a0b-899d-37902831efba`
- `57a8f8cf-a818-4d47-9d2d-9e2a2520cec5`
- `ee72e7cc-65e4-4f2b-97ad-3b8f0f0ed1b2`
- `f9df93d4-4a5c-435d-be4e-45bd75293afb`

Code: `llm._end_user`, `llm.read_headroom`

#### 5. Guardrails

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> the custom profile tally-web-injection was evaluated by Otari on 239 sealed calls (239 passed, 0 flagged) in dossiers 821, 822, 823, 824, 825, 826 and earlier; newest: dossier 826, narrator call, request f7493042-d6ca-4a0b-899d-37902831efba, header status evaluated, mode monitor, valid true, score 0. The guardrail drill (block mode, not sealed, not counted) served a benign headline with an evaluated verdict and refused a malicious one with 403 guardrail_violation (score 0.985)

Request ids:

- `f7493042-d6ca-4a0b-899d-37902831efba`
- `57a8f8cf-a818-4d47-9d2d-9e2a2520cec5`
- `ee72e7cc-65e4-4f2b-97ad-3b8f0f0ed1b2`
- `34854aa9-88ba-4992-a6d3-63cb2a825e96` (drill, not sealed)
- `eda1a945-0631-4704-b6ef-4793b490d999` (drill, not sealed)

Code: `llm.guardrail_request`, `llm.parse_receipt`, `services/guardrail/`

#### 6. Web search and fetch

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 820

> one search and one page fetch through Otari's tools; 3 sources and an excerpt frozen (excerpt_verified True); the analysts cited 1 of 3 source titles and 1 of 1 page excerpts in the claims the judge weighed

Request ids:

- `a5446f0e-f2e5-41cf-9edd-44a4a8f84bd8`
- `aa5e54d6-39d7-4203-b7f5-a2060f431219`

Code: `otari_web.py`

#### 7. Code execution

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> the verdict re-derived in Otari's sandbox: its own result block is identical to the local run, the nonce proof matches and the code that ran is the code sent

Request ids:

- `9fcf7c45-024b-43ac-9300-203ea831511a`

Code: `sandbox_rederive.py`

#### 8. MCP

**PASS** &nbsp;|&nbsp; Sealed receipts in the ledger &nbsp;|&nbsp; dossier 826

> prices read from coingecko through Otari's MCP loop, from the server's own result block (check: unavailable)

Request ids:

- `41c06a6b-16a9-4b01-8791-e861e6e0ffc0`

Code: `otari_mcp.py`
<!-- otari:proofs:end -->

`otari-audit` prints this table from the ledger. A proof "sealed" lives in the hash-chained ledger and is replayed offline; a "drill" proof lives in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl) and is not sealed. The guardrail row is counted strictly. It turns to PASS only when sealed receipts carry an `Otari-Guardrails` entry with `status: evaluated` for the custom profile `tally-web-injection`; a header with status `unavailable` or `inconclusive`, or an evaluated verdict of another profile, is never counted, and a drill is named beside the row but not counted. Before the screen we built went live (dossier 817), the row stayed NOT PROVEN on purpose: the hosted gateway had no guardrails service, so every call came back with `valid: null` and, from dossier 805, `status: unavailable`, `reason: not_configured`.

<img src="docs/readme/icons/board.svg" width="44" height="44" alt="">

## Open-model scoreboard

<details>
<summary>Every call of the Otari era, grouped by the model that answered</summary>

<!-- otari:board:start -->
| Model (provider:model) | Calls | OK | Refusals / failures | p50 ms | p95 ms | Fallbacks | Accepted claims | USD per claim |
|---|---|---|---|---|---|---|---|---|
| `deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507` | 432 | 432 | - | 20145 | 29071 | 0 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731` | 186 | 186 | - | 2921 | 10418 | 29 | - | - |
| `bedrock:nvidia.nemotron-super-3-120b` | 34 | 34 | - | 1980 | 2824 | 0 | 134 | 0.000163 |
| `nebius:google/gemma-3-27b-it` | 34 | 34 | - | 5654 | 14097 | 0 | - | - |
| `deepinfra:openai/gpt-oss-120b` | 32 | 32 | - | 23739 | 28870 | 0 | 126 | 0.000087 |
| `bedrock:qwen.qwen3-235b-a22b-2507-v1:0` | 27 | 27 | - | 7059 | 13228 | 27 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Pro` | 23 | 23 | - | 2206 | 6808 | 0 | - | - |
| `(no answer) alibaba/qwen3-235b-a22b-instruct-2507` | 8 | 0 | 409 x7, pricing_required x1 | - | - | 0 | - | - |
| `ovhcloud:gpt-oss-120b` | 2 | 2 | - | - | - | 2 | 8 | 0.000249 |
| `(no answer) nvidia/nemotron-3-super-120b-a12b` | 1 | 0 | pricing_required x1 | - | - | 0 | - | - |
| `(no answer) openai/gpt-oss-120b` | 1 | 0 | pricing_required x1 | - | - | 0 | - | - |
<!-- otari:board:end -->

Every call of every Otari-era dossier, grouped by the model that answered. The fallbacks in this table have two sources. Real traffic: calls that Otari re-routed to a second provider when the first did not answer (receipts with `Otari-Fallback: true` and attempt count 2). They are mostly narrator batches, with an occasional analyst call, all sent as plain catalog ids with no routing policy of ours, so this is Otari's own provider failover and not the `vd-judge` policy; the audit row above counts them from the ledger. The deliberate test: the failover canary, one small call per cycle through a policy built to fail over, which shows the backup route working even when no real call needs it.

</details>

<img src="docs/readme/icons/learn.svg" width="44" height="44" alt="">

## What we learned about Otari

Putting a real agent behind the gateway turned up friction worth reporting. Every instance is written down in the [feedback log](docs/FEEDBACK_LOG.md) with the symptom, the steps to reproduce, what the docs say and what we did about it. A few entries an Otari engineer may want first:

- a slow model answers with an edge 504 and an HTML page, with no Otari request id (entry 1);
- the `user` field with an ordinary key is refused with a message that does not name the cause (entry 2);
- `usage.cost_usd` is rounded to six decimals, which erases the cost of small calls (entry 3);
- the `Otari-Guardrails` header says a guardrail is attached but not whether it ran, and `valid` stays null (entries 11 and 13);
- MCP activity is invisible on Chat Completions, so the proof needs a streamed Messages request (entry 16).

The log also records what worked on the first try (entry 19), since that is feedback too. No GitHub issue has been opened for these yet, so the log is the one place to read them.

<img src="docs/readme/icons/details.svg" width="44" height="44" alt="">

## Details

<details>
<summary>One ledger, two eras</summary>

`ledger/ledger.jsonl` is a single hash chain. Dossiers 1 to 791 were recorded by the earlier version of this project, before it ran on Otari (models behind other providers, no gateway receipts). **The Otari era starts at dossier 792**: its `prev_hash` is the hash of dossier 791, so the chain was continued, not restarted, and `python3 -m verdict_desk verify` checks all of it. Every dossier from 792 carries the Otari run summary and a receipt for each call; the audit, the scoreboard and `web/cycle.html` read only those. Dossiers 792 to 800 were recorded from a local machine (by hand or by the local runner); from dossier 801 on, a scheduled GitHub Actions job (the workflow `otari-cycle`, hourly) records the cycles.

</details>

<details>
<summary>The checker has a consequence (rules v7)</summary>

Four models take part in every decision: a bull and a bear analyst, a judge, and a **citation checker** that re-reads each validated claim against the evidence it cites. Through dossier 801 (rules v6) the checker was advisory. From rules v7 its marks are part of the verdict: if the judge's lean rests on a claim the checker marked unsupported, that lean carries no weight (the judge only breaks ties, so this can send a close call back to no position and never opens one). The marks are frozen in the dossier, the rule is written in the verdict's `policy`, the outcome is sealed as `verdict.checker_gate`, and the replay recomputes it, so editing a mark fails the replay. A missing or failed checker changes nothing. Dossiers recorded by the scheduled job after this change use `decide-v7`; dossiers 792 to 801 stay v6 and still replay to the same verdicts.

</details>

<details>
<summary>Guardrails: the screen we built, and what it proves</summary>

Our agents put untrusted text in prompts: news headlines, fetched web pages, Telegram previews. A prompt-injection screen on that text is a real need. The built-in `prompt-injection` profile has no service behind it on the hosted Otari gateway (it gave `valid: null` on every call; feedback log, entries 11 and 13), but Otari lets an organization mandate a profile that points at a service of its own, with a bearer credential, so we wrote one.

- **The service** is [`services/guardrail/`](services/guardrail/README.md): a zero-dependency JavaScript service for Cloudflare Workers (Vercel functions also work) that speaks Otari's `POST /validate` contract with the profile `tally-web-injection`. It is **rules, not a model**: weighted patterns for instruction override, system prompt exfiltration, role or format hijack, requests to send data to a URL, and hidden characters, with a score from 0 to 1 and `valid: false` at 0.7 or more. It is tested on 48 injection samples and 80 benign ones, and on every text of this repository's ledger (news titles, page excerpts, the evidence the analysts read, narrative voices, claims; 20,252 distinct texts) where it flags none, because a false positive blocks a cycle. It misses rephrased and translated attacks, and says so.
- **The switch** is `guardrails.enabled` in `config/otari.json`, and it is **true** since 2026-11-05. The requests of the analysts, the checker, the judge, the narrator and the search and fetch tools carry `guardrails: [{"profile": "tally-web-injection", "mode": "monitor", "on_unavailable": "monitor"}]`, so a service outage never stops a cycle. The endpoint and the credential live in an organization mandate in the Otari dashboard, never in this repository; the credential is a random token held as a Worker secret. Each receipt records the `Otari-Guardrails` entry (profile, mode, valid, score, status, reason), the cycle page shows a chip per call, and a soft closure check `guardrail_ran` applies to a cycle run with the switch on.
- **The drill** `python3 -m verdict_desk otari-drill guardrail` sends a benign and a deliberately malicious headline through the narrator in `block` mode and writes an evidence line. Run on 2026-11-05 with a service key: the benign headline was served (`valid: true`, score 0, `status: evaluated`, request `34854aa9-88ba-4992-a6d3-63cb2a825e96`), and the injection was refused with HTTP 403 `guardrail_violation`, score 0.985, no provider call (request `eda1a945-0631-4704-b6ef-4793b490d999`). The line is in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl); it is a drill, not sealed in the ledger.
- **What the audit says** is **PASS** (row 5), and it counts only sealed receipts whose header says `status: evaluated` for the custom profile; it names the dossiers and request ids. Production cycles run in monitor mode, so they record a verdict and never block; the block is what the drill shows.

</details>

<details>
<summary>Budgets: what Otari enforces, and what the app adds</summary>

Three different things, kept apart on purpose:

**1. Budget objects on the Otari side** &nbsp;|&nbsp; enforced by **Otari**

- What it is: `vd-analyst`, USD 0.10 a day per end user, attached to each role's end user (`vd-bull`, `vd-judge`, and so on) through the `Otari-End-User-Budget` header when the end user is created; `vd-org-lifetime`, a USD 1.50 a day ceiling for the organization; `vd-drill-tiny`, USD 0.01, for one drill.
- Proof in this repository: the echoed budget id on the receipts (audit row 4), and a `403 budget_exceeded` refusal (scope `user`) in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl).

**2. Otari's spend state, read by the app** &nbsp;|&nbsp; Otari holds the numbers, the **app only reads them**

- What it is: each cycle reads `GET /end-users` (spend plus reserve per end user), and the judge may use the larger model only while its end user is under 80 percent of its day. The read, with the HTTP status of every lookup, is sealed in `llm.otari.headroom`.
- Proof in this repository: audit row 3, from the newest dossier whose read of the judge worked.

**3. Cycle caps and runner limits** &nbsp;|&nbsp; **the app alone** (`llm._cycle_check`, `tools/otari_runner.py`)

- What it is: no new call starts once a cycle has spent USD 0.02 on the core roles or USD 0.06 on the tool roles; the runner stops at USD 2.00 in all and at 8 cycles a day.
- Proof in this repository: a refusal by a cap is a client-side receipt (`cycle_cap_exceeded`, `source: client`), never an Otari refusal.


What is and is not shown: Otari's own `403 budget_exceeded` refusal was shown on purpose in a drill against the USD 0.01 budget, and once for real at organization scope (below); neither is in a sealed production dossier. One production cycle was refused by an Otari budget: on 2026-11-05 at 11:04Z the gateway answered bull and bear with `403 budget_exceeded`, scope `organization`; the closure gate refused the dossier and sealed nothing, and the refusal is recorded in [`ledger/closure-refusals.jsonl`](ledger/closure-refusals.jsonl). The cycle caps are our own code and are not an Otari feature.

</details>

<details>
<summary>Who sees what: every host the code names</summary>

Every prompt goes to the gateway, then to the upstream provider named in the receipt (the page shows it per dossier). Every other host the code names:

<!-- otari:hosts:start -->
- **`otari.ai`**, the Otari gateway (base URL https://otari.ai/api/v1). Receives: every prompt of every model call, the tool requests (search, fetch, sandbox code, MCP), and our end-user ids. When: every cycle, with OTARI_API_KEY set.
- **`mcp.api.coingecko.com`**, CoinGecko's public MCP server. Receives: the TypeScript price query the model writes and Otari sends; our code never contacts it. When: once per cycle, by Otari.
- **`data-api.binance.vision`**, Binance public market data. Receives: our IP address and the symbol asked for (no key). When: every decision.
- **`www.okx.com`**, OKX public market data (funding rate). Receives: our IP address and the instrument asked for (no key). When: every decision.
- **`api.gdeltproject.org`**, GDELT news search. Receives: our IP address and the search words (no key). When: every decision, unless news is off.
- **`cointelegraph.com`**, public RSS feed. Receives: our IP address (a GET of the feed). When: every cycle, narrative voices.
- **`www.coindesk.com`**, public RSS feed. Receives: our IP address (a GET of the feed). When: every cycle, narrative voices.
- **`decrypt.co`**, public RSS feed. Receives: our IP address (a GET of the feed). When: every cycle, narrative voices.
- **`www.theblock.co`**, public RSS feed. Receives: our IP address (a GET of the feed). When: every cycle, narrative voices.
- **`cryptoslate.com`**, public RSS feed. Receives: our IP address (a GET of the feed). When: every cycle, narrative voices.
- **`t.me`**, Telegram public channel previews (t.me/s/...). Receives: our IP address (a GET of a public preview page). When: every cycle, narrative voices.
- **`search.sigstore.dev`**, the public Sigstore transparency log. Receives: a lookup of a published head hash. When: only in the witness checks.
- **`andriyamaro.github.io`**, the published site and ledger (read-only). Receives: a GET of public files. When: only in the verification tools.
- **`github.com`**, the source repository and its workflow evidence. Receives: git pushes of dossiers (branch otari) and reads of public pages. When: when a dossier is pushed or a witness is read.
<!-- otari:hosts:end -->

</details>

<details>
<summary>Architecture and file map</summary>

| Path | What lives there |
|---|---|
| `verdict_desk/` | the Python package: `llm.py` (the gateway client, receipts, fallback chains, budgets), `closure.py` (the closure gate), `source_drift.py`, `agents_v7.py` (rules v7; `agents.py` and `agents_v2` to `agents_v6` replay the older dossiers), `otari_*.py` (web tools, MCP, canary, drill, audit and report), `sandbox_rederive.py`, `dossier.py` and `canonical.py` (the hash chain) |
| `config/` | `otari.json` (roles, models, policies, caps), `voices.json` (news sources), `external_hosts.json` (every host the code names) |
| `ledger/` | `ledger.jsonl` (the chain), the head files, OpenTimestamps proofs in `timestamps/`, Sigstore bundles in `rekor/` |
| `web/` | the static pages: landing, call-by-call cycle, in-browser verifier, chain dashboard, witnesses, the record Q and A, the lab and the market voices |
| `tools/` | `otari_runner.py` (the cycle runner the scheduled job calls) and `otari_catalog_check.py` |
| `scripts/` | generators for the README blocks and the cards in `docs/readme/` |
| `docs/` | the [feedback log](docs/FEEDBACK_LOG.md), [architecture](docs/architecture.md), the [lineage](docs/HISTORY.md) and the [evidence files](docs/evidence/) |
| `tests/` | the Python and Node suites |

The README blocks (proofs, models, scoreboard, hosts) and the figures card are rewritten from the ledger by `python3 scripts/refresh_otari_readme.py` on every cycle. The header, the buttons and the cycle diagram are drawn by `python3 scripts/otari_cards.py`. Nothing generated is edited by hand.

</details>

<details>
<summary>Tests and how to run them</summary>

```bash
env -u OTARI_API_KEY python3 -m unittest discover -s tests -q   # the Python suite, no network, no key
node --test tests/*.test.js                                      # the browser code (verifier, cycle page, stats)
python3 scripts/refresh_otari_readme.py --check                  # the README blocks and the figures card match the ledger
```

The suite guards the chain (edit, delete, swap, forge and truncate attacks must be caught), the Python and JavaScript parity of the verifier and the scoreboard, the audit, and these README blocks. No test spends on Otari or sends a transaction.

</details>

<details>
<summary>Limits and honest caveats</summary>

- **Paper trading only.** No order is routed anywhere and there is no key with trading rights. The verdicts are simulated positions, not advice.
- **Guardrails run in monitor mode in production.** They record a verdict on every screened call and never stop a cycle; the block is shown in a drill, not in a sealed cycle. The detector is rules, not a model: it misses rephrased and translated attacks, and it flags an article that quotes an attack verbatim. Before our own service, the built-in `prompt-injection` profile gave `valid: null` on every call (since dossier 805 the header says `unavailable, not_configured`; feedback log, entries 11 and 13).
- **Otari's budget refusal is shown by a drill and was met once in production.** The cycle of 2026-11-05 11:04Z was refused at organization scope and the closure gate sealed nothing; the per-cycle caps are our own code, not an Otari feature.
- **A hash chain shows integrity, not honesty at collection time.** The ledger proves a record was not edited afterwards. The outside witnesses and public market data narrow the gap, they do not close it.
- **Reconciliation with the scheduled job's key is partial.** The ids of dossier 801 belong to the key the CI job uses, so a local key cannot look them up; they can only be compared with that key. The rest agree with Otari's usage log, which does not carry the model, so the model is not compared.
- **Earlier era.** Dossiers before 792 have no gateway receipts and are not part of the Otari audit.
- **Role fallback chains are new.** They are tested against a fake gateway; until a live cycle has a receipt with `role_fallback`, no real call has used one. The audit counts them apart from Otari's failover and does not use them as proof of routing.
- **No on-chain anchor.** The earlier project also anchored heads on the Sepolia testnet; that workflow stays in its frozen repository and nothing here uses a chain.

</details>

## More

[Case study](docs/CASE_STUDY.md) (one page: what it does, five numbers, what was hard), [architecture of the code](docs/architecture.md) (with a section on bringing another agent), [bring your own agent](docs/bring-your-own-agent.md) (a `seal()` API, one runnable example in [`examples/`](examples/README.md)), [lineage of the project](docs/HISTORY.md) and [evidence files](docs/evidence/).

<img src="docs/readme/icons/about.svg" width="44" height="44" alt="">

## About this repository

It started as a snapshot of the earlier project, [AndriyAmaro/verdict-desk](https://github.com/AndriyAmaro/verdict-desk) at commit `c65bab9`, which is kept frozen as it was; [`docs/HISTORY.md`](docs/HISTORY.md) says what carried over and what is new. The Otari work lives on the `otari` branch. The scheduled job `otari-cycle` is defined on `main` and runs `tools/otari_runner.py` against the `otari` branch. The other scheduled workflows belong to the earlier project and stay in its repository; `test_workflows` and `test_rekor_workflow`, which read those files, skip cleanly when they are absent.

## License and credits

MIT, see [`LICENSE`](LICENSE). Built by Andri Amaro with the help of an AI coding agent. The AI assistance is disclosed here on purpose.
