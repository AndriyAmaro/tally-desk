# Verdict Desk on Otari: a trustworthy agent, open models only, a receipt for every call

An agent that decides on paper trades must answer for every model call it makes. Here every call goes through the [Otari](https://otari.ai) gateway, each role runs on an **open-weight model**, and **each call leaves a receipt** (who asked, which model answered through which provider, what it cost, how it stopped) sealed in a hash-chained ledger that anyone can re-verify and replay offline.
Every cycle also proves two Otari features from inside the app: a **failover canary** shows the backup route working before the judge is trusted with it, and the cycle reads Otari's own **spend state** per end user, so the judge may use the costlier model only while its budget has room.
Receipts are checked against Otari's own usage log with free GET calls: the latest run, [`docs/evidence/otari-reconcile-798-801.txt`](docs/evidence/otari-reconcile-798-801.txt), compared 54 request ids and found no divergence in status, tokens, cost or usage rows (the 20 ids of dossier 801 belong to the key of the scheduled job and can only be checked with that key). Paper trading only: no real-money order is ever placed.

## Quickstart (Otari)

```bash
export OTARI_API_KEY=...   # from the Otari dashboard; never commit it
VERDICT_DESK_APPEND_PUBLIC=1 python3 -m verdict_desk decide-v6 BTCUSDT --config config/voices.json   # one cycle, every call through Otari
python3 -m verdict_desk verify && python3 -m verdict_desk replay   # the chain is intact and every verdict is re-derived offline
python3 -m verdict_desk otari-audit --reconcile   # the eight resources, and each receipt against Otari's usage log
python3 -m http.server   # then open http://localhost:8000/web/cycle.html
```

**See one cycle call by call: [`web/cycle.html`](web/cycle.html)** (a timeline of every call with its receipt, the canary, the budget headroom, who saw each prompt, and a scoreboard of the open models).

## One ledger, two eras

`ledger/ledger.jsonl` is a single hash chain. Dossiers 1 to 791 were recorded by the earlier version of this project, before it ran on Otari (models behind other providers, no gateway receipts). **The Otari era starts at dossier 792**: its `prev_hash` is the hash of dossier 791, so the chain was continued, not restarted, and `python3 -m verdict_desk verify` checks all of it. Every dossier from 792 carries the Otari run summary and a receipt for each call; the audit, the scoreboard and `web/cycle.html` read only those. Dossiers 792 to 800 were recorded from a local machine (by hand or by the local runner); from dossier 801 on, a scheduled GitHub Actions job (the workflow `otari-cycle`, every three hours) records the cycles.

## Proofs, resource by resource

<!-- otari:proofs:start -->
| # | Resource | Status | Where the proof lives | Dossier | Request id(s) | Code |
|---|---|---|---|---|---|---|
| 1 | Several open-weight models in distinct roles | **PASS** | sealed receipts in the ledger: bull, bear, judge and checker each answered on a different open model: bull openai/gpt-oss-120b, bear nvidia.nemotron-super-3-120b, judge deepseek-ai/DeepSeek-V4-Flash-0731, checker google/gemma-3-27b-it | 801 | `d6992578-b27b-4014-b0d9-8f0be467809a`<br>`b349e7c6-d31d-4061-8c78-d855b3036e0c`<br>`7d0e2d2d-7ea9-4843-b114-d61b42bd3e5a`<br>`1b9a20ee-ef5d-4d0a-b64a-4733b42df220` | `config/otari.json`, `llm.py` |
| 2 | Routing and fallback | **PASS** | sealed receipts in the ledger: 15 real calls were answered by a backup route after the first attempt did not succeed (Otari-Fallback true, attempt count 2), in dossiers 800, 801; newest: a narrator call sent as alibaba/qwen3-235b-a22b-instruct-2507 (the plain catalog id, no routing policy of ours, so this is Otari's own provider failover and not the vd-judge policy) was answered on its second attempt by bedrock:qwen.qwen3-235b-a22b-2507-v1:0. Besides, the failover canary of dossier 801 is the deliberate test of the same route: policy vd-judge-drill served by deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731 after 2 attempts (Otari-Fallback true). All sealed in the ledger | 801 | `347df8a1-834a-46c6-b0fc-1f8df503ff5e`<br>`e20438c7-8d1b-4cea-b3f6-09ba3a4cc545`<br>`83f87ee9-687a-405a-83d9-1ad471e5ffe4`<br>`1dd7daa2-c2ea-4c8f-81b7-aec04e383a71` (drill, not sealed) | `otari_canary.py` |
| 3 | Budgets and spend limits | **PASS** | sealed receipts in the ledger: budget-aware routing: Otari's spend for the judge's end user (USD 0.001370 of USD 0.10) was read and sealed, and the router allowed the hard route on it; per-cycle caps sealed too. Otari's own 403 budget_exceeded refusal is shown by the drill only (not sealed) | 800 | `43f2a31e-68b6-433a-97f8-3c779c0ebb60`<br>`d9b44c76-c18e-4f75-8ed1-4a5781e0d8e7` (drill, not sealed) | `llm.read_headroom`, `llm.route_decision` |
| 4 | Budget per user | **PASS** | sealed receipts in the ledger: 10 of the 10 end users found sit on the vd-analyst budget; 10 of 10 asked were found (read back from Otari, sealed in the dossier); Otari echoed the budget on the receipts | 800 | `347df8a1-834a-46c6-b0fc-1f8df503ff5e`<br>`e20438c7-8d1b-4cea-b3f6-09ba3a4cc545`<br>`ec5598b7-a31f-40c8-83c1-7bdf442637a2`<br>`a54cdac6-c534-468a-a206-45274298931e` | `llm._end_user`, `llm.read_headroom` |
| 5 | Guardrails | **NOT PROVEN** | attached to the vd-judge policy, but Otari reported valid null on 3 calls: nothing was evaluated (the hosted gateway has no guardrails service); not counted | 801 | `7d0e2d2d-7ea9-4843-b114-d61b42bd3e5a`<br>`1daefb36-6e97-4c3e-95c0-406a8e864147` | `llm.parse_receipt` |
| 6 | Web search and fetch | **PASS** | sealed receipts in the ledger: one search and one page fetch through Otari's tools; 3 sources and an excerpt frozen (excerpt_verified True); the analysts cited 2 of 3 source titles and 0 of 1 page excerpts in the claims the judge weighed | 796 | `7e7888e0-8902-4ddf-8af7-98e98554c458`<br>`607c9d50-6e28-44bb-a427-7a6c0f025b63` | `otari_web.py` |
| 7 | Code execution | **PASS** | sealed receipts in the ledger: the verdict re-derived in Otari's sandbox: its own result block is identical to the local run, the nonce proof matches and the code that ran is the code sent | 801 | `e35a135b-b4d9-433e-91a9-4dcaa4c79b37` | `sandbox_rederive.py` |
| 8 | MCP | **PASS** | sealed receipts in the ledger: prices read from coingecko through Otari's MCP loop, from the server's own result block (check: ok) | 801 | `933eb032-9f4b-4a88-8445-8d0714d64007` | `otari_mcp.py` |

**8 resources: 7 proven from sealed receipts + 0 from drills (not sealed) + 1 NOT PROVEN.**
<!-- otari:proofs:end -->

`otari-audit` prints this table from the ledger. A proof "sealed" lives in the hash-chained ledger and is replayed offline; a "drill" proof lives in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl) and is not sealed. The guardrail row is NOT PROVEN on purpose: it is attached to the `vd-judge` policy, but Otari reported no verdict (`valid: null`) on every call.

## Open-model scoreboard

<!-- otari:board:start -->
| Model (provider:model) | Calls | OK | Refusals / failures | p50 ms | p95 ms | Fallbacks | Accepted claims | USD per claim |
|---|---|---|---|---|---|---|---|---|
| `deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507` | 70 | 70 | - | 22285 | 29780 | 0 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731` | 40 | 40 | - | 4233 | 12462 | 4 | - | - |
| `bedrock:qwen.qwen3-235b-a22b-2507-v1:0` | 15 | 15 | - | 8340 | 16479 | 15 | - | - |
| `bedrock:nvidia.nemotron-super-3-120b` | 10 | 10 | - | 1802 | 2802 | 0 | 40 | 0.000181 |
| `deepinfra:openai/gpt-oss-120b` | 10 | 10 | - | 19580 | 24990 | 0 | 40 | 0.000079 |
| `nebius:google/gemma-3-27b-it` | 10 | 10 | - | 4684 | 11622 | 0 | - | - |
| `(no answer) alibaba/qwen3-235b-a22b-instruct-2507` | 7 | 0 | 409 x7 | - | - | 0 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Pro` | 5 | 5 | - | 1665 | 2707 | 0 | - | - |
<!-- otari:board:end -->

Every call of every Otari-era dossier, grouped by the model that answered. The fallbacks in this table have two sources. Real traffic: calls that Otari re-routed to a second provider when the first did not answer (receipts with `Otari-Fallback: true` and attempt count 2; so far only narrator batches: 8 in dossier 800 and 7 in dossier 801, sent as the plain catalog id `alibaba/qwen3-235b-a22b-instruct-2507`, with no routing policy of ours, and served by bedrock where earlier cycles were served by deepinfra, so this is Otari's own provider failover and not the `vd-judge` policy). The deliberate test: the failover canary, one small call per cycle through a policy built to fail over, which shows the backup route working even when no real call needs it.

## The checker has a consequence (rules v7)

Four models take part in every decision: a bull and a bear analyst, a judge, and a **citation checker** that re-reads each validated claim against the evidence it cites. Through dossier 801 (rules v6) the checker was advisory. From rules v7 its marks are part of the verdict: if the judge's lean rests on a claim the checker marked unsupported, that lean carries no weight (the judge only breaks ties, so this can send a close call back to no position and never opens one). The marks are frozen in the dossier, the rule is written in the verdict's `policy`, the outcome is sealed as `verdict.checker_gate`, and the replay recomputes it, so editing a mark fails the replay. A missing or failed checker changes nothing. Dossiers recorded by the scheduled job after this change use `decide-v7`; dossiers 792 to 801 stay v6 and still replay to the same verdicts.

## Budgets: what Otari enforces, and what the app adds

Three different things, kept apart on purpose:

| Layer | Who enforces it | What it is | Proof in this repository |
|---|---|---|---|
| Budget objects on the Otari side | **Otari** | `vd-analyst`: USD 0.10 a day per end user, attached to each role's end user (`vd-bull`, `vd-judge`, and so on) through the `Otari-End-User-Budget` header when the end user is created; `vd-org-lifetime`: a USD 1.50 a day ceiling for the organization; `vd-drill-tiny`: USD 0.01, for one drill | the echoed budget id on the receipts (audit row 4); a refusal `403 budget_exceeded` (scope `user`) in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl) |
| Otari's spend state, read by the app | Otari holds the numbers, the **app only reads them** | each cycle reads `GET /end-users` (spend plus reserve per end user) and the judge may use the larger model only while its end user is under 80 percent of its day; the read, with the HTTP status of every lookup, is sealed in `llm.otari.headroom` | audit row 3, from the newest dossier whose read of the judge worked |
| Cycle caps and runner limits | **The app alone** (`llm._cycle_check`, `tools/otari_runner.py`) | no new call starts once a cycle has spent USD 0.02 on the core roles or USD 0.06 on the tool roles; the runner stops at USD 2.00 in all and at 8 cycles a day | a refusal by a cap is a client-side receipt (`cycle_cap_exceeded`, `source: client`), never an Otari refusal |

What is and is not shown: Otari's own `403 budget_exceeded` refusal has been seen once, in a deliberate drill against the USD 0.01 budget, and is **not** in a sealed production dossier. No production cycle has been refused by an Otari budget. The cycle caps are our own code and are not an Otari feature.

## Who sees what

Every prompt goes to the gateway, then to the upstream provider named in the receipt (the page shows it per dossier). Every other host the code names:

<!-- otari:hosts:start -->
| Host | What it is | What it receives | When |
|---|---|---|---|
| `otari.ai` | the Otari gateway (base URL https://otari.ai/api/v1) | every prompt of every model call, the tool requests (search, fetch, sandbox code, MCP), and our end-user ids | every cycle, with OTARI_API_KEY set |
| `mcp.api.coingecko.com` | CoinGecko's public MCP server | the TypeScript price query the model writes and Otari sends; our code never contacts it | once per cycle, by Otari |
| `data-api.binance.vision` | Binance public market data | our IP address and the symbol asked for (no key) | every decision |
| `www.okx.com` | OKX public market data (funding rate) | our IP address and the instrument asked for (no key) | every decision |
| `api.gdeltproject.org` | GDELT news search | our IP address and the search words (no key) | every decision, unless news is off |
| `cointelegraph.com` | public RSS feed | our IP address (a GET of the feed) | every cycle, narrative voices |
| `www.coindesk.com` | public RSS feed | our IP address (a GET of the feed) | every cycle, narrative voices |
| `decrypt.co` | public RSS feed | our IP address (a GET of the feed) | every cycle, narrative voices |
| `www.theblock.co` | public RSS feed | our IP address (a GET of the feed) | every cycle, narrative voices |
| `cryptoslate.com` | public RSS feed | our IP address (a GET of the feed) | every cycle, narrative voices |
| `t.me` | Telegram public channel previews (t.me/s/...) | our IP address (a GET of a public preview page) | every cycle, narrative voices |
| `search.sigstore.dev` | the public Sigstore transparency log | a lookup of a published head hash | only in the witness checks |
| `andriyamaro.github.io` | the published site and ledger (read-only) | a GET of public files | only in the verification tools |
| `github.com` | the source repository and its workflow evidence | git pushes of dossiers (branch otari) and reads of public pages | when a dossier is pushed or a witness is read |
| `ethereum-sepolia-rpc.publicnode.com` | a public Sepolia RPC | read calls and, only with SEPOLIA_PRIVATE_KEY set, practice transactions | not used on the otari branch (no key) |
| `sepolia.gateway.tenderly.co` | a public Sepolia RPC (fallback) | read calls | not used on the otari branch (no key) |
| `sepolia.etherscan.io` | a block explorer link | nothing from us: it is a link a reader may open | never contacted by the code |
| `api.mistral.ai` | the original, pre-Otari LLM provider (the default when OTARI_API_KEY is not set) | prompts, only when MISTRAL_API_KEY is set and OTARI_API_KEY is not | never in Otari mode |
| `api.anthropic.com` | the optional native Claude provider (LLM_PROVIDER=anthropic) | prompts, only when that provider is selected | never in Otari mode |
<!-- otari:hosts:end -->

## More

[Friction log: every real snag met with the gateway](docs/FEEDBACK_LOG.md) · [architecture of the code](docs/architecture.md) · evidence files in [`docs/evidence/`](docs/evidence/).

Background, not part of the Otari work: the earlier chain and its tooling (hash-chained dossiers, the citation validator, a Chainlink CRE anchor on Sepolia) are described in [`docs/HISTORY.md`](docs/HISTORY.md).

## About this repository

It started as a snapshot of [AndriyAmaro/verdict-desk](https://github.com/AndriyAmaro/verdict-desk) at commit `c65bab9` (the state of that project when the BLI Legal Tech Hackathon 2 closed), which is kept as it was. The Otari work lives on the `otari` branch. The scheduled job `otari-cycle` is defined on `main` and runs `tools/otari_runner.py` against the `otari` branch. The other scheduled workflows belong to the earlier project and stay in its repository; `test_workflows` and `test_rekor_workflow`, which read those files, skip cleanly when they are absent.

MIT, see [`LICENSE`](LICENSE). Built by Andri Amaro with the help of an AI coding agent (Claude Code). The AI assistance is disclosed here on purpose.
