"""Regras v7: v6 + o checker de citacoes passa a ter consequencia no veredito.

O checker (um quarto modelo) relê cada afirmacao validada contra a evidencia que ela cita e marca as que nao se sustentam. Ate a v6 isso era
so consultivo. Na v7, se o juiz LLM se apoia numa afirmacao que o checker marcou como nao sustentada, a inclinacao do juiz perde o peso (o juiz
so desempata, entao isso pode devolver a decisao a FLAT, nunca abre posicao). A marca vem congelada no dossier e a consequencia e recalculada
dela no replay; sem checker (ou com checker que falhou) a v7 decide como a v6. Os dossies v1 a v6 nao mudam.
"""

from . import agents_v2 as _v2

V7 = _v2.V7
DEFAULT = V7
Rules = _v2.Rules
SYSTEM_MAX_RISK_PCT = _v2.SYSTEM_MAX_RISK_PCT
with_risk_pct = _v2.with_risk_pct
convergence_args = _v2.convergence_args
llm_judge_args = _v2.llm_judge_args
checker_gate = _v2.checker_gate
bull = _v2.bull
bear = _v2.bear
judge = _v2.judge
sensitivity = _v2.sensitivity
USES_LLM_JUDGE = True


def decide(f: dict, rules: Rules = DEFAULT, narrative: dict | None = None, symbol: str | None = None, llm: dict | None = None) -> dict:
    return _v2.decide(f, rules, narrative, symbol, llm)
