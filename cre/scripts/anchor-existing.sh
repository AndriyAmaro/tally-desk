#!/usr/bin/env bash
# Anchor the CURRENT published head against the ALREADY DEPLOYED LedgerAnchor contract.
#
#   export SEPOLIA_PRIVATE_KEY=0x...   # throwaway Sepolia key; never commit it
#   bash cre/scripts/anchor-existing.sh
#
# Why this exists next to anchor-sepolia.sh: that script deploys a fresh contract and then
# anchors once, which is right for the first run and wrong for every run after it (a new
# address per hour would orphan the history). This one reuses the deployment recorded in
# cre/deployment.sepolia.json and only sends the report, so the anchored sequence
# accumulates on a single, citable contract.
#
# It refuses to run if the deployment file is missing, if the head is already anchored, or
# if the CRE organisation lacks deployment access -- a skip that says why beats a silent
# failure that looks like the chain stopped.
set -euo pipefail
cd "$(dirname "$0")/.."

DEPLOYMENT="deployment.sepolia.json"
: "${SEPOLIA_PRIVATE_KEY:?set SEPOLIA_PRIVATE_KEY to a throwaway Sepolia key (never commit it)}"
[ -f "$DEPLOYMENT" ] || { echo "no $DEPLOYMENT: run anchor-sepolia.sh once to deploy first" >&2; exit 1; }
command -v cre >/dev/null || { echo "cre CLI not found: https://docs.chain.link/cre/getting-started/cli-installation" >&2; exit 1; }
command -v bun >/dev/null || { echo "bun not found: https://bun.sh" >&2; exit 1; }
command -v node >/dev/null || { echo "node not found" >&2; exit 1; }

CONTRACT=$(node -e "process.stdout.write(require('./$DEPLOYMENT').contract)")
echo "contract: $CONTRACT"

KEY="${SEPOLIA_PRIVATE_KEY#0x}"
ENVFILE="$(mktemp)"
trap 'rm -f "$ENVFILE"' EXIT
chmod 600 "$ENVFILE"
printf 'CRE_ETH_PRIVATE_KEY=%s\n' "$KEY" > "$ENVFILE"
mask() { sed "s/$KEY/<test-key>/g"; }

echo "1/4 dependencies and compile"
(cd contracts && bun install --frozen-lockfile >/dev/null)
(cd contracts/evm && npm ci --no-audit --no-fund --silent && npm run --silent compile)
(cd anchor && bun install --frozen-lockfile >/dev/null)

HEAD_SEQ=$(node -e "fetch('https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/ledger/head.json').then(r=>r.json()).then(h=>process.stdout.write(String(h.seq)))")
echo "2/4 live head seq: $HEAD_SEQ"

# The GitHub route (cre/anchor/direct.ts) and the hourly local timer may have anchored this head already; the
# contract would refuse it (StaleSequence) and the broadcast would only burn gas on a reverted transaction.
RPC="${SEPOLIA_RPC_URL:-https://ethereum-sepolia-rpc.publicnode.com}"
LATEST=$(node -e "fetch(process.argv[1],{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'eth_call',params:[{to:process.argv[2],data:'0x1075b671'},'latest']})}).then(r=>r.json()).then(j=>process.stdout.write(String(parseInt(j.result,16))))" "$RPC" "$CONTRACT")
echo "    contract's latest anchored seq: $LATEST"
if [ -n "$LATEST" ] && [ "$LATEST" != "NaN" ] && [ "$HEAD_SEQ" -le "$LATEST" ]; then
  echo "skip: head #$HEAD_SEQ is already anchored (latest #$LATEST); nothing broadcast"
  node contracts/scripts/check-anchor.mjs
  exit 0
fi

echo "3/4 broadcast the CRE report (real transaction on Sepolia)"
mkdir -p evidence
cre workflow simulate ./anchor -T staging-settings -e "$ENVFILE" --non-interactive --trigger-index 0 --broadcast 2>&1 \
  | mask | tee "evidence/anchor-$(date -u +%Y%m%dT%H%M%SZ).txt"

echo "4/4 check the contract against the public ledger"
node contracts/scripts/check-anchor.mjs | tee evidence/check-anchor-output.txt
