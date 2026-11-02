# Verdict Desk on Otari: a trustworthy agent, open models only, a receipt for every call

An agent that decides on paper trades must answer for every model call it makes. Here every call goes through the [Otari](https://otari.ai) gateway, each role runs on an **open-weight model**, and **each call leaves a receipt** (who asked, which model answered through which provider, what it cost, how it stopped) sealed in a hash-chained ledger that anyone can re-verify and replay offline.
Every cycle also proves two Otari features from inside the app: a **failover canary** shows the backup route working before the judge is trusted with it, and the cycle reads Otari's own **spend state** per end user, so the judge may use the costlier model only while its budget has room.
Each receipt is reconciled against Otari's own usage log. Paper trading only: no real-money order is ever placed.

## Quickstart (Otari)

```bash
export OTARI_API_KEY=...   # from the Otari dashboard; never commit it
VERDICT_DESK_APPEND_PUBLIC=1 python3 -m verdict_desk decide-v6 BTCUSDT --config config/voices.json   # one cycle, every call through Otari
python3 -m verdict_desk verify && python3 -m verdict_desk replay   # the chain is intact and every verdict is re-derived offline
python3 -m verdict_desk otari-audit --reconcile   # the eight resources, and each receipt against Otari's usage log
python3 -m http.server   # then open http://localhost:8000/web/cycle.html
```

**See one cycle call by call: [`web/cycle.html`](web/cycle.html)** (a timeline of every call with its receipt, the canary, the budget headroom, who saw each prompt, and a scoreboard of the open models).

## Proofs, resource by resource

<!-- otari:proofs:start -->
| # | Resource | Status | Where the proof lives | Dossier | Request id(s) | Code |
|---|---|---|---|---|---|---|
| 1 | Several open-weight models in distinct roles | **PASS** | sealed receipts in the ledger: bull, bear, judge and checker each answered on a different open model: bull openai/gpt-oss-120b, bear nvidia.nemotron-super-3-120b, judge deepseek-ai/DeepSeek-V4-Pro, checker google/gemma-3-27b-it | 799 | `5a9937d2-3ecf-4c2c-8acd-694002c4507e`<br>`f9f0caec-af45-48ab-9c08-d7f80f6aea65`<br>`35ecceeb-e990-43ba-a6a6-9baebd241390`<br>`8e1e9455-a52d-4ce0-8c83-18189c0bde2c` | `config/otari.json`, `llm.py` |
| 2 | Routing and fallback | **PASS** | sealed receipts in the ledger: the failover canary of the cycle: policy vd-judge-drill served by deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731 after 2 attempts (Otari-Fallback true), sealed in the dossier | 799 | `53c1e757-ebcf-4f60-8466-70d2b89a85eb`<br>`1dd7daa2-c2ea-4c8f-81b7-aec04e383a71` (drill, not sealed) | `otari_canary.py` |
| 3 | Budgets and spend limits | **PASS** | sealed receipts in the ledger: budget-aware routing: Otari's spend for the judge's end user (USD 0.008490 of USD 0.10) was read and sealed, and the router allowed the hard route on it; per-cycle caps sealed too. Otari's own 403 budget_exceeded refusal is shown by the drill only (not sealed) | 799 | `35ecceeb-e990-43ba-a6a6-9baebd241390`<br>`d9b44c76-c18e-4f75-8ed1-4a5781e0d8e7` (drill, not sealed) | `llm.read_headroom`, `llm.route_decision` |
| 4 | Budget per user | **PASS** | sealed receipts in the ledger: 10 of 10 end users read back from Otari sit on the vd-analyst budget (sealed in the dossier); Otari echoed the budget on the receipts | 799 | `8541a3bb-d190-4f11-97ea-dd76054c6e42`<br>`4764d2e9-7fc2-4c28-b073-42ee13d532ee`<br>`dbd1f339-3ec0-42f8-9534-2347a0625df0`<br>`00360bfa-35a6-4801-a59f-6375c91fbbf2` | `llm._end_user`, `llm.read_headroom` |
| 5 | Guardrails | **NOT PROVEN** | attached to the vd-judge policy, but Otari reported valid null on 2 calls: nothing was evaluated (the hosted gateway has no guardrails service); not counted | 795 | `1daefb36-6e97-4c3e-95c0-406a8e864147`<br>`380d22b8-daed-4f86-9885-44b7ae29e2b8` | `llm.parse_receipt` |
| 6 | Web search and fetch | **PASS** | sealed receipts in the ledger: one search and one page fetch through Otari's tools; 3 sources and an excerpt frozen (excerpt_verified True) | 799 | `ebed7252-3c2e-4bb4-b324-1b5eb1096df9`<br>`0961fb87-8d42-4159-8764-84b4e054c1d6` | `otari_web.py` |
| 7 | Code execution | **PASS** | sealed receipts in the ledger: the verdict re-derived in Otari's sandbox: its own result block is identical to the local run, the nonce proof matches and the code that ran is the code sent | 799 | `ef0d9b66-a849-42d5-90dd-3405eedcc10e` | `sandbox_rederive.py` |
| 8 | MCP | **PASS** | sealed receipts in the ledger: prices read from coingecko through Otari's MCP loop, from the server's own result block (check: ok) | 799 | `c347fe73-6b83-4095-9f1f-004ce4440f5f` | `otari_mcp.py` |

**8 resources: 7 proven from sealed receipts + 0 from drills (not sealed) + 1 NOT PROVEN.**
<!-- otari:proofs:end -->

`otari-audit` prints this table from the ledger. A proof "sealed" lives in the hash-chained ledger and is replayed offline; a "drill" proof lives in [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl) and is not sealed. The guardrail row is NOT PROVEN on purpose: it is attached to the `vd-judge` policy, but Otari reported no verdict (`valid: null`) on every call.

## Open-model scoreboard

<!-- otari:board:start -->
| Model (provider:model) | Calls | OK | Refusals / failures | p50 ms | p95 ms | Fallbacks | Accepted claims | USD per claim |
|---|---|---|---|---|---|---|---|---|
| `deepinfra:Qwen/Qwen3-235B-A22B-Instruct-2507` | 69 | 69 | - | 22285 | 29780 | 0 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731` | 29 | 29 | - | 4233 | 11182 | 2 | - | - |
| `bedrock:nvidia.nemotron-super-3-120b` | 8 | 8 | - | 1959 | 2802 | 0 | 32 | 0.000184 |
| `deepinfra:openai/gpt-oss-120b` | 8 | 8 | - | 19580 | 24990 | 0 | 32 | 0.000080 |
| `nebius:google/gemma-3-27b-it` | 8 | 8 | - | 4684 | 11622 | 0 | - | - |
| `deepinfra:deepseek-ai/DeepSeek-V4-Pro` | 4 | 4 | - | 1339 | 2448 | 0 | - | - |
| `(no answer) alibaba/qwen3-235b-a22b-instruct-2507` | 3 | 0 | 409 x3 | - | - | 0 | - | - |
<!-- otari:board:end -->

Every call of every Otari-era dossier, grouped by the model that answered. The fallbacks in this table come from the failover canary: one small call per cycle through a policy built to fail over; the production roles only fall back when Otari has to.

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

[Integration notes](docs/OTARI_NOTES.md) · [friction log](docs/FEEDBACK_LOG.md) · [first map of the gateway](docs/OTARI_MAP.md) · [the BLI Legal Tech Hackathon 2 history of this code](docs/HISTORY.md) (hash-chained dossiers, citation validator, Chainlink CRE).

## About this repository

It starts as a snapshot of [AndriyAmaro/verdict-desk](https://github.com/AndriyAmaro/verdict-desk) at commit `c65bab9` (its state when the BLI Legal Tech Hackathon 2 closed), which stays frozen while it is judged. The work for MozFest Developers Wilding: Online lives on the `otari` branch. The scheduled GitHub workflows stay in the original, and so do three tests that read them (`test_workflows`, `test_rekor_workflow`, `test_numbers_fresh`).

MIT, see [`LICENSE`](LICENSE). Built by Andri Amaro with the help of an AI coding agent (Claude Code). The AI assistance is disclosed here on purpose.
