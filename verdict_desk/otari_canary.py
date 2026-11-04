"""The failover canary: proof, inside every decision cycle, that Otari's backup route works before the cycle trusts the judge with it.

One small call goes through the routing policy `vd-judge-drill`, whose main model has a 32k-token context and whose backup is DeepSeek.
The prompt is plain filler, longer than that context and said so in the system message, so the main model refuses it (400) and Otari moves
on to the backup: `Otari-Fallback: true`, `Otari-Attempt-Count: 2`. The receipt is sealed in the dossier (llm.otari.canary) with an
unmistakable label. It is a canary and not a judgment: its answer is never read. If the backup did not take over, the record says so and
the judge stays on the standard route (llm.route_decision).

The call is billed to its own end user (`vd-canary`) on the same daily budget as the others, and costs about USD 0.002 (the backup's
input price for a little over 32k tokens).
"""

import hashlib

from . import llm

SCHEMA = "otari-canary/1"
LABEL = "failover canary (not a judgment: its answer is never read)"
UNIT = " ok"
UNITS = 35_000  # about one token each on the backup, a little over 7% above the 32,768 tokens of the main model's context
SYSTEM = ("This is a failover canary, not a task. The user message is meaningless filler, long on purpose, so that the main model of the routing "
          "policy cannot take it and the backup must. Answer with the single word: ok.")
MAX_COST_USD = 0.004
INPUT_USD_PER_MILLION = 0.06  # deepseek/deepseek-v4-flash-0731 in the public catalog on 2026-11-01


def prompt() -> str:
    return (UNIT * UNITS).strip() + "\nThe text above is filler. Reply: ok."


def run(ask=None, cfg: dict | None = None) -> dict:
    """Runs the canary and returns the record to seal: label, policy, end user, the filler's hash, the receipt and whether the backup took over."""
    ask = ask or llm.chat  # looked up at call time, so a test can replace it
    cfg = cfg or llm.otari_config()
    policy = cfg.get("drill_policy") or "vd-judge-drill"
    user = cfg.get("canary_user") or "vd-canary"
    text = prompt()
    record = {"schema": SCHEMA, "label": LABEL, "policy": policy, "end_user": user,
              "prompt": {"filler_units": UNITS, "chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
              "estimated_cost_usd": round(UNITS * INPUT_USD_PER_MILLION / 1e6, 6)}
    try:
        ask(SYSTEM, text, timeout=60, max_tokens=8, role="judge", model=policy, end_user=user, end_user_budget=cfg.get("end_user_budget"),
            extra={"guardrails": None})  # the filler is not untrusted text, and a long body has no business at the guardrails service
    except (OSError, ValueError) as err:
        return {**record, "passed": False, "error": str(err)[:300], "receipt": llm.served_receipt(),
                "reading": "the canary call did not complete, so the backup route is not shown: the judge stays on the standard route"}
    receipt = llm.served_receipt()
    return {**record, "receipt": receipt, "passed": passes(receipt), "reading": reading(receipt)}


def passes(receipt: dict | None) -> bool:
    """The backup took over: Otari said fallback true after two attempts under the policy that was asked for, and nothing was refused."""
    r = receipt or {}
    return bool(r.get("fallback") is True and r.get("attempt_count") == 2 and not r.get("refusal") and not r.get("error"))


def reading(receipt: dict | None) -> str:
    if passes(receipt):
        return "the main model of the policy could not take the prompt and Otari served the backup (fallback true, two attempts): the backup route works"
    return "the receipt does not show the backup taking over: the judge stays on the standard route"
