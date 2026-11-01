"""Practice trades executed on Sepolia, bound to the dossier in both directions.

When a decision opens a position, a transaction on the PracticeDesk contract (cre/contracts/evm/src/PracticeDesk.sol)
carries a digest of that decision, and its hash is written into the dossier before the dossier is sealed. ETHUSDT
positions are real swaps on Uniswap v3's WETH/USDC pool on Sepolia, in the same transaction as the receipt; BTC and
SOL have no testnet market, so they get a receipt marked as not executed. When a later candle hits the position's
stop or target, the reverse swap is sent and recorded in the next dossier of that symbol.

Testnet practice only: the testnet pool price (tens of thousands of test USDC per WETH) is not the market price,
and the on-chain size is the simulated position scaled down by SCALE. Without a key nothing changes.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import portfolio
from .canonical import canonical

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "cre" / "practice.sepolia.json"
SCRIPT = ROOT / "cre" / "contracts" / "scripts" / "practice.mjs"
SCALE = 0.0005  # on-chain WETH per unit of the simulated ETH position: a 4 ETH practice position trades 0.002 WETH
MARKETS = {"ETHUSDT": "WETH/USDC"}  # the only pair with testnet liquidity; BTC and SOL are recorded, not executed
NETWORK = "ethereum-testnet-sepolia"
DONE = ("executed", "recorded")


def _digest(body) -> str:
    return "0x" + hashlib.sha256(canonical(body)).hexdigest()


def decision_digest(seq: int, symbol: str, verdict: dict, evidence: dict) -> str:
    """What the transaction commits to: the position's numbers and the evidence it was decided on."""
    fields = {k: verdict.get(k) for k in ("action", "entry", "stop", "take_profit", "size")}
    return _digest({"seq": seq, "symbol": symbol, **fields, "evidence": _digest(evidence)})


def _contract():
    try:
        return json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["contract"]
    except (OSError, ValueError, KeyError):
        return None


def _label(status: str, symbol: str) -> str:
    if status == "executed":
        return "practice trade executed on Sepolia testnet"
    if status == "recorded":
        return f"practice position recorded on Sepolia testnet, not executed: no testnet market for {symbol}"
    return "the Sepolia send failed; this position stays simulated"


def _record(request: dict, result: dict) -> dict:
    out = {"network": NETWORK, "contract": _contract(), "kind": request["kind"], "side": request["side"],
           "symbol": request["symbol"], "market": request["market"], "decision_digest": request["decision"], **result}
    if request.get("weth") is not None:
        out["scale"] = SCALE
    out["label"] = _label(out.get("status", "failed"), request["symbol"])
    if result.get("recovered"):
        # The contract had this decision already: a run that lost its push race sent it, and its dossier was
        # dropped. The transaction recorded here is that earlier one, read back from the chain.
        out["label"] += " (sent by an earlier attempt whose dossier lost a push race; transaction read back from the chain)"
    return out


def open_request(seq: int, symbol: str, verdict: dict, evidence: dict) -> dict | None:
    if verdict.get("action") not in ("LONG", "SHORT") or verdict.get("entry") is None:
        return None
    market = MARKETS.get(symbol)
    return {"kind": "open", "seq": seq, "symbol": symbol, "side": verdict["action"].lower(), "market": market,
            "decision": decision_digest(seq, symbol, verdict, evidence),
            "weth": round(verdict["size"] * SCALE, 6) if market else None}


def execute_open(seq: int, symbol: str, verdict: dict, evidence: dict, execute) -> dict | None:
    """The execution record for a new position, or None when there is no executor or no position."""
    if execute is None:
        return None
    request = open_request(seq, symbol, verdict, evidence)
    if request is None:
        return None
    try:
        result = execute(request)
    except Exception as err:  # the decision must still be recorded; the failure is part of the record
        result = {"status": "failed", "error": str(err)[:300]}
    return _record(request, result)


def _closed(ledger: list[dict]) -> set[int]:
    return {c["opened_seq"] for d in ledger for c in d.get("executions_closed") or [] if c.get("status") in DONE}


def plan_closes(ledger: list[dict], symbol: str, packet: dict) -> list[dict]:
    """Close requests for this symbol's on-chain positions whose stop or target the candles now reach."""
    already = _closed(ledger)
    requests = []
    for i, d in enumerate(ledger):
        ex = d.get("execution") or {}
        if d["evidence"].get("symbol") != symbol or ex.get("kind") != "open" or ex.get("status") not in DONE or d["seq"] in already:
            continue
        later = portfolio._later_candles(ledger, i)
        seen = {c["t"] for c in later}
        entry_t = d["evidence"]["market"]["candles"][-1]["t"]
        later += [c for c in packet["market"]["candles"] if c["t"] > entry_t and c["t"] not in seen]
        outcome = portfolio.resolve(d, sorted(later, key=lambda c: c["t"]))
        if outcome["status"] not in ("STOP", "TAKE"):
            continue
        request = {"kind": "close", "seq": d["seq"], "opened_seq": d["seq"], "symbol": symbol, "side": ex["side"],
                   "market": ex.get("market"), "decision": ex["decision_digest"], "reason": outcome["status"], "exit": outcome["exit"]}
        if ex.get("status") == "executed":
            # undo the opening swap: a SHORT sold WETH for USDC, so it buys back with that USDC; a LONG sells the WETH it got
            request["token_in"] = "usdc" if ex["side"] == "short" else "weth"
            request["amount_in"] = ex.get("amount_out")
        requests.append(request)
    return requests


def execute_closes(ledger: list[dict], symbol: str, packet: dict, execute) -> list[dict]:
    if execute is None:
        return []
    out = []
    for request in plan_closes(ledger, symbol, packet):
        try:
            result = execute(request)
        except Exception as err:
            result = {"status": "failed", "error": str(err)[:300]}
        out.append({**_record(request, result), "opened_seq": request["opened_seq"], "reason": request["reason"], "exit": request["exit"]})
    return out


def chain_executor():
    """The real executor, when a key, the deployment record, node and the script are all present; else None."""
    if not os.environ.get("SEPOLIA_PRIVATE_KEY") or not DEPLOYMENT.exists() or not SCRIPT.exists() or not shutil.which("node"):
        return None

    def execute(request: dict) -> dict:
        out = subprocess.run(["node", str(SCRIPT), json.dumps(request)], capture_output=True, text=True, timeout=300, cwd=SCRIPT.parent)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"status": "failed", "error": (out.stderr or out.stdout or "no output")[-300:]}

    return execute
