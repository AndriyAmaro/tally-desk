# Chainlink CRE anchor for the Verdict Desk ledger

A Chainlink Runtime Environment (CRE) workflow that makes the decision ledger tamper-evident beyond the operator's word, and checks the decision itself before vouching for it. Every hour (a scheduler on the operator's machine runs `cre workflow simulate --broadcast`) it:

1. fetches the published ledger head (`seq`, SHA-256 `hash`, `symbol`) over HTTP from **two independent publications** (the raw repository file and the GitHub Pages site); each node requires both to be identical, and the nodes must then agree with each other (identical consensus). If either source is down or they differ, nothing is anchored;
2. fetches the **whole dossier behind that head** (`ledger/head-dossier.json`) from the same two publications, recomputes its SHA-256 over the canonical JSON and requires it to equal the head, then **re-derives the verdict from the frozen evidence** inside the workflow ([`anchor/verdict.ts`](anchor/verdict.ts): the features, the argument rules of v2 to v6, the multi-KOL convergence and the v6 LLM judge check). If the recomputed action or edge differs from the recorded one, nothing is anchored;
3. reads a Chainlink **Data Feed** price (BTC/USD) on **two chains**, Sepolia and Ethereum mainnet, and refuses if they disagree by more than 500 basis points (the gap is logged and returned either way);
4. writes the head and the price to the `LedgerAnchor` contract on Sepolia through a CRE report.

**Measured: attacks on that check.** `cd anchor && bun run battery.ts 3000` edits a copy of the head dossier 3,000 ways (candles, a dropped candle, funding, news, features, the verdict, the scores, the position, the v6 judge's vote) and asks the check twice. Under the head both publications agree on, every edit was refused (3,000 of 3,000). With the hash rewritten and republished as the head, every edit that changes the decision was refused (2,760 of 2,760, none got through); the other 240 changed nothing the decision is computed from (for example a candle's open price), so no replay can see them, and only the hash chain and the external witnesses can. Result: [`evidence/cre-battery.json`](evidence/cre-battery.json) (seed: the head hash, so anyone re-running gets the same attacks). First live anchor with both chains read: dossier #365, gap 39 bps, log in [`evidence/anchor-two-chains-live-365.txt`](evidence/anchor-two-chains-live-365.txt).

So an anchor on Sepolia now means two things: this exact record existed at that block, and an independent re-computation in the CRE runtime reached the same decision from the same evidence. `anchor/verdict.test.ts` runs the TypeScript replay over every v2+ dossier of the published ledger and fails on the first one whose features, action or edge differ from the Python's.

`LedgerAnchor` only accepts reports from the CRE forwarder and only with a sequence number higher than the last one, so an anchored head can never be overwritten or rolled back. A judge compares `isAnchored(seq, hash)` on-chain with the head they recompute from the public ledger in their own browser (`../web/index.html`).

```
ledger/head.json (raw file + Pages copy, must be identical) --HTTP, identical consensus--> CRE workflow --report--> Forwarder --> LedgerAnchor (Sepolia)
ledger/head-dossier.json (both copies) --hash = head? verdict replays?--------^
Chainlink Data Feed BTC/USD, Sepolia + Ethereum mainnet --EVM reads, gap <= 500 bps--^
```

## What has been verified, and what has not

| Piece | Status | How |
|---|---|---|
| Head format shared by Python and the workflow | Verified | `python -m verdict_desk head`, parsed by `anchor/logic.ts` |
| Workflow logic (HTTP head parsing, Data Feed read, report encoding, failure paths) | Verified with the SDK's test mocks | `cd anchor && bun test`, `bunx tsc --noEmit` |
| Verdict replay in the workflow (`anchor/verdict.ts`) | Verified against the Python on every v2+ dossier of the ledger, and in the CRE WASM simulator: the real head replays, a forged head with a consistent hash is refused | `cd anchor && bun test verdict.test.ts`; [`evidence/simulate-verdict-replay.txt`](evidence/simulate-verdict-replay.txt), [`evidence/simulate-verdict-replay-forged.txt`](evidence/simulate-verdict-replay-forged.txt) (both served the published files from a local copy; the forged one changed the action and re-hashed it). First live run on the public URLs: dossier #296 replayed (`SHORT edge -4.25, the same as recorded`) and anchored in [0x27a2ce3c...](https://sepolia.etherscan.io/tx/0x27a2ce3c0cf38722ef05959ec4638318bd93a33fe84d08fb465527ec228dfbdf), log in [`evidence/anchor-replay-live-296.txt`](evidence/anchor-replay-live-296.txt) |
| `LedgerAnchor.sol` | Verified in a real in-memory EVM | `cd contracts/evm && npm install && python -m unittest discover -s test` (6 tests: forwarder-only, append-only, owner check, ERC165, exact custom errors) |
| Deploy script against Sepolia | Dry run done (forwarder code check, gas estimate: about 0.0000006 ETH) | `cd contracts && node scripts/deploy.mjs --dry-run` |
| `cre workflow simulate` | **Run on 2026-10-21** (dry run and `--broadcast`, CRE login session) | [`DEPLOY_SEPOLIA.md`](DEPLOY_SEPOLIA.md) |
| Live deploy on Sepolia and a real report | **Done on 2026-10-21.** `LedgerAnchor` deployed and the head of dossier 80 anchored by the broadcast run; see `evidence/` and `deployment.sepolia.json` | [`DEPLOY_SEPOLIA.md`](DEPLOY_SEPOLIA.md), `scripts/anchor-sepolia.sh` |
| Evidence file with the simulation output, anchored head, feed value, contract and transaction | **Filled from the 2026-10-21 run** | [`SIMULATION-OUTPUT.md`](SIMULATION-OUTPUT.md) |

## Run it

```bash
# unit tests, no account needed
cd anchor && bun install && bun test && bunx tsc --noEmit
cd ../contracts/evm && npm install && npm run compile && python -m unittest discover -s test

# simulation, needs a CRE account (https://docs.chain.link/cre/account/creating-account)
export CRE_API_KEY=...            # or: cre login
cre workflow simulate ./anchor -T staging-settings --non-interactive --trigger-index 0
# add --broadcast to send the report for real (needs a funded Sepolia key as CRE_ETH_PRIVATE_KEY in an ignored .env)

# or everything in order (deploy, simulate, broadcast, check) from a funded throwaway key:
SEPOLIA_PRIVATE_KEY=0x... bash scripts/anchor-sepolia.sh
# anyone can check what the contract holds against the public ledger, no key needed:
node contracts/scripts/check-anchor.mjs
```

The full runbook, with the status of each step, is [`DEPLOY_SEPOLIA.md`](DEPLOY_SEPOLIA.md).

`anchor/config.staging.json` already points `headUrl` at the raw `ledger/head.json` of this repository; before a real transaction, set `anchorAddress` to the deployed `LedgerAnchor` (it is the zero address while only simulating). Deploy `LedgerAnchor(forwarder, expectedWorkflowOwner)` with the Sepolia `MockKeystoneForwarder` for simulation or the production CRE forwarder for a deployed workflow; confirm both addresses in the CRE Forwarder Directory first.

| Reference | Value | Source |
|---|---|---|
| BTC/USD Data Feed, Sepolia | `0x1b44F3514812d835EB1BDB0acB33d3fA3351Ee43` | `smartcontractkit/cre-templates`, prediction-market config |
| Sepolia MockKeystoneForwarder (simulation) | `0x15fc6ae953e024d975e77382eeec56a9101f9f88` | same repository, READMEs |
| Sepolia CRE forwarder (deployed workflows) | `0xf8344cfd5c43616a4366c34e3eee75af79a74482` | same repository, sports-resolution README |

## Layout

| Path | What it is |
|---|---|
| `anchor/` | The workflow: `workflow.ts` (handler), `logic.ts` (pure functions), `main.ts` (entry), tests, `config.staging.json` |
| `contracts/evm/src/LedgerAnchor.sol` | Receiver contract, written for this project, no dependencies |
| `contracts/evm/src/abi/*.abi` | ABIs the bindings are generated from (`cre generate-bindings evm --language typescript`) |
| `contracts/evm/ts/generated/` | Generated bindings (do not edit) |
| `contracts/evm/test/` | EVM-level contract tests |
| `contracts/scripts/` | `deploy.mjs` (deploy from a throwaway key held in the environment, never printed) and `check-anchor.mjs` (read-only: verifies the ledger chain, reads the anchor, asks `isAnchored`) |
| `scripts/anchor-sepolia.sh` | Deploy, simulate, broadcast and check in order; saves the outputs in `evidence/` |
| `DEPLOY_SEPOLIA.md` | Runbook and status of each step |
| `SIMULATION-OUTPUT.md` | Where the evidence lands (filled by the 2026-10-21 run) |
| `project.yaml`, `secrets.yaml` | CRE project settings (no secrets are used) |

## Credits and licenses

Project layout and the use of the CRE SDK follow the MIT-licensed `smartcontractkit/cre-templates`; the workflow, contract and tests are original. `@chainlink/cre-sdk` is BUSL-1.1 and is used as a dependency, not vendored. Developed with AI assistance (Claude Code).


## Anchoring on a schedule (reusing the deployment)

`cre/scripts/anchor-sepolia.sh` deploys and anchors once — right for the first run, wrong afterwards, because a new address per hour orphans the history. For every run after the first:

```bash
export SEPOLIA_PRIVATE_KEY=0x...    # throwaway Sepolia key, never committed
bash cre/scripts/anchor-existing.sh
```

It broadcasts the report against the contract recorded in `cre/deployment.sepolia.json`.

**The cadence without the operator's machine.** The job in `.github/workflows/anchor.yml` runs on GitHub's runners after each new head is published (the hourly deploy dispatches it; its own :07 and :37 schedule is a backup that GitHub often drops). With the repository variable `ANCHOR_ROUTE=cre` and a valid `CRE_API_KEY` it runs the script above. This account cannot create a valid CRE API key (the one stored on 25 Oct is rejected as unauthorized, and `cre login` needs a browser), so by default it runs `cre/anchor/direct.ts` with `SEPOLIA_PRIVATE_KEY`: **not the CRE runtime**, but the same checks with the same code (the head must agree on two publications, its dossier must hash to it and re-derive its own verdict with `verdict.ts`, the BTC/USD Data Feed must agree on Sepolia and Ethereum mainnet within 500 bps) and the same report handed to the same `MockKeystoneForwarder`. Its report header names the workflow `gh-actions`, so every anchor on the contract says on-chain which route wrote it; the witnesses page reads that name from each transaction. `bun run direct.ts --dry-run` does every check and an `eth_call` of the transaction without sending it. A head already anchored, or a Pages copy still behind the repository, is skipped; a missing key skips with a visible notice.

**If a run says `sources disagree`:** the Pages copy is published a few minutes after the repository file, at the end of the hourly deploy. A run made in that window is refused on purpose; wait for the deploy and run again.

## Evidence kept in this folder

| File | What it proves |
|---|---|
| `evidence/simulate-broadcast-output.txt` | a real transaction: seq 80, `0x7caa1f…`, later confirmed on chain at block 11964023 |
| `evidence/simulate-two-sources.txt` | the workflow fetching the head from both publications (seq 224, no broadcast) and naming the feed it read, BTC / USD |
| `evidence/simulate-live-head.txt` | the workflow reading the live published head (seq 113) under consensus and the Data Feed price |
| `evidence/check-anchor-output.txt` | the contract read back and compared with the public ledger |

`tests/test_cre_evidence.py` re-checks those files against `ledger/ledger.jsonl` on every test run, so the evidence cannot quietly stop being true.
