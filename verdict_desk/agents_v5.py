"""Regras v5: v4 + o peso de cada voz da convergencia vem da habilidade medida dela.

A v4 conta 0.5 por voz distinta. A v5 conta 0.5 x o peso da voz, e o peso e o historico de acertos
ACIMA DO ACASO que o proprio ledger mediu (trust.py), congelado no dossier e recalculavel so com os
dossiers anteriores (`python3 -m verdict_desk trust-check`). Voz sem historico vale 1.0, entao a v5
so se afasta da v4 quando ha evidencia. Os dossies v1 a v4 nao mudam: cada um carrega a versao que o gravou.
"""

from . import agents_v2 as _v2

V5 = _v2.V5
DEFAULT = V5
Rules = _v2.Rules
SYSTEM_MAX_RISK_PCT = _v2.SYSTEM_MAX_RISK_PCT
with_risk_pct = _v2.with_risk_pct
convergence_args = _v2.convergence_args
bull = _v2.bull
bear = _v2.bear
judge = _v2.judge
sensitivity = _v2.sensitivity


def decide(f: dict, rules: Rules = DEFAULT, narrative: dict | None = None, symbol: str | None = None) -> dict:
    return _v2.decide(f, rules, narrative, symbol)
