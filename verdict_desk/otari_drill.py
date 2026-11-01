"""Drills for the two Otari features that ordinary traffic never trips on purpose: routing fallback and a budget refusal.

Both are real requests to the real gateway, started by a command of the app (`python3 -m verdict_desk otari-drill ...`) and
written, receipts and all, to a versioned evidence file. They are not decisions, so nothing here touches the ledger.

- fallback: the routing policy `vd-judge-drill` has a 32k-context Mistral as its main model and DeepSeek as its backup. The
  prompt is the frozen evidence of the latest dossiers, too long for Mistral's context, so the main model refuses it (400) and
  Otari moves on to the backup. The proof is `Otari-Fallback: true`, `Otari-Attempt-Count: 2` and the request's usage rows.
- budget: the end user `vd-drill` sits on the panel budget `vd-drill-tiny` (USD 0.01 a day). A small call passes, then a call
  whose worst-case cost is larger than what remains is refused with 403 `budget_exceeded` before any model is called. The
  refusal is caught and kept as a refusal receipt.
"""

import datetime
import hashlib
import json
from pathlib import Path

from . import dossier, llm

EVIDENCE = Path("docs/evidence/otari-drills.jsonl")
SCHEMA = "otari-drill/1"
TARGET_CHARS = 165_000  # over 32,768 tokens, the context of the Mistral main model (the first run counted 66,438 prompt tokens for 187,928 characters, about 2.8 characters a token)
MAX_PROMPT_COST_USD = 0.005  # the backup's input price times the prompt: the drill refuses to run if it would cost more
INPUT_USD_PER_MILLION = 0.06  # deepseek/deepseek-v4-flash-0731 in the public catalog on 2026-11-01
BIG_MAX_TOKENS = 100_000  # a worst-case hold of about USD 0.018 on the judge model, more than the USD 0.01 budget holds


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def long_pack(ledger: list[dict], target: int = TARGET_CHARS) -> tuple[str, dict]:
    """The frozen evidence of the latest dossiers, newest first, until it is `target` characters long, and what it was."""
    parts, seqs = [], []
    for d in reversed(ledger):
        parts.append(f"Dossier {d['seq']} evidence: " + json.dumps(d["evidence"], ensure_ascii=False, separators=(",", ":")))
        seqs.append(d["seq"])
        if sum(len(p) for p in parts) >= target:
            break
    text = "\n".join(parts)
    return text, {"chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "source": f"frozen evidence of dossiers {min(seqs)} to {max(seqs)} of the ledger",
                  "estimated_cost_usd": round(len(text) / 2.8 * INPUT_USD_PER_MILLION / 1e6, 6)}


def fallback_drill(ledger: list[dict], ask=None, usage=None) -> dict:
    ask, usage = ask or llm.chat, usage or llm.usage_for  # looked up at call time, so a test can replace them
    cfg = llm.otari_config()
    policy = cfg.get("drill_policy") or "vd-judge-drill"
    pack, source = long_pack(ledger)
    record = {"schema": SCHEMA, "drill": "fallback", "at": _now(), "policy": policy, "prompt": source}
    if source["estimated_cost_usd"] > MAX_PROMPT_COST_USD:
        return {**record, "pass": False, "error": f"estimated prompt cost ${source['estimated_cost_usd']} is over the ${MAX_PROMPT_COST_USD} drill limit; nothing was sent"}
    system = "You are the judge in a market debate. Read the evidence packs and answer with one word: bull, bear or neutral."
    user = cfg.get("drill_fallback_user") or "vd-drill-fallback"  # never the production end user vd-judge: its spend is the judge's alone
    record["end_user"] = user
    try:
        answer = ask(system, pack, timeout=120, max_tokens=16, role="judge", model=policy, end_user=user, end_user_budget=cfg.get("end_user_budget"))
    except (OSError, ValueError) as err:
        return {**record, "pass": False, "error": str(err), "receipt": llm.served_receipt()}
    receipt = llm.served_receipt()
    row = usage((receipt or {}).get("request_id"))
    ok = bool(receipt and receipt.get("fallback") is True and receipt.get("attempt_count") == 2 and receipt.get("sent_model") == policy)
    return {**record, "answer_chars": len(answer), "receipt": receipt, "usage": row, "pass": ok,
            "reading": "the main model of the policy refused the prompt as too long for its context and Otari served the backup"
                       if ok else "the fallback did not show in the receipt"}


def budget_drill(ask=None, usage=None, end_user_info=None) -> dict:
    ask, usage, end_user_info = ask or llm.chat, usage or llm.usage_for, end_user_info or llm.end_user_info
    cfg = llm.otari_config()
    user, budget = cfg.get("drill_user") or "vd-drill", cfg.get("drill_budget")
    model = llm.role_model("judge", cfg)
    record = {"schema": SCHEMA, "drill": "budget", "at": _now(), "end_user": user, "budget_id": budget, "model": model}
    if not budget:
        return {**record, "pass": False, "error": "drill_budget is not set in config/otari.json"}
    record["end_user_before"] = end_user_info(user)
    system, prompt = "You are the judge in a market debate. Answer with one word.", "Reply ok."
    try:
        ask(system, prompt, max_tokens=16, role="judge", model=model, end_user=user, end_user_budget=budget)
        record["small_call"] = {"passed": True, "receipt": llm.served_receipt()}
    except (OSError, ValueError) as err:
        return {**record, "pass": False, "error": f"the small call failed: {err}", "receipt": llm.served_receipt()}
    refusal, error = None, None
    try:
        ask(system, prompt, max_tokens=BIG_MAX_TOKENS, role="judge", model=model, end_user=user, end_user_budget=budget)
    except llm.OtariError as err:
        error = {"status": err.status, "code": err.code, "budget_scope": err.scope, "retry_after": err.retry_after, "request_id": err.request_id, "message": str(err)[:300]}
    except (OSError, ValueError) as err:
        error = {"message": str(err)[:300]}
    refusal = llm.served_receipt()
    record["end_user_after"] = end_user_info(user)
    ok = bool(error and error.get("status") == 403 and error.get("code") == "budget_exceeded" and error.get("budget_scope") == "user")
    return {**record, "big_call": {"max_tokens": BIG_MAX_TOKENS, "refused": ok, "error": error, "receipt": refusal,
                                   "usage": usage((refusal or {}).get("request_id")) if ok else None},
            "pass": ok, "reading": "the worst-case cost of the second call is larger than what the drill budget has left, so Otari refused it before calling a model; the refusal is a receipt, not a crash"}


def end_users_check(end_user_info=None) -> dict:
    """What Otari says about the production end users, read with GET /end-users (no cost): each must sit on the vd-analyst
    budget. A receipt may say "billed on vd-analyst" only while this record says pass. An end user does not take a budget after
    it exists, so one born without the header is replaced by a new name, never patched."""
    end_user_info = end_user_info or llm.end_user_info
    cfg = llm.otari_config()
    want = cfg.get("end_user_budget")
    rows = {}
    for role in cfg["roles"]:
        name = f"{cfg.get('end_user_prefix') or ''}{role}"
        info = end_user_info(name)
        rows[name] = {"role": role, "found": info is not None, "budget_id": (info or {}).get("budget_id"), "spend": (info or {}).get("spend"),
                      "current_requests": (info or {}).get("current_requests"), "blocked": (info or {}).get("blocked"),
                      "on_vd_analyst": bool(info and info.get("budget_id") == want)}
    return {"schema": SCHEMA, "drill": "end-users", "at": _now(), "expected_budget_id": want, "end_users": rows,
            "pass": all(r["on_vd_analyst"] for r in rows.values()),
            "reading": "each production end user was created with the vd-analyst budget id" }


def append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def run(which: str, ledger: list[dict], path: Path = EVIDENCE, ask=None) -> list[dict]:
    llm.reset_cycle()
    out = []
    if which in ("fallback", "all"):
        out.append(fallback_drill(ledger, ask=ask))
    if which in ("budget", "all"):
        out.append(budget_drill(ask=ask))
    if which in ("users", "all"):
        out.append(end_users_check())
    for record in out:
        append(path, record)
    return out
