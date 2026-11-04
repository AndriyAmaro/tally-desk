"""CLI: python -m verdict_desk decide-v7|replay|verify|otari-audit and the other chain tools (python -m verdict_desk --help)."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import voices as voices_config
from . import battery, closure, dossier, evidence, livestats, llm, narrative, portfolio, trust

DEFAULT_LEDGER = Path("ledger/ledger.jsonl")


def may_append(ledger_path: Path) -> bool:
    """Only the hourly workflow appends to the public ledger.

    A demo script, a test run or a local agent that appends there puts its dossiers into the chain that
    judges read: dossiers 54 to 68 are demo and test runs for exactly that reason, and the append-only
    ledger cannot take them back. GitHub Actions sets GITHUB_ACTIONS; a person who really means it sets
    VERDICT_DESK_APPEND_PUBLIC=1. Any other ledger path is free to use.
    """
    if ledger_path != DEFAULT_LEDGER or os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("VERDICT_DESK_APPEND_PUBLIC") == "1":
        return True
    print(
        f"Refusing to append to the public ledger ({ledger_path}) outside the hourly workflow.\n"
        "Use --ledger PATH for a scratch copy (copy ledger/ledger.jsonl there first if the demo needs history),\n"
        "or set VERDICT_DESK_APPEND_PUBLIC=1 if you really mean to record in the public chain.",
        file=sys.stderr,
    )
    return False


def publish_head(ledger_path: Path) -> None:
    """Keep ledger/head.json in step with the public ledger.

    A run that appends dossiers outside the hourly workflow (a person, a local agent) used to leave a
    stale head behind: on 2026-10-20 the published head sat at seq 53 while the chain had reached 68.
    """
    ledger = dossier.read_ledger(ledger_path)
    try:
        head = dossier.head(ledger)
    except ValueError as err:
        print(f"head.json not refreshed: {err}", file=sys.stderr)
        return
    (ledger_path.parent / "head.json").write_text(json.dumps(head, separators=(",", ":")) + "\n", encoding="utf-8")
    write_head_dossier(ledger_path.parent, ledger)


def write_head_dossier(folder: Path, ledger: list[dict]) -> None:
    """The whole last dossier, next to head.json (ask.html reads it).

    head.json carries only seq, hash and symbol; this file lets a reader check that the dossier hashes to that head and replay its verdict from the frozen evidence.
    """
    (folder / "head-dossier.json").write_text(json.dumps(ledger[-1], ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _rules_from_opts(opts, rules_version: str):
    """Regras efetivas: o padrao do modulo, ou o limite de risco pedido pelo usuario."""
    pct = getattr(opts, "risk_pct", None)
    if pct is None:
        return None
    if rules_version == "v1":
        print("refused: --risk-pct requires rules v2 or v3", file=sys.stderr)
        raise SystemExit(2)
    from . import agents_v2, agents_v3, agents_v4, agents_v5, agents_v6, agents_v7
    mod = {"v7": agents_v7, "v6": agents_v6, "v5": agents_v5, "v4": agents_v4, "v3": agents_v3}.get(rules_version, agents_v2)
    try:
        return mod.with_risk_pct(mod.DEFAULT, pct)
    except ValueError as err:
        print(f"refused: {err}", file=sys.stderr)
        raise SystemExit(2)


def _decide_flow(opts, rules_version: str) -> int:
    """Run the decide flow with the agents/features of the requested rules version."""
    if not may_append(opts.ledger):
        return 2
    llm.reset_cycle()  # the app's own spend cap counts one decision cycle at a time
    feeds = dict(narrative.DEFAULT_FEEDS) if opts.default_feeds else {}
    for spec in opts.feed or []:
        name, _, url = spec.partition("=")
        feeds[name] = url
    config = None
    if getattr(opts, "config", None):
        try:
            config = voices_config.load(opts.config)
        except voices_config.ConfigError as err:
            print(f"refused: {err}", file=sys.stderr)
            return 2
        feeds |= config["feeds"]
        if getattr(opts, "risk_pct", None) is None:
            opts.risk_pct = config["risk_pct"]  # o limite do usuario vale quando a linha de comando nao pede outro
    block = None
    if feeds:
        voices, gaps = narrative.collect_with_gaps(feeds, log=lambda m: print(m, file=sys.stderr))
        ask = (lambda system, user: llm.chat(system, user, timeout=narrative.LLM_TIMEOUT, max_tokens=narrative.max_tokens(), role="narrator")) if llm.available() else None
        block = narrative.build(voices, ask, llm.model_name() if ask else "")
        if config is not None:
            block["config"] = {**config["record"], "gaps": gaps}
        if rules_version in ("v5", "v6", "v7"):
            # A nota de cada voz sai do ledger ANTERIOR a este ciclo e e congelada no dossier.
            block["trust"] = trust.record_for(dossier.read_ledger(opts.ledger), {v["name"] for v in voices})
        print(f"narrative ({block['method']}): {len(voices)} headlines from {len({v['name'] for v in voices})} voices, {len(block['convergence'])} convergences"
              + (f", {len(gaps)} voices without a message ({', '.join(sorted(gaps))})" if gaps else ""))
    rules = _rules_from_opts(opts, rules_version)
    # Otari tools, one use each per cycle: a web search and an MCP price read, frozen into every packet of the cycle
    web = market_mcp = None
    if llm.feature_on("web_search"):
        from . import otari_web
        web = otari_web.collect(opts.symbols, block)
        page = web.get("page") or {}
        print(f"web via Otari: {len(web['items'])} items, page excerpt {'yes' if page.get('excerpt') else 'no'}" + (f" ({web['error'][:80]})" if web.get("error") else ""))
    drift = None
    if web is not None and llm._flag("OTARI_SOURCE_DRIFT", True):
        from . import source_drift
        drift = source_drift.collect(dossier.read_ledger(opts.ledger))  # one more fetch, of a page an earlier dossier cited; never moves a verdict
        print(f"source drift via Otari: {drift['status']}" + (f" ({drift['reason'][:80]})" if drift.get("reason") else ""))
    if llm.feature_on("mcp"):
        from . import otari_mcp
        market_mcp = otari_mcp.collect()
        print(f"MCP via Otari ({market_mcp['server']}): prices {market_mcp.get('prices')}, code trusted {market_mcp.get('trusted')}" + (f" ({market_mcp['error'][:80]})" if market_mcp.get("error") else ""))
    recorded = refused = 0
    gate = llm.otari_active()  # the closure gate guards every dossier of an Otari run
    for symbol in opts.symbols:
        try:
            packet = evidence.collect(symbol, opts.interval, with_news=not opts.no_news)
        except (OSError, ValueError, KeyError) as err:
            print(f"{symbol}: no evidence collected, nothing recorded ({err})", file=sys.stderr)
            continue
        if block is not None:
            packet["narrative"] = block
        if web is not None:
            packet["web"] = web
        if market_mcp is not None:
            from . import otari_mcp
            check = otari_mcp.apply(packet, market_mcp, float(llm.otari_config().get("market_check_limit_pct", otari_mcp.LIMIT_PCT)))
            print(f"{symbol}: MCP price check {check['status']}" + (f" ({check['diff_pct']}% from the exchange close, limit {check['limit_pct']}%)" if "diff_pct" in check else f" ({check.get('reason')})"))
        try:
            record = dossier.append(packet, opts.ledger, rules_version=rules_version, rules=rules, drift=drift, gate=gate)
        except closure.ClosureRefused as err:
            refused += 1
            _log_refusal(opts.ledger, err)
            continue
        recorded += 1
        print_record(record)
    if recorded and opts.ledger == DEFAULT_LEDGER:
        publish_head(opts.ledger)
    return 0 if recorded else (closure.EXIT_CODE if refused else 1)


def _log_refusal(ledger_path: Path, err) -> None:
    """A dossier the closure gate refused: nothing was appended. The reason goes to the screen (the runner log) and to ledger/closure-refusals.jsonl."""
    record = closure.refusal_record(err, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    closure.log_refusal(ledger_path, record)
    print(f"closure gate REFUSED {record['symbol']} (it would have been dossier {record['seq_not_used']}), nothing appended: {err}; cycle cost USD {record['usd']}", file=sys.stderr)


def cmd_decide_v7(opts) -> int:
    return _decide_flow(opts, "v7")


def cmd_otari_drill(opts) -> int:
    """Runs the fallback and/or budget drill against the live gateway (needs OTARI_API_KEY) and appends the records."""
    from . import otari_drill
    if not llm.otari_active():
        print("refused: the drills talk to the Otari gateway, set OTARI_API_KEY", file=sys.stderr)
        return 2
    records = otari_drill.run(opts.which, dossier.read_ledger(opts.ledger), opts.evidence or otari_drill.EVIDENCE)
    for r in records:
        if r["drill"] == "end-users":
            print("end users: " + ("PASS" if r["pass"] else "FAIL") + "; " + ", ".join(f"{n} {'on vd-analyst' if v['on_vd_analyst'] else 'budget ' + str(v['budget_id'])}" for n, v in r["end_users"].items()))
            continue
        if r["drill"] == "role-fallback":
            print("\n".join(otari_drill.summary_lines(r)))
            continue
        if r["drill"] == "mcp":
            b = r["block"]
            print(f"drill mcp: {'PASS' if r['pass'] else 'FAIL'} request {(b.get('receipt') or {}).get('request_id')} prices {b.get('prices')} cost ${(b.get('receipt') or {}).get('cost_usd')}")
            continue
        receipt = r.get("receipt") or (r.get("big_call") or {}).get("receipt") or {}
        print(f"drill {r['drill']}: {'PASS' if r['pass'] else 'FAIL'}"
              + (f" request {receipt.get('request_id')}" if receipt.get("request_id") else "")
              + (f" attempts {receipt.get('attempt_count')} fallback {receipt.get('fallback')} served by {receipt.get('served_by')}" if r["drill"] == "fallback" and receipt else "")
              + (f" refused {r['big_call']['error'].get('code')} scope {r['big_call']['error'].get('budget_scope')}" if r["drill"] == "budget" and (r.get("big_call") or {}).get("error") else "")
              + (f" ({r['error']})" if r.get("error") else ""))
    return 0 if all(r["pass"] for r in records) else 1


def cmd_otari_audit(opts) -> int:
    """Prints the eight resources with PASS or NOT PROVEN, where each proof lives (sealed receipts or a drill) and the request ids; with --reconcile also compares receipts with Otari's usage."""
    from . import otari_report
    ledger = dossier.read_ledger(opts.ledger)
    print(otari_report.render_audit(otari_report.audit(ledger, otari_report.read_evidence(opts.evidence) if opts.evidence else None)))
    if not opts.reconcile:
        return 0
    if not llm.otari_active():
        print("--reconcile reads Otari's usage endpoint: set OTARI_API_KEY (a GET, no cost)", file=sys.stderr)
        return 2
    receipts = [c["receipt"] for d in otari_report.era(ledger)[-max(1, opts.last):] for c in otari_report.calls_of(d)]
    result = otari_report.reconcile(receipts, lambda rid: llm.usage_for(rid, tries=2))
    print(f"reconciliation with Otari's usage records: {result['checked']} request ids compared, {len(result['unsettled'])} not found (not settled, or not this key's)")
    for item in result["divergences"]:
        print(f"  DIVERGES {item['request_id']} ({item['role']}): " + "; ".join(item["problems"]))
    if not result["divergences"]:
        print("  no divergence: status, tokens, cost and usage rows agree (Otari's usage record does not carry the model, so the model is not compared)")
    return 1 if result["divergences"] else 0


def cmd_trust(opts) -> int:
    """Per-voice track record, measured from the ledger alone."""
    result = trust.compute(dossier.read_ledger(opts.ledger))
    if opts.out:
        Path(opts.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}")
        return 0
    print(f"voice track record as of dossier {result['as_of_seq']} (horizon {result['horizon_hours']} h, a voice needs {result['min_calls']} settled calls to carry weight; {result['pending_calls']} calls still pending)")
    print(f"{'voice':18s} {'calls':>5s} {'hit':>5s} {'chance':>7s} {'skill':>7s} {'weight':>7s}")
    for name, v in sorted(result["voices"].items(), key=lambda kv: -kv[1]["weight"]):
        print(f"{name:18s} {v['n']:5d} {v['hit_rate']:5.2f} {v['chance_rate']:7.2f} {v['skill']:+7.3f} {v['weight']:7.3f}" + ("" if v["proven"] else "  (no track record yet)"))
    return 0


def cmd_battery(opts) -> int:
    """Attack a copy of the ledger in every way we know and count what the verifier misses."""
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print(f"no ledger at {opts.ledger}", file=sys.stderr)
        return 1
    if dossier.verify(ledger):
        print("the ledger itself does not verify, so there is nothing to attack", file=sys.stderr)
        return 1
    stamps = Path(opts.stamps) if opts.stamps else opts.ledger.parent / "timestamps"
    result = battery.run(ledger, stamps, opts.seed or ledger[-1]["hash"], trials=opts.trials)
    if opts.out:
        Path(opts.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}")
    print(f"tamper battery on {result['dossiers']} dossiers (head {result['head']['hash'][:16]}, seed {str(result['seed'])[:16]}): "
          f"{result['total_trials']} attacks, {result['total_missed']} missed")
    for row in result["attacks"]:
        alone = f", {row['chain_alone_missed']} invisible to the chain alone" if row["chain_alone_missed"] else ""
        print(f"  {row['attack']:34s} {row['caught']:5d}/{row['trials']:<5d} caught by {row['caught_by']}{alone}")
    return 0 if result["all_caught"] else 1


def cmd_witnesses(opts) -> int:
    """Write the index the witnesses page reads: the stamped heads, their proofs and the signed heads."""
    stamps = Path(opts.stamps) if opts.stamps else opts.ledger.parent / "timestamps"
    rekor = Path(opts.rekor) if opts.rekor else opts.ledger.parent / "rekor"
    index = battery.witness_index(stamps, rekor=rekor)
    if opts.out:
        Path(opts.out).write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}")
    print(f"{len(index['heads'])} stamped heads, {len(index['rekor'])} signed in the Sigstore log")
    return 0


def cmd_market(opts) -> int:
    """Every candle the ledger froze, per symbol (Ask the record replays what happened after a decision on it)."""
    from . import portfolio
    series = portfolio.market_series(dossier.read_ledger(opts.ledger))
    Path(opts.out).write_text(json.dumps(series, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {opts.out}: " + ", ".join(f"{s} {len(e['candles'])} candles" for s, e in series.items()))
    return 0


def cmd_trust_check(opts) -> int:
    """Recompute every frozen trust table from the dossiers before it."""
    ledger = dossier.read_ledger(opts.ledger)
    problems = trust.check(ledger)
    frozen = sum(1 for d in ledger if ((d.get("evidence") or {}).get("narrative") or {}).get("trust") is not None)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"trust tables: {frozen} frozen, {len(problems)} that the earlier ledger does not support")
    return 1 if problems else 0


def print_record(record: dict) -> None:
    v = record["verdict"]
    print(f"#{record['seq']} {record['evidence']['symbol']} {v['action']} edge={v['edge']} hash={record['hash'][:16]}")
    print(f"  {v['rationale']}")
    for side in ("bull", "bear"):
        for arg in record[side]:
            print(f"  [{side}] {arg['claim']} ({arg['evidence_ref']} = {arg['value']})")
    sens = record.get("sensitivity")
    if sens:
        flips = [d for d in sens["drop_one"] if d["flips"]]
        print(f"  to flip: +{sens['needs_for_long']} net edge for LONG, +{sens['needs_for_short']} for SHORT; "
              + (f"arguments that alone decide it: {len(flips)}" if flips else "no single argument decides it"))
    layer = record.get("llm")
    if layer:
        for side in ("bull", "bear"):
            for c in layer[side]["accepted"]:
                print(f"  [llm {side}] {c['claim']} cites {c['cites']}")
            print(f"  [llm {side}] {len(layer[side]['rejected'])} claims rejected by the citation validator"
                  + (f" (answered by {layer[side]['served_by']})" if layer[side].get("served_by") else ""))
        canary = (layer.get("otari") or {}).get("canary")
        if isinstance(canary, dict):
            cr = canary.get("receipt") or {}
            print(f"  [otari canary] {'PASSED' if canary.get('passed') else 'FAILED'}: policy {canary.get('policy')} "
                  + (f"served by {cr.get('served_by')} after {cr.get('attempt_count')} attempts, fallback {cr.get('fallback')}, ${cr.get('cost_usd')}" if cr.get("served_by") else f"not answered ({canary.get('error') or (cr.get('refusal') or {}).get('code')})")
                  + f" (request {cr.get('request_id')})")
        headroom = (layer.get("otari") or {}).get("headroom")
        if isinstance(headroom, dict):
            print(f"  [otari headroom] {headroom.get('reason')}")
        judge = layer.get("judge")
        if isinstance(judge, dict):
            route = judge.get("route_rule")
            if isinstance(route, dict):
                print(f"  [otari judge route] {'HARD, the larger model' if route['hard'] else 'standard'}: {route['model']}; {route['reason']}")
            ruling = judge.get("ruling") or {}
            print(f"  [llm judge] lean {ruling.get('lean')} ({ruling.get('conviction')}) on {ruling.get('relied_on')}: "
                  + (f"counts {judge['weight']}" if judge.get("accepted") else f"voided ({judge.get('rejected_because') or judge.get('error')})"))
        for part in ("bull", "bear", "checker", "judge"):
            receipt = (layer.get(part) or {}).get("receipt") or {}
            refused = receipt.get("refusal") or receipt.get("error")
            if refused:  # a call Otari (or the cycle cap) refused is shown, not hidden; the decision still recorded
                what = refused.get("code") or refused.get("kind") or refused.get("status")
                print(f"  [otari {part}] NOT answered: {what}" + (f", budget scope {refused['budget_scope']}" if refused.get("budget_scope") else "")
                      + (f", retry after {refused['retry_after']} s" if refused.get("retry_after") else "") + f" (request {receipt.get('request_id') or 'none'})")
        for part in ("bull", "bear", "checker", "judge"):
            receipt = (layer.get(part) or {}).get("receipt") or {}
            for g in receipt.get("guardrails") or []:  # the guardrail Otari attached to the call, as the header reported it
                verdict = ("not evaluated (valid null" + (f", {g['status']}" if g.get("status") else "") + (f", {g['reason']}" if g.get("reason") else "") + ")"
                           if g.get("valid") is None else f"valid={g['valid']} score={g.get('score')}")
                print(f"  [otari {part}] guardrail {g.get('profile')} ({g.get('mode')}): {verdict}")
        cycle = (layer.get("otari") or {}).get("cycle")
        web = (record.get("evidence") or {}).get("web") or {}
        use = (layer.get("otari") or {}).get("evidence_use") or {}
        if web.get("items"):
            page = web.get("page") or {}
            print(f"  [otari web] {len(web['items'])} items frozen: " + "; ".join(i["domain"] for i in web["items"]) + f" (request {(web.get('receipt') or {}).get('request_id')})"
                  + (f"; page excerpt sha256 {page['excerpt_sha256'][:12]}, verified {page.get('excerpt_verified')} (request {page.get('request_id')})" if page.get("excerpt") else "")
                  + f"; cited by the analysts: {use.get('web_items_cited', 0)} of {use.get('web_items', 0)} source titles, {use.get('web_excerpts_cited', 0)} of {use.get('web_excerpts', 0)} excerpts")
        mcp = (record.get("evidence") or {}).get("market_mcp") or {}
        if mcp.get("prices"):
            chk = mcp.get("check") or {}
            print(f"  [otari mcp] {mcp['server']} prices {mcp['prices']} from the server's own result block (request {(mcp.get('receipt') or {}).get('request_id')}); "
                  f"check vs the exchange close: {chk.get('status')}" + (f" {chk['diff_pct']}% of a {chk['limit_pct']}% limit" if "diff_pct" in chk else ""))
        sandbox = record.get("sandbox")
        if sandbox:
            print(f"  [otari sandbox] sandbox ({sandbox.get('evidence')}) "
                  + ("did NOT run the code that was sent (no run used exactly that code), so nothing was re-derived"
                     if sandbox.get("evidence") == "native" and not sandbox.get("code_ran_is_code_sent")
                     else f"output {'IDENTICAL to' if sandbox.get('equal') else 'DIFFERENT from'} the local re-derivation, proof hash {'ok' if sandbox.get('proof_ok') else 'NOT ok'}")
                  + f"; local agrees with the verdict: {sandbox.get('local_agrees_with_verdict')} (request {(sandbox.get('receipt') or {}).get('request_id')})")
        if cycle:
            print(f"  [otari] cycle spend ${cycle['spent_usd']} over {cycle['calls']} calls"
                  + (f", cap ${cycle['cap_usd']}" if cycle.get("cap_usd") else "") + (f", {cycle['refused']} refused by the cap" if cycle.get("refused") else ""))
    else:
        print("  LLM analysts off (no OTARI_API_KEY): deterministic core only")


def cmd_replay(opts) -> int:
    ledger = dossier.read_ledger(opts.ledger)
    targets = [d for d in ledger if opts.seq is None or d["seq"] == opts.seq]
    if not targets:
        print("No dossier found.", file=sys.stderr)
        return 1
    mismatches = 0
    for d in targets:
        rv = d.get("verdict", {}).get("rules_version", "v1")
        result = dossier.replay(d, rules_version=rv)
        mismatches += not result["matches"]
        print(f"#{result['seq']} replay {'MATCHES' if result['matches'] else 'DIFFERS'}: {result['replayed']['action']}")
    return 1 if mismatches else 0


def cmd_verify(opts) -> int:
    ledger = dossier.read_ledger(opts.ledger)
    problems = dossier.verify(ledger)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Chain intact: {len(ledger)} dossiers, head {ledger[-1]['hash'][:16] if ledger else '(empty)'}")
    return 0


EXPORT_INSTRUCTIONS = [
    "1. Open this file and read `dossier`. Nothing in it is trusted yet.",
    "2. Remove the `hash` key, canonicalize the rest as UTF-8 JSON with sorted keys and",
    "   no whitespace (verdict_desk/canonical.py), then SHA-256 it. It must equal",
    "   `recomputed_hash` below and `dossier.hash`.",
    "3. Run: python3 -m verdict_desk --ledger <ledger.jsonl> replay <seq>",
    "   It recomputes the verdict from the frozen evidence alone. It must MATCH.",
    "4. Compare `neighbors.prev_hash` with the real previous dossier, and this dossier's",
    "   hash with the next one. Any edit here breaks both links.",
    "5. `pack_digest` covers everything above (without itself). Change one byte and it",
    "   changes; that is how you know this pack was not edited in transit.",
]


def export_pack(ledger: list[dict], seq: int) -> dict:
    """Pacote de evidencia autoverificavel para UM dossie, para entregar a um auditor."""
    target = next((d for d in ledger if d["seq"] == seq), None)
    if target is None:
        raise ValueError(f"seq {seq} not found in the ledger")
    index = ledger.index(target)
    replay_result = dossier.replay(target, rules_version=target.get("verdict", {}).get("rules_version", "v1"))
    body = {k: v for k, v in target.items() if k != "hash"}
    pack = {
        "format": "verdict-desk/evidence-pack/v1",
        "seq": seq,
        "symbol": target["evidence"]["symbol"],
        "recorded_hash": target["hash"],
        "recomputed_hash": dossier.digest(body),
        "verdict": target["verdict"],
        "replay": {
            "matches": replay_result["matches"],
            "replayed_action": replay_result["replayed"]["action"],
            "replayed_edge": replay_result["replayed"]["edge"],
            "llm_citations_valid": replay_result.get("llm_citations_valid"),
        },
        "neighbors": {
            "prev_hash": target["prev_hash"],
            "next_hash": ledger[index + 1]["hash"] if index + 1 < len(ledger) else None,
            "is_head": index + 1 == len(ledger),
        },
        "chain_position": {"index": index, "ledger_length": len(ledger)},
        "how_to_verify_this_file_without_trusting_us": EXPORT_INSTRUCTIONS,
        "dossier": target,
    }
    pack["pack_digest"] = dossier.digest(pack)
    return pack


def cmd_export(opts) -> int:
    ledger = dossier.read_ledger(opts.ledger)
    try:
        pack = export_pack(ledger, opts.seq)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    text = json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False)
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {opts.out}  ({len(text):,} bytes)")
        print(f"pack digest   {pack['pack_digest']}")
        print(f"recomputed    {pack['recomputed_hash']}  (recorded {pack['recorded_hash']})")
        print(f"replay        {'MATCHES' if pack['replay']['matches'] else 'DIFFERS'} -> {pack['replay']['replayed_action']}")
        if pack["neighbors"]["is_head"]:
            print("this dossier is the current head of the chain")
    else:
        print(text)
    return 0 if pack["replay"]["matches"] and pack["recomputed_hash"] == pack["recorded_hash"] else 1


def cmd_packs(opts) -> int:
    """Write evidence packs for the newest dossiers into a directory the site serves (ask.html reads them)."""
    ledger = dossier.read_ledger(opts.ledger) if Path(opts.ledger).exists() else []
    if not ledger:
        print(f"no ledger at {opts.ledger}", file=sys.stderr)
        return 1
    wanted = {d["seq"] for d in ledger[-opts.recent:]}
    outdir = Path(opts.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for seq in sorted(wanted):
        pack = export_pack(ledger, seq)
        (outdir / f"{seq}.json").write_text(json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(wanted)} evidence packs to {outdir}")
    return 0


def cmd_stats(opts) -> int:
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print("The ledger is empty; nothing to count.", file=sys.stderr)
        return 1
    problems = dossier.verify(ledger)
    if problems:
        print("Refusing to count a broken chain: " + "; ".join(problems), file=sys.stderr)
        return 1
    summary = livestats.summarize(ledger, opts.first_llm_seq, opts.last_llm_seq)
    if opts.json:
        print(livestats.to_json(summary))
    else:
        print(livestats.format_report(summary, livestats.commit_authors(opts.ledger, ledger) if opts.git else None))
    return 0


def cmd_portfolio(opts) -> int:
    s = portfolio.summarize(dossier.read_ledger(opts.ledger))
    print(f"Decisions {s['decisions']} (flat {s['flat']}), positions {len(s['positions'])}: closed {s['closed']}, open {s['open']}")
    for p in s["positions"]:
        print(f"  #{p['seq']} {p['symbol']} {p['action']} entry {p['entry']} -> {p['status']} exit {p['exit']} pnl {p['pnl']}")
    print(f"Win rate {s['win_rate_pct']}%  realized {s['realized_pnl']}  unrealized {s['unrealized_pnl']}  max drawdown {s['max_drawdown_pct']}%  (simulated, start {s['start_capital']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="verdict_desk")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decide-v7", help="decide with rules v7: v6 + the citation checker has a consequence: a judge ruling that relies on a claim the checker marked unsupported carries no weight")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v7)
    p = sub.add_parser("trust", help="per-voice track record: hit rate against chance, measured from the ledger alone")
    p.add_argument("--out", metavar="FILE", help="write the full table as JSON instead of printing it")
    p.set_defaults(func=cmd_trust)
    p = sub.add_parser("battery", help="attack a copy of the ledger (edit, delete, swap, duplicate, forge, truncate, rewrite) and count what the verifier misses")
    p.add_argument("--trials", type=int, default=2000, help="value edits to try; the other attacks scale from it")
    p.add_argument("--seed", help="seed for the attacks (default: the head hash, so anyone re-running gets the same ones)")
    p.add_argument("--stamps", metavar="DIR", help="directory with the timestamped heads (default: <ledger dir>/timestamps)")
    p.add_argument("--out", metavar="FILE", help="also write the full result as JSON")
    p.set_defaults(func=cmd_battery)
    p = sub.add_parser("market", help="every candle the ledger froze, per symbol, as JSON (read by ask.html)")
    p.add_argument("--out", metavar="FILE", required=True)
    p.set_defaults(func=cmd_market)
    p = sub.add_parser("witnesses", help="write the index of timestamped and signed heads that the witnesses page reads")
    p.add_argument("--stamps", metavar="DIR", help="directory with the timestamped heads (default: <ledger dir>/timestamps)")
    p.add_argument("--rekor", metavar="DIR", help="directory with the Sigstore bundles (default: <ledger dir>/rekor)")
    p.add_argument("--out", metavar="FILE", help="write the index as JSON")
    p.set_defaults(func=cmd_witnesses)
    p = sub.add_parser("trust-check", help="recompute every frozen trust table from the dossiers before it (no lookahead)")
    p.set_defaults(func=cmd_trust_check)
    p = sub.add_parser("replay", help="re-run decisions from the frozen evidence")
    p.add_argument("seq", nargs="?", type=int)
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("export", help="write a self-verifying evidence pack for one dossier (hand it to an auditor)")
    p.add_argument("--seq", type=int, required=True, help="dossier sequence number to export")
    p.add_argument("--out", type=Path, required=True, help="where to write the pack (JSON)")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("packs", help="write evidence packs for the newest dossiers into a directory the site serves")
    p.add_argument("--recent", type=int, default=24, metavar="N", help="how many of the newest dossiers (default 24)")
    p.add_argument("--outdir", required=True, metavar="DIR", help="where to write <seq>.json")
    p.set_defaults(func=cmd_packs)
    p = sub.add_parser("otari-drill", help="real Otari requests that prove a routing fallback, the app's role fallback chain and a budget refusal; receipts go to an evidence file, not to the ledger")
    p.add_argument("which", choices=["fallback", "role-fallback", "budget", "mcp", "users", "all"])
    p.add_argument("--evidence", type=Path, default=None, help="evidence file to append to (default docs/evidence/otari-drills.jsonl)")
    p.set_defaults(func=cmd_otari_drill)
    p = sub.add_parser("otari-audit", help="the eight Otari resources proven from the sealed receipts, with the request id of each proof; --reconcile compares receipts with Otari's own usage records")
    p.add_argument("--reconcile", action="store_true", help="GET /usage/requests/{id} for the receipts of the newest dossiers (free, needs OTARI_API_KEY) and list divergences")
    p.add_argument("--last", type=int, default=3, help="how many of the newest Otari-era dossiers to reconcile (default 3)")
    p.add_argument("--evidence", type=Path, default=None, help="drill evidence file (default docs/evidence/otari-drills.jsonl)")
    p.set_defaults(func=cmd_otari_audit)
    p = sub.add_parser("verify", help="check the hash chain")
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("stats", help="live-operation numbers counted from the verified ledger: dossiers, window, cadence, citation validator results")
    p.add_argument("--first-llm-seq", type=int, default=livestats.FIRST_LLM_SEQ, help="first dossier of the LLM era (default %(default)s)")
    p.add_argument("--last-llm-seq", type=int, default=None, help="last dossier of the range to count (default: the head)")
    p.add_argument("--git", action="store_true", help="also count dossiers per git author of the line that recorded them")
    p.add_argument("--json", action="store_true", help="print the full summary as JSON")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("portfolio", help="mark simulated positions against later candles")
    p.set_defaults(func=cmd_portfolio)
    opts = parser.parse_args()
    return opts.func(opts)


if __name__ == "__main__":
    sys.exit(main())
