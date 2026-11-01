# CRE simulation and anchor evidence

**Status: RUN on 2026-10-21.** The workflow was simulated twice (once sending nothing, once with `--broadcast`), `LedgerAnchor` was deployed to Sepolia, and the broadcast run anchored the ledger head in a real transaction. `node contracts/scripts/check-anchor.mjs` answers `ok: true` for this run (full output below). Every value below comes from that run; the "Where each value comes from" table says which command gives which field.

## Summary

Copied into the root `README.md` (section "Chainlink CRE", between the `cre-evidence` markers).

| Field | Value |
|---|---|
| Date and time of the run (UTC) | 2026-10-21 (simulation 14:47 local log time; anchoring block time 2026-10-21T17:53:24Z) |
| CRE CLI version | v1.38.0 |
| Workflow | `vd-anchor-staging` (`cre/anchor`, target `staging-settings`) |
| Ledger head the workflow read (seq, hash) | 80, `04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283` |
| Head anchored in the contract (seq, hash) | 80, `04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283` |
| BTC/USD Data Feed value recorded with it (answer, decimals, feed updated at) | 84378.12678601 (answer 8437812678601, 8 decimals, feed updated 2026-10-21T17:11:24Z) |
| `LedgerAnchor` contract on Sepolia | [0x50443D51Db71A61E131B147B1d36aF21d48CDdF7](https://sepolia.etherscan.io/address/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7) (deploy tx [0x43f62890...3f8b8fe8](https://sepolia.etherscan.io/tx/0x43f628906aa81e5c16636ea6fbe035c7c1dfc9d0f6c3cb01d56efbc33f8b8fe8), block 11963992) |
| Forwarder | `MockKeystoneForwarder` 0x15fC6ae953E024d975e77382eEeC56A9101f9F88 (simulation) |
| Anchoring transaction | [0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d](https://sepolia.etherscan.io/tx/0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d) |
| `isAnchored(seq, hash)` answer | true |
| Commit of this repository that the run used | `6c0a69fd5dc2da127455cc164e5e3eb2767d3773` (plus the run's own changes: the deployed address and the gas limit below) |

A simulation without `--broadcast` only proves that the workflow runs end to end against the simulator. Only the broadcast run produces a transaction, so only it fills the anchor rows.

## Run notes

The first broadcast attempt (tx `0x7caa1fbcce191f9fc4f0d9f2d47627b6bd6ab6fa52cb29af69d4f0a8d8e04be8`, output in `evidence/simulate-broadcast-output.txt`) reached the forwarder but did not execute the receiver: it used 490,016 of the configured 500,000 gas limit, leaving nothing for the contract call. The `gasLimit` in `anchor/config.staging.json` was raised to 3,000,000 and the run was repeated; the second broadcast used 712,428 gas and the `Anchored` event landed. The check script's event scan needs an RPC with unpruned log history; it was run with `SEPOLIA_RPC_URL=https://sepolia.gateway.tenderly.co` (the default public RPC answers "pruned history unavailable" for the scan range, while its state reads work).

## Simulation output (dry run, nothing is sent)

```
Initializing...
Loading settings...
Checking RPC connectivity...
Compiling workflow...
✓ Workflow compiled
✓ Simulation limits enabled
  HTTP: req=120kb resp=250kb timeout=10s | ConfHTTP: req=125kb resp=500kb timeout=1m30s | Consensus obs=25kb | ChainWrite evm_report=50kb evm_gas=10000000 solana_report=265b solana_cu=300000 | WASM binary=100mb compressed=20mb
  Binary hash: ea47e6d3061ccfa35155b7f70246722918e74d1f4ce50c66d1d477d7974735ec
  Config hash: 19c17c41acf11ae429f4faafe9a68a8ad4e568a2e8585749661213e3e3c0d28e
2026-10-21T14:47:49Z [SIMULATION] Simulator Initialized

2026-10-21T14:47:49Z [SIMULATION] Running trigger trigger=cron-trigger@1.0.0
2026-10-21T14:47:49Z [USER LOG] Anchoring ledger head from https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/ledger/head.json
2026-10-21T14:47:50Z [USER LOG] Data Feed 0x1b44F3514812d835EB1BDB0acB33d3fA3351Ee43: 84378.12678601 (updatedAt 1792602684)
2026-10-21T14:47:50Z [USER LOG] Anchored seq 80 head 04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283 at tx 0x

✓ Workflow Simulation Result:
"{\"seq\":80,\"head\":\"04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283\",\"symbol\":\"SOLUSDT\",\"price\":\"84378.12678601\",\"txHash\":\"0x\"}"

2026-10-21T14:47:50Z [SIMULATION] Execution finished signal received
2026-10-21T14:47:50Z [SIMULATION] Skipping WorkflowEngineV2

╭──────────────────────────────────────────────────────╮
│ Simulation complete! Ready to deploy your workflow?  │
│                                                      │
│ Run cre account access to request deployment access. │
╰──────────────────────────────────────────────────────╯
```

## Broadcast output (the report reaches the contract in a real Sepolia transaction)

```
Initializing...
Loading settings...
Checking RPC connectivity...
Compiling workflow...
✓ Workflow compiled
✓ Simulation limits enabled
  HTTP: req=120kb resp=250kb timeout=10s | ConfHTTP: req=125kb resp=500kb timeout=1m30s | Consensus obs=25kb | ChainWrite evm_report=50kb evm_gas=10000000 solana_report=265b solana_cu=300000 | WASM binary=100mb compressed=20mb
  Binary hash: ea47e6d3061ccfa35155b7f70246722918e74d1f4ce50c66d1d477d7974735ec
  Config hash: e7d47de260671f5a63134146aacab74ea190f38179282ab08ce02c83badd0db6
2026-10-21T14:53:15Z [SIMULATION] Simulator Initialized

2026-10-21T14:53:15Z [SIMULATION] Running trigger trigger=cron-trigger@1.0.0
2026-10-21T14:53:16Z [USER LOG] Anchoring ledger head from https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/ledger/head.json
2026-10-21T14:53:16Z [USER LOG] Data Feed 0x1b44F3514812d835EB1BDB0acB33d3fA3351Ee43: 84378.12678601 (updatedAt 1792602684)
2026-10-21T14:53:25Z [USER LOG] Anchored seq 80 head 04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283 at tx 0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d

✓ Workflow Simulation Result:
"{\"seq\":80,\"head\":\"04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283\",\"symbol\":\"SOLUSDT\",\"price\":\"84378.12678601\",\"txHash\":\"0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d\"}"

2026-10-21T14:53:25Z [SIMULATION] Execution finished signal received
2026-10-21T14:53:25Z [SIMULATION] Skipping WorkflowEngineV2

╭──────────────────────────────────────────────────────╮
│ Simulation complete! Ready to deploy your workflow?  │
│                                                      │
│ Run cre account access to request deployment access. │
╰──────────────────────────────────────────────────────╯
```

## Check against the public ledger

```
ledger /home/andri-amaro/Dev/verdict-desk-cre/ledger/ledger.jsonl: 80 dossiers, chain intact (every hash recomputed here)
contract 0x50443D51Db71A61E131B147B1d36aF21d48CDdF7 on Sepolia: latest anchored seq 80, head hash 04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283
  Data Feed price recorded with it: 84378.12678601 (feed updated 2026-10-21T17:11:24.000Z), anchored at 2026-10-21T17:53:24.000Z
  dossier 80 in this ledger has hash 04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283
  isAnchored(80, hash) on-chain: TRUE: the anchored head is exactly this dossier
  anchoring transaction: https://sepolia.etherscan.io/tx/0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d
```

## Where each value comes from

| Field | Source |
|---|---|
| Date, CLI version | `cre version`, and the timestamps inside the two outputs above |
| Ledger head the workflow read | The line "Anchoring ledger head from ..." and the `seq` and `head` fields of the result line in `evidence/broadcast-output.txt` |
| Head anchored, feed value, contract, transaction, `isAnchored` | `node cre/contracts/scripts/check-anchor.mjs --json`: it reads them from the chain, not from files in this repository |
| Forwarder, deployer, deploy transaction | `cre/deployment.sepolia.json`, written by `contracts/scripts/deploy.mjs` |
| Commit of this repository | `git rev-parse HEAD` when the run started |

The anchored head is the head at the time of the run, not the current one: the ledger grows every hour, and the contract only keeps a strictly increasing sequence number. `check-anchor.mjs` therefore compares the anchored hash with the ledger's dossier at that same sequence number.

## How a judge checks it

```bash
node cre/contracts/scripts/check-anchor.mjs
```

No key is needed. It verifies the whole hash chain of the ledger with the code the dashboard runs, reads the latest anchored sequence number and head hash from Sepolia, asks the contract `isAnchored(seq, hash)` for the hash this ledger has at that sequence number, and prints the anchoring transaction. The exit code is 0 only when the chain is intact and the anchored head is exactly that dossier. (On a free public RPC the event scan at the end can fail with "pruned history unavailable"; the state reads before it are the verdict, and any archive-capable Sepolia RPC passed as `SEPOLIA_RPC_URL` completes it.)

## What this proves, and what it does not

An anchored head pins the hash of one dossier, and through the chain every dossier before it, at a time the operator cannot move: the Sepolia block time. Rewriting history afterwards produces a different head, and `isAnchored(seq, hash)` answers false for it. It does not show that the evidence was true when it was collected, and the head is only as recent as the last anchoring run.
