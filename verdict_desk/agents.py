"""Debate touro contra urso e juiz, todos deterministicos.

Cada argumento cita o indicador exato (evidence_ref) e o valor que o sustenta,
para a causa e o efeito ficarem explicitos e o replay dar sempre o mesmo veredito.
"""

from typing import NamedTuple

RULES_VERSION = "v1"
ENTRY_THRESHOLD = 2.0
MAX_RISK_PCT = 1.0  # risco maximo por operacao, em % do capital simulado
STOP_ATR = 1.5
TAKE_ATR = 3.0


class Rules(NamedTuple):
    """Parametros do juiz. V1 e fixa e e a que roda no historico ao vivo."""

    version: str = RULES_VERSION
    entry_threshold: float = ENTRY_THRESHOLD
    stop_atr: float = STOP_ATR
    take_atr: float = TAKE_ATR
    max_risk_pct: float = MAX_RISK_PCT


V1 = Rules()


def _arg(claim: str, ref: str, value, weight: float) -> dict:
    return {"claim": claim, "evidence_ref": ref, "value": value, "weight": weight}


def bull(f: dict) -> list[dict]:
    args = []
    if f["ema_fast"] > f["ema_slow"]:
        args.append(_arg("Short-term trend above long-term trend", "features.ema_fast>ema_slow", [f["ema_fast"], f["ema_slow"]], 1.0))
    if f["last_close"] > f["range_high_24"]:
        args.append(_arg("Close broke above the 24-candle range high", "features.range_high_24", f["range_high_24"], 1.5))
    if f["return_24"] > 2:
        args.append(_arg("Strong momentum over the last 24 candles", "features.return_24", f["return_24"], 0.75))
    if f["volume_ratio"] > 1.5 and f["return_24"] > 0:
        args.append(_arg("Rising price on above-average volume", "features.volume_ratio", f["volume_ratio"], 0.75))
    if f["funding_rate"] is not None and f["funding_rate"] < 0:
        args.append(_arg("Negative funding: shorts are paying, crowded short side", "features.funding_rate", f["funding_rate"], 0.5))
    if f["news"]["bullish"] > f["news"]["bearish"]:
        args.append(_arg("News flow leans positive", "features.news.bullish_titles", f["news"]["bullish_titles"], 0.5))
    return args


def bear(f: dict) -> list[dict]:
    args = []
    if f["ema_fast"] < f["ema_slow"]:
        args.append(_arg("Short-term trend below long-term trend", "features.ema_fast<ema_slow", [f["ema_fast"], f["ema_slow"]], 1.0))
    if f["last_close"] < f["range_low_24"]:
        args.append(_arg("Close broke below the 24-candle range low", "features.range_low_24", f["range_low_24"], 1.5))
    if f["return_24"] < -2:
        args.append(_arg("Strong downside momentum over the last 24 candles", "features.return_24", f["return_24"], 0.75))
    if f["volume_ratio"] > 1.5 and f["return_24"] < 0:
        args.append(_arg("Falling price on above-average volume", "features.volume_ratio", f["volume_ratio"], 0.75))
    if f["funding_rate"] is not None and f["funding_rate"] > 0.0005:
        args.append(_arg("High positive funding: longs are crowded", "features.funding_rate", f["funding_rate"], 0.5))
    if f["news"]["bearish"] > f["news"]["bullish"]:
        args.append(_arg("News flow leans negative", "features.news.bearish_titles", f["news"]["bearish_titles"], 0.5))
    return args


def _action(edge: float, rules: Rules) -> str:
    if edge >= rules.entry_threshold:
        return "LONG"
    if edge <= -rules.entry_threshold:
        return "SHORT"
    return "FLAT"


def sensitivity(bull_args: list[dict], bear_args: list[dict], rules: Rules = V1) -> dict:
    """Causa e efeito explicitos: o que mudaria o veredito.

    `needs_for_long` e `needs_for_short` dizem quanto de vantagem liquida faltaria para
    cada entrada. `drop_one` refaz o juiz sem cada argumento, um de cada vez, e marca os
    que sozinhos virariam a decisao. Tudo vem dos mesmos argumentos e pesos do juiz.
    """
    edge = round(sum(a["weight"] for a in bull_args) - sum(a["weight"] for a in bear_args), 4)
    base = _action(edge, rules)
    drops = []
    for side, args in (("bull", bull_args), ("bear", bear_args)):
        for a in args:
            without = round(edge - a["weight"] if side == "bull" else edge + a["weight"], 4)
            action = _action(without, rules)
            drops.append({"side": side, "claim": a["claim"], "weight": a["weight"], "edge_without": without, "action_without": action, "flips": action != base})
    return {
        "edge": edge,
        "needs_for_long": max(0.0, round(rules.entry_threshold - edge, 4)),
        "needs_for_short": max(0.0, round(rules.entry_threshold + edge, 4)),
        "drop_one": drops,
    }


def judge(f: dict, bull_args: list[dict], bear_args: list[dict], capital: float = 10_000.0, rules: Rules = V1) -> dict:
    bull_score = sum(a["weight"] for a in bull_args)
    bear_score = sum(a["weight"] for a in bear_args)
    edge = round(bull_score - bear_score, 4)
    action = _action(edge, rules)
    verdict = {
        "rules_version": rules.version,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "edge": edge,
        "threshold": rules.entry_threshold,
        "action": action,
        "rationale": (
            f"Bull {bull_score} vs bear {bear_score}: edge {edge} "
            + ("reaches" if action != "FLAT" else "does not reach")
            + f" the entry threshold of {rules.entry_threshold}."
        ),
    }
    if action == "FLAT":
        return verdict
    entry = f["last_close"]
    stop_distance = f["atr_14"] * rules.stop_atr
    direction = 1 if action == "LONG" else -1
    risk_cash = capital * rules.max_risk_pct / 100
    verdict.update(
        {
            "entry": entry,
            "stop": round(entry - direction * stop_distance, 6),
            "take_profit": round(entry + direction * f["atr_14"] * rules.take_atr, 6),
            "size": round(risk_cash / stop_distance, 8),
            "risk_pct": rules.max_risk_pct,
            "simulated": True,
        }
    )
    return verdict


def decide(f: dict, rules: Rules = V1, narrative: dict | None = None, symbol: str | None = None) -> dict:
    bull_args = bull(f)
    bear_args = bear(f)
    return {
        "bull": bull_args,
        "bear": bear_args,
        "verdict": judge(f, bull_args, bear_args, rules=rules),
        "sensitivity": sensitivity(bull_args, bear_args, rules),
    }
