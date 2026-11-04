"""What the Otari-era dossiers prove, read from their receipts: the audit of the eight resources, the reconciliation with Otari's own
usage records, the open-model scoreboard, and who saw a dossier's prompts. Nothing here calls a model; `reconcile` reads Otari's usage
endpoint (a GET that costs nothing), everything else reads the ledger and the evidence file. The same scoreboard and "who saw" views are
computed in `web/cycle.js` for the page, and a parity test keeps the two equal.
"""

import json
import math
import urllib.parse
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "otari-drills.jsonl"
HOSTS = ROOT / "config" / "external_hosts.json"
CONFIG = ROOT / "config" / "otari.json"
LOGICAL = ("narrator", "searcher", "fetcher", "mcp", "bull", "bear", "checker", "judge", "sandbox")
RUBRIC = ("Several open-weight models in distinct roles", "Routing and fallback", "Budgets and spend limits", "Budget per user",
          "Guardrails", "Web search and fetch", "Code execution", "MCP")


def era(ledger: list[dict]) -> list[dict]:
    """The dossiers recorded through Otari: the ones that carry the run summary in their LLM layer."""
    return [d for d in ledger if isinstance((d.get("llm") or {}).get("otari"), dict)]


def calls_of(d: dict) -> list[dict]:
    """Every call of a dossier's cycle with its receipt, in time order when the receipts say when each began, else in the order the cycle makes them."""
    ev, llm = d.get("evidence") or {}, d.get("llm") or {}
    found = []
    for r in (ev.get("narrative") or {}).get("receipts") or []:
        found.append(("narrator", r))
    web = ev.get("web") or {}
    if web.get("receipt"):
        found.append(("searcher", web["receipt"]))
    if (web.get("page") or {}).get("receipt"):
        found.append(("fetcher", web["page"]["receipt"]))
    if (d.get("drift") or {}).get("receipt"):  # the source drift seal: one more fetch of the cycle, a call like any other
        found.append(("drift", d["drift"]["receipt"]))
    if (ev.get("market_mcp") or {}).get("receipt"):
        found.append(("mcp", ev["market_mcp"]["receipt"]))
    for part in ("bull", "bear", "checker", "judge"):
        if (llm.get(part) or {}).get("receipt"):
            found.append((part, llm[part]["receipt"]))
    canary = ((llm.get("otari") or {}).get("canary") or {})
    if canary.get("receipt"):
        found.append(("canary", canary["receipt"]))
    if (d.get("sandbox") or {}).get("receipt"):
        found.append(("sandbox", d["sandbox"]["receipt"]))
    calls = [{"label": label, "receipt": r, "n": i} for i, (label, r) in enumerate(found)]
    if any(isinstance(c["receipt"].get("started_offset_ms"), int) for c in calls):
        calls.sort(key=lambda c: (c["receipt"].get("started_offset_ms") if isinstance(c["receipt"].get("started_offset_ms"), int) else 10**12, c["n"]))
    return calls


def closure_line(d: dict) -> str:
    """The cycle page's one line for the closure gate of a dossier; web/cycle.js writes the same words (tests/test_otari_report.py compares)."""
    closure = d.get("closure")
    if not isinstance(closure, dict) or not isinstance(closure.get("checks"), list):
        return "Closure gate: not applied to this dossier (recorded before the gate)"
    checks = [c for c in closure["checks"] if isinstance(c, dict)]
    warned = [str(c.get("name")) for c in checks if not c.get("pass") and c.get("class") == "soft"]
    line = f"Closure gate: {sum(1 for c in checks if c.get('pass'))} of {len(checks)} checks passed"
    return line + (f" ({len(warned)} warning{'' if len(warned) == 1 else 's'}: {', '.join(warned)})" if warned else "")


def drift_line(d: dict) -> str:
    """The same for the source drift seal."""
    block = d.get("drift")
    if not isinstance(block, dict):
        return "Source drift: not checked in this dossier (recorded before the seal existed)"
    status = block.get("status")
    if status == "none":
        return "Source drift: no earlier page old enough to read again"
    after = block.get("minutes_after_decision")
    head = f"dossier {block.get('source_dossier')}" + (f", {after} min after the decision" if isinstance(after, int) else "")
    if status == "unavailable":
        return f"Source drift: unavailable ({head}): {block.get('reason')}"
    return f"Source drift: {status} ({head})"


def _failed(r: dict) -> bool:
    return bool(r.get("refusal") or r.get("error"))


def _rid(r: dict) -> str | None:
    return r.get("request_id") if r.get("request_id_source") == "otari" else None


def read_evidence(path: Path | None = None) -> list[dict]:
    path = path or EVIDENCE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _judge_read(d: dict) -> bool:
    """Whether the dossier's headroom holds a successful read of the judge's end user (a row with its spend), not an absent user or a failed lookup."""
    h = (d["llm"].get("otari") or {}).get("headroom") or {}
    mine = (h.get("users") or {}).get(h.get("judge_user")) or {}
    return bool(mine.get("found")) and mine.get("used_usd") is not None


def _read_counts(h: dict) -> tuple[int, int, int, int]:
    """(asked, found, absent, failed) of one sealed headroom read. Dossiers sealed before the lookup was traced have no `lookup`: a user not found there counts as failed."""
    users = (h.get("users") or {}).values()
    asked = len(users)
    found = sum(1 for u in users if u.get("found"))
    absent = sum(1 for u in users if not u.get("found") and u.get("lookup") == "absent")
    return asked, found, absent, asked - found - absent


def audit(ledger: list[dict], evidence: list[dict] | None = None) -> list[dict]:
    """The eight resources: PASS or NOT PROVEN, where the proof comes from (`source`: sealed receipts in the ledger, or a drill that lives
    in docs/evidence and is not sealed), the request ids that prove it, the dossier it was found in, and a note."""
    evidence = read_evidence() if evidence is None else evidence
    newest = list(reversed(era(ledger)))
    rows = []

    def row(n, ok, ids, seq, note, source="sealed", drill_ids=()):
        rows.append({"n": n, "resource": RUBRIC[n - 1], "status": "PASS" if ok else "NOT PROVEN", "source": source if ok else "none",
                     "proof": [i for i in ids if i], "drill_proof": [i for i in drill_ids if i], "dossier": seq, "note": note})

    # 1 several open-weight models in distinct roles
    done = False
    for d in newest:
        by = {c["label"]: c["receipt"] for c in calls_of(d) if c["label"] in ("bull", "bear", "judge", "checker") and not _failed(c["receipt"])}
        models = {r.get("served_model") for r in by.values()}
        if len(by) == 4 and len(models) == 4:
            row(1, True, [_rid(by[k]) for k in ("bull", "bear", "judge", "checker")], d["seq"], "bull, bear, judge and checker each answered on a different open model: " + ", ".join(f"{k} {by[k].get('served_model')}" for k in ("bull", "bear", "judge", "checker")))
            done = True
            break
    if not done:
        row(1, False, [], None, "no dossier has four roles answered by four distinct models")
    # 2 routing and fallback: fallbacks of real traffic (sealed receipts of the cycle's own calls) first, and the failover canary of the
    # cycle, the deliberate test of the same route, beside them; the drill (not sealed) only when nothing is sealed
    canary = next(((d["seq"], d["llm"]["otari"]["canary"]) for d in newest if (d["llm"]["otari"].get("canary") or {}).get("passed")), None)
    fb = next((e for e in reversed(evidence) if e.get("drill") == "fallback" and e.get("pass")), None)
    real = [(d["seq"], c["label"], c["receipt"]) for d in newest for c in calls_of(d) if c["label"] != "canary" and c["receipt"].get("fallback") and not _failed(c["receipt"]) and _rid(c["receipt"])]
    drill_ids = [fb["receipt"]["request_id"]] if fb else ()
    if real and canary:
        seq, label, r = real[0]
        seqs = sorted({s for s, _, _ in real})
        cr = canary[1]["receipt"]
        row(2, True, [_rid(x) for _, _, x in real[:2]] + [_rid(cr)], seq,
            f"{len(real)} real calls were answered by a backup route after the first attempt did not succeed (Otari-Fallback true, attempt count {r.get('attempt_count')}), in dossier{'s' if len(seqs) > 1 else ''} {', '.join(map(str, seqs))}; newest: a {label} call sent as {r.get('sent_model')} ({'routing policy ' + str(r.get('policy')) if r.get('policy') else 'the plain catalog id, no routing policy of ours, so this is Otari\'s own provider failover and not the vd-judge policy'}) was answered on its second attempt by {r.get('served_by')}. "
            f"Besides, the failover canary of dossier {canary[0]} is the deliberate test of the same route: policy {canary[1].get('policy')} served by {cr.get('served_by')} after {cr.get('attempt_count')} attempts (Otari-Fallback true). All sealed in the ledger",
            drill_ids=drill_ids)
    elif canary:
        r = canary[1]["receipt"]
        row(2, True, [_rid(r)], canary[0], f"the failover canary of the cycle (the deliberate test; no real call fell back yet): policy {canary[1].get('policy')} served by {r.get('served_by')} after {r.get('attempt_count')} attempts (Otari-Fallback true), sealed in the dossier",
            drill_ids=drill_ids)
    elif real:
        seq, label, r = real[0]
        row(2, True, [_rid(r)], seq, f"{len(real)} real calls were answered by a backup route (Otari-Fallback true); newest: a {label} call sent as {r.get('sent_model')} served by {r.get('served_by')}, sealed in the dossier", drill_ids=drill_ids)
    elif fb:
        r = fb["receipt"]
        row(2, True, [], None, f"drill only (not sealed in the ledger): policy {fb.get('policy')} served by {r.get('served_by')} after {r.get('attempt_count')} attempts (Otari-Fallback true)", source="drill", drill_ids=[r["request_id"]])
    else:
        row(2, False, [], None, "no fallback seen")
    # 3 budgets and spend limits: Otari's spend state sealed with the verdict and used by the router (sealed); the 403 refusal itself is a drill
    bd = next((e for e in reversed(evidence) if e.get("drill") == "budget" and e.get("pass")), None)
    capped = next((d["seq"] for d in newest if (d["llm"]["otari"].get("cycle") or {}).get("cap_usd")), None)
    # the newest dossier whose router really read the judge's spend: a lookup that failed or found no user proves nothing about a budget
    gated = next(((d["seq"], d) for d in newest if ((d["llm"].get("judge") or {}).get("route_rule") or {}).get("gates", {}).get("headroom") and _judge_read(d)), None)
    drill_ids = [bd["big_call"]["receipt"].get("request_id")] if bd else []
    if gated:
        d = gated[1]
        rr, h = d["llm"]["judge"]["route_rule"], d["llm"]["otari"]["headroom"]
        mine = h["users"].get(h["judge_user"]) or {}
        row(3, True, [_rid(d["llm"]["judge"].get("receipt") or {})], gated[0],
            f"budget-aware routing: Otari's spend for the judge's end user (USD {mine.get('used_usd')} of USD {h['limit_usd']}) was read and sealed, and the router {'allowed' if rr['gates']['headroom']['ok'] else 'refused'} the hard route on it; per-cycle caps sealed too. "
            + (f"Otari's own 403 budget_exceeded refusal is shown by the drill only (not sealed)" if bd else "no 403 refusal drill recorded"), drill_ids=drill_ids)
    elif bd:
        big = bd["big_call"]
        row(3, True, [], capped, f"drill only (not sealed in the ledger): {big['error'].get('status')} {big['error'].get('code')} (scope {big['error'].get('budget_scope')}) kept as a refusal receipt; per-cycle caps sealed in the dossier (dossier {capped})", source="drill", drill_ids=drill_ids)
    else:
        row(3, False, [], capped, "no budget evidence recorded")
    # 4 budget per user: the end users read back from Otari, sealed with the verdict (else the drill)
    users = next((e for e in reversed(evidence) if e.get("drill") == "end-users" and e.get("pass")), None)
    echo = next(((d["seq"], [_rid(c["receipt"]) for c in calls_of(d) if c["receipt"].get("end_user_budget")][:4]) for d in newest if any(c["receipt"].get("end_user_budget") for c in calls_of(d))), None)
    sealed = [(d["seq"], d["llm"]["otari"]["headroom"]) for d in newest if (d["llm"]["otari"].get("headroom") or {}).get("users")]
    seal = next((x for x in sealed if _read_counts(x[1])[1] == _read_counts(x[1])[0]), None) or max(sealed, key=lambda x: _read_counts(x[1])[1], default=None)  # newest complete read, else the fullest
    if seal and echo:
        found = {n: u for n, u in seal[1]["users"].items() if u.get("found")}
        on = [n for n, u in found.items() if u.get("budget_id") == seal[1].get("expected_budget_id")]
        asked, got, absent, failed = _read_counts(seal[1])
        gaps = "" if got == asked else f"; {absent} not created yet on this key (answered without a row) and {failed} lookups failed or were not recorded as answered"
        row(4, bool(found) and len(on) == len(found), echo[1], seal[0],
            f"{len(on)} of the {got} end users found sit on the vd-analyst budget; {got} of {asked} asked were found{gaps} (read back from Otari, sealed in the dossier); Otari echoed the budget on the receipts")
    elif users and echo:
        row(4, True, echo[1], echo[0], f"drill only (not sealed in the ledger): {len(users['end_users'])} end users read back on the vd-analyst budget; Otari echoed the budget on the receipts", source="drill")
    else:
        row(4, False, [], None, "no end-user proof")
    # 5 guardrails
    seen = [(d["seq"], c["receipt"]) for d in newest for c in calls_of(d) if c["receipt"].get("guardrail_result")]
    evaluated = next(((s, r) for s, r in seen if any(v in ("pass", "flagged") for v in r["guardrail_result"].values())), None)
    if evaluated:
        row(5, True, [_rid(evaluated[1])], evaluated[0], f"evaluated: {evaluated[1]['guardrail_result']}")
    else:
        row(5, False, [_rid(r) for _, r in seen[:2]], seen[0][0] if seen else None,
            f"attached to the vd-judge policy, but Otari reported valid null on {len(seen)} calls: nothing was evaluated (the hosted gateway has no guardrails service); not counted")
    # 6 web search and fetch: the newest dossier with a completed search and fetch; among those, the newest where the analysts really
    # cited what was found (the page excerpt first, then a source title), so the proof shows the evidence used and not only fetched
    cands = []
    for d in newest:
        web = (d.get("evidence") or {}).get("web") or {}
        page = web.get("page") or {}
        searched = any(t.get("type") == "web_search" and t.get("status") == "completed" for t in (web.get("receipt") or {}).get("tool_calls") or [])
        if searched and page.get("otari_reported_fetch") and page.get("excerpt"):
            use = (d["llm"].get("otari") or {}).get("evidence_use") or {}
            cands.append((int(use.get("web_excerpts_cited") or 0) > 0, int(use.get("web_items_cited") or 0) > 0, d, web, page, use))
    if cands:
        _, _, d, web, page, use = max(cands, key=lambda c: (c[0], c[1]))  # max keeps the first (newest) of equals
        cited = int(use.get("web_items_cited") or 0) + int(use.get("web_excerpts_cited") or 0)
        usage = (f"the analysts cited {use.get('web_items_cited', 0)} of {use.get('web_items', 0)} source titles and {use.get('web_excerpts_cited', 0)} of {use.get('web_excerpts', 0)} page excerpts in the claims the judge weighed" if cited
                 else "in this dossier the analysts cited none of what was found (no dossier with a cited web item yet)")
        row(6, True, [_rid(web["receipt"]), _rid(page["receipt"])], d["seq"], f"one search and one page fetch through Otari's tools; {len(web.get('items') or [])} sources and an excerpt frozen (excerpt_verified {page.get('excerpt_verified')}); {usage}")
    else:
        row(6, False, [], None, "no dossier with a completed search and a fetch")
    # 7 code execution
    done = False
    for d in newest:
        s = d.get("sandbox") or {}
        if s.get("schema") == "otari-sandbox/2" and s.get("evidence") == "native" and s.get("equal") and s.get("proof_ok") and s.get("code_ran_is_code_sent"):
            row(7, True, [_rid(s["receipt"])], d["seq"], "the verdict re-derived in Otari's sandbox: its own result block is identical to the local run, the nonce proof matches and the code that ran is the code sent")
            done = True
            break
    if not done:
        row(7, False, [], None, "no dossier with an identical native sandbox result")
    # 8 MCP
    done = False
    for d in newest:
        m = (d.get("evidence") or {}).get("market_mcp") or {}
        if m.get("trusted") and m.get("mcp_calls") and _rid(m.get("receipt") or {}):
            row(8, True, [_rid(m["receipt"])], d["seq"], f"prices read from {m.get('server')} through Otari's MCP loop, from the server's own result block (check: {(m.get('check') or {}).get('status')})")
            done = True
            break
    if not done:
        row(8, False, [], None, "no dossier with a trusted MCP result")
    return rows


def summary_line(rows: list[dict]) -> str:
    sealed = sum(1 for r in rows if r["status"] == "PASS" and r["source"] == "sealed")
    drills = sum(1 for r in rows if r["status"] == "PASS" and r["source"] == "drill")
    none = sum(1 for r in rows if r["status"] != "PASS")
    return f"{len(rows)} resources: {sealed} proven from sealed receipts + {drills} from drills (not sealed) + {none} NOT PROVEN"


def render_audit(rows: list[dict]) -> str:
    out = []
    for r in rows:
        out.append(f"{r['status']:10s} {r['n']}  {r['resource']:36s} {('[' + r['source'] + ']'):9s} dossier {r['dossier'] if r['dossier'] else '-':>4}  proof: {', '.join(r['proof']) or '-'}"
                   + (f"  drill: {', '.join(r['drill_proof'])}" if r["drill_proof"] else ""))
        out.append(f"           {r['note']}")
    out.append(summary_line(rows))
    return "\n".join(out)


# ---------------------------------------------------------------- reconciliation with Otari's own usage records

def _dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def reconcile(receipts: list[dict], fetch) -> dict:
    """Compares each receipt that carries an Otari request id with Otari's own settled record of that request
    (`fetch(request_id)` returns what GET /usage/requests/{id} says, or None): status, token counts, cost, and the number of usage rows
    (a fallback leaves one absorbed row per extra attempt). The usage endpoint does not return the model, so the model is not compared.
    Retried calls that Otari replayed share an id and are compared once."""
    checked, divergences, unsettled, seen = 0, [], [], set()
    for r in receipts:
        rid = _rid(r)
        if not rid or rid in seen:
            continue
        seen.add(rid)
        row = fetch(rid)
        if not row:
            unsettled.append(rid)
            continue
        checked += 1
        problems = []
        want_status = "error" if _failed(r) else "success"
        if row.get("status") != want_status:
            problems.append(f"status: receipt {want_status}, Otari {row.get('status')}")
        if not _failed(r) and isinstance(r.get("tokens"), dict) and r["tokens"].get("prompt") is not None:
            for key, field in (("prompt", "prompt_tokens"), ("completion", "completion_tokens")):
                if r["tokens"].get(key) != row.get(field):
                    problems.append(f"{key} tokens: receipt {r['tokens'].get(key)}, Otari {row.get(field)}")
        if not _failed(r) and _dec(r.get("cost_usd")) != _dec(row.get("cost_usd")):
            problems.append(f"cost: receipt {r.get('cost_usd')}, Otari {row.get('cost_usd')}")
        attempts = r.get("attempt_count") if isinstance(r.get("attempt_count"), int) else 1
        if not _failed(r) and row.get("row_count") not in (None, attempts):
            problems.append(f"usage rows: Otari {row.get('row_count')}, receipt attempt_count {attempts}")
        if problems:
            divergences.append({"request_id": rid, "role": r.get("role"), "problems": problems})
    return {"checked": checked, "unsettled": unsettled, "divergences": divergences}


# ---------------------------------------------------------------- scoreboard and "who saw this prompt"

def percentile(values: list[float], p: float) -> float | None:
    """Nearest rank: the smallest value with at least p of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def board(ledger: list[dict]) -> list[dict]:
    """Per model that answered (provider:model as the receipts name it): calls, how many succeeded, refusals and failures by code, latency
    p50 and p95 of the successful ones, fallbacks, accepted claims and the cost per accepted claim (bull and bear only)."""
    rows: dict[str, dict] = {}
    for d in era(ledger):
        accepted = {side: len(((d.get("llm") or {}).get(side) or {}).get("accepted") or []) for side in ("bull", "bear")}
        for c in calls_of(d):
            r = c["receipt"]
            key = r.get("served_by") or f"(no answer) {r.get('sent_model')}"
            row = rows.setdefault(key, {"model": key, "calls": 0, "ok": 0, "refusals": {}, "latencies": [], "fallbacks": 0, "claims": 0, "claim_cost": Decimal(0)})
            row["calls"] += 1
            if _failed(r):
                bad = r.get("refusal") or r.get("error") or {}
                code = str(bad.get("code") or bad.get("kind") or bad.get("status"))
                row["refusals"][code] = row["refusals"].get(code, 0) + 1
                continue
            row["ok"] += 1
            if isinstance(r.get("duration_ms"), int):
                row["latencies"].append(r["duration_ms"])
            row["fallbacks"] += 1 if r.get("fallback") else 0
            if c["label"] in ("bull", "bear"):
                row["claims"] += accepted[c["label"]]
                row["claim_cost"] += _dec(r.get("cost_usd")) or Decimal(0)
    out = []
    for row in rows.values():
        out.append({"model": row["model"], "calls": row["calls"], "ok": row["ok"], "refusals": dict(sorted(row["refusals"].items())),
                    "p50_ms": percentile(row["latencies"], 0.5), "p95_ms": percentile(row["latencies"], 0.95), "fallbacks": row["fallbacks"],
                    "claims": row["claims"], "cost_per_claim_usd": (f"{row['claim_cost'] / row['claims']:.6f}" if row["claims"] else None)})
    return sorted(out, key=lambda r: (-r["calls"], r["model"]))


def _models_open(cfg: dict) -> dict:
    flags = dict(cfg.get("models_open_weights") or {})
    for spec in (cfg.get("roles") or {}).values():
        flags[spec["model"]] = bool(spec.get("open_weights"))
    return flags


def who_saw(d: dict, cfg: dict | None = None) -> dict:
    """Who received the prompts of one dossier: the gateway (with the region its base URL names) and, per upstream provider, the models,
    the roles, whether each is an open-weight model, and the request ids. Only what the receipts and the config say."""
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    flags = _models_open(cfg)
    base = ((d.get("llm") or {}).get("otari") or {}).get("base_url") or ""
    host = urllib.parse.urlsplit(base).hostname or ""
    gateway = {"host": host, "region": ("EU (the host starts with eu.)" if host.startswith("eu.") else "Otari's default region (the host has no eu. prefix)") if host else "unknown"}
    providers: dict[str, dict] = {}
    for c in calls_of(d):
        r = c["receipt"]
        if not r.get("provider"):
            continue
        p = providers.setdefault(r["provider"], {"provider": r["provider"], "models": set(), "roles": set(), "open_weights": [], "calls": 0, "request_ids": []})
        p["models"].add(r.get("served_model"))
        p["roles"].add(c["label"])
        p["calls"] += 1
        if flags.get(r.get("sent_model")) is not None:
            p["open_weights"].append(flags[r["sent_model"]])
        elif r.get("sent_model") is not None:
            p["open_weights"].append(None)
        if _rid(r):
            p["request_ids"].append(_rid(r))
    rows = [{"provider": p["provider"], "models": sorted(m for m in p["models"] if m), "roles": sorted(p["roles"]), "calls": p["calls"],
             "open_weights": "yes" if p["open_weights"] and all(v is True for v in p["open_weights"]) else ("no" if any(v is False for v in p["open_weights"]) else "unknown"),
             "request_ids": p["request_ids"]} for p in providers.values()]
    return {"seq": d.get("seq"), "gateway": gateway, "providers": sorted(rows, key=lambda r: (-r["calls"], r["provider"]))}


# ---------------------------------------------------------------- README blocks

def proofs_markdown(rows: list[dict]) -> str:
    """A short table to scan, then one block per resource: the evidence in a sentence, one request id per line, the code that does it."""
    where = {1: "`config/otari.json`, `llm.py`", 2: "`otari_canary.py`", 3: "`llm.read_headroom`, `llm.route_decision`", 4: "`llm._end_user`, `llm.read_headroom`", 5: "`llm.parse_receipt`",
             6: "`otari_web.py`", 7: "`sandbox_rederive.py`", 8: "`otari_mcp.py`"}
    origin = {"sealed": "Sealed receipts in the ledger", "drill": "A drill file, not sealed", "none": "No proof"}
    lines = ["| # | Resource | Status | Dossier |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['n']} | {r['resource']} | **{r['status']}** | {r['dossier'] or '-'} |")
    lines.append("")
    lines.append(f"**{summary_line(rows)}.**")
    for r in rows:
        lines.append("")
        lines.append(f"#### {r['n']}. {r['resource']}")
        lines.append("")
        lines.append(f"**{r['status']}** &nbsp;|&nbsp; {origin[r['source']]} &nbsp;|&nbsp; dossier {r['dossier'] or '-'}")
        lines.append("")
        lines.append(f"> {r['note']}")
        ids = [f"- `{i}`" for i in r["proof"]] + [f"- `{i}` (drill, not sealed)" for i in r["drill_proof"]]
        if ids:
            lines.append("")
            lines.append("Request ids:")
            lines.append("")
            lines.extend(ids)
        lines.append("")
        lines.append(f"Code: {where[r['n']]}")
    return "\n".join(lines)


def board_markdown(rows: list[dict]) -> str:
    lines = ["| Model (provider:model) | Calls | OK | Refusals / failures | p50 ms | p95 ms | Fallbacks | Accepted claims | USD per claim |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        refusals = ", ".join(f"{k} x{v}" for k, v in r["refusals"].items()) or "-"
        lines.append(f"| `{r['model']}` | {r['calls']} | {r['ok']} | {refusals} | {r['p50_ms'] if r['p50_ms'] is not None else '-'} | {r['p95_ms'] if r['p95_ms'] is not None else '-'} | {r['fallbacks']} | {r['claims'] or '-'} | {r['cost_per_claim_usd'] or '-'} |")
    return "\n".join(lines)


def hosts_markdown(cfg: dict | None = None) -> str:
    cfg = cfg or json.loads(HOSTS.read_text(encoding="utf-8"))
    lines = []
    for h in cfg["hosts"]:
        lines.append(f"- **`{h['host']}`**, {h['what']}. Receives: {h['receives']}. When: {h['when']}.")
    return "\n".join(lines)


# Role groups of the "Models and roles" table: (name, what it does, roles of config/otari.json that share a row, why this model).
# The words are ours; the model ids come from the config and the answers from the sealed receipts.
ROLE_ROWS = [
    ("Bull analyst", "argues the long case", ("bull",), "an open-weight 120B model from a different vendor than the bear, so the two sides do not echo one model"),
    ("Bear analyst", "argues the short case", ("bear",), "an open-weight 120B model from another vendor, so a claim's side can be traced to one model"),
    ("Judge", "weighs the validated claims", ("judge",), "a fast model by default; the router may take the larger route of the same family only while the judge's end user is under 80 percent of its daily budget"),
    ("Citation checker", "re-reads each claim against the evidence it cites", ("checker",), "small on purpose, since it only compares a claim with its source; a Llama 4 route was tried first and dropped for 504s (feedback log, entry 1)"),
    ("Narrator", "reads news headlines in batches", ("narrator",), "a large open-weight model for the highest-volume reading job, sent as a plain catalog id so Otari's own provider failover applies"),
    ("Tool drivers", "search, fetch, sandbox, MCP", ("searcher", "fetcher", "drift", "sandbox", "mcp"), "the tool does the work and the model only drives it, so a small fast model is enough"),
]


def models_markdown(ledger: list[dict], cfg: dict | None = None) -> str:
    """The roles and the open-weight model each is asked for (config/otari.json), next to the provider:model routes that really answered
    (counted from the sealed receipts of the Otari era)."""
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    answered: dict[str, dict[str, int]] = {}
    for d in era(ledger):
        for c in calls_of(d):
            r = c["receipt"]
            if r.get("served_by") and not _failed(r):
                bucket = answered.setdefault(c["label"], {})
                bucket[r["served_by"]] = bucket.get(r["served_by"], 0) + 1
    lines: list[str] = []
    for name, job, roles, why in ROLE_ROWS:
        specs = [cfg["roles"][r] for r in roles if r in cfg.get("roles", {})]
        asked = sorted({s["model"] for s in specs})
        vendors = sorted({s.get("vendor", "") for s in specs if s.get("vendor")})
        merged: dict[str, int] = {}
        for r in roles:
            for key, n in answered.get(r, {}).items():
                merged[key] = merged.get(key, 0) + n
        top = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"`{k}` ({n})" for k, n in top[:3]) + (f", and {len(top) - 3} more" if len(top) > 3 else "")
        asked_cell = ", ".join(f"`{m}`" for m in asked) + (f" ({', '.join(vendors)})" if vendors else "")
        if lines:
            lines.append("")
        lines.append(f"**{name}** &nbsp;|&nbsp; {job}")
        lines.append("")
        lines.append(f"- Asked for (open weights): {asked_cell or '-'}")
        lines.append(f"- Answered by, from the receipts: {shown or '-'}")
        lines.append(f"- Why this model: {why}")
    return "\n".join(lines)


MARKERS = {"proofs": "otari:proofs", "board": "otari:board", "hosts": "otari:hosts", "models": "otari:models"}


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {MARKERS[name]}:start -->", f"<!-- {MARKERS[name]}:end -->"
    i, j = text.index(start), text.index(end)
    return text[:i + len(start)] + "\n" + body + "\n" + text[j:]


def refresh_readme(text: str, ledger: list[dict], evidence: list[dict] | None = None) -> str:
    text = replace_block(text, "proofs", proofs_markdown(audit(ledger, evidence)))
    text = replace_block(text, "board", board_markdown(board(ledger)))
    text = replace_block(text, "models", models_markdown(ledger))
    return replace_block(text, "hosts", hosts_markdown())
