"""Decision exhibit: one dossier rendered as a document a reviewer can file.

A legal hackathon is judged by people whose instinct is "can I rely on this in a
dispute?". A JSON line is auditable but not presentable; this module turns one dossier
into a self-contained, printable exhibit with four things a filing needs:

  1. what the agent saw, with the source and time of every item (custody);
  2. who argued what, and which claims were refused for lack of a citation;
  3. which single argument decided the outcome (attribution, not narration);
  4. what the record does **not** prove (limits, stated before anyone asks).

The integrity block is recomputed at render time from the dossier body, so an exhibit
generated from a tampered record says so on its face.
"""

import hashlib
import html
import json
from pathlib import Path

from . import canonical, citations

GENESIS = "0" * 64


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _digest(body: dict) -> str:
    """The same digest the ledger uses: RFC 8785 canonical JSON, then SHA-256."""
    return hashlib.sha256(canonical.canonical(body)).hexdigest()


def _pct(value) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return ""


def _plain_summary(dossier: dict, integrity_ok: bool) -> list[str]:
    """The decision in words a non-specialist can follow, from the record's own fields and nothing else.

    No model writes this: every sentence is a template filled from the dossier, so it can be checked
    against the sections below it and it says the same thing every time it is regenerated.
    """
    verdict = dossier.get("verdict") or {}
    ev = dossier.get("evidence") or {}
    llm = dossier.get("llm") or {}
    sens = dossier.get("sensitivity") or {}
    symbol, seq = ev.get("symbol", "the market"), dossier.get("seq")
    when = str(ev.get("collected_at", ""))
    when = f"{when[:10]} at {when[11:16]} UTC" if len(when) >= 16 else when
    action = verdict.get("action")
    out = [f"On {when}, an automated desk looked at the market for {symbol} and recorded a decision. This is record #{seq}."]

    if action in ("LONG", "SHORT") and verdict.get("entry") is not None:
        way = "buy (bet on a rise)" if action == "LONG" else "sell short (bet on a fall)"
        out.append(f"It decided to {way} {symbol} on paper at {verdict['entry']}, with a stop at {verdict.get('stop')} and a target at {verdict.get('take_profit')}. "
                   f"The size is set so that if the stop is hit the loss is {verdict.get('risk_pct')}% of the simulated capital. No real order was placed.")
    elif action == "BLOCKED_STALE":
        out.append("It refused to decide, because the newest price it had was too old to trust. That is a safety rule, and the refusal itself is recorded. No real order was placed.")
    elif action in ("WATCH_REVERSAL", "REDUCE_EXPOSURE", "FLAT"):
        reason = {"WATCH_REVERSAL": "The two sides were close, and a single argument decides which way it would go.",
                  "REDUCE_EXPOSURE": "Both sides were strong and the market was volatile, so it would rather shrink risk than add any.",
                  "FLAT": "Neither side was strong enough."}[action]
        out.append(f"It did not open a position. {reason} No real order was placed.")
    else:
        out.append(f"The recorded action is {action}. No real order was placed.")

    if verdict.get("bull_score") is not None and verdict.get("bear_score") is not None:
        flips = [d for d in (sens.get("drop_one") or []) if d.get("flips")]
        line = (f"The arguments for a rise weighed {verdict['bull_score']} and the arguments for a fall weighed {verdict['bear_score']}; "
                f"a position needs a gap of at least {verdict.get('threshold')}.")
        if flips:
            line += f" If this one argument were removed the verdict would change: \"{flips[0].get('claim')}\"."
        out.append(line)

    sides = [llm.get(side) or {} for side in ("bull", "bear")] if llm else []
    if sides and all(b.get("error") for b in sides):
        out.append("The AI analysts could not run for this record, so the decision rests on the fixed rules alone.")
    elif sides:
        kept = sum(len(b.get("accepted") or []) for b in sides)
        refused = sum(len(b.get("rejected") or []) for b in sides)
        out.append(f"AI analysts wrote {kept + refused} claims; {kept} passed the check that every number must appear in the evidence it cites and {refused} {'was' if refused == 1 else 'were'} refused.")
    judge = llm.get("judge") if llm else None
    if isinstance(judge, dict):
        out.append(_judge_sentence(judge))

    out.append("Checked just now: this record matches its saved fingerprint, so nobody has changed it since it was written."
               if integrity_ok else "Checked just now: this record does NOT match its saved fingerprint, so it was changed after it was written. Do not rely on it.")
    return out


_PRACTICE = Path(__file__).resolve().parents[1] / "cre" / "practice.sepolia.json"


def _tokens() -> dict:
    """The test tokens of the Sepolia practice desk: address -> (symbol, decimals), from cre/practice.sepolia.json."""
    try:
        data = json.loads(_PRACTICE.read_text(encoding="utf-8"))
        return {data["weth"].lower(): ("WETH", 18), data["usdc"].lower(): ("USDC", 6)}
    except (OSError, ValueError, KeyError):
        return {}


def _units(raw, token) -> str:
    """1895000000000000 of WETH -> '0.001895 WETH': an on-chain amount is an integer count of the smallest unit."""
    known = _tokens().get(str(token).lower())
    if not known or not str(raw).isdigit():
        return f"{raw} (raw units of {token})"
    symbol, decimals = known
    digits = str(raw).rjust(decimals + 1, "0")
    value = f"{digits[:-decimals]}.{digits[-decimals:]}".rstrip("0").rstrip(".")
    return f"{value} {symbol}"


def _practice_rows(dossier: dict) -> list[str]:
    """The Sepolia practice trade this record sent (and any earlier position it closed on-chain)."""
    rows = []
    ex = dossier.get("execution")
    if isinstance(ex, dict):
        tx = str(ex.get("tx") or "")
        link = f'<a href="https://sepolia.etherscan.io/tx/{_e(tx)}"><code>{_e(tx)}</code></a>' if tx.startswith("0x") else "none"
        rows.append(f"<p><b>Practice trade on Sepolia testnet:</b> {_e(ex.get('label'))}. Transaction {link}"
                    + (f", block {_e(ex.get('block'))}" if ex.get("block") else "") + ".</p>")
        if ex.get("status") == "executed" and ex.get("amount_in") is not None:
            rows.append(f"<p class=\"small\">The swap sent {_e(_units(ex.get('amount_in'), ex.get('token_in')))} and received "
                        f"{_e(_units(ex.get('amount_out'), ex.get('token_out')))} on the Uniswap v3 test pool, whose price is not the market price.</p>")
        if ex.get("decision_digest"):
            rows.append(f"<p class=\"small\">The transaction carries the digest of this decision, <code>{_e(ex['decision_digest'])}</code>, "
                        "and this record holds the transaction: it was written in before the record was sealed. Test tokens only.</p>")
    for c in dossier.get("executions_closed") or []:
        tx = str(c.get("tx") or "")
        rows.append(f"<p class=\"small\">This record also closed the on-chain position of record #{_e(c.get('opened_seq'))}: "
                    f"{_e(c.get('label') or c.get('status'))}"
                    + (f', transaction <a href="https://sepolia.etherscan.io/tx/{_e(tx)}"><code>{_e(tx[:18])}…</code></a>' if tx.startswith("0x") else "") + ".</p>")
    return rows


def _who(block: dict) -> str:
    """ · answered by X, and, when the gateway logged its route, which models it tried first and why it moved on."""
    if not block.get("served_by"):
        return ""
    route = block.get("route") if isinstance(block.get("route"), list) else []
    before = [a for a in route[:-1] if isinstance(a, dict)]
    if before:
        tried = ", ".join(f"{a.get('model')} returned {a.get('status')}" for a in before)
        return f" · answered by {block['served_by']} after {tried}"
    if route:
        return f" · answered by {block['served_by']}, first in the routing policy"
    return f" · answered by {block['served_by']}"


def _judge_sentence(judge: dict) -> str:
    """One sentence on the rules-v6 AI judge, filled from the frozen ruling and its check."""
    ruling = judge.get("ruling") if isinstance(judge.get("ruling"), dict) else {}
    if judge.get("accepted") and judge.get("weight"):
        return (f"An AI judge then read only the claims that passed and leaned {ruling.get('lean')} with {ruling.get('conviction')} conviction, "
                f"relying on {', '.join(ruling.get('relied_on') or [])}. That adds {judge['weight']} to that side, "
                "never enough to open a position on its own.")
    if judge.get("accepted"):
        return "An AI judge read the claims that passed, found the two cases balanced, and added nothing."
    why = judge.get("rejected_because") or judge.get("error") or "it did not hold"
    return f"An AI judge was asked to rule, but its ruling was voided ({why}), so it added nothing."


def render(dossier: dict, chain: dict | None = None, anchor: dict | None = None, pack_href: str | None = None) -> str:
    """Render one dossier as a printable exhibit. Pure function of its inputs."""
    body = {k: v for k, v in dossier.items() if k != "hash"}
    recomputed = _digest(body)
    recorded = dossier.get("hash", "")
    integrity_ok = recomputed == recorded

    verdict = dossier.get("verdict") or {}
    ev = dossier.get("evidence") or {}
    feats = dossier.get("features") or {}
    sens = dossier.get("sensitivity") or {}
    llm = dossier.get("llm") or {}
    fresh = ev.get("freshness") or {}
    narrative = ev.get("narrative") or {}
    symbol = ev.get("symbol", "")
    seq = dossier.get("seq")
    seen_at = ev.get("collected_at", "")

    chain = chain or {}
    anchor = anchor or {}
    prev = dossier.get("prev_hash", "")

    rows = []

    # --- 0. in plain English -----------------------------------------------------
    rows.append("<h2 id=\"plain\">In plain English</h2>")
    rows.extend(f"<p class=\"lead\">{_e(line)}</p>" for line in _plain_summary(dossier, integrity_ok))

    # --- 1. custody --------------------------------------------------------------
    market = ev.get("market") or {}
    candles = market.get("candles") or []
    funding = ev.get("funding") or {}
    news = ev.get("news") or {}
    sources = [
        ("Price candles", "Binance public REST", f"{len(candles)} candles, interval {market.get('interval', 'n/a')}",
         f"newest candle age {fresh.get('newest_candle_age_minutes', 'n/a')} min"),
        ("Funding rate", funding.get("source", "none"), funding.get("instrument", ""),
         f"rate {funding.get('rate', 'n/a')}"),
        ("News", news.get("source", "none"), f"{len(news.get('articles') or [])} articles", news.get("query", "")),
        ("Voices", f"{len({v.get('name') for v in (narrative.get('voices') or [])})} outlets",
         f"{len(narrative.get('voices') or [])} headlines",
         f"{len(narrative.get('convergence') or [])} convergences, {len(narrative.get('rejected_candidates') or [])} tokens not converged"),
    ]
    rows.append("<h2 id=\"custody\">1. What the agent saw, and when</h2>")
    rows.append(f"<p class=\"lead\">Evidence packet collected at <b>{_e(seen_at)}</b> for <b>{_e(symbol)}</b>. "
                "Every item below is frozen inside the record; the decision cannot be re-argued against different data later.</p>")
    rows.append("<table><thead><tr><th>Class</th><th>Source</th><th>Items</th><th>Detail</th></tr></thead><tbody>")
    for name, src, items, detail in sources:
        rows.append(f"<tr><td>{_e(name)}</td><td class=\"mono\">{_e(src)}</td><td>{_e(items)}</td><td>{_e(detail)}</td></tr>")
    rows.append("</tbody></table>")

    classes = fresh.get("classes") or {}
    if classes:
        rows.append("<p class=\"small\"><b>Freshness, judged per source class:</b> "
                    + " · ".join(
                        f"{_e(k)}: {_e(v.get('age_minutes'))} min against a {_e(v.get('window_minutes'))}-minute window "
                        f"({'blocks' if v.get('blocking') else 'warns'})"
                        for k, v in classes.items())
                    + "</p>")
    if fresh.get("warnings"):
        rows.append("<p class=\"small warn\">Warnings recorded: " + "; ".join(_e(w) for w in fresh["warnings"]) + "</p>")

    # --- 2. the debate -----------------------------------------------------------
    rows.append("<h2 id=\"debate\">2. Who argued what</h2>")
    if not llm:
        rows.append("<p class=\"small\">This record carries no analyst layer; the decision rests on the deterministic engine alone.</p>")
    else:
        rows.append("<p class=\"lead\">Two analysts read the same frozen packet. A claim survives only if its citation "
                    "matches the cited evidence item; claims that fail are kept, with the reason, rather than hidden.</p>")
        # Resolve cada citacao ao texto do item que ela aponta: e isso que torna a
        # atribuicao verificavel em vez de retorica.
        try:
            items = citations.index(ev, feats)
        except (KeyError, TypeError, ValueError):
            items = {}
        for side in ("bull", "bear"):
            block = llm.get(side) or {}
            accepted = block.get("accepted") or []
            rejected = block.get("rejected") or []
            who = _who(block)
            rows.append(f"<h3>{_e(side.capitalize())} analyst · {len(accepted)} kept, {len(rejected)} refused{_e(who)}</h3>")
            if accepted:
                rows.append("<table><thead><tr><th>Claim</th><th>Evidence it cites</th></tr></thead><tbody>")
                for c in accepted:
                    ids = c.get("cites") or []
                    cited = "".join(
                        f"<div class=\"cite\"><span class=\"id\">{_e(i)}</span> {_e(items.get(i, 'unresolved'))}</div>"
                        for i in ids) or "<span class=\"small\">no citation</span>"
                    rows.append(f"<tr><td>{_e(c.get('claim'))}</td><td>{cited}</td></tr>")
                rows.append("</tbody></table>")
            elif block.get("error"):
                # A provider failure is a fact about this record, not an empty debate: say what failed and what decided.
                rows.append(f"<p class=\"small\">The {_e(side)} analyst layer returned an error and produced no claims for this record "
                            f"({_e(str(block['error'])[:160])}). The decision below rests on the deterministic engine alone.</p>")
            else:
                rows.append("<p class=\"small\">Nothing kept.</p>")
            if rejected:
                rows.append("<table class=\"refused\"><thead><tr><th>Claim refused</th><th>Why</th></tr></thead><tbody>")
                for r in rejected:
                    reasons = [r.get("rejected_because") or r.get("reason") or "no valid citation"]
                    rows.append(f"<tr><td>{_e(r.get('claim'))}</td><td class=\"small\">{_e('; '.join(reasons))}</td></tr>")
                rows.append("</tbody></table>")
        judge = llm.get("judge")
        if isinstance(judge, dict):
            ruling = judge.get("ruling") if isinstance(judge.get("ruling"), dict) else {}
            status = (f"counts {judge.get('weight')}" if judge.get("accepted") and judge.get("weight")
                      else "balanced, adds nothing" if judge.get("accepted") else "voided, adds nothing")
            who = _who(judge)
            rows.append(f"<h3>AI judge (rules v6) · {_e(status)}{_e(who)}</h3>")
            rows.append("<p class=\"small\">The judge reads only the claims kept above. Its lean counts only if it rests on at least "
                        "one kept claim of that side and every number in its reason is in that evidence; the weight comes from a fixed "
                        "table capped below the entry band, so it can tip a close call but never open a position alone.</p>")
            if ruling:
                rows.append("<table><tbody>"
                            f"<tr><th>Lean</th><td>{_e(ruling.get('lean'))} ({_e(ruling.get('conviction'))} conviction)</td></tr>"
                            f"<tr><th>Relies on</th><td>{_e(', '.join(map(str, ruling.get('relied_on') or [])) or 'nothing')}</td></tr>"
                            f"<tr><th>Reason</th><td>{_e(ruling.get('reason'))}</td></tr></tbody></table>")
            if not judge.get("accepted"):
                rows.append(f"<p class=\"small\">Voided: {_e(judge.get('rejected_because') or judge.get('error') or 'no ruling')}</p>")

    # --- 3. attribution ----------------------------------------------------------
    rows.append("<h2 id=\"attribution\">3. Which argument decided it</h2>")
    drops = sens.get("drop_one") or []
    flips = [d for d in drops if d.get("flips")]
    if not drops:
        rows.append("<p class=\"small\">This record predates the sensitivity analysis.</p>")
    else:
        if flips:
            rows.append(f"<p class=\"lead\">{len(flips)} argument{'s' if len(flips) > 1 else ''} would change the verdict "
                        "if removed. Those rows are the attribution: the outcome does not rest on the conclusion, it rests on them.</p>")
        else:
            rows.append("<p class=\"lead\">No single argument changes the outcome. The verdict rests on the whole set, "
                        "which is itself a finding worth recording.</p>")
        rows.append("<table><thead><tr><th>Side</th><th>Argument</th><th>Weight</th><th>Edge without it</th>"
                    "<th>Action without it</th><th>Decides</th></tr></thead><tbody>")
        for d in drops:
            cls = " class=\"flip\"" if d.get("flips") else ""
            rows.append(f"<tr{cls}><td>{_e(d.get('side'))}</td><td>{_e(d.get('claim'))}</td><td>{_e(d.get('weight'))}</td>"
                        f"<td>{_e(d.get('edge_without'))}</td><td>{_e(d.get('action_without'))}</td>"
                        f"<td>{'yes' if d.get('flips') else 'no'}</td></tr>")
        rows.append("</tbody></table>")

    # --- 4. the decision ---------------------------------------------------------
    rows.append("<h2 id=\"decision\">4. The decision</h2>")
    action = verdict.get("action", "")
    rows.append(f"<p class=\"action {_e(action)}\">{_e(action)}</p>")
    if verdict.get("rationale"):
        rows.append(f"<p class=\"lead\">{_e(verdict['rationale'])}</p>")
    facts = []
    if verdict.get("edge") is not None:
        facts.append(("Edge (bull minus bear)", verdict.get("edge")))
    if verdict.get("entry") is not None:
        facts.append(("Entry", verdict.get("entry")))
        facts.append(("Stop-loss", verdict.get("stop")))
        facts.append(("Take-profit", verdict.get("take_profit")))
        facts.append(("Size", verdict.get("size")))
    if verdict.get("risk_pct") is not None:
        facts.append(("Risk declared", _pct(verdict.get("risk_pct"))))
    if verdict.get("system_max_risk_pct") is not None:
        facts.append(("System cap", _pct(verdict.get("system_max_risk_pct"))))
    if feats.get("atr_14") is not None:
        facts.append(("ATR(14)", feats.get("atr_14")))
    if verdict.get("rules_version"):
        facts.append(("Rules version", verdict.get("rules_version")))
    if facts:
        rows.append("<table class=\"kv\"><tbody>"
                    + "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in facts)
                    + "</tbody></table>")
    policy = verdict.get("policy")
    if policy:
        labels = {
            "entry_threshold": "Entry threshold (edge)",
            "stop_atr": "Stop distance (ATR x)",
            "take_atr": "Target distance (ATR x)",
            "max_risk_pct": "Risk per position",
            "system_max_risk_pct": "System risk cap",
            "rich_states": "Named declined states",
            "volatility_pct": "High-volatility threshold (ATR %)",
            "market_window_minutes": "Price freshness window",
            "slow_window_minutes": "News/voice freshness window",
        }
        rows.append("<p class=\"small\"><b>Rules frozen in this record</b>: the exact numbers that produced this verdict, "
                    "inside the hashed body, so changing a rule later would change the hash and the anchor would no longer match.</p>")
        rows.append("<table class=\"kv\"><tbody>"
                    + "".join(f"<tr><th>{_e(labels.get(k, k))}</th><td>{_e(v)}{' min' if 'window' in k else ''}</td></tr>"
                              for k, v in policy.items())
                    + "</tbody></table>")

    rows += _practice_rows(dossier)

    # --- 5. narrative, if any ----------------------------------------------------
    if narrative.get("convergence") or narrative.get("rejected_candidates"):
        rows.append("<h2 id=\"narrative\">5. Market voices</h2>")
        conv = narrative.get("convergence") or []
        if conv:
            rows.append("<table><thead><tr><th>Token</th><th>Bias</th><th>Voices agreeing</th><th>Conviction</th><th>Urgency</th></tr></thead><tbody>")
            for c in conv:
                rows.append(f"<tr><td><b>{_e(c.get('token'))}</b></td><td>{_e(c.get('bias'))}</td>"
                            f"<td>{_e(', '.join(c.get('voices') or []))}</td><td>{_e(c.get('max_conviction'))}</td>"
                            f"<td>{_e(c.get('max_urgency'))}</td></tr>")
            rows.append("</tbody></table>")
        if narrative.get("rejected_candidates"):
            rows.append("<p class=\"small\"><b>Tokens that never became a narrative:</b> "
                        + " · ".join(f"{_e(r.get('token'))} ({_e(r.get('reason'))})" for r in narrative["rejected_candidates"])
                        + "</p>")

    # --- 6. integrity ------------------------------------------------------------
    rows.append("<h2 id=\"integrity\">6. Integrity</h2>")
    rows.append("<table class=\"kv\"><tbody>")
    rows.append(f"<tr><th>Recorded SHA-256</th><td class=\"mono\">{_e(recorded)}</td></tr>")
    rows.append(f"<tr><th>Recomputed when this exhibit was made</th><td class=\"mono\">{_e(recomputed)}</td></tr>")
    rows.append(f"<tr><th>Verdict</th><td><b class=\"{'ok' if integrity_ok else 'bad'}\">"
                f"{'MATCH: the record is unchanged' if integrity_ok else 'MISMATCH: this record was edited'}</b></td></tr>")
    rows.append(f"<tr><th>Previous record</th><td class=\"mono\">{_e(prev)}"
                f"{' (genesis)' if prev == GENESIS else ''}</td></tr>")
    if chain:
        rows.append(f"<tr><th>Position in the published chain</th><td>{_e(chain.get('position', seq))} of {_e(chain.get('length', 'n/a'))}"
                    f" {'· every link verified' if chain.get('ok') else '· chain problem: ' + _e(chain.get('problem'))}</td></tr>")
    if anchor:
        state = "matches the hash written on Ethereum Sepolia" if anchor.get("matches") else "does NOT match the on-chain anchor"
        rows.append(f"<tr><th>On-chain anchor</th><td>{_e(state)}"
                    + (f" · anchor #{_e(anchor.get('anchored_seq'))}, {_e(anchor.get('anchored_at'))}" if anchor.get("anchored_seq") else "")
                    + "</td></tr>")
    rows.append("</tbody></table>")
    if not anchor:
        rows.append("<p class=\"small\">No anchor lookup was attached to this exhibit. The chain link above is still "
                    "verifiable independently from the published ledger.</p>")

    # --- 7. limits ---------------------------------------------------------------
    rows.append("<h2 id=\"limits\">7. What this exhibit does not prove</h2>")
    rows.append("""<ul class="limits">
      <li>It does not prove the decision was wise, only that it was <b>made on this evidence, by this rule, at this time</b>, and that nobody edited it afterwards.</li>
      <li>Positions are <b>simulated</b>: no real-money order is ever placed and the system holds no exchange credentials. From record 375 each new position is also sent to Sepolia testnet with test tokens (ETHUSDT as a swap, BTC and SOL as a receipt), shown in section 4.</li>
      <li>Results exclude fees, funding costs and slippage. Realised figures over a short window are not evidence of edge.</li>
      <li>The analyst layer is an LLM. Its claims are constrained to cited evidence and validated automatically, but the validator checks the citation, not the financial merit of the opinion.</li>
      <li>News and voices are secondary sources read through public feeds; they inherit those feeds' errors.</li>
    </ul>""")

    if pack_href:
        pack_block = (f'<div class="pack"><b>Evidence pack for this record.</b> '
                      f'<a href="{_e(pack_href)}" download>Download the pack (JSON, about 90 KB)</a>. '
                      "It holds the full record, the recomputed hash, the replayed verdict and the links to the records either side. "
                      'To check it without trusting us, paste it into <a href="../verify.html">the verifier</a> (the hash is recomputed in your browser), '
                      "or follow the numbered steps inside the file.</div>")
    else:
        pack_block = (f'<div class="pack"><b>Evidence pack.</b> No downloadable pack is published for this older record; produce one with '
                      f"<code>python3 -m verdict_desk export --seq {_e(seq)} --out pack.json</code> on a copy of the public ledger.</div>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decision exhibit #{_e(seq)} · {_e(symbol)}</title>
<style>
:root{{--fg:#111;--mut:#555;--line:#ccc;--ok:#0a7a3d;--bad:#b3261e;--warn:#8a5300;--accent:#1f5fbf}}
*{{box-sizing:border-box}}
body{{font:14px/1.55 Georgia,'Times New Roman',serif;color:var(--fg);background:#fff;max-width:820px;margin:0 auto;padding:28px 24px 60px}}
h1{{font-size:22px;margin:0 0 2px;letter-spacing:.01em}}
h2{{font-size:15px;margin:26px 0 6px;padding-bottom:3px;border-bottom:1.5px solid var(--line);font-family:system-ui,sans-serif}}
h3{{font-size:13px;margin:14px 0 4px;font-family:system-ui,sans-serif}}
.sub{{font-family:system-ui,sans-serif;color:var(--mut);font-size:12px;margin-bottom:14px}}
.lead{{margin:6px 0 10px}}
.small{{font-size:12px;color:var(--mut);font-family:system-ui,sans-serif}}
.mono{{font-family:ui-monospace,monospace;font-size:11px;word-break:break-all}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-family:system-ui,sans-serif;font-size:12px}}
th,td{{border:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}}
thead th{{background:#f3f3f3;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
tr.flip td{{background:#fdecea;font-weight:600}}
.kv th{{width:210px;background:#fafafa;font-weight:600}}
.quote{{font-style:italic;color:#333}}
.action{{font-family:system-ui,sans-serif;font-size:20px;font-weight:700;margin:6px 0}}
.LONG,.ok{{color:var(--ok)}}.SHORT,.bad{{color:var(--bad)}}
.FLAT{{color:var(--mut)}}.WATCH_REVERSAL,.REDUCE_EXPOSURE{{color:var(--warn)}}
.BLOCKED_STALE,.BLOCKED_STALE_OR_INVALID{{color:var(--bad)}}
.warn{{color:var(--warn)}}
.cert-header{{border:2px solid var(--accent);border-radius:8px;padding:10px 16px;margin:6px 0 2px;text-align:center}}
.cert-header .t{{font-family:system-ui,sans-serif;font-size:17px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}}
.cert-header .s{{font-family:system-ui,sans-serif;font-size:11px;color:var(--mut);margin-top:2px}}
.seal{{display:inline-block;border:5px double var(--ok);color:var(--ok);border-radius:12px;padding:8px 24px;font-family:system-ui,sans-serif;font-weight:800;font-size:22px;letter-spacing:.16em;text-transform:uppercase;transform:rotate(-8deg);margin:12px 0 4px;background:transparent}}
.seal.bad{{border-color:var(--bad);color:var(--bad)}}
.sealwrap{{text-align:center}}
.cite{{margin:1px 0}}.cite .id{{font-family:ui-monospace,monospace;font-size:10px;background:#eef2f9;border:1px solid #d8e0ee;border-radius:4px;padding:0 4px;margin-right:4px}}
table.refused th{{background:#fdf6f5}}table.refused td{{color:#555}}
.limits li{{margin:5px 0}}
.pack{{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:12px 0;font-family:system-ui,sans-serif;font-size:13px;background:#f6f8fc}}@media print{{.pack{{display:none}}}}
footer{{margin-top:26px;padding-top:10px;border-top:1px solid var(--line);font-family:system-ui,sans-serif;font-size:11px;color:var(--mut)}}
@media print{{body{{padding:0;max-width:none}}h2{{page-break-after:avoid}}table{{page-break-inside:avoid}}}}
</style></head><body>
<h1>Decision exhibit #{_e(seq)} · {_e(symbol)}</h1>
<div class="cert-header"><div class="t">Certificate of Filing</div>
<div class="s">record #{_e(seq)} · {_e(symbol)} · evidence collected {_e(seen_at)} · rules {_e(verdict.get('rules_version', 'v1'))}</div></div>
<div class="sealwrap"><span class="seal{' bad' if not integrity_ok else ''}">{'Verified' if integrity_ok else 'Tampered'}</span></div>
<p class="sub">Verdict Desk · simulated decision record · evidence collected {_e(seen_at)} · rules {_e(verdict.get('rules_version', 'v1'))}<br>
This document is generated from the public ledger line for record #{_e(seq)}. It can be regenerated byte for byte from that line alone.</p>
{pack_block}
<div class="pack"><b>Ask this record.</b> <a href="../ask.html?seq={_e(seq)}">Ask it why it acted, what would have changed its mind and what happened next</a>, on the chart of the candles it saw. Every number in an answer is cited and checked in your browser.</div>
{''.join(rows)}
<footer>Verdict Desk publishes one dossier per hour and hash-chains each to the previous one. The chain head is anchored on Ethereum Sepolia.
No real-money order is placed by this system; practice trades use test tokens on Sepolia testnet. This exhibit is evidence of a decision process, not investment advice.</footer>
</body></html>
"""


def chain_context(ledger: list[dict], seq: int) -> dict:
    """Verify the whole chain and report where this dossier sits in it."""
    prev = GENESIS
    for row in ledger:
        body = {k: v for k, v in row.items() if k != "hash"}
        if _digest(body) != row.get("hash"):
            return {"ok": False, "problem": f"record #{row.get('seq')} does not hash to its recorded value",
                    "position": seq, "length": len(ledger)}
        if row.get("prev_hash") != prev:
            return {"ok": False, "problem": f"record #{row.get('seq')} does not link to its predecessor",
                    "position": seq, "length": len(ledger)}
        prev = row["hash"]
    return {"ok": True, "position": seq, "length": len(ledger)}


def render_index(entries: list[tuple[dict, str]]) -> str:
    """An index page for a directory of exhibits: newest first, one line per decision."""
    rows = []
    for row, href in sorted(entries, key=lambda e: e[0].get("seq", 0), reverse=True):
        v = row.get("verdict") or {}
        ev = row.get("evidence") or {}
        rows.append(
            f"<tr><td class=\"mono\"><a href=\"{_e(href)}\">#{_e(row.get('seq'))}</a></td>"
            f"<td>{_e(ev.get('symbol'))}</td>"
            f"<td class=\"{_e(v.get('action'))}\"><b>{_e(v.get('action'))}</b></td>"
            f"<td>{_e(v.get('edge'))}</td>"
            f"<td class=\"small\">{_e(ev.get('collected_at'))}</td>"
            f"<td class=\"mono small\">{_e((row.get('hash') or '')[:16])}…</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verdict Desk · decision exhibits</title>
<style>
body{{font:14px/1.55 Georgia,serif;max-width:820px;margin:0 auto;padding:26px 22px 60px;color:#111;background:#fff}}
h1{{font-size:21px;margin:0 0 4px}}p.sub{{font-family:system-ui,sans-serif;color:#555;font-size:12px;margin:0 0 16px}}
table{{border-collapse:collapse;width:100%;font-family:system-ui,sans-serif;font-size:12px}}
th,td{{border:1px solid #ccc;padding:6px 8px;text-align:left}}thead th{{background:#f3f3f3;font-size:11px;text-transform:uppercase}}
.mono{{font-family:ui-monospace,monospace}}.small{{color:#555;font-size:11px}}
.LONG{{color:#0a7a3d}}.SHORT{{color:#b3261e}}.WATCH_REVERSAL,.REDUCE_EXPOSURE{{color:#8a5300}}.FLAT{{color:#555}}
a{{color:#1f5fbf}}
</style></head><body>
<h1>Decision exhibits</h1>
<p class="sub">One document per recorded decision, generated from the public ledger line for that record. Each exhibit states what the agent saw, who argued what, which argument decided the outcome, and what the record does not prove.</p>
<table><thead><tr><th>Record</th><th>Symbol</th><th>Decision</th><th>Edge</th><th>Evidence collected</th><th>Hash</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table>
<p class="sub">Simulated decisions only: no real-money order is ever placed. From record 375, practice trades also go to Sepolia testnet with test tokens.</p>
</body></html>
"""
