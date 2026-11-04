"""Drills for the Otari features that ordinary traffic never trips on purpose: routing fallback, a budget refusal and the app's own role chain.

Both are real requests to the real gateway, started by a command of the app (`python3 -m verdict_desk otari-drill ...`) and
written, receipts and all, to a versioned evidence file. They are not decisions, so nothing here touches the ledger.

- fallback: the routing policy `vd-judge-drill` has a 32k-context Mistral as its main model and DeepSeek as its backup. The
  prompt is the frozen evidence of the latest dossiers, too long for Mistral's context, so the main model refuses it (400) and
  Otari moves on to the backup. The proof is `Otari-Fallback: true`, `Otari-Attempt-Count: 2` and the request's usage rows.
- budget: the end user `vd-drill` sits on the panel budget `vd-drill-tiny` (USD 0.01 a day). A small call passes, then a call
  whose worst-case cost is larger than what remains is refused with 403 `budget_exceeded` before any model is called. The
  refusal is caught and kept as a refusal receipt.
- role-fallback: the app's OWN chain (`fallbacks` of a role in config/otari.json), not Otari's provider failover. The bull role is asked
  with a first candidate the gateway does not know (`drill/does-not-exist`, HTTP 400 `invalid_model`, refused before any model is called,
  so it costs nothing); the app moves to the first real entry of the role's chain, which answers. Both receipts are kept.
"""

import datetime
import decimal
import hashlib
import json
from pathlib import Path

from . import dossier, llm

EVIDENCE = Path("docs/evidence/otari-drills.jsonl")
SCHEMA = "otari-drill/1"
TARGET_CHARS = 165_000  # over 32,768 tokens, the context of the Mistral main model (the first run counted 66,438 prompt tokens for 187,928 characters, about 2.8 characters a token)
MAX_PROMPT_COST_USD = 0.005  # the backup's input price times the prompt: the drill refuses to run if it would cost more
INPUT_USD_PER_MILLION = 0.06  # deepseek/deepseek-v4-flash-0731 in the public catalog on 2026-11-01
MISSING_MODEL = "drill/does-not-exist"  # a selector no provider, alias, policy or catalog id resolves: HTTP 400 invalid_model on the hosted gateway (docs/FEEDBACK_LOG.md, entry 10), refused before a model is called
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


def _cost(row: dict | None) -> decimal.Decimal | None:
    try:
        return decimal.Decimal(str(row["cost_usd"])) if row and row.get("cost_usd") is not None else None
    except (decimal.InvalidOperation, KeyError):
        return None


def role_fallback_drill(ask=None, usage=None) -> dict:
    """Asks the bull role with a first model the gateway refuses without charge, so the app's own chain moves to the role's first real entry.

    Passes only when: the refused entry is the one named, the refusal is a model-side reason (the app's rule, `llm.model_side_reason`), the next
    entry of the role's chain answered, nothing was billed for the refused request, and both request ids are Otari's."""
    ask, usage = ask or llm.chat, usage or llm.usage_for
    cfg = llm.otari_config()
    role = cfg.get("drill_role") or "bull"
    missing = cfg.get("drill_missing_model") or MISSING_MODEL
    chain = [missing] + [m for m in llm.role_fallbacks(role, cfg) if m != missing]
    user = cfg.get("drill_role_user") or "vd-drill-role"  # never the production end user vd-bull: its spend is the bull's alone
    record = {"schema": SCHEMA, "drill": "role-fallback", "at": _now(), "role": role, "chain": chain, "refused_model": missing, "end_user": user}
    if len(chain) < 2:
        return {**record, "pass": False, "error": f"role {role!r} has no fallbacks in config/otari.json; nothing was sent"}
    llm.served_receipt()
    llm.refused_receipts()
    try:
        answer = ask("You are the bull analyst in a market debate.", "Reply with the single word ok.", timeout=60, max_tokens=16, role=role, model=missing,
                     role_fallbacks=True, end_user=user, end_user_budget=cfg.get("end_user_budget"))
    except llm.OtariError as err:
        why = (f"the gateway answered HTTP {err.status} {err.code or ''}".strip() + ", which the app does not treat as a model-side refusal, so nothing fell back"
               if not llm.model_side_reason(err) else f"the chain failed: {err}")
        return {**record, "pass": False, "error": why, "receipt": llm.served_receipt(), "refused_receipts": llm.refused_receipts()}
    except (OSError, ValueError) as err:
        return {**record, "pass": False, "error": str(err), "receipt": llm.served_receipt(), "refused_receipts": llm.refused_receipts()}
    receipt, refused = llm.served_receipt(), llm.refused_receipts()
    note = (receipt or {}).get("role_fallback") or {}
    skipped = note.get("skipped") or []
    first = skipped[0] if skipped else {}
    rid_refused, rid_answered = first.get("request_id"), (receipt or {}).get("request_id")
    rows = {"refused": usage(rid_refused) if first.get("request_id_source") == "otari" else None,
            "answered": usage(rid_answered) if (receipt or {}).get("request_id_source") == "otari" else None}
    billed = _cost(rows["refused"])
    ok = bool(receipt and not receipt.get("refusal") and len(skipped) == 1 and first.get("model") == missing and first.get("reason")
              and note.get("answered") == chain[1] and note.get("answered_index") == 1 and receipt.get("sent_model") == chain[1]
              and first.get("request_id_source") == "otari" and receipt.get("request_id_source") == "otari"
              and any(r.get("request_id") == rid_refused and r.get("refusal") for r in refused) and not billed)
    return {**record, "answer_chars": len(answer), "refused_receipt": next((r for r in refused if r.get("request_id") == rid_refused), None),
            "receipt": receipt, "role_fallback": note, "usage": rows,
            "otari_failover": {"fallback": (receipt or {}).get("fallback"), "attempt_count": (receipt or {}).get("attempt_count"),
                               "note": "Otari's own provider failover, a different mechanism: the app moved between catalog ids, Otari did not"},
            "cost_usd": {"refused": str(billed) if billed is not None else "0 (no cost on the usage row)" if rows["refused"] else "not confirmed (no usage row)",
                         "answered": (receipt or {}).get("cost_usd")},
            "pass": ok,
            "reading": "the first model of the chain was refused by the gateway before any model was called, and the app asked the next catalog id of the role, which answered"
                       if ok else "the app's chain did not show the expected refusal and answer in the receipts"}


def summary_lines(r: dict) -> list[str]:
    """What the role-fallback drill prints for a screen: which entry was refused and why, which answered, and the cost."""
    head = f"drill role-fallback: {'PASS' if r['pass'] else 'FAIL'} (role {r['role']}, end user {r['end_user']}, chain {' -> '.join(r['chain'])})"
    if r.get("error") and not r.get("receipt"):
        return [head, f"  {r['error']}"]
    out = [head]
    note = r.get("role_fallback") or {}
    for entry in note.get("skipped") or []:
        out.append(f"  1. REFUSED  {entry['model']}: HTTP {entry.get('status')} {entry.get('code')} ({entry.get('reason')}), request {entry.get('request_id')}, no model was called")
    rc = r.get("receipt") or {}
    if note.get("answered"):
        out.append(f"  2. ANSWERED {note['answered']}: served by {rc.get('served_by')}, request {rc.get('request_id')}, {rc.get('duration_ms')} ms")
    elif r.get("error"):
        out.append(f"  2. NOT ANSWERED: {r['error']}")
    cost = r.get("cost_usd") or {}
    out.append(f"  cost: refused entry {cost.get('refused')}; answering entry USD {cost.get('answered')}")
    fo = r.get("otari_failover") or {}
    out.append(f"  Otari's own provider failover: Otari-Fallback {fo.get('fallback')}, attempt count {fo.get('attempt_count')} (a different mechanism, not this drill)")
    out.append("  evidence line appended to docs/evidence/otari-drills.jsonl; not sealed in the ledger")
    return out


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


def mcp_drill(collect=None) -> dict:
    """One MCP price read through Otari (the public CoinGecko server, free): passes when Otari reported the tool use, the server's
    result block carried a price, and the receipt has a request id. Costs a fraction of a cent."""
    from . import otari_mcp
    collect = collect or otari_mcp.collect
    block = collect()
    ok = bool(block.get("mcp_calls") and block.get("prices") and (block.get("receipt") or {}).get("request_id_source") == "otari")
    return {"schema": SCHEMA, "drill": "mcp", "at": _now(), "pass": ok, "block": block,
            "reading": "Otari ran the model and called the public MCP server; the price is the server's own result, not the model's text" if ok
                       else "no MCP tool call with a price was reported"}


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
    if which in ("role-fallback", "all"):
        out.append(role_fallback_drill())
    if which in ("mcp", "all"):
        out.append(mcp_drill())
    if which in ("users", "all"):
        out.append(end_users_check())
    for record in out:
        append(path, record)
    return out
