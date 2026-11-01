# Source verification of LedgerAnchor (Sepolia)

The deployed receiver contract is **source-verified with an exact match**, so a judge does not have to
trust this repository for what the contract does: the explorer serves the source and the compiler
settings that match the deployed bytecode byte for byte.

| Field | Value |
|---|---|
| Contract | `LedgerAnchor` |
| Address | [`0x50443D51Db71A61E131B147B1d36aF21d48CDdF7`](https://sourcify.dev/#/lookup/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7) |
| Chain | Ethereum Sepolia (`11155111`) |
| Match | `exact_match` on **creation** and **runtime** |
| Verified at | 2026-10-22T21:04:55Z |
| Compiler | `solc 0.8.28+commit.7893614a`, optimizer on, 200 runs |
| Constructor args | `forwarder = 0x15fc6ae953e024d975e77382eeec56a9101f9f88`, `expectedWorkflowOwner = 0x0` |
| Sourcify record | https://sourcify.dev/server/v2/contract/11155111/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7 |

The runtime bytecode compiled locally is 1743 bytes, the same as the deployed runtime, and the
verification service reports an exact match against both the creation and the runtime code.

## Reproduce it

```bash
cd cre/contracts/evm
node prepare_verify.js          # reads the two immutables from the chain, compiles with solc 0.8.28,
                                # writes verify/standard-input.json, verify/metadata.json,
                                # verify/constructor_args.txt and verify/LedgerAnchor.sol
```

Then submit the standard JSON input to any verification service. The exact body used for the
verification above:

```json
{
  "stdJsonInput": "<contents of verify/standard-input.json>",
  "compilerVersion": "0.8.28+commit.7893614a",
  "contractIdentifier": "LedgerAnchor.sol:LedgerAnchor"
}
```

```
POST https://sourcify.dev/server/v2/verify/11155111/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7
```

The service also forwards the submission to Etherscan, Blockscout and Routescan. Etherscan rejected
its share on a daily submission limit; the Sourcify record above is the authoritative one and is
independent of any single explorer's quota.

## Why this matters for the CRE bounty

An anchor is only worth something if the reader can see what the receiver actually enforces. With the
source verified, "forwarder-only, strictly increasing sequence, immutable anchor per sequence number"
is a claim anyone can check against the deployed code instead of taking on trust.
