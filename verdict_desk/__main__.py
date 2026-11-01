"""CLI: python -m verdict_desk decide|replay|verify."""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import voices as voices_config
from . import anchor, backtest, battery, cadence, dossier, evidence, journal, learning, livestats, llm, narrative, portfolio, research, scoreboard, trust

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
    """The whole last dossier, next to head.json: the CRE workflow re-derives its verdict before it anchors.

    head.json carries only seq, hash and symbol; the workflow fetches this file from the two publications,
    checks that it hashes to that head, and replays the verdict from its frozen evidence.
    """
    (folder / "head-dossier.json").write_text(json.dumps(ledger[-1], ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def cmd_decide(opts) -> int:
    if not may_append(opts.ledger):
        return 2
    feeds = dict(narrative.DEFAULT_FEEDS) if opts.default_feeds else {}
    for spec in opts.feed or []:
        name, _, url = spec.partition("=")
        feeds[name] = url
    block = None
    if feeds:
        # As vozes valem para todos os simbolos: busca e extracao rodam uma vez por execucao.
        voices = narrative.collect(feeds, log=lambda m: print(m, file=sys.stderr))
        ask = (lambda system, user: llm.chat(system, user, timeout=narrative.LLM_TIMEOUT, max_tokens=narrative.LLM_MAX_TOKENS)) if llm.available() else None
        block = narrative.build(voices, ask, llm.MODEL if ask else "")
        print(f"narrative ({block['method']}): {len(voices)} headlines from {len({v['name'] for v in voices})} voices, {len(block['convergence'])} convergences")
    recorded = 0
    for symbol in opts.symbols:
        try:
            packet = evidence.collect(symbol, opts.interval, with_news=not opts.no_news)
        except (OSError, ValueError, KeyError) as err:
            # Lacuna real: nada e gravado e o erro aparece, o historico nao finge uma decisao.
            print(f"{symbol}: no evidence collected, nothing recorded ({err})", file=sys.stderr)
            continue
        if block is not None:
            packet["narrative"] = block
        recorded += 1
        print_record(dossier.append(packet, opts.ledger))
    if recorded and opts.ledger == DEFAULT_LEDGER:
        publish_head(opts.ledger)
    return 0 if recorded else 1


def _rules_from_opts(opts, rules_version: str):
    """Regras efetivas: o padrao do modulo, ou o limite de risco pedido pelo usuario."""
    pct = getattr(opts, "risk_pct", None)
    if pct is None:
        return None
    if rules_version == "v1":
        print("refused: --risk-pct requires rules v2 or v3", file=sys.stderr)
        raise SystemExit(2)
    from . import agents_v2, agents_v3, agents_v4, agents_v5, agents_v6
    mod = {"v6": agents_v6, "v5": agents_v5, "v4": agents_v4, "v3": agents_v3}.get(rules_version, agents_v2)
    try:
        return mod.with_risk_pct(mod.DEFAULT, pct)
    except ValueError as err:
        print(f"refused: {err}", file=sys.stderr)
        raise SystemExit(2)


def _decide_flow(opts, rules_version: str) -> int:
    """Run the decide flow with the agents/features of the requested rules version."""
    if not may_append(opts.ledger):
        return 2
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
        ask = (lambda system, user: llm.chat(system, user, timeout=narrative.LLM_TIMEOUT, max_tokens=narrative.LLM_MAX_TOKENS)) if llm.available() else None
        block = narrative.build(voices, ask, llm.MODEL if ask else "")
        if config is not None:
            block["config"] = {**config["record"], "gaps": gaps}
        if rules_version in ("v5", "v6"):
            # A nota de cada voz sai do ledger ANTERIOR a este ciclo e e congelada no dossier.
            block["trust"] = trust.record_for(dossier.read_ledger(opts.ledger), {v["name"] for v in voices})
        print(f"narrative ({block['method']}): {len(voices)} headlines from {len({v['name'] for v in voices})} voices, {len(block['convergence'])} convergences"
              + (f", {len(gaps)} voices without a message ({', '.join(sorted(gaps))})" if gaps else ""))
    rules = _rules_from_opts(opts, rules_version)
    from . import practice
    execute = practice.chain_executor()  # practice trades on Sepolia when the key is present; otherwise simulated as before
    recorded = 0
    for symbol in opts.symbols:
        try:
            packet = evidence.collect(symbol, opts.interval, with_news=not opts.no_news)
        except (OSError, ValueError, KeyError) as err:
            print(f"{symbol}: no evidence collected, nothing recorded ({err})", file=sys.stderr)
            continue
        if block is not None:
            packet["narrative"] = block
        recorded += 1
        print_record(dossier.append(packet, opts.ledger, rules_version=rules_version, rules=rules, execute=execute))
    if recorded and opts.ledger == DEFAULT_LEDGER:
        publish_head(opts.ledger)
    return 0 if recorded else 1


def cmd_decide_v2(opts) -> int:
    return _decide_flow(opts, "v2")


def cmd_decide_v4(opts) -> int:
    return _decide_flow(opts, "v4")


def cmd_decide_v5(opts) -> int:
    return _decide_flow(opts, "v5")


def cmd_decide_v6(opts) -> int:
    return _decide_flow(opts, "v6")


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
    """Write the index the witnesses page reads: the stamped heads, their proofs, the anchor contract."""
    stamps = Path(opts.stamps) if opts.stamps else opts.ledger.parent / "timestamps"
    rekor = Path(opts.rekor) if opts.rekor else opts.ledger.parent / "rekor"
    index = battery.witness_index(stamps, Path(opts.deployment), rekor=rekor)
    if opts.out:
        Path(opts.out).write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}")
    print(f"{len(index['heads'])} stamped heads, {len(index['rekor'])} signed in the Sigstore log" + (f", anchor contract {index['anchor']['address']}" if index["anchor"] else ", no anchor contract on file"))
    return 0


def cmd_market(opts) -> int:
    """Every candle the ledger froze, per symbol (Ask the record replays what happened after a decision on it)."""
    from . import portfolio
    series = portfolio.market_series(dossier.read_ledger(opts.ledger))
    Path(opts.out).write_text(json.dumps(series, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {opts.out}: " + ", ".join(f"{s} {len(e['candles'])} candles" for s, e in series.items()))
    return 0


def cmd_models(opts) -> int:
    """The model bench, and the routing policy the gateway follows (re-ranked by the fixed rule, never by hand)."""
    from datetime import datetime, timezone
    from . import models
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print("empty ledger: nothing to rank")
        return 0
    if opts.route:
        path = Path(opts.route)
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        now = datetime.now(timezone.utc).isoformat()
        if current and models.frozen(now):
            print(f"routing policy frozen until {models.FROZEN[1][:10]}: kept as of #{current['as_of_seq']}")
        elif current and ledger[-1]["seq"] - current["as_of_seq"] < opts.every:
            print(f"routing policy as of #{current['as_of_seq']} is recent enough (re-ranked every {opts.every} dossiers)")
        else:
            fresh = models.policy(ledger)
            path.write_text(json.dumps(fresh, indent=1) + "\n", encoding="utf-8")
            print(f"wrote {path}: order {', '.join(fresh['order'])} (as of #{fresh['as_of_seq']})")
    if opts.out:
        out = models.bench(ledger)
        policy_path = Path(opts.policy)
        if policy_path.exists():
            raw = policy_path.read_bytes()
            out["policy"] = json.loads(raw)
            out["policy_sha256"] = hashlib.sha256(raw).hexdigest()
        Path(opts.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}: {len(out['models'])} models")
    return 0


def cmd_series(opts) -> int:
    """The judged series: which model wrote each stretch of the ledger, and why it changed, read from the ledger."""
    from . import series
    eras = series.eras(dossier.read_ledger(opts.ledger))
    if opts.out:
        Path(opts.out).write_text(json.dumps({"eras": eras}, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {opts.out}")
    for e in eras:
        served = ", ".join(f"{m} x{n}" for m, n in sorted(e["served_by"].items(), key=lambda kv: -kv[1]))
        print(f"#{e['from_seq']}-#{e['to_seq']} ({e['records']}) {e['model'] or '-'}: {e['status']}"
              + (f", {e['reason']}" if e["reason"] else "") + (f"; answered by {served}" if served else ""))
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


def cmd_decide_v3(opts) -> int:
    return _decide_flow(opts, "v3")


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
        judge = layer.get("judge")
        if isinstance(judge, dict):
            ruling = judge.get("ruling") or {}
            print(f"  [llm judge] lean {ruling.get('lean')} ({ruling.get('conviction')}) on {ruling.get('relied_on')}: "
                  + (f"counts {judge['weight']}" if judge.get("accepted") else f"voided ({judge.get('rejected_because') or judge.get('error')})"))
    else:
        print("  LLM analysts off (no MISTRAL_API_KEY): deterministic core only")


def cmd_scorecard(opts) -> int:
    """Score the decisions against what the market did next, from the ledger alone."""
    from . import scorecard
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print(f"no ledger at {opts.ledger}", file=sys.stderr)
        return 1
    result = scorecard.compute(ledger)
    summary = portfolio.summarize(ledger)
    if opts.out:
        Path(opts.out).write_text(scorecard.render(result, summary), encoding="utf-8")
        print(f"wrote {opts.out}")
        return 0
    if opts.markdown:
        print(scorecard.render_markdown(result))
        return 0
    print(scorecard.render_markdown(result))
    print(f"\nrecords by state: {result['counts']}")
    return 0


def always_published(also=None) -> set[int]:
    """Dossiers the site keeps serving by number: config/always_published.json plus any --also given."""
    path = Path(__file__).resolve().parent.parent / "config" / "always_published.json"
    listed = json.loads(path.read_text(encoding="utf-8")).get("dossiers", []) if path.exists() else []
    return {int(seq) for seq in listed} | set(also or [])


def cmd_exhibit(opts) -> int:
    """Render dossiers as printable decision exhibits (stdout, a file, or a directory)."""
    from . import exhibit
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print(f"no ledger at {opts.ledger}", file=sys.stderr)
        return 1

    if opts.recent:
        target = ledger[-opts.recent:]
    elif opts.seq:
        target = [d for d in ledger if d.get("seq") == opts.seq]
        if not target:
            print(f"no dossier with seq {opts.seq}", file=sys.stderr)
            return 1
    else:
        target = [ledger[-1]]

    chain = exhibit.chain_context(ledger, ledger[-1]["seq"])
    anchor_info = {}
    if opts.anchor:
        try:
            anchored_seq = anchor.read_latest_seq(opts.rpc, opts.address)
            onchain = anchor.read_anchor(opts.rpc, opts.address, anchored_seq)
            onchain["seq"] = anchored_seq
            anchor_info[anchored_seq] = onchain
            print(f"anchor: record #{anchored_seq} read from {opts.address} on Sepolia", file=sys.stderr)
            # O dossie ancorado entra sempre: e ele que prova a custodia na cadeia.
            if opts.recent and anchored_seq not in {d.get("seq") for d in target}:
                anchored_row = next((d for d in ledger if d.get("seq") == anchored_seq), None)
                if anchored_row is not None:
                    target = [anchored_row] + target
        except (OSError, ValueError, KeyError) as err:
            print(f"anchor: could not read the chain ({err}); exhibits are produced without it", file=sys.stderr)

    if opts.recent:
        # Dossiers a page links by number (the use case walks through one) keep their exhibit after they leave the recent window.
        have = {d.get("seq") for d in target}
        target = [d for d in ledger if d.get("seq") in always_published(getattr(opts, "also", None)) - have] + target

    def anchor_for(row):
        onchain = anchor_info.get(row["seq"])
        if not onchain:
            return None
        # O contrato grava o hash com prefixo 0x e o ledger grava sem: normalizar antes
        # de comparar. Comparar cru produzia "nao bate" num registro perfeitamente ancorado.
        published = (row.get("hash") or "").lower().removeprefix("0x")
        anchored = (onchain.get("head_hash") or "").lower().removeprefix("0x")
        return {"matches": published == anchored,
                "anchored_seq": row["seq"], "anchored_at": onchain.get("anchored_at")}

    if opts.outdir:
        outdir = Path(opts.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        written = []
        for row in target:
            href = None
            if getattr(opts, "packs", None) and (Path(opts.packs) / f"{row['seq']}.json").exists():
                href = f"../{Path(opts.packs).name}/{row['seq']}.json"
            page = exhibit.render(row, chain=chain, anchor=anchor_for(row), pack_href=href)
            path = outdir / f"exhibit-{row['seq']}.html"
            path.write_text(page, encoding="utf-8")
            written.append((row, path))
            print(f"wrote {path}")
        index = exhibit.render_index([(row, f"exhibit-{row['seq']}.html") for row, _ in written])
        (outdir / "index.html").write_text(index, encoding="utf-8")
        print(f"wrote {outdir / 'index.html'}")
        return 0

    page = exhibit.render(target[0], chain=chain, anchor=anchor_for(target[0]))
    if opts.out:
        Path(opts.out).write_text(page, encoding="utf-8")
        print(f"wrote {opts.out}")
    else:
        sys.stdout.write(page)
    return 0


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


def cmd_head(opts) -> int:
    ledger = dossier.read_ledger(opts.ledger)
    try:
        head = dossier.head(ledger)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    text = json.dumps(head, separators=(",", ":"))
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(text + "\n", encoding="utf-8")
        write_head_dossier(opts.out.parent, ledger)
    print(text)
    return 0


EXPORT_INSTRUCTIONS = [
    "1. Open this file and read `dossier`. Nothing in it is trusted yet.",
    "2. Remove the `hash` key, canonicalize the rest as UTF-8 JSON with sorted keys and",
    "   no whitespace (verdict_desk/canonical.py), then SHA-256 it. It must equal",
    "   `recomputed_hash` below and `dossier.hash`.",
    "3. Run: python3 -m verdict_desk --ledger <ledger.jsonl> replay --seq <seq>",
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
    """Write evidence packs for the newest dossiers (and the anchored one) into a directory the site serves."""
    ledger = dossier.read_ledger(opts.ledger) if Path(opts.ledger).exists() else []
    if not ledger:
        print(f"no ledger at {opts.ledger}", file=sys.stderr)
        return 1
    wanted = {d["seq"] for d in ledger[-opts.recent:]}
    # Dossiers a page links by number (the use case walks through one) stay served after they leave the recent window.
    known = {d["seq"] for d in ledger}
    wanted |= {seq for seq in always_published(getattr(opts, "also", None)) if seq in known}
    if opts.anchored:
        root = Path(opts.ledger).resolve().parent.parent
        found = scoreboard._sepolia(root, ledger, None)
        if found:
            wanted.add(found["seq"])
    outdir = Path(opts.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for seq in sorted(wanted):
        pack = export_pack(ledger, seq)
        (outdir / f"{seq}.json").write_text(json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(wanted)} evidence packs to {outdir}")
    return 0


def cmd_monitor(opts) -> int:
    from . import anchor as anchor_mod

    ledger = dossier.read_ledger(opts.ledger)
    try:
        result = anchor_mod.monitor(ledger, rpc_url=opts.rpc, address=opts.address, seq=opts.seq)
    except Exception as err:  # rede fora do ar nao deve ser confundido com adulteracao
        print(f"could not read the anchor: {err}", file=sys.stderr)
        return 2
    d = result["price_decimals"] if result.get("price_decimals") is not None else 0
    price = result["price"] / (10 ** d) if result.get("price") is not None else None
    print(f"anchor on chain : seq {result['anchored_seq']}  {result['anchored_hash']}")
    if result.get("anchored_at"):
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(result["anchored_at"], tz=_dt.timezone.utc).isoformat(timespec="seconds")
        print(f"                  anchored at {when}" + (f", BTC/USD {price:,.2f}" if price else ""))
    print(f"published ledger: seq {result['published_seq']}  {result['published_hash']}")
    if result.get("ledger_head_seq") is not None:
        print(f"                  ledger has {result.get('ledger_length')} dossiers, head at seq {result['ledger_head_seq']}")
    if result["matches"]:
        print("RESULT: MATCH — the anchored dossier still has the hash that was anchored")
        return 0
    print("RESULT: MISMATCH — the published history no longer matches what the chain anchored")
    for p in result["problems"]:
        print(f"  - {p}")
    return 1


def _count_tests() -> int:
    import unittest
    return unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent.parent / "tests")).countTestCases()


def cmd_scoreboard(opts) -> int:
    """Print (or save) the scoreboard block for the build page, or check a saved one against the ledger."""
    ledger = dossier.read_ledger(opts.ledger)
    if not ledger:
        print("The ledger is empty; nothing to count.", file=sys.stderr)
        return 1
    root = Path(opts.ledger).resolve().parent.parent
    tests = opts.tests if opts.tests is not None else _count_tests()
    if opts.check:
        problems = scoreboard.check(Path(opts.check).read_text(encoding="utf-8"), ledger, root, tests)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"scoreboard {opts.check}: " + ("every figure reproduced from the ledger" if not problems else f"{len(problems)} figure line(s) differ"))
        return 1 if problems else 0
    seq = opts.seq or ledger[-1]["seq"]
    text = scoreboard.render(scoreboard.compute(ledger, seq, root, tests))
    if opts.out:
        Path(opts.out).write_text(text, encoding="utf-8")
        print(f"wrote {opts.out} (as of dossier {seq})")
    else:
        print(text, end="")
    return 0


def cmd_due(opts) -> int:
    """Print `run=true` or `run=false` for a workflow to append to its outputs; never fails the job."""
    now = datetime.now(timezone.utc)
    ledger = dossier.read_ledger(opts.ledger) if Path(opts.ledger).exists() else []
    print(f"run={'true' if cadence.due(ledger, now, opts.min_minutes) else 'false'}")
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


def cmd_backtest(opts) -> int:
    candles = backtest.fetch_history(opts.symbol, opts.interval, opts.candles)
    if len(candles) < backtest.WINDOW + opts.max_hold:
        print("Not enough history.", file=sys.stderr)
        return 1
    rep = backtest.report(opts.symbol, opts.interval, candles, max_hold=opts.max_hold, fee_pct=opts.fee_pct)
    s = rep["summary"]
    print(f"{rep['label']}: {rep['symbol']} {rep['interval']}, {rep['data']['candles']} candles, data sha256 {rep['data']['sha256'][:16]}")
    print(f"Trades {s['trades']} (long {s['longs']}, short {s['shorts']}), exits {s['by_exit']}, win rate {s['win_rate_pct']}%, profit factor {s['profit_factor']}")
    print(f"Net {s['net_pnl']} ({s['return_pct']}% of {s['start_capital']}), fees {s['fees_paid']}, max drawdown {s['max_drawdown_pct']}%, buy and hold {s['buy_and_hold_pct']}%")
    for seg in rep["segments"]:
        print(f"  part {seg['part']}: {seg['trades']} trades, net {seg['net_pnl']}")
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
        print(f"Saved {opts.out}")
    return 0


def cmd_research(opts) -> int:
    studies = []
    for symbol in opts.symbols:
        candles = backtest.fetch_history(symbol, opts.interval, opts.candles)
        if len(candles) < backtest.WINDOW + backtest.MAX_HOLD * 4:
            print(f"{symbol}: not enough history.", file=sys.stderr)
            continue
        studies.append(research.study(symbol, candles, fee_pct=opts.fee_pct))
    if not studies:
        return 1
    print(research.LABEL)
    for s in studies:
        v1, v2 = s["v1_test"], s["v2"]
        print(f"{s['symbol']}: data sha256 {s['data']['sha256'][:16]}, train {s['data']['train_candles']} candles, test {s['data']['test_candles']} candles")
        print(f"  v1 on test : {v1['trades']} trades, net {v1['net_pnl']} ({v1['return_pct']}%), profit factor {v1['profit_factor']}, buy and hold {v1['buy_and_hold_pct']}%")
        if v2:
            print(f"  v2 chosen on train {v2['chosen']} (train net {v2['train']['net_pnl']}, {v2['train_configs_with_positive_net']} of {v2['configs_scored']} configs positive on train)")
            print(f"  v2 on test : {v2['test']['trades']} trades, net {v2['test']['net_pnl']} ({v2['test']['return_pct']}%), profit factor {v2['test']['profit_factor']}; beats v1: {v2['test_beats_v1']}")
    summary = research.verdict(studies)
    print(f"Verdict: v2 beats v1 out of sample on {summary['v2_beats_v1_out_of_sample']}, net positive on {summary['v2_net_positive_out_of_sample']}. {summary['reading']}")
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(json.dumps({"label": research.LABEL, "verdict": summary, "studies": studies}, indent=1) + "\n", encoding="utf-8")
        print(f"Saved {opts.out}")
    return 0


def cmd_audit_journal(opts) -> int:
    rows = journal.read_journal(opts.journal)
    print(f"{journal.LABEL}\nJournal: {len(rows)} turns, sha256 of the file lines kept per turn")
    result = journal.audit(rows, pause=0.2)
    outcomes = journal.mark_outcomes(result)
    summary = journal.summarize(result, outcomes)
    c = summary["journal_check"]
    print(f"Check against public Binance spot data (the journal reads futures, so volume is not compared): {c['closed_within_basis']} of {c['candles_closed']} closed candles agree within 0.3% ({c['closed_within_basis_pct']}%), "
          f"{c['candles_recorded'] - c['candles_closed']} candles were still forming and are not counted; "
          f"price inside the candle range on {c['prices_plausible']} of {c['prices_checked']} turns")
    print(f"Shadow decisions: {summary['shadow_decisions']} (one per symbol, hour and decision type), desk actions {summary['desk_actions']}")
    print(f"Automaton / desk: {summary['automaton_vs_desk']}")
    print(f"Hypothetical positions closed: {summary['hypothetical_closed']}, net {summary['hypothetical_net']} (past data, not a trade result); skipped turns {summary['skipped']}")
    if opts.out:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(json.dumps({"summary": summary, "outcomes": outcomes, "skipped": result["skipped"]}, indent=1) + "\n", encoding="utf-8")
        print(f"Saved {opts.out}")
    if opts.shadow_ledger:
        opts.shadow_ledger.parent.mkdir(parents=True, exist_ok=True)
        opts.shadow_ledger.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in result["ledger"]), encoding="utf-8")
        print(f"Saved shadow ledger {opts.shadow_ledger} (chain intact: {not dossier.verify(result['ledger'])})")
    return 0


def cmd_learn(opts) -> int:
    """Analyze past decisions and outcomes to learn adaptive weights."""
    if opts.reset:
        learning.LEARNING_FILE = learning.LEARNING_FILE  # ensure Path
        if learning.LEARNING_FILE.exists():
            learning.LEARNING_FILE.unlink()
            print(f"Removed {learning.LEARNING_FILE}")
        return 0
    if opts.apply:
        result = learning.apply_learned_weights()
        if result:
            print(f"Learned {len(result.get('adaptive_weights', {}))} weight adjustments")
        return 0
    learning.print_learning_report(opts.ledger)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="verdict_desk")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decide", help="collect evidence, debate, record a simulated decision")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.set_defaults(func=cmd_decide)
    p = sub.add_parser("decide-v2", help="decide with agents_v2 + features_v2 (RSI, MACD, Bollinger)")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v2)
    p = sub.add_parser("decide-v3", help="decide with rules v3: same sizing as v2, but a declined entry says why (WATCH_REVERSAL / REDUCE_EXPOSURE)")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v3)
    p = sub.add_parser("decide-v4", help="decide with rules v4: v3 + a multi-KOL convergence becomes a deterministic signal")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v4)
    p = sub.add_parser("decide-v5", help="decide with rules v5: v4 + each convergence voice weighted by its measured skill above chance")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v5)
    p = sub.add_parser("decide-v6", help="decide with rules v6: v5 + an LLM judge that counts only on claims that passed the citation validator, capped below the entry band")
    p.add_argument("symbols", nargs="+", metavar="SYMBOL")
    p.add_argument("--interval", default="1h")
    p.add_argument("--no-news", action="store_true")
    p.add_argument("--feed", action="append", metavar="NAME=URL", help="public RSS or Atom voice for the narrative agent (repeatable, up to 20)")
    p.add_argument("--default-feeds", action="store_true", help="also read the built-in public crypto news voices")
    p.add_argument("--risk-pct", type=float, metavar="PCT", help="risk per practice position as %% of simulated capital (max 25, default 1)")
    p.add_argument("--config", metavar="PATH", help="voices and risk limit chosen by the user (JSON, see config/voices.json); its hash is recorded in every dossier")
    p.set_defaults(func=cmd_decide_v6)
    p = sub.add_parser("trust", help="per-voice track record: hit rate against chance, measured from the ledger alone")
    p.add_argument("--out", metavar="FILE", help="write the full table as JSON instead of printing it")
    p.set_defaults(func=cmd_trust)
    p = sub.add_parser("battery", help="attack a copy of the ledger (edit, delete, swap, duplicate, forge, truncate, rewrite) and count what the verifier misses")
    p.add_argument("--trials", type=int, default=2000, help="value edits to try; the other attacks scale from it")
    p.add_argument("--seed", help="seed for the attacks (default: the head hash, so anyone re-running gets the same ones)")
    p.add_argument("--stamps", metavar="DIR", help="directory with the timestamped heads (default: <ledger dir>/timestamps)")
    p.add_argument("--out", metavar="FILE", help="also write the full result as JSON")
    p.set_defaults(func=cmd_battery)
    p = sub.add_parser("models", help="the model bench and the routing policy the LLM gateway follows")
    p.add_argument("--out", metavar="FILE", help="write the bench, with the policy in force, as JSON (read by judge.html)")
    p.add_argument("--route", metavar="FILE", help="re-rank the routing policy by its fixed rule when it is due")
    p.add_argument("--every", type=int, default=24, help="re-rank at most once every N dossiers (default 24)")
    p.add_argument("--policy", default="config/model_routing.json", help="the policy file to publish with --out")
    p.set_defaults(func=cmd_models)
    p = sub.add_parser("market", help="every candle the ledger froze, per symbol, as JSON (read by ask.html)")
    p.add_argument("--out", metavar="FILE", required=True)
    p.set_defaults(func=cmd_market)
    p = sub.add_parser("series", help="the judged series: which model wrote each stretch of the ledger and why it changed")
    p.add_argument("--out", metavar="FILE", help="write the eras as JSON (the judge page reads series.json)")
    p.set_defaults(func=cmd_series)
    p = sub.add_parser("witnesses", help="write the index of timestamped heads and the anchor contract that the witnesses page reads")
    p.add_argument("--stamps", metavar="DIR", help="directory with the timestamped heads (default: <ledger dir>/timestamps)")
    p.add_argument("--deployment", default="cre/deployment.sepolia.json", metavar="FILE", help="the anchor deployment record")
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
    p = sub.add_parser("packs", help="write evidence packs for the newest dossiers (and the anchored one) into a directory the site serves")
    p.add_argument("--recent", type=int, default=24, metavar="N", help="how many of the newest dossiers (default 24)")
    p.add_argument("--outdir", required=True, metavar="DIR", help="where to write <seq>.json")
    p.add_argument("--anchored", action="store_true", help="also write the dossier anchored on Sepolia")
    p.add_argument("--also", type=int, action="append", metavar="SEQ", help="also write this dossier, whatever its age (repeatable; config/always_published.json is always included)")
    p.set_defaults(func=cmd_packs)
    p = sub.add_parser("scorecard", help="score the decisions against later prices, from the ledger alone")
    p.add_argument("--out", metavar="FILE", help="write the HTML scorecard to FILE")
    p.add_argument("--markdown", action="store_true", help="print a markdown table instead of the HTML page")
    p.set_defaults(func=cmd_scorecard)
    p = sub.add_parser("exhibit", help="render a dossier as a printable decision exhibit")
    p.add_argument("seq", nargs="?", type=int, help="dossier seq (default: the latest)")
    p.add_argument("--recent", type=int, metavar="N", help="render the last N dossiers (use with --outdir)")
    p.add_argument("--outdir", metavar="DIR", help="write one exhibit per dossier plus an index")
    p.add_argument("--out", metavar="FILE", help="write a single exhibit to FILE instead of stdout")
    p.add_argument("--anchor", action="store_true", help="also attach the on-chain anchor status (network read)")
    p.add_argument("--rpc", default=anchor.DEFAULT_RPC, help="RPC endpoint for --anchor")
    p.add_argument("--address", default=anchor.DEFAULT_ADDRESS, help="LedgerAnchor address")
    p.add_argument("--packs", metavar="DIR", help="directory of evidence packs written by `packs`; an exhibit links its pack when the file exists")
    p.add_argument("--also", type=int, action="append", metavar="SEQ", help="with --recent, also render this dossier, whatever its age (repeatable; config/always_published.json is always included)")
    p.set_defaults(func=cmd_exhibit)
    p = sub.add_parser("monitor", help="compare the ledger against the head anchored on Sepolia (read-only)")
    p.add_argument("--rpc", default="https://ethereum-sepolia-rpc.publicnode.com")
    p.add_argument("--address", default="0x50443D51Db71A61E131B147B1d36aF21d48CDdF7")
    p.add_argument("--seq", type=int, help="check a specific anchored sequence instead of the latest")
    p.set_defaults(func=cmd_monitor)
    p = sub.add_parser("verify", help="check the hash chain")
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("head", help="print the verified head of the chain as JSON, for the CRE anchor workflow")
    p.add_argument("--out", type=Path, help="also write the JSON to this file (published next to the ledger)")
    p.set_defaults(func=cmd_head)
    p = sub.add_parser("scoreboard", help="the exact-numbers block for the build page, counted from the raw ledger; --check recomputes a saved block and fails on any difference")
    p.add_argument("--seq", type=int, metavar="N", help="count as of dossier N (default: the head)")
    p.add_argument("--out", metavar="FILE", help="write the block to FILE")
    p.add_argument("--check", metavar="FILE", help="recompute the block FILE cites and fail if any figure differs")
    p.add_argument("--tests", type=int, metavar="N", help="the number of tests (default: counted by discovering the suite)")
    p.set_defaults(func=cmd_scoreboard)
    p = sub.add_parser("due", help="print run=true when the last decision is old enough for a new one (the workflow is triggered several times an hour)")
    p.add_argument("--min-minutes", type=float, default=50, metavar="N", help="minimum minutes since the newest dossier (default 50)")
    p.set_defaults(func=cmd_due)
    p = sub.add_parser("stats", help="live-operation numbers counted from the verified ledger: dossiers, window, cadence, citation validator results")
    p.add_argument("--first-llm-seq", type=int, default=livestats.FIRST_LLM_SEQ, help="first dossier of the LLM era (default %(default)s)")
    p.add_argument("--last-llm-seq", type=int, default=None, help="last dossier of the range to count (default: the head)")
    p.add_argument("--git", action="store_true", help="also count dossiers per git author of the line that recorded them")
    p.add_argument("--json", action="store_true", help="print the full summary as JSON")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("portfolio", help="mark simulated positions against later candles")
    p.set_defaults(func=cmd_portfolio)
    p = sub.add_parser("backtest", help="walk-forward test on past candles (separate from the live ledger)")
    p.add_argument("symbol")
    p.add_argument("--interval", default="1h")
    p.add_argument("--candles", type=int, default=4000)
    p.add_argument("--max-hold", type=int, default=backtest.MAX_HOLD)
    p.add_argument("--fee-pct", type=float, default=backtest.FEE_PCT_PER_SIDE, help="taker fee per side, in percent")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_backtest)
    p = sub.add_parser("research", help="v2 rule search with a time split: choose on train, judge on test (separate from the live ledger)")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--interval", default="1h")
    p.add_argument("--candles", type=int, default=4000)
    p.add_argument("--fee-pct", type=float, default=backtest.FEE_PCT_PER_SIDE)
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_research)
    p = sub.add_parser("audit-journal", help="check a third-party agent journal against public data and replay it in shadow")
    p.add_argument("journal", type=Path)
    p.add_argument("--out", type=Path, help="summary and per-turn outcomes (no free-text reasoning)")
    p.add_argument("--shadow-ledger", type=Path, help="write the shadow dossiers as a separate hash chain")
    p.set_defaults(func=cmd_audit_journal)
    p = sub.add_parser("learn", help="analyze past outcomes and show adaptive weight adjustments")
    p.add_argument("--apply", action="store_true", help="save learned weights for v2 agent")
    p.add_argument("--reset", action="store_true", help="reset learned weights")
    p.set_defaults(func=cmd_learn)
    opts = parser.parse_args()
    return opts.func(opts)


if __name__ == "__main__":
    sys.exit(main())
