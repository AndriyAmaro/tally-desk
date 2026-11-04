"""Prints, as JSON, the system prompts the app sends to the models (captured through stub `ask` functions, no network, no key).
tests/guardrail_ledger.test.js runs the guardrail detector over them: a mandated guardrail may screen the whole input, system prompt included."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verdict_desk import llm, narrative, otari_canary, otari_drill, otari_mcp, otari_web, sandbox_rederive, source_drift  # noqa: E402

seen: list[str] = []


def ask(system, user, **kw):
    seen.append(system)
    return "[]"


items = {"E1": "SOLUSDT last close 95.09", "E2": "Web news: Solana price slips below $110 (undated)", "E3": "Web page excerpt: SOL traded lower on Tuesday"}
for side in ("bull", "bear"):
    llm.argue(side, items, ask=ask)
    llm.argue(side, {"E1": "SOLUSDT last close 95.09"}, ask=ask)
labels = {"B1": {"claim": "Price is rising", "cites": ["E1"]}}
for call in (lambda: llm.rule(labels, items, ask=ask), lambda: llm.check_claims(labels, items, ask=ask)):
    try:
        call()
    except (ValueError, KeyError, TypeError):
        pass  # the stub answers an empty list; only the prompt matters here
seen.extend([narrative.SYSTEM, otari_web.SEARCH_SYSTEM, otari_web.FETCH_SYSTEM, source_drift.SYSTEM, sandbox_rederive.SYSTEM, otari_mcp.SYSTEM, otari_canary.SYSTEM])
drill = {"system": otari_drill.GUARDRAIL_SYSTEM, "benign": otari_drill._headline_prompt(otari_drill.BENIGN_HEADLINE),
         "injection": otari_drill._headline_prompt(otari_drill.MALICIOUS_HEADLINE)}  # the inputs of `otari-drill guardrail`
print(json.dumps({"prompts": sorted(set(seen)), "drill": drill}))
