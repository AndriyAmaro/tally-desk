"""Regras v4: v3 + o sinal deterministico da narrativa multi-KOL.

A v3 nomeia os estados de nao-entrada (WATCH_REVERSAL / REDUCE_EXPOSURE). A v4 acrescenta o
destaque do premio: uma convergencia da narrativa multi-KOL vira um argumento deterministico,
com peso transparente (0.5 por voz distinta, max 4, +0.5 se conviccao alta). Assim a narrativa
cria a posicao por uma REGRA de sinalizacao, nao pela opiniao do LLM — e o replay reproduz.

Os dossies v1/v2/v3 nao mudam: o sinal so liga quando `rules.narrative_signal` e True, e cada
dossie carrega a versao que o gravou.
"""

from . import agents_v2 as _v2

V4 = _v2.V4
DEFAULT = V4
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
