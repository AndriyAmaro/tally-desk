# Practice trades executed on Sepolia

From the first dossier recorded after this was switched on, every new practice position also gets a Sepolia
transaction on [`PracticeDesk`](contracts/evm/src/PracticeDesk.sol), deployed at
[`0xe42c24dd93ACe0178239f03c43dC3a707d20DF86`](https://sepolia.etherscan.io/address/0xe42c24dd93ACe0178239f03c43dC3a707d20DF86)
([`practice.sepolia.json`](practice.sepolia.json)). Earlier positions stay as they were: simulated.

**What happens**
- **ETHUSDT** positions are executed: a swap on Uniswap v3's WETH/USDC pool on Sepolia, in the same transaction as
  the receipt. A SHORT sells WETH for test USDC, a LONG buys WETH with test USDC; when a later candle hits the
  position's stop or target, the reverse swap is sent. If the swap fails, the whole transaction reverts: there is
  no receipt without the trade, and one decision is opened once and closed once (the contract enforces both).
- **BTCUSDT and SOLUSDT** have no testnet market with liquidity, so they get a receipt only, emitted as a
  different event and labelled "recorded on Sepolia testnet, not executed".
- The hourly run on GitHub Actions sends these; no operator machine is involved.

**Bound both ways**
- The transaction carries `decision_digest`: SHA-256 of the canonical JSON of the position's seq, symbol, action,
  entry, stop, target, size and the digest of the evidence it was decided on (`verdict_desk/practice.py`).
- The dossier records the transaction (`execution`, and `executions_closed` for a close) before it is sealed, so
  the hash chain and the external witnesses cover it.

**What it is not**
- Testnet practice: test tokens, no real order. The testnet pool price (about 46,000 test USDC per WETH when the
  desk was funded) is not the market price, so the on-chain amounts say nothing about profit; the scorecard
  keeps using market candles.
- The on-chain size is the simulated position scaled down (`SCALE = 0.0005` WETH per ETH of position).
- A send that fails is recorded as `status: failed`, and that position stays simulated.
- If a recording loses a push race and is re-recorded, the receipts it already sent stay on Sepolia with a digest
  that matches no published dossier; the dossier that replaced it carries its own transaction.

**Check it**: open the transaction from a dossier's `execution.tx`, read the `PracticeTrade` (or `PracticeRecord`)
event, and compare its `decision` with the digest recomputed from the dossier.

## Keeping the desk funded

Each ETHUSDT SHORT sells about 0.0019 test WETH from the desk and each LONG spends about 88 test USDC; a close returns
the other token. When a balance runs out, the send fails and the dossier records the position as simulated, saying so.
`cre/contracts/scripts/fund-practice.mjs <eth>` wraps that much of the wallet's Sepolia ETH into WETH and sends it to
the desk (dry run without `--send`; it keeps 0.002 ETH for gas). Sepolia ETH comes from a public faucet.
