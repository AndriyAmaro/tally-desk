# Chainlink CRE on Sepolia: simulate, deploy and anchor

This is the runbook for the one thing the CRE bounty asks to see that unit tests cannot show: the workflow running against the CRE simulator and a real Sepolia transaction that anchors the head of the ledger. Everything below is a command you can run; nothing here is a claim about a result that has not happened. The status table says what has run.

## Status

| Piece | Status |
|---|---|
| Workflow logic: head parsing, Data Feed read, report encoding, failure paths | Verified: 10 tests with the SDK mocks, type-checks (`anchor-workflow` job of the `tests` workflow) |
| `LedgerAnchor.sol` | Verified: 6 tests in an in-memory EVM |
| Deploy script against Sepolia, dry run (`--dry-run`: forwarder code check, gas estimate) | Run on 2026-10-20: the deployment costs about 0.0000006 ETH at current Sepolia gas, so any faucet drip is enough |
| `cre workflow simulate` | **Run on 2026-10-21** (dry run and `--broadcast`; outputs in `evidence/`) |
| `LedgerAnchor` on Sepolia | **Deployed on 2026-10-21**: [0x50443D51Db71A61E131B147B1d36aF21d48CDdF7](https://sepolia.etherscan.io/address/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7) |
| Transaction anchoring the head | **Exists**: [0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d](https://sepolia.etherscan.io/tx/0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d) anchored dossier 80 on 2026-10-21. The evidence is in [`SIMULATION-OUTPUT.md`](SIMULATION-OUTPUT.md) |

## What you need

- **A CRE account**, and a way for the CLI to authenticate: `cre login` (opens a browser) or the environment variable `CRE_API_KEY`. The key is created on the CRE platform, Organization page, APIs tab, "+ Organization API". Per the CRE docs, API key authentication also needs deploy access approval on the account; `cre login` does not.
- **Tools:** the CRE CLI, [Bun](https://bun.sh) 1.0 or newer, Node 18 or newer.
- **A throwaway Sepolia key** that only ever holds testnet ETH. Never use a key that has been in git: the placeholder key that was committed in `cre/anchor/.env` on 2026-10-20 is public, was removed from the tree, and must never be funded.

```bash
cd cre/contracts && bun install --frozen-lockfile
node -e 'const {generatePrivateKey,privateKeyToAccount}=require("viem/accounts");const k=generatePrivateKey();require("fs").writeFileSync(".sepolia.key",k,{mode:0o600});console.log("fund this address with Sepolia ETH:",privateKeyToAccount(k).address)'
# fund the printed address from any Sepolia faucet; .sepolia.key is ignored by git and is never printed
```

## One command

```bash
export SEPOLIA_PRIVATE_KEY=$(cat cre/contracts/.sepolia.key)
cre login            # or: export CRE_API_KEY=...
bash cre/scripts/anchor-sepolia.sh
```

It installs and compiles, deploys `LedgerAnchor` with the Sepolia `MockKeystoneForwarder`, writes the address into `anchor/config.staging.json` and `deployment.sepolia.json`, simulates the workflow once without sending anything, runs it again with `--broadcast` so the report reaches the contract in a real transaction, and checks the contract against the public ledger. The outputs go to `cre/evidence/` with the key masked.

## Step by step

```bash
# 1. dependencies and compile
cd cre/contracts && bun install --frozen-lockfile
cd evm && npm install && npm run compile && cd ..

# 2. deploy (the dry run first: it sends nothing)
SEPOLIA_PRIVATE_KEY=$(cat .sepolia.key) node scripts/deploy.mjs --dry-run
SEPOLIA_PRIVATE_KEY=$(cat .sepolia.key) node scripts/deploy.mjs --write-config

# 3. simulate without broadcasting, then with it (CRE_ETH_PRIVATE_KEY is the name the CLI reads from .env)
cd .. && printf 'CRE_ETH_PRIVATE_KEY=%s\n' "$(cat contracts/.sepolia.key | sed 's/^0x//')" > .env
cre workflow simulate ./anchor -T staging-settings --non-interactive --trigger-index 0
cre workflow simulate ./anchor -T staging-settings --non-interactive --trigger-index 0 --broadcast

# 4. read-only check of the contract against the ledger (no key needed)
node contracts/scripts/check-anchor.mjs
```

`.env` is ignored by git. `check-anchor.mjs --json` prints the same facts as JSON, which is what fills [`SIMULATION-OUTPUT.md`](SIMULATION-OUTPUT.md). `check-anchor.mjs` verifies the whole hash chain of the ledger with the same code the dashboard runs, reads the latest anchored sequence number and head hash from the contract, asks the contract `isAnchored(seq, hash)` for the hash this ledger has at that sequence number, and prints the anchoring transaction.

## Which forwarder

| Forwarder | Address | Use |
|---|---|---|
| `MockKeystoneForwarder` | `0x15fc6ae953e024d975e77382eeec56a9101f9f88` | Simulation, including `--broadcast`. The default of `deploy.mjs` |
| `KeystoneForwarder` | `0xF8344CFd5c43616a4366C34E3EEE75af79a74482` | Workflows deployed to the CRE network (`deploy.mjs --forwarder production --owner <your CRE account address>`) |

Both had contract code on Sepolia when checked on 2026-10-20. Confirm them in the Chainlink Forwarder Directory before relying on them, and `cre workflow supported-chains` lists the forwarders enabled for your tenant. A contract deployed for simulation accepts reports only from the mock forwarder, so a workflow deployed to the CRE network needs its own `LedgerAnchor` with the production forwarder.

## Reference addresses

| Resource | Address or URL |
|---|---|
| BTC/USD Data Feed, Sepolia | `0x1b44F3514812d835EB1BDB0acB33d3fA3351Ee43` |
| Public Sepolia RPC | `https://ethereum-sepolia-rpc.publicnode.com` |
| Ledger head the workflow reads | `https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/ledger/head.json` |

## What the anchor proves, and what it does not

An anchored head pins the hash of one dossier, and through the chain every dossier before it, at a time the operator cannot move: the Sepolia block time. Rewriting history afterwards produces a different head, and `isAnchored(seq, hash)` answers false for it. It does not show that the evidence was true when it was collected, and the head is only as fresh as the last anchoring run (the sequence number must increase, so an old head can never be written over a newer one).
