"""Debate touro/urso v2 com indicadores tecnicos avancados (RSI, MACD, Bollinger).

Mantem compatibilidade com v1 (mesmo juiz, mesma hash chain).
Adiciona argumentos baseados em indicadores que geram edge mais alto.
"""

import math
from typing import NamedTuple

RULES_VERSION = "v2"
ENTRY_THRESHOLD = 1.5  # Reduced from 2.0 to allow more decisions while still requiring real signal
MAX_RISK_PCT = 1.0
# Teto do sistema: um usuario pode REDUZIR o risco, nunca elevar acima disto.
# O limite declarado fica congelado no dossie ao lado do teto, entao quem audita ve
# exatamente qual regra valeu naquela decisao.
SYSTEM_MAX_RISK_PCT = 25.0
STOP_ATR = 1.5
TAKE_ATR = 3.0


class Rules(NamedTuple):
    version: str = RULES_VERSION
    entry_threshold: float = ENTRY_THRESHOLD
    stop_atr: float = STOP_ATR
    take_atr: float = TAKE_ATR
    max_risk_pct: float = MAX_RISK_PCT
    # v3: quando True, um FLAT ganha um nome que diz POR QUE ficou de fora
    # (WATCH_REVERSAL / REDUCE_EXPOSURE). Desligado por padrao para que os
    # dossies gravados em v2 continuem reproduzindo exatamente como foram.
    rich_states: bool = False
    volatility_pct: float = 1.5
    # v4: quando True, uma convergencia da narrativa multi-KOL vira argumento
    # deterministico (regra transparente de peso), fechando o destaque do premio:
    # "criar uma posicao pratica usando regras de sinalizacao transparentes".
    narrative_signal: bool = False
    # v5: o peso de cada voz da convergencia vem da habilidade medida dela (trust.py), congelada
    # no proprio dossier. Sem historico a voz vale 1.0, o mesmo da v4.
    trust_weighted: bool = False
    # v6: um juiz LLM le so as afirmacoes que passaram no validador de citacao e inclina para um lado.
    # A inclinacao vira um argumento com peso de tabela (teto 1.0, abaixo da faixa de entrada), entao o
    # LLM desempata mas nunca abre posicao sozinho. O peso e recalculado do veredito congelado no replay.
    llm_judge: bool = False


V2 = Rules()
V3 = Rules(version="v3", rich_states=True)
V4 = Rules(version="v4", rich_states=True, narrative_signal=True)
V5 = Rules(version="v5", rich_states=True, narrative_signal=True, trust_weighted=True)
V6 = Rules(version="v6", rich_states=True, narrative_signal=True, trust_weighted=True, llm_judge=True)
DEFAULT = V2


def convergence_args(narrative, symbol, trust_weighted: bool = False) -> tuple[list[dict], list[dict]]:
    """Convergencia multi-KOL -> argumento deterministico, com peso transparente.

    Regra (documentada, replay reproduz): cada convergencia que casa com o simbolo vira um
    argumento do lado correspondente (bullish -> bull, bearish -> bear). Peso = 0.5 por voz
    distinta (maximo 4) + 0.5 se a conviccao de topo for 'high'. Duas vozes sem conviccao alta
    valem 1.0; quatro vozes com conviccao alta valem 2.5. Nada de LLM decide aqui.

    v5 (`trust_weighted`): cada voz vale 0.5 x o peso congelado em `narrative.trust` (1.0 sem
    historico), contando as 4 de maior peso. Uma voz que acertou acima do acaso pesa mais; uma que
    nao, menos. A nota vem do ledger anterior ao dossier e e verificavel (`trust-check`).
    """
    if not narrative or not symbol:
        return [], []
    base = symbol.upper()
    for suffix in ("USDT", "USDC", "BUSD", "USDE", "USD", "DAI"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    bull_args, bear_args = [], []
    for c in narrative.get("convergence") or []:
        if (c.get("token") or "").upper() != base:
            continue
        voices = list(c.get("voices") or [])
        n = len(voices)
        if trust_weighted:
            table = (narrative.get("trust") or {}).get("voices") or {}
            weights = {v: float((table.get(v) or {}).get("weight", 1.0)) for v in voices}
            weight = math.fsum(sorted(weights.values(), reverse=True)[:4]) * 0.5  # fsum: the same exact sum in every Python and in web/trust.js
        else:
            weight = min(n, 4) * 0.5
        if c.get("max_conviction") == "high":
            weight += 0.5
        weight = round(weight, 2)
        claim = (f"{n} market voices converge {c.get('bias')} on {c.get('token')} "
                 f"(top conviction {c.get('max_conviction')}, urgency {c.get('max_urgency')})")
        if trust_weighted:
            claim += ", weighted by track record: " + ", ".join(f"{v} {w:.2f}" for v, w in sorted(weights.items()))
        arg = {"claim": claim, "evidence_ref": f"narrative.convergence.{c.get('token')}",
               "value": voices, "weight": weight}
        (bull_args if c.get("bias") == "bullish" else bear_args).append(arg)
    return bull_args, bear_args


def llm_judge_args(layer) -> tuple[list[dict], list[dict]]:
    """Veredito do juiz LLM -> no maximo um argumento, so se ele se apoia em afirmacoes validadas.

    O peso sai de `citations.validate_ruling` sobre o que o dossier congelou (afirmacoes aceitas,
    indice de evidencia, veredito bruto), nunca de um campo gravado: editar o peso nao muda nada,
    editar o veredito muda a decisao e o replay acusa.
    """
    from . import citations

    if not isinstance(layer, dict) or not isinstance(layer.get("judge"), dict):
        return [], []
    ruling = layer["judge"].get("ruling")
    bull_ok = (layer.get("bull") or {}).get("accepted") or []
    bear_ok = (layer.get("bear") or {}).get("accepted") or []
    check = citations.validate_ruling(ruling, bull_ok, bear_ok, layer.get("evidence_index") or {})
    if not check["accepted"] or check["weight"] <= 0:
        return [], []
    relied = list(ruling["relied_on"])
    arg = {"claim": f"LLM judge leans {ruling['lean']} ({ruling['conviction']} conviction) on validated claims {', '.join(relied)}",
           "evidence_ref": "llm.judge", "value": relied, "weight": check["weight"]}
    return ([arg], []) if ruling["lean"] == "bull" else ([], [arg])


def with_risk_pct(rules: Rules, pct) -> Rules:
    """Aplica um risco pedido pelo usuario, recusando o que passa do teto do sistema.

    Recusar em voz alta (em vez de cortar em silencio) importa: um usuario que pede 30%
    precisa saber que a regra gravada foi 25%, nao descobrir depois auditando.
    """
    if pct is None:
        return rules
    try:
        value = float(pct)
    except (TypeError, ValueError):
        raise ValueError(f"risk pct must be a number, got {pct!r}") from None
    if not (value > 0):
        raise ValueError(f"risk pct must be greater than 0, got {value}")
    if value > SYSTEM_MAX_RISK_PCT:
        raise ValueError(f"risk pct {value} exceeds the system cap of {SYSTEM_MAX_RISK_PCT}%")
    return rules._replace(max_risk_pct=value)


def _arg(claim: str, ref: str, value, weight: float) -> dict:
    return {"claim": claim, "evidence_ref": ref, "value": value, "weight": weight}


def bull(f: dict) -> list[dict]:
    args = []
    # === Indicadores v1 (mantidos) ===
    if f["ema_fast"] > f["ema_slow"]:
        args.append(_arg("Short-term trend above long-term trend", "features.ema_fast>ema_slow", [f["ema_fast"], f["ema_slow"]], 1.5))
    if f["last_close"] > f["range_high_24"]:
        args.append(_arg("Close broke above the 24-candle range high", "features.range_high_24", f["range_high_24"], 2.0))
    if f["return_24"] > 2:
        args.append(_arg("Strong momentum over the last 24 candles", "features.return_24", f["return_24"], 0.75))
    if f["volume_ratio"] > 1.5 and f["return_24"] > 0:
        args.append(_arg("Rising price on above-average volume", "features.volume_ratio", f["volume_ratio"], 0.75))
    if f["funding_rate"] is not None and f["funding_rate"] < 0:
        args.append(_arg("Negative funding: shorts are paying, crowded short side", "features.funding_rate", f["funding_rate"], 0.5))
    if f["news"]["bullish"] > f["news"]["bearish"]:
        args.append(_arg("News flow leans positive", "features.news.bullish_titles", f["news"]["bullish_titles"], 0.5))

    # === Novos indicadores v2 ===
    # RSI oversold = bounce coming
    if f.get("rsi_14") is not None and f["rsi_14"] < 35:
        args.append(_arg("RSI oversold (<35): bounce likely", "features.rsi_14", f["rsi_14"], 1.25))
    # MACD bullish crossover
    if f.get("macd_histogram") is not None and f["macd_histogram"] > 0:
        args.append(_arg("MACD histogram positive: bullish momentum", "features.macd_histogram", f["macd_histogram"], 1.0))
    # Bollinger: price at lower band = oversold
    if f.get("bb_pct_b") is not None and f["bb_pct_b"] < 0.1:
        args.append(_arg("Price near Bollinger lower band (%B<0.1): oversold", "features.bb_pct_b", f["bb_pct_b"], 1.0))
    # Volume surge confirmation
    if f["volume_ratio"] > 2.0:
        args.append(_arg("Volume surge (>2x average): strong conviction", "features.volume_ratio", f["volume_ratio"], 1.0))
    # Mean reversion from Bollinger lower
    if f.get("bb_lower") is not None and f["last_close"] < f["bb_lower"]:
        args.append(_arg("Price below Bollinger lower band: mean reversion likely", "features.bb_lower", f["bb_lower"], 1.25))
    return args


def bear(f: dict) -> list[dict]:
    args = []
    # === Indicadores v1 (mantidos) ===
    if f["ema_fast"] < f["ema_slow"]:
        args.append(_arg("Short-term trend below long-term trend", "features.ema_fast<ema_slow", [f["ema_fast"], f["ema_slow"]], 1.5))
    if f["last_close"] < f["range_low_24"]:
        args.append(_arg("Close broke below the 24-candle range low", "features.range_low_24", f["range_low_24"], 2.0))
    if f["return_24"] < -2:
        args.append(_arg("Strong downside momentum over the last 24 candles", "features.return_24", f["return_24"], 1.0))
    if f["return_24"] < -4:
        args.append(_arg("Severe drop over 24 candles: selloff intensifying", "features.return_24", f["return_24"], 1.5))
    if f["volume_ratio"] > 1.5 and f["return_24"] < 0:
        args.append(_arg("Falling price on above-average volume", "features.volume_ratio", f["volume_ratio"], 0.75))
    if f["funding_rate"] is not None and f["funding_rate"] > 0.0005:
        args.append(_arg("High positive funding: longs are crowded", "features.funding_rate", f["funding_rate"], 0.5))
    if f["news"]["bearish"] > f["news"]["bullish"]:
        args.append(_arg("News flow leans negative", "features.news.bearish_titles", f["news"]["bearish_titles"], 0.5))

    # === Novos indicadores v2 ===
    # RSI overbought = pullback coming
    if f.get("rsi_14") is not None and f["rsi_14"] > 65:
        args.append(_arg("RSI elevated (>65): overbought, pullback risk", "features.rsi_14", f["rsi_14"], 1.0))
    if f.get("rsi_14") is not None and f["rsi_14"] > 75:
        args.append(_arg("RSI very high (>75): extreme overbought", "features.rsi_14", f["rsi_14"], 1.25))
    # MACD bearish crossover
    if f.get("macd_histogram") is not None and f["macd_histogram"] < 0:
        args.append(_arg("MACD histogram negative: bearish momentum", "features.macd_histogram", f["macd_histogram"], 1.0))
    # Bollinger: price at upper band = overbought
    if f.get("bb_pct_b") is not None and f["bb_pct_b"] > 0.85:
        args.append(_arg("Price near Bollinger upper band (%B>0.85): stretched", "features.bb_pct_b", f["bb_pct_b"], 1.0))
    if f.get("bb_pct_b") is not None and f["bb_pct_b"] > 0.95:
        args.append(_arg("Price above Bollinger upper band (%B>0.95): extreme", "features.bb_pct_b", f["bb_pct_b"], 1.25))
    # Mean reversion from Bollinger upper
    if f.get("bb_upper") is not None and f["last_close"] > f["bb_upper"]:
        args.append(_arg("Price above Bollinger upper: mean reversion due", "features.bb_upper", f["bb_upper"], 1.25))
    # Divergence: price up but momentum weakening
    if f.get("macd_histogram") is not None and f["return_24"] > 0 and f["macd_histogram"] < 0:
        args.append(_arg("Price up but MACD negative: bearish divergence", "features.macd_histogram", f["macd_histogram"], 1.5))
    # Price below both EMAs = strong downtrend
    if f["last_close"] < f["ema_fast"] < f["ema_slow"]:
        args.append(_arg("Price below both EMAs: confirmed downtrend", "features.ema_fast<ema_slow", [f["ema_fast"], f["ema_slow"]], 1.0))
    # Close at/near 24-candle low = distribution
    if f["last_close"] <= f["range_low_24"] * 1.002:
        args.append(_arg("Close at 24-candle low: distribution pattern", "features.range_low_24", f["range_low_24"], 0.75))
    return args


def _action(edge: float, rules: Rules) -> str:
    if edge >= rules.entry_threshold:
        return "LONG"
    if edge <= -rules.entry_threshold:
        return "SHORT"
    return "FLAT"


def _rich_flat(edge: float, bull_args: list[dict], bear_args: list[dict], f: dict, rules: Rules) -> tuple[str, str]:
    """Da nome ao motivo de nao entrar. So roda em v3; v2 continua devolvendo FLAT.

    WATCH_REVERSAL  um unico argumento vale o bastante para virar o veredito.
    REDUCE_EXPOSURE os dois lados estao fortes e a volatilidade esta alta: manter, menor.
    FLAT            nada disso: fora do radar mesmo.
    """
    threshold = rules.entry_threshold
    bull_score = sum(a["weight"] for a in bull_args)
    bear_score = sum(a["weight"] for a in bear_args)
    last_close = f.get("last_close") or 0
    atr_pct = (f.get("atr_14") or 0) / last_close * 100 if last_close else 0.0

    # Ordem de leitura deliberada: primeiro a instrucao de risco (os dois lados
    # fortes com volatilidade alta), depois o alerta de que um unico argumento
    # decide. Quando os dois valem, "reduzir" e a acao, nao "vigiar".
    if min(bull_score, bear_score) >= threshold and atr_pct >= rules.volatility_pct:
        return "REDUCE_EXPOSURE", (
            f"Neither side clears the band (bull {bull_score} vs bear {bear_score}) while both are strong "
            f"and volatility is high ({atr_pct:.2f}% ATR): nothing new is opened and existing exposure should shrink."
        )

    for a in bull_args:
        if edge - a["weight"] <= -threshold:
            return "WATCH_REVERSAL", (
                f"Edge {edge} sits inside the entry band, but the bull argument "
                f"'{a['claim']}' is worth {a['weight']}: losing it alone would flip the verdict to SHORT."
            )
    for a in bear_args:
        if edge + a["weight"] >= threshold:
            return "WATCH_REVERSAL", (
                f"Edge {edge} sits inside the entry band, but the bear argument "
                f"'{a['claim']}' is worth {a['weight']}: losing it alone would flip the verdict to LONG."
            )

    return "FLAT", (
        f"Bull {bull_score} vs bear {bear_score}: edge {edge} does not reach the entry threshold "
        f"of {threshold} and no single argument is worth enough to flip it."
    )


def sensitivity(bull_args: list[dict], bear_args: list[dict], rules: Rules = V2) -> dict:
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


def policy(rules: Rules) -> dict:
    """As regras exatas que produziram este veredito, congeladas no proprio dossie.

    O concorrente forte publica um policy_hash on-chain; nos congelamos o TUPLE legivel das
    regras dentro do veredito. Como o veredito entra no hash do dossie, qualquer mudanca de
    regra muda o hash — e a ancora (Sepolia + Bitcoin) denuncia a troca. E por ser legivel,
    quem audita nao precisa confiar em um hash opaco: le os numeros.
    """
    from . import citations, evidence
    out = {
        "entry_threshold": rules.entry_threshold,
        "stop_atr": rules.stop_atr,
        "take_atr": rules.take_atr,
        "max_risk_pct": rules.max_risk_pct,
        "system_max_risk_pct": SYSTEM_MAX_RISK_PCT,
        "rich_states": rules.rich_states,
        "volatility_pct": rules.volatility_pct,
        "market_window_minutes": evidence.MARKET_WINDOW_MINUTES,
        "slow_window_minutes": evidence.SLOW_WINDOW_MINUTES,
    }
    if rules.llm_judge:  # so a v6 grava a tabela: os vereditos v2 a v5 ficam identicos ao que sempre foram
        out["llm_judge_weights"] = dict(citations.JUDGE_WEIGHT)
    return out


def judge(f: dict, bull_args: list[dict], bear_args: list[dict], capital: float = 10_000.0, rules: Rules = V2) -> dict:
    bull_score = sum(a["weight"] for a in bull_args)
    bear_score = sum(a["weight"] for a in bear_args)
    edge = round(bull_score - bear_score, 4)

    # Fail-closed: evidencia de mercado vencida bloqueia a decisao em vez de
    # decidir com preco velho. O frescor vem congelado nas features, entao o
    # replay reproduz o mesmo bloqueio. Dossies antigos nao tem a chave e
    # seguem decidindo como sempre decidiram.
    fresh = f.get("freshness")
    if fresh is not None and not fresh.get("ok", True):
        return {
            "rules_version": rules.version,
            "policy": policy(rules),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "edge": edge,
            "threshold": rules.entry_threshold,
            "action": "BLOCKED_STALE",
            "rationale": (
                f"Refused to decide: {fresh.get('reason') or 'evidence is stale'}. "
                "The evidence is still frozen in this dossier so the refusal itself is auditable, "
                "but no simulated position is opened on stale prices."
            ),
            "freshness": fresh,
        }

    action = _action(edge, rules)
    if action == "FLAT" and rules.rich_states:
        action, rationale = _rich_flat(edge, bull_args, bear_args, f, rules)
    else:
        rationale = (
            f"Bull {bull_score} vs bear {bear_score}: edge {edge} "
            + ("reaches" if action != "FLAT" else "does not reach")
            + f" the entry threshold of {rules.entry_threshold}."
        )
    verdict = {
        "rules_version": rules.version,
        "policy": policy(rules),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "edge": edge,
        "threshold": rules.entry_threshold,
        "action": action,
        "rationale": rationale,
    }
    if action not in ("LONG", "SHORT"):
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
            "system_max_risk_pct": SYSTEM_MAX_RISK_PCT,
            "simulated": True,
        }
    )
    return verdict


def decide(f: dict, rules: Rules = V2, narrative: dict | None = None, symbol: str | None = None, llm: dict | None = None) -> dict:
    bull_args = bull(f)
    bear_args = bear(f)
    if rules.narrative_signal:
        cb, cs = convergence_args(narrative, symbol, rules.trust_weighted)
        bull_args.extend(cb)
        bear_args.extend(cs)
    if rules.llm_judge:
        jb, js = llm_judge_args(llm)
        bull_args.extend(jb)
        bear_args.extend(js)
    return {
        "bull": bull_args,
        "bear": bear_args,
        "verdict": judge(f, bull_args, bear_args, rules=rules),
        "sensitivity": sensitivity(bull_args, bear_args, rules),
    }
