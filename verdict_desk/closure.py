"""The closure gate: before a dossier is appended, a deterministic check that does not look at the answer inspects the finished state.

The gate never asks whether the verdict is right. It asks whether the record is in a state that may be sealed: both analysts answered on
their last attempt, every call it names has a receipt, the frozen evidence is the evidence the cycle collected, the verdict replays from that evidence, the checker's consequence is what a
recomputation gives, the headroom read says how each lookup went, the spend is priced and inside the caps, the narrative is not silently
partial. Two classes:

- hard: the record would be wrong or unverifiable. The dossier is NOT appended; the refusal and its reason go to the runner log and to
  `ledger/closure-refusals.jsonl`.
- soft: the record is true but degraded (a declared partial narrative, an unpriced call, a guardrail that was switched on and never evaluated). It is sealed, with the warning in the closure.

The outcome is sealed in the new dossier as `closure` (version, the checks with pass and the reason in one line). Dossiers recorded before
the gate have no such field and are untouched: `recheck` is silent about them, and so are `verify` and the page. `recheck` recomputes the
checks of a sealed closure from the dossier alone, so a closure that was edited, or a dossier edited under it, fails the replay.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = 1
HARD, SOFT = "hard", "soft"
EXIT_CODE = 4  # decide-v7 exits with this when the gate refused every dossier of the run: a normal outcome, not a failure
REFUSALS = "closure-refusals.jsonl"
SCHEMA = "closure-refusal/1"
_BATCH = re.compile(r"^batch (\d+) of (\d+)")


class ClosureRefused(Exception):
    """A hard check failed: `closure` is the full result, `body` the candidate dossier (without hash) that must not be appended."""

    def __init__(self, closure: dict, body: dict):
        failed = [c for c in closure["checks"] if c["class"] == HARD and not c["pass"]]
        super().__init__("; ".join(f"{c['name']}: {c['reason']}" for c in failed))
        self.closure, self.body = closure, body
        self.failed = failed


def _digest(obj) -> str:
    from . import dossier
    return dossier.digest(obj)


# ---------------------------------------------------------------- the calls a body names

def _accounted(receipt) -> bool:
    """A call is accounted for when its receipt carries the request id Otari gave, or records how it failed before or without an answer
    (a refusal, an error, a client-side stop), which is a recorded outcome and has no Otari id to show."""
    if not isinstance(receipt, dict):
        return False
    if receipt.get("refusal") or receipt.get("error"):
        return True
    return receipt.get("request_id_source") == "otari" and bool(receipt.get("request_id"))


def _named_calls(body: dict) -> list[tuple[str, object]]:
    """(label, receipt or None) for every model or tool call the body says was made."""
    ev, layer = body.get("evidence") or {}, body.get("llm") or {}
    out: list[tuple[str, object]] = []
    for r in (ev.get("narrative") or {}).get("receipts") or []:
        out.append((f"narrator batch {r.get('batch', '?')}" if isinstance(r, dict) else "narrator", r))
    web = ev.get("web")
    if isinstance(web, dict) and web.get("method"):
        out.append(("searcher", web.get("receipt")))
        page = web.get("page")
        if isinstance(page, dict):
            out.append(("fetcher", page.get("receipt")))
    mcp = ev.get("market_mcp")
    if isinstance(mcp, dict):
        out.append(("mcp", mcp.get("receipt")))
    drift = body.get("drift")
    if isinstance(drift, dict) and drift.get("status") in ("unchanged", "changed", "unavailable") and "receipt" in drift:
        out.append(("drift", drift.get("receipt")))
    for side in ("bull", "bear"):
        if isinstance(layer.get(side), dict):
            out.append((side, layer[side].get("receipt")))
    checker = layer.get("checker")
    if isinstance(checker, dict) and not checker.get("skipped"):
        out.append(("checker", checker.get("receipt")))
    judge = layer.get("judge")
    if isinstance(judge, dict) and (judge.get("ruling") is not None or "error" in judge):
        out.append(("judge", judge.get("receipt")))
    canary = (layer.get("otari") or {}).get("canary")
    if isinstance(canary, dict):
        out.append(("canary", canary.get("receipt")))
    sandbox = body.get("sandbox")
    if isinstance(sandbox, dict) and ("local_stdout" in sandbox or "receipt" in sandbox):
        out.append(("sandbox", sandbox.get("receipt")))
    return out


def _list(names: list[str], limit: int = 4) -> str:
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


# ---------------------------------------------------------------- the checks: each returns (passed, one-line reason)

def analyst_failures(body: dict) -> list[dict]:
    """The analysts that did not produce an answer on their last attempt: {role, code, status, why}. Empty when there is no LLM layer to judge
    (a run without a model) and when both answered. A side missing from an Otari run's layer counts as not having answered."""
    layer = body.get("llm")
    if not isinstance(layer, dict) or not isinstance(layer.get("otari"), dict):
        return []
    out = []
    for side in ("bull", "bear"):
        block = layer.get(side)
        receipt = (block or {}).get("receipt") if isinstance(block, dict) else None
        bad = (receipt.get("refusal") or receipt.get("error")) if isinstance(receipt, dict) else None
        if isinstance(block, dict) and not block.get("error") and not bad:
            continue
        bad = bad if isinstance(bad, dict) else {}
        code = bad.get("code") or bad.get("kind") or (str(bad["status"]) if bad.get("status") else None)
        out.append({"role": side, "code": code, "status": bad.get("status"), "why": str((block or {}).get("error") or "no answer was recorded")[:160]})
    return out


def check_analysts(body: dict, frozen) -> tuple[bool, str]:
    failed = analyst_failures(body)
    if failed:
        return False, "; ".join(f"{f['role']} did not answer ({f['code'] or 'no code'}{', HTTP ' + str(f['status']) if f['status'] else ''})" for f in failed) + ": a dossier with no argument on a side is not a decision"
    return True, "bull and bear each answered on their last attempt" if (body.get("llm") or {}).get("otari") else "no model layer in this dossier"


def check_receipts(body: dict, frozen) -> tuple[bool, str]:
    calls = _named_calls(body)
    bad = [label for label, r in calls if not _accounted(r)]
    if bad:
        return False, f"{len(bad)} of {len(calls)} calls have no receipt with an Otari request id: {_list(bad)}"
    return True, f"{len(calls)} calls, each with an Otari request id or a recorded failure"


def check_evidence(body: dict, frozen) -> tuple[bool, str]:
    from . import otari_mcp, otari_web
    if frozen is not None and _digest(body["evidence"]) != frozen:
        return False, "the evidence in the dossier is not the evidence the cycle collected (its digest changed)"
    bad = [k for k, v in {**otari_web.recheck(body), **otari_mcp.recheck(body)}.items() if k.endswith("_ok") and v is not True]
    if bad:
        return False, f"a sealed hash or check inside the evidence does not recompute: {_list(sorted(bad))}"
    return True, "the evidence digest is the one collected; the sealed excerpt and MCP hashes recompute"


def check_replay(body: dict, frozen) -> tuple[bool, str]:
    from . import dossier
    result = dossier.replay({k: v for k, v in body.items() if k != "closure"}, rules_version="auto")  # the gate's own seal is not part of what it replays
    if result["matches"]:
        return True, f"replaying the rules over the frozen evidence gives {result['replayed']['action']}, as recorded"
    off = sorted(k for k, v in result.items() if k.endswith("_ok") and v is False) + (["llm_citations_valid"] if result.get("llm_citations_valid") is False else [])
    detail = f" ({_list(off)})" if off else ""
    return False, f"the verdict does not replay: recorded {body['verdict']['action']}, replayed {result['replayed']['action']}{detail}"


def check_checker_gate(body: dict, frozen) -> tuple[bool, str]:
    verdict = body.get("verdict") or {}
    if verdict.get("rules_version") != "v7":
        return True, "not applicable: the checker gate is a rule of v7"
    from . import agents_v2
    again = agents_v2.checker_gate(body.get("llm"))
    if again != verdict.get("checker_gate"):
        return False, f"the sealed checker gate differs from its recomputation (recomputed flagged {again['flagged']}, voided {again['judge_voided']})"
    return True, f"recomputed from the marks: {len(again['flagged'])} flagged, judge voided {str(again['judge_voided']).lower()}"


def check_narrative_flag(body: dict, frozen) -> tuple[bool, str]:
    nar = (body.get("evidence") or {}).get("narrative")
    if not isinstance(nar, dict):
        return True, "no narrative in this dossier"
    errors = nar.get("chunk_errors") or []
    if errors and nar.get("partial") is not True:
        return False, f"{len(errors)} extraction batches failed but the narrative is not marked partial"
    if nar.get("partial") is True and not errors:
        return False, "the narrative is marked partial with no batch error to explain it"
    parsed = [_BATCH.match(str(e)) for e in errors]
    total = max((int(m.group(2)) for m in parsed if m), default=0)
    have = {r.get("batch") for r in nar.get("receipts") or [] if isinstance(r, dict)}
    missing = [n for n in range(1, total + 1) if n not in have]
    if missing:
        return False, f"batches {_list([str(n) for n in missing])} of {total} left no receipt, so the narrative could be partial without a trace"
    return True, "partial and declared as such" if errors else "no batch failed, and the narrative is not marked partial"


def check_headroom(body: dict, frozen) -> tuple[bool, str]:
    h = ((body.get("llm") or {}).get("otari") or {}).get("headroom")
    if not isinstance(h, dict):
        return True, "no headroom read in this cycle"
    users = h.get("users") or {}
    blank = sorted(n for n, u in users.items() if not (isinstance(u, dict) and ((u.get("found") and u.get("used_usd") is not None) or u.get("lookup") in ("absent", "error"))))
    counts = h.get("read") or {}
    summed = sum(int(counts.get(k) or 0) for k in ("found", "absent", "failed"))
    if blank:
        return False, f"{len(blank)} of {len(users)} headroom lookups carry no recorded status: {_list(blank)}"
    if counts and (counts.get("asked") != len(users) or summed != len(users)):
        return False, f"the headroom read counts ({counts.get('asked')} asked, {summed} answered) do not match its {len(users)} lookups"
    return True, f"{len(users)} lookups, each with a recorded status"


def _usd(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def check_priced(body: dict, frozen) -> tuple[bool, str]:
    cycle = ((body.get("llm") or {}).get("otari") or {}).get("cycle")
    if not isinstance(cycle, dict):
        return True, "no cycle spend sealed in this dossier"
    pools = [("core", cycle), ("tools", cycle.get("tools") or {})]
    problems = []
    for name, pool in pools:
        if int(pool.get("unpriced_calls") or 0):
            problems.append(f"{pool['unpriced_calls']} {name} calls are unpriced, so the cap cannot count them")
        cap, spent = _usd(pool.get("cap_usd")), _usd(pool.get("spent_usd"))
        if cap is not None and spent is not None and spent > cap:
            problems.append(f"{name} spend USD {spent} passed its cap of USD {cap}")
    if problems:
        return False, "; ".join(problems)
    return True, f"0 unpriced calls; core USD {cycle.get('spent_usd')} of {cycle.get('cap_usd')}, tools USD {(cycle.get('tools') or {}).get('spent_usd')} of {(cycle.get('tools') or {}).get('cap_usd')}"


def check_narrative_complete(body: dict, frozen) -> tuple[bool, str]:
    nar = (body.get("evidence") or {}).get("narrative")
    if not isinstance(nar, dict):
        return True, "no narrative in this dossier"
    if nar.get("partial") is True:
        return False, f"the narrative is partial: {len(nar.get('chunk_errors') or [])} extraction batches failed and their headlines were not read"
    if nar.get("llm_error"):
        return False, "the model reading of the headlines failed, so the narrative fell back to keywords"
    return True, "every extraction batch answered"


def check_guardrail_ran(body: dict, frozen):
    """Soft, and applied only to a cycle whose run summary says the guardrail switch was on (`llm.otari.guardrails`): at least one of the calls that
    asked for the guardrail carries a verdict the gateway evaluated. A cycle sealed without the switch has no such check, so older dossiers and
    a cycle run with the switch off replay as before. Returns None when it does not apply."""
    g = ((body.get("llm") or {}).get("otari") or {}).get("guardrails")
    if not isinstance(g, dict):
        return None
    from . import otari_report
    asked = [c for c in otari_report.calls_of(body) if c["receipt"].get("guardrails_requested") and not c["receipt"].get("refusal") and not c["receipt"].get("error")]
    if not asked:
        return False, f"the guardrail {g.get('profile')} was switched on, but no call that was answered carried it"
    states: dict[str, int] = {}
    for call in asked:
        entry = next((e for e in call["receipt"].get("guardrails") or [] if isinstance(e, dict) and e.get("profile") == g.get("profile")), None)
        if entry is None:
            state = "no header"
        elif entry.get("status") == "evaluated" and isinstance(entry.get("valid"), bool):
            state = "evaluated"
        else:
            state = str(entry.get("status") or "no status")
        states[state] = states.get(state, 0) + 1
    if states.get("evaluated"):
        flagged = sum(1 for c in asked for e in c["receipt"].get("guardrails") or [] if isinstance(e, dict) and e.get("profile") == g.get("profile") and e.get("status") == "evaluated" and e.get("valid") is False)
        return True, f"{states['evaluated']} of {len(asked)} calls carry a verdict the gateway evaluated for {g.get('profile')} ({flagged} flagged)"
    return False, f"none of {len(asked)} calls carries an evaluated verdict for {g.get('profile')}: " + ", ".join(f"{n} {k}" for k, n in sorted(states.items()))


CHECKS = (
    ("analysts_answered", HARD, check_analysts),
    ("receipts", HARD, check_receipts),
    ("evidence_frozen", HARD, check_evidence),
    ("verdict_replays", HARD, check_replay),
    ("checker_gate", HARD, check_checker_gate),
    ("narrative_flagged", HARD, check_narrative_flag),
    ("headroom_lookups", SOFT, check_headroom),
    ("priced_within_cap", SOFT, check_priced),
    ("narrative_complete", SOFT, check_narrative_complete),
)
OPTIONAL_CHECKS = (("guardrail_ran", SOFT, check_guardrail_ran),)  # a check that may not apply: it answers None and is left out of the closure


def evaluate(body: dict, frozen: str | None) -> dict:
    """Runs every check over a candidate body (a dossier without `hash` and without `closure`). `frozen` is the digest of the evidence
    taken when collection ended; None compares nothing. A check that cannot run fails, and fails closed if it is hard."""
    checks = []
    for name, klass, fn in CHECKS + OPTIONAL_CHECKS:
        try:
            outcome = fn(body, frozen)
            if outcome is None:
                continue
            ok, reason = outcome
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, ZeroDivisionError) as err:
            ok, reason = False, f"the check could not run ({type(err).__name__})"
        checks.append({"name": name, "class": klass, "pass": bool(ok), "reason": reason})
    warned = any(c["class"] == SOFT and not c["pass"] for c in checks)
    return {"version": VERSION, "evidence_sha256": frozen or _digest(body.get("evidence")), "checks": checks, "outcome": "warning" if warned else "pass"}


def seal(body: dict, frozen: str) -> dict:
    """The `closure` object to put in the dossier, or ClosureRefused when a hard check failed."""
    result = evaluate(body, frozen)
    if any(c["class"] == HARD and not c["pass"] for c in result["checks"]):
        result["outcome"] = "refused"
        raise ClosureRefused(result, body)
    return result


def recheck(dossier_: dict) -> dict:
    """The replay of a sealed closure: recomputed from the dossier alone, against the evidence digest it sealed. Silent for a dossier that
    has none. The recomputation must pass every hard check and give the same pass and fail as the seal, check by check."""
    sealed = dossier_.get("closure")
    if sealed is None:
        return {}
    if not isinstance(sealed, dict) or sealed.get("version") != VERSION or not isinstance(sealed.get("checks"), list):
        return {"closure_ok": False}
    body = {k: v for k, v in dossier_.items() if k not in ("hash", "closure")}
    again = evaluate(body, sealed.get("evidence_sha256"))
    mine = {c.get("name"): c.get("pass") for c in sealed["checks"] if isinstance(c, dict)}
    theirs = {c["name"]: c["pass"] for c in again["checks"]}
    hard_ok = all(c["pass"] for c in again["checks"] if c["class"] == HARD)
    return {"closure_ok": mine == theirs and hard_ok}


# ---------------------------------------------------------------- the refusal on record

def spend_of(body: dict) -> dict:
    """What the cycle of a candidate cost, from its receipts, by end user: the runner counts a refused cycle's money like any other."""
    from . import otari_report
    users: dict[str, Decimal] = {}
    for call in otari_report.calls_of(body):
        r = call["receipt"]
        cost = _usd(r.get("cost_usd")) if r.get("cost_usd") is not None else None
        if r.get("end_user") and cost is not None:
            users[r["end_user"]] = users.get(r["end_user"], Decimal(0)) + cost
    cycle = ((body.get("llm") or {}).get("otari") or {}).get("cycle") or {}
    usd = _usd(cycle.get("total_spent_usd")) if cycle.get("total_spent_usd") is not None else None
    return {"usd": str(usd if usd is not None else sum(users.values(), Decimal(0))), "users": {u: str(c) for u, c in users.items()}}


def refusal_record(error: ClosureRefused, at: str) -> dict:
    body = error.body
    return {"schema": SCHEMA, "at": at, "symbol": (body.get("evidence") or {}).get("symbol"), "seq_not_used": body.get("seq"),
            "prev_hash": body.get("prev_hash"), "candidate_sha256": _digest(body),
            "failed": [{"name": c["name"], "reason": c["reason"]} for c in error.failed], "roles_failed": analyst_failures(body),
            "closure": error.closure, **spend_of(body)}


def log_refusal(ledger_path: Path, record: dict) -> Path:
    """One line in ledger/closure-refusals.jsonl, next to the ledger the dossier was refused for."""
    path = Path(ledger_path).parent / REFUSALS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_refusals(ledger_path: Path) -> list[dict]:
    path = Path(ledger_path).parent / REFUSALS
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
