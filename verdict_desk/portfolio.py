"""Carteira simulada: resolve cada posicao do ledger contra candles posteriores.

Nada e inventado: o resultado de uma posicao so usa candles que aparecem na
evidencia congelada de dossies registrados depois dela. Se stop e alvo caem no
mesmo candle, vale o stop (hipotese conservadora). Sem candle posterior, a
posicao fica OPEN e nao conta no resultado realizado.
"""

START_CAPITAL = 10_000.0
# Fee model of the net result: a taker fee on the notional at entry and again at exit, the Binance USD-M futures
# base rate (the desk reads perpetual funding, so futures fees are the coherent model; the backtest uses the same
# rate). Slippage is not modelled.
FEE_PCT_PER_SIDE = 0.05


def _last_candle_time(dossier: dict) -> int:
    return dossier["evidence"]["market"]["candles"][-1]["t"]


def _later_candles(ledger: list[dict], index: int) -> list[dict]:
    """Candles de dossies posteriores do mesmo simbolo, sem repeticao, em ordem."""
    symbol = ledger[index]["evidence"]["symbol"]
    entry_t = _last_candle_time(ledger[index])
    seen, out = set(), []
    for later in ledger[index + 1 :]:
        if later["evidence"]["symbol"] != symbol:
            continue
        for c in later["evidence"]["market"]["candles"]:
            if c["t"] > entry_t and c["t"] not in seen:
                seen.add(c["t"])
                out.append(c)
    return sorted(out, key=lambda c: c["t"])


def market_series(ledger: list[dict]) -> dict:
    """Every candle the ledger froze, per symbol, once, in the version first recorded (what _later_candles keeps).

    Ask the record replays "what happened next" on it in the browser: the candles after a dossier's last one are
    exactly the ones _later_candles would collect, so the position resolves the same as here."""
    out: dict[str, dict] = {}
    for d in ledger:
        entry = out.setdefault(d["evidence"]["symbol"], {"candles": {}, "records": []})
        entry["records"].append(d["seq"])
        for c in d["evidence"]["market"]["candles"]:
            entry["candles"].setdefault(c["t"], c)
    return {s: {"records": e["records"], "candles": [e["candles"][t] for t in sorted(e["candles"])]} for s, e in out.items()}


def resolve(dossier: dict, candles: list[dict]) -> dict:
    v = dossier["verdict"]
    direction = 1 if v["action"] == "LONG" else -1
    base = {"seq": dossier["seq"], "symbol": dossier["evidence"]["symbol"], "action": v["action"], "entry": v["entry"]}
    for bars, c in enumerate(candles, 1):
        hit_stop = c["l"] <= v["stop"] if direction == 1 else c["h"] >= v["stop"]
        hit_take = c["h"] >= v["take_profit"] if direction == 1 else c["l"] <= v["take_profit"]
        if hit_stop or hit_take:
            exit_price = v["stop"] if hit_stop else v["take_profit"]
            pnl = (exit_price - v["entry"]) * direction * v["size"]
            return {**base, "status": "STOP" if hit_stop else "TAKE", "exit": exit_price, "pnl": round(pnl, 4), "bars": bars}
    if candles:
        mark = candles[-1]["c"]
        return {**base, "status": "OPEN", "exit": mark, "pnl": round((mark - v["entry"]) * direction * v["size"], 4), "bars": len(candles)}
    return {**base, "status": "OPEN", "exit": v["entry"], "pnl": 0.0, "bars": 0}


POSITION_ACTIONS = ("LONG", "SHORT")


def summarize(ledger: list[dict]) -> dict:
    positions = [
        resolve(d, _later_candles(ledger, i))
        for i, d in enumerate(ledger)
        if d["verdict"]["action"] in POSITION_ACTIONS
    ]
    closed = [p for p in positions if p["status"] != "OPEN"]
    wins = [p for p in closed if p["pnl"] > 0]
    realized = round(sum(p["pnl"] for p in closed), 4)
    sizes = {d["seq"]: d["verdict"]["size"] for d in ledger if d["verdict"]["action"] in POSITION_ACTIONS}
    fees = round(sum((p["entry"] + p["exit"]) * sizes[p["seq"]] * FEE_PCT_PER_SIDE / 100 for p in closed), 4)
    unrealized = round(sum(p["pnl"] for p in positions if p["status"] == "OPEN"), 4)
    equity, peak, max_dd = START_CAPITAL, START_CAPITAL, 0.0
    for p in closed:
        equity += p["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
    return {
        "decisions": len(ledger),
        "flat": sum(1 for d in ledger if d["verdict"]["action"] not in POSITION_ACTIONS),
        "positions": positions,
        "closed": len(closed),
        "open": len(positions) - len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "realized_pnl": realized,
        "fees": fees,
        "realized_net": round(realized - fees, 4),
        "fee_pct_per_side": FEE_PCT_PER_SIDE,
        "unrealized_pnl": unrealized,
        "max_drawdown_pct": round(max_dd, 2),
        "start_capital": START_CAPITAL,
    }
