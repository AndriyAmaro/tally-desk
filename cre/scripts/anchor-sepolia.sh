#!/usr/bin/env bash
# From a funded throwaway Sepolia key to one real transaction that anchors the current ledger head.
#
#   export SEPOLIA_PRIVATE_KEY=0x...      # a TEST key that only ever holds Sepolia ETH; never commit it
#   cre login                             # or: export CRE_API_KEY=...   (the simulator needs a CRE account)
#   bash cre/scripts/anchor-sepolia.sh
#
# What it does, in order: install and compile, deploy LedgerAnchor (MockKeystoneForwarder as the forwarder),
# simulate the workflow without sending anything, run it again with --broadcast so the report reaches the
# contract in a real Sepolia transaction, then check the contract against the public ledger.
# The outputs of the two simulations are saved in cre/evidence/ (the key is masked before saving).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SEPOLIA_PRIVATE_KEY:?set SEPOLIA_PRIVATE_KEY to a throwaway Sepolia key (never commit it)}"
command -v cre >/dev/null || { echo "cre CLI not found: https://docs.chain.link/cre/getting-started/cli-installation" >&2; exit 1; }
command -v bun >/dev/null || { echo "bun not found: https://bun.sh" >&2; exit 1; }
command -v node >/dev/null || { echo "node not found" >&2; exit 1; }

KEY="${SEPOLIA_PRIVATE_KEY#0x}"
ENVFILE="$(mktemp)"
trap 'rm -f "$ENVFILE"' EXIT
chmod 600 "$ENVFILE"
printf 'CRE_ETH_PRIVATE_KEY=%s\n' "$KEY" > "$ENVFILE"
mask() { sed "s/$KEY/<test-key>/g"; }
mkdir -p evidence

echo "1/5 dependencies and compile"
(cd contracts && bun install --frozen-lockfile >/dev/null)
(cd contracts/evm && npm install --no-audit --no-fund --silent && npm run --silent compile)
(cd anchor && bun install --frozen-lockfile >/dev/null)

echo "2/5 deploy LedgerAnchor to Sepolia"
(cd contracts && node scripts/deploy.mjs --write-config)

echo "3/5 simulate the workflow (dry run: no transaction is sent)"
cre workflow simulate ./anchor -T staging-settings -e "$ENVFILE" --non-interactive --trigger-index 0 2>&1 | mask | tee evidence/simulate-output.txt

echo "4/5 run it again with --broadcast: the report reaches the contract in a real transaction"
cre workflow simulate ./anchor -T staging-settings -e "$ENVFILE" --non-interactive --trigger-index 0 --broadcast 2>&1 | mask | tee evidence/broadcast-output.txt

echo "5/5 check the contract against the public ledger"
node contracts/scripts/check-anchor.mjs | tee evidence/check-anchor-output.txt
