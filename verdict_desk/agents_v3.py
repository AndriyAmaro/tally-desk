"""Regras v3: as mesmas contas da v2, com um veredito que diz POR QUE nao entrou.

A v2 devolve `FLAT` para tudo que nao passa do limiar. Isso esconde dois casos muito
diferentes: o de um mercado sem sinal nenhum e o de um mercado em que um unico
argumento decide (basta ele cair para o veredito virar) ou em que os dois lados estao
fortes com volatilidade alta (hora de reduzir, nao de abrir).

A v3 nomeia esses casos: `WATCH_REVERSAL` e `REDUCE_EXPOSURE`. O dimensionamento, os
limiares e o juiz sao exatamente os mesmos da v2, entao entrar ou nao entrar nao muda:
muda o que o dossie conta para quem audita. Os dossies gravados em v2 continuam
reproduzindo com o modulo v2, sem alteracao.
"""

from . import agents_v2 as _v2

V3 = _v2.V3
DEFAULT = V3
Rules = _v2.Rules
SYSTEM_MAX_RISK_PCT = _v2.SYSTEM_MAX_RISK_PCT
with_risk_pct = _v2.with_risk_pct
bull = _v2.bull
bear = _v2.bear
judge = _v2.judge
sensitivity = _v2.sensitivity


def decide(f: dict, rules: Rules = V3, narrative: dict | None = None, symbol: str | None = None) -> dict:
    return _v2.decide(f, rules, narrative, symbol)
