"""Pesquisa de regras v2 com divisao treino e teste, separada do historico ao vivo.

Protocolo (fixado antes de olhar o resultado):
1. A serie e cortada no tempo: os primeiros `TRAIN_FRACTION` candles treinam, o resto testa.
2. Uma grade pequena e fixa de parametros do juiz e avaliada SO no treino; a escolha e o
   melhor resultado liquido de taxa com no minimo `MIN_TRADES` operacoes.
3. Os parametros escolhidos ficam congelados e rodam no teste, ao lado da v1 no mesmo trecho.
4. O resultado sai rotulado, com o numero de configuracoes testadas (comparacoes multiplas
   inflam o melhor do treino) e a serie identificada por hash para reproduzir.

A v1 nao muda e segue sendo a que decide no historico ao vivo. A v2 so entraria ao vivo se
vencesse a v1 fora da amostra em varios ativos, e entraria com `rules_version` proprio.
"""

import itertools

from . import agents, backtest

TRAIN_FRACTION = 0.6
MIN_TRADES = 20
GRID = {
    "entry_threshold": (1.5, 2.0, 2.5, 3.0),
    "stop_atr": (1.0, 1.5, 2.0),
    "take_atr": (2.0, 3.0, 4.0),
}
LABEL = "RESEARCH on past data with a time split, not live performance"


def configs() -> list[agents.Rules]:
    return [
        agents.Rules(version="v2-candidate", entry_threshold=e, stop_atr=s, take_atr=t)
        for e, s, t in itertools.product(GRID["entry_threshold"], GRID["stop_atr"], GRID["take_atr"])
    ]


def split(candles: list[dict], window: int = backtest.WINDOW, fraction: float = TRAIN_FRACTION) -> tuple[list[dict], list[dict]]:
    """Treino e teste em ordem de tempo. O teste leva `window` candles antes do corte so para aquecer os indicadores."""
    cut = int(len(candles) * fraction)
    return candles[:cut], candles[cut - window :]


def _evaluate(candles: list[dict], symbol: str, rules: agents.Rules, fee_pct: float, max_hold: int) -> dict:
    trades = backtest.run(candles, symbol, max_hold=max_hold, fee_pct=fee_pct, rules=rules)
    return backtest.summarize(trades, candles) if trades else {"trades": 0, "net_pnl": 0.0, "return_pct": 0.0, "profit_factor": None, "win_rate_pct": None,
                                                                "max_drawdown_pct": 0.0, "buy_and_hold_pct": 0.0}


def study(symbol: str, candles: list[dict], fee_pct: float = backtest.FEE_PCT_PER_SIDE, max_hold: int = backtest.MAX_HOLD) -> dict:
    train, test = split(candles)
    scored = []
    for rules in configs():
        summary = _evaluate(train, symbol, rules, fee_pct, max_hold)
        if summary["trades"] >= MIN_TRADES:
            scored.append((summary["net_pnl"], rules, summary))
    scored.sort(key=lambda item: item[0], reverse=True)
    result = {
        "label": LABEL,
        "symbol": symbol.upper(),
        "data": {"candles": len(candles), "from": candles[0]["t"], "to": candles[-1]["t"], "sha256": backtest.series_hash(candles),
                 "train_candles": len(train), "test_candles": len(test) - backtest.WINDOW},
        "protocol": {"train_fraction": TRAIN_FRACTION, "min_trades": MIN_TRADES, "configs_tried": len(configs()), "grid": GRID,
                     "fee_pct_per_side": fee_pct, "max_hold": max_hold},
        "v1_test": _evaluate(test, symbol, agents.V1, fee_pct, max_hold),
        "v1_train": _evaluate(train, symbol, agents.V1, fee_pct, max_hold),
    }
    if not scored:
        result["v2"] = None
        result["note"] = "no configuration reached the minimum number of training trades"
        return result
    _, best, train_summary = scored[0]
    test_summary = _evaluate(test, symbol, best, fee_pct, max_hold)
    result["v2"] = {
        "chosen": {"entry_threshold": best.entry_threshold, "stop_atr": best.stop_atr, "take_atr": best.take_atr},
        "train": train_summary,
        "test": test_summary,
        "test_beats_v1": test_summary["net_pnl"] > result["v1_test"]["net_pnl"],
        "test_net_positive": test_summary["net_pnl"] > 0,
        "train_rank_of_chosen": 1,
        "train_configs_with_positive_net": sum(1 for net, _, _ in scored if net > 0),
        "configs_scored": len(scored),
    }
    return result


def verdict(studies: list[dict]) -> dict:
    """Leitura agregada sem enfeite: a v2 so merece ir ao vivo se vencer a v1 e ficar positiva fora da amostra na maioria dos ativos."""
    usable = [s for s in studies if s.get("v2")]
    beats = sum(1 for s in usable if s["v2"]["test_beats_v1"])
    positive = sum(1 for s in usable if s["v2"]["test_net_positive"])
    adopt = bool(usable) and beats > len(usable) / 2 and positive > len(usable) / 2
    return {
        "symbols": [s["symbol"] for s in studies],
        "v2_beats_v1_out_of_sample": f"{beats} of {len(usable)}",
        "v2_net_positive_out_of_sample": f"{positive} of {len(usable)}",
        "adopt_v2_live": adopt,
        "reading": ("v2 beat v1 and stayed positive out of sample on most symbols: still treat as a hypothesis, the grid has "
                    f"{len(configs())} candidates." if adopt else
                    "v2 did not clear the bar (beat v1 and stay positive out of sample on most symbols): v1 stays as the live rule set."),
    }
