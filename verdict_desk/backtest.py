"""Backtest walk-forward sobre candles historicos, separado do historico ao vivo.

As regras (agents.py) sao fixas e nao foram ajustadas a estes dados, entao cada
decisao e fora da amostra por construcao: em cada passo o agente ve so os
`window` candles ate a decisao, nunca o futuro. Funding e noticias nao existem
no historico aqui, por isso o backtest usa so preco e volume (as outras
evidencias contam apenas no historico ao vivo). Custo de taxa entra nos dois
lados. Uma posicao por vez; sem stop nem alvo em `max_hold` candles, fecha a
mercado (TIME). Resultado vem sempre rotulado como teste no passado.
"""

import hashlib
import json
import time

from . import agents, evidence, features, portfolio

WINDOW = 72
MAX_HOLD = 48
FEE_PCT_PER_SIDE = 0.05
LABEL = "BACKTEST on past data, not live performance"


def fetch_history(symbol: str, interval: str, total: int, until_ms: int | None = None) -> list[dict]:
    """Pagina o espelho publico da Binance para tras ate juntar `total` candles.

    `until_ms` fixa o fim da janela (so entram candles que abriram antes dele), para refazer uma medicao com os mesmos dados.
    """
    rows: list = []
    end = until_ms - 1 if until_ms is not None else None
    while len(rows) < total:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(1000, total - len(rows))}
        if end is not None:
            params["endTime"] = end
        _, batch = evidence._get_json(evidence.BINANCE_KLINES, params)
        if not batch:
            break
        rows = batch + rows
        end = int(batch[0][0]) - 1
        time.sleep(0.2)
    return evidence.parse_klines(rows)


def series_hash(candles: list[dict]) -> str:
    """Impressao digital da serie, para qualquer um reproduzir com os mesmos dados."""
    blob = json.dumps(candles, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _packet(symbol: str, window: list[dict]) -> dict:
    return {
        "symbol": symbol,
        "market": {"candles": window},
        "funding": {"rate": None},
        "news": {"articles": []},
    }


def run(candles: list[dict], symbol: str = "BTCUSDT", window: int = WINDOW, max_hold: int = MAX_HOLD,
        fee_pct: float = FEE_PCT_PER_SIDE, rules: agents.Rules = agents.V1) -> list[dict]:
    trades: list[dict] = []
    i = window
    while i < len(candles):
        win = candles[i - window : i]
        verdict = agents.decide(features.compute(_packet(symbol, win)), rules)["verdict"]
        if verdict["action"] == "FLAT":
            i += 1
            continue
        future = candles[i : i + max_hold]
        dossier = {"seq": len(trades) + 1, "evidence": {"symbol": symbol}, "verdict": verdict}
        result = portfolio.resolve(dossier, future)
        if result["status"] == "OPEN" and len(future) < max_hold:
            break  # fim dos dados antes de a operacao resolver: nao conta
        if result["status"] == "OPEN":
            result["status"] = "TIME"
        fees = verdict["entry"] * verdict["size"] * fee_pct / 100 * 2
        result.update(
            t=win[-1]["t"],
            edge=verdict["edge"],
            fees=round(fees, 4),
            net_pnl=round(result["pnl"] - fees, 4),
        )
        trades.append(result)
        i += max(result["bars"], 1)
    return trades


def summarize(trades: list[dict], candles: list[dict], window: int = WINDOW, capital: float = portfolio.START_CAPITAL) -> dict:
    wins = [t for t in trades if t["net_pnl"] > 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in trades if t["net_pnl"] <= 0)
    equity, peak, max_dd = capital, capital, 0.0
    for t in trades:
        equity += t["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
    net = round(sum(t["net_pnl"] for t in trades), 4)
    first, last = candles[window - 1]["c"], candles[-1]["c"]
    return {
        "label": LABEL,
        "trades": len(trades),
        "longs": sum(1 for t in trades if t["action"] == "LONG"),
        "shorts": sum(1 for t in trades if t["action"] == "SHORT"),
        "by_exit": {k: sum(1 for t in trades if t["status"] == k) for k in ("TAKE", "STOP", "TIME")},
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "net_pnl": net,
        "return_pct": round(net / capital * 100, 2),
        "fees_paid": round(sum(t["fees"] for t in trades), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 2),
        "avg_bars_held": round(sum(t["bars"] for t in trades) / len(trades), 1) if trades else None,
        "buy_and_hold_pct": round((last / first - 1) * 100, 2),
        "start_capital": capital,
    }


def segments(trades: list[dict], candles: list[dict], window: int = WINDOW, parts: int = 4) -> list[dict]:
    """Resultado por faixa de tempo igual, para mostrar se o ganho e estavel ou de um trecho so."""
    start, end = candles[window - 1]["t"], candles[-1]["t"]
    step = (end - start) / parts
    out = []
    for k in range(parts):
        lo, hi = start + step * k, start + step * (k + 1)
        chunk = [t for t in trades if lo <= t["t"] < hi or (k == parts - 1 and t["t"] == end)]
        out.append({"part": k + 1, "from": int(lo), "to": int(hi), "trades": len(chunk),
                    "net_pnl": round(sum(t["net_pnl"] for t in chunk), 4)})
    return out


def report(symbol: str, interval: str, candles: list[dict], window: int = WINDOW, max_hold: int = MAX_HOLD,
           fee_pct: float = FEE_PCT_PER_SIDE, rules: agents.Rules = agents.V1) -> dict:
    trades = run(candles, symbol, window, max_hold, fee_pct, rules)
    return {
        "label": LABEL,
        "symbol": symbol.upper(),
        "interval": interval,
        "params": {"window": window, "max_hold": max_hold, "fee_pct_per_side": fee_pct,
                   "rules_version": rules.version, "entry_threshold": rules.entry_threshold,
                   "stop_atr": rules.stop_atr, "take_atr": rules.take_atr},
        "data": {"candles": len(candles), "from": candles[0]["t"], "to": candles[-1]["t"], "sha256": series_hash(candles),
                 "source": evidence.BINANCE_KLINES},
        "summary": summarize(trades, candles, window),
        "segments": segments(trades, candles, window),
        "trades": trades,
    }
