"""Regras v6: v5 + um juiz LLM que so pesa quando se apoia em afirmacoes que passaram no validador.

O juiz le apenas as afirmacoes ACEITAS dos dois analistas (as recusadas nunca chegam a ele) e inclina
para touro, urso ou neutro. A inclinacao vira um argumento com peso de tabela (0.5 / 0.75 / 1.0 por
conviccao), abaixo da faixa de entrada de 1.5: o LLM desempata, nunca abre posicao sozinho. O veredito
bruto fica congelado no dossier e o peso e recalculado dele no replay. Os dossies v1 a v5 nao mudam.
"""

from . import agents_v2 as _v2

V6 = _v2.V6
DEFAULT = V6
Rules = _v2.Rules
SYSTEM_MAX_RISK_PCT = _v2.SYSTEM_MAX_RISK_PCT
with_risk_pct = _v2.with_risk_pct
convergence_args = _v2.convergence_args
llm_judge_args = _v2.llm_judge_args
bull = _v2.bull
bear = _v2.bear
judge = _v2.judge
sensitivity = _v2.sensitivity
USES_LLM_JUDGE = True


def decide(f: dict, rules: Rules = DEFAULT, narrative: dict | None = None, symbol: str | None = None, llm: dict | None = None) -> dict:
    return _v2.decide(f, rules, narrative, symbol, llm)
