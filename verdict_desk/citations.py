"""Indice de evidencia e validador de citacoes para agentes com LLM.

Toda afirmacao de um agente LLM precisa citar ids de itens do pacote de
evidencia. O validador descarta afirmacao sem citacao, com id inexistente ou
com numero que nao aparece nos itens citados.
"""

import re

from . import narrative as narrative_mod

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
ID_TOKEN = re.compile(r"\bE\d+\b")  # id do indice escrito no texto, ex.: "(E3)": e referencia, nao numero afirmado


def index(packet: dict, feats: dict | None = None) -> dict[str, str]:
    """Mapeia ids estaveis (E1, E2, ...) para o texto de cada item de evidencia.

    Com `feats`, os indicadores deterministicos entram como itens citaveis depois
    dos itens de mercado, noticias e vozes, para touro e urso poderem escolher
    os fatos que sustentam cada lado.
    """
    items: list[str] = []
    candles = packet["market"]["candles"]
    last, first = candles[-1], candles[0]
    items.append(f"{packet['symbol']} last close {last['c']} at t={last['t']}, volume {last['v']}")
    items.append(f"{packet['symbol']} range over {len(candles)} candles: high {max(c['h'] for c in candles)}, low {min(c['l'] for c in candles)}, first close {first['c']}")
    rate = packet.get("funding", {}).get("rate")
    if rate is not None:
        items.append(f"Perpetual funding rate {rate} ({packet['funding'].get('instrument', '')})")
    for article in packet.get("news", {}).get("articles", []):
        items.append(f"News ({article.get('domain', '')}): {article.get('title', '')}")
    block = packet.get("narrative") or {}
    base = packet["symbol"].upper().removesuffix("USDT")
    relevant_only = block.get("index_version", 1) >= 2  # blocos antigos mantem o indice antigo: o replay deles nao muda
    for voice in block.get("voices", []):
        if relevant_only and not narrative_mod.mentions(voice.get("title", ""), base):
            continue
        items.append(f"Voice {voice.get('name', '')}: {voice.get('title', '')}")
    if feats:
        items.append(f"Trend: fast EMA {feats['ema_fast']} versus slow EMA {feats['ema_slow']}")
        items.append(f"Return over the last 24 candles: {feats['return_24']} percent")
        items.append(
            f"Previous 24-candle range: high {feats['range_high_24']}, low {feats['range_low_24']}; last close {feats['last_close']}"
        )
        items.append(f"Last candle volume is {feats['volume_ratio']} times the 24-candle average")
        items.append(f"Average true range over 14 candles: {feats['atr_14']}")
    if "method" in block:  # pacotes antigos nao tinham o campo; seus ids nao mudam
        for c in block.get("convergence", []):
            if relevant_only and c.get("token") != base:
                continue
            extra = f", top conviction {c['max_conviction']}, top urgency {c['max_urgency']}" if "max_conviction" in c else ""
            items.append(f"Narrative convergence: {len(c['voices'])} voices ({', '.join(c['voices'])}) are {c['bias']} on {c['token']}{extra}")
    return {f"E{i}": text for i, text in enumerate(items, 1)}


def validate(claims: list[dict], evidence_items: dict[str, str]) -> dict:
    """Separa afirmacoes aceitas das descartadas, com o motivo de cada descarte."""
    accepted, rejected = [], []
    for claim in claims:
        text = str(claim.get("claim", "")).strip()
        cites = claim.get("cites") or []
        reason = None
        if not text:
            reason = "empty claim"
        elif not cites:
            reason = "no citation"
        elif not isinstance(cites, list) or any(not isinstance(c, str) or c not in evidence_items for c in cites):
            # Id e a string do indice ("E3"): numero solto ou valor aninhado nao e citacao, e um valor
            # nao hashavel tem de ser recusado aqui em vez de derrubar a rodada inteira no teste `in`.
            reason = "unknown evidence id"
        else:
            cited = " ".join(evidence_items[c] for c in cites)
            cited_numbers = {_norm(n) for n in NUMBER.findall(cited)}
            stray = [n for n in NUMBER.findall(ID_TOKEN.sub(" ", text)) if _norm(n) not in cited_numbers]
            if stray:
                reason = f"number not in cited evidence: {stray[0]}"
        (rejected if reason else accepted).append({**claim, **({"rejected_because": reason} if reason else {})})
    return {"accepted": accepted, "rejected": rejected}


# Regras v6: peso do juiz LLM por conviccao. O teto (1.0) fica abaixo da faixa de entrada (1.5), entao o
# juiz sozinho nunca abre posicao: so desempata quando as regras de mercado ja apontam para o mesmo lado.
JUDGE_WEIGHT = {"low": 0.5, "medium": 0.75, "high": 1.0}
CLAIM_TOKEN = re.compile(r"\b[BSE]\d+\b")  # B1, S2, E3 no texto sao referencias, nao numeros afirmados


def claim_labels(bull_accepted: list[dict], bear_accepted: list[dict]) -> dict[str, dict]:
    """Rotulos estaveis das afirmacoes VALIDADAS: B1.. para o touro, S1.. para o urso, na ordem congelada."""
    labels = {f"B{i}": c for i, c in enumerate(bull_accepted, 1)}
    labels.update({f"S{i}": c for i, c in enumerate(bear_accepted, 1)})
    return labels


def validate_ruling(ruling, bull_accepted: list[dict], bear_accepted: list[dict], evidence_items: dict[str, str]) -> dict:
    """Confere o veredito do juiz LLM so com o que o dossier congelou; devolve aceito, peso e motivo.

    Um lado so ganha peso se o juiz se apoiar em pelo menos uma afirmacao validada desse lado, todo id
    citado existir e todo numero do motivo estar na evidencia das afirmacoes em que ele se apoia.
    """
    def void(reason: str) -> dict:
        return {"accepted": False, "weight": 0.0, "rejected_because": reason}

    if not isinstance(ruling, dict):
        return void("ruling is not an object")
    lean, conviction = ruling.get("lean"), ruling.get("conviction")
    if lean not in ("bull", "bear", "neutral"):
        return void(f"unknown lean: {lean!r}")
    if lean == "neutral":
        return {"accepted": True, "weight": 0.0}
    if conviction not in JUDGE_WEIGHT:
        return void(f"unknown conviction: {conviction!r}")
    relied = ruling.get("relied_on")
    if not isinstance(relied, list) or not relied:
        return void("no validated claim relied on")
    labels = claim_labels(bull_accepted, bear_accepted)
    if any(not isinstance(r, str) or r not in labels for r in relied):
        return void(f"unknown claim id: {next(r for r in relied if not isinstance(r, str) or r not in labels)!r}")
    if not any(r.startswith("B" if lean == "bull" else "S") for r in relied):
        return void(f"lean not supported by its own side: {lean} rests on {', '.join(relied)}")
    cited = " ".join(evidence_items.get(e, "") for r in relied for e in labels[r].get("cites") or [] if isinstance(e, str))
    allowed = {_norm(n) for n in NUMBER.findall(cited)}
    stray = [n for n in NUMBER.findall(CLAIM_TOKEN.sub(" ", str(ruling.get("reason", "")))) if _norm(n) not in allowed]
    if stray:
        return void(f"number not in relied-on evidence: {stray[0]}")
    return {"accepted": True, "weight": JUDGE_WEIGHT[conviction]}


def _norm(n: str) -> str:
    try:
        return repr(round(float(n), 6))
    except ValueError:
        return n
