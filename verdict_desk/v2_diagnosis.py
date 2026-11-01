"""Direction mix of the v2 rules: market regime or built-in bias? It measures; it changes no rule and no ledger line.

  python3 -m verdict_desk.v2_diagnosis                       # reads ledger/ledger.jsonl, fetches 4,000 hourly candles per symbol
  python3 -m verdict_desk.v2_diagnosis --out research/V2_DIAGNOSIS.md
  python3 -m verdict_desk.v2_diagnosis --candles-dir DIR     # offline: DIR/BTCUSDT.json etc. (lists of candles)
  python3 -m verdict_desk.v2_diagnosis --until 2026-10-21T07:00:00Z --candles 3999   # the same window again (closed candles before that instant)

Three measurements, each with its own table:

1. What the v2 dossiers in the ledger decided, per symbol, which arguments carried them, and what the market looked like
   at those moments (EMA trend, MACD sign, RSI), and how often funding and news (which history does not have) changed the verdict.
2. What the same rules decide, hour by hour, over a long public history (the walk-forward windows of `backtest`), split by
   a market regime that is not one of the rule inputs: the 72-candle return.
3. A mirror test. The same history is mirrored (every move negated, so rises become equal falls). A rule set with no direction
   bias decides LONG on the real series as often as it decides SHORT on the mirror. The difference of LONG minus SHORT shares
   splits into a part that flips sign under the mirror (the market regime) and a part that does not (bias built into the rules).

The v1 rules run through the same tables as a baseline. Funding and news do not exist in the history, so the replay uses
price and volume only, like the backtest.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import agents, agents_v2, backtest, dossier, features, features_v2, portfolio

WINDOW = backtest.WINDOW
REGIME_BAND_PCT = 3.0  # 72-candle return beyond which a window counts as an up or a down regime
HOUR_MS = 3_600_000
ACTIONS = ("LONG", "SHORT", "FLAT")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# The two core signals each side weighs, by the claim text the v2 rules attach to them (a test pins these strings to agents_v2).
TREND_UP, TREND_DOWN = "Short-term trend above long-term trend", "Short-term trend below long-term trend"
MACD_UP, MACD_DOWN = "MACD histogram positive: bullish momentum", "MACD histogram negative: bearish momentum"
STATES = ("both bullish", "both bearish", "mixed")


def _pct(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0


def _packet(symbol: str, window: list[dict], funding=None, articles=()) -> dict:
    return {"symbol": symbol, "market": {"candles": window}, "funding": {"rate": funding}, "news": {"articles": list(articles)}}


# ---- mirrors ------------------------------------------------------------------------------------------------------


def mirror(candles: list[dict]) -> list[dict]:
    """Every move negated: each open, high, low and close is mirrored around the previous close, so a rise of x% becomes a fall of x%.

    The high of the mirror is the mirrored low and the other way round. Volume is untouched.
    """
    out, prev_real, prev_mirror = [], candles[0]["o"], candles[0]["o"]
    for c in candles:
        def flip(x, base=prev_real, anchor=prev_mirror):
            return anchor * (1 - (x / base - 1))

        mirrored = {"t": c["t"], "o": flip(c["o"]), "h": flip(c["l"]), "l": flip(c["h"]), "c": flip(c["c"]), "v": c["v"]}
        out.append(mirrored)
        prev_real, prev_mirror = c["c"], mirrored["c"]
    return out


def mirror_inverse(candles: list[dict]) -> list[dict]:
    """Second mirror, exact in log terms: every price becomes k / price, so each log return is negated."""
    k = candles[0]["o"] ** 2
    return [{"t": c["t"], "o": k / c["o"], "h": k / c["l"], "l": k / c["h"], "c": k / c["c"], "v": c["v"]} for c in candles]


# ---- replay -------------------------------------------------------------------------------------------------------


def decide_v1(window: list[dict], symbol: str) -> dict:
    d = agents.decide(features.compute(_packet(symbol, window)))
    return {"action": d["verdict"]["action"], "edge": d["verdict"]["edge"], "bull": [a["claim"] for a in d["bull"]], "bear": [a["claim"] for a in d["bear"]], "flips": [x["claim"] for x in d["sensitivity"]["drop_one"] if x["flips"]]}


def decide_v2(window: list[dict], symbol: str) -> dict:
    d = agents_v2.decide(features_v2.compute(_packet(symbol, window)))
    return {"action": d["verdict"]["action"], "edge": d["verdict"]["edge"], "bull": [a["claim"] for a in d["bull"]], "bear": [a["claim"] for a in d["bear"]], "flips": [x["claim"] for x in d["sensitivity"]["drop_one"] if x["flips"]]}


def regime(window: list[dict]) -> str:
    move = (window[-1]["c"] / window[0]["c"] - 1) * 100
    return "up" if move > REGIME_BAND_PCT else "down" if move < -REGIME_BAND_PCT else "range"


def replay(candles: list[dict], symbol: str, decide, window: int = WINDOW) -> list[dict]:
    """One decision per hour over the history, each seeing only the 72 candles before it (like the backtest)."""
    rows = []
    for end in range(window, len(candles) + 1):
        win = candles[end - window : end]
        rows.append({**decide(win, symbol), "regime": regime(win)})
    return rows


def walk_forward_v2(candles: list[dict], symbol: str, window: int = WINDOW, max_hold: int = backtest.MAX_HOLD, fee_pct: float = backtest.FEE_PCT_PER_SIDE) -> list[dict]:
    """The protocol of `backtest.run` (one position at a time, 48-candle time exit, fees on both sides) with the v2 rules."""
    trades: list[dict] = []
    i = window
    while i < len(candles):
        win = candles[i - window : i]
        verdict = agents_v2.decide(features_v2.compute(_packet(symbol, win)))["verdict"]
        if verdict["action"] == "FLAT":
            i += 1
            continue
        future = candles[i : i + max_hold]
        result = portfolio.resolve({"seq": len(trades) + 1, "evidence": {"symbol": symbol}, "verdict": verdict}, future)
        if result["status"] == "OPEN" and len(future) < max_hold:
            break  # the data ends before the trade resolves: it does not count
        if result["status"] == "OPEN":
            result["status"] = "TIME"
        fees = verdict["entry"] * verdict["size"] * fee_pct / 100 * 2
        result.update(t=win[-1]["t"], edge=verdict["edge"], fees=round(fees, 4), net_pnl=round(result["pnl"] - fees, 4))
        trades.append(result)
        i += max(result["bars"], 1)
    return trades


def _with_sides(trades: list[dict], candles: list[dict]) -> dict:
    summary = backtest.summarize(trades, candles)
    summary["net_by_side"] = {side: round(sum(t["net_pnl"] for t in trades if t["action"] == side), 2) for side in ("LONG", "SHORT")}
    return summary


def walk_forward(candles: list[dict], symbol: str) -> dict:
    return {"v1": _with_sides(backtest.run(candles, symbol), candles), "v2": _with_sides(walk_forward_v2(candles, symbol), candles)}


# ---- summaries ----------------------------------------------------------------------------------------------------


def shares(rows: list[dict]) -> dict:
    counts = Counter(r["action"] for r in rows)
    return {"n": len(rows), **{a: counts.get(a, 0) for a in ACTIONS}, **{f"{a}_pct": _pct(counts.get(a, 0), len(rows)) for a in ACTIONS}}


def by_regime(rows: list[dict]) -> dict:
    return {name: shares([r for r in rows if r["regime"] == name]) for name in ("up", "range", "down")}


def state_of(bull_claims, bear_claims) -> str:
    """'both bullish': EMA12 above EMA48 and a positive MACD histogram. 'both bearish': the reverse. Anything else is 'mixed'."""
    if TREND_UP in bull_claims and MACD_UP in bull_claims:
        return "both bullish"
    if TREND_DOWN in bear_claims and MACD_DOWN in bear_claims:
        return "both bearish"
    return "mixed"


def by_state(rows: list[dict]) -> dict:
    """What the rules decided in each indicator state. A rule set that treats both sides alike answers the first two states in mirror."""
    return {name: shares([r for r in rows if state_of(r["bull"], r["bear"]) == name]) for name in STATES}


def decomposition(real: dict, mirrored: dict) -> dict:
    """LONG minus SHORT share on the real series (a) and on the mirror (a2).

    A mirror-symmetric rule set gives a2 = -a. What flips sign is the market (regime); what stays is the rules (bias).
    """
    a = real["LONG_pct"] - real["SHORT_pct"]
    a2 = mirrored["LONG_pct"] - mirrored["SHORT_pct"]
    return {"real_long_minus_short": round(a, 1), "mirror_long_minus_short": round(a2, 1), "bias_pp": round((a + a2) / 2, 1), "regime_pp": round((a - a2) / 2, 1)}


def argument_rates(rows: list[dict], side: str, top: int = 8) -> list[tuple[str, float]]:
    counts = Counter(claim for r in rows for claim in r[side])
    return [(claim, _pct(n, len(rows))) for claim, n in counts.most_common(top)]


def decisive_arguments(rows: list[dict], action: str, top: int = 5) -> list[tuple[str, float]]:
    """Among the decisions with this action, how often removing one argument alone would change the action."""
    chosen = [r for r in rows if r["action"] == action]
    counts = Counter(claim for r in chosen for claim in r["flips"])
    return [(claim, _pct(n, len(chosen))) for claim, n in counts.most_common(top)]


def measure(candles: list[dict], symbol: str) -> dict:
    """Everything the history says about one symbol, for both rule sets and both mirrors."""
    sets = {"real": candles, "mirror": mirror(candles), "inverse": mirror_inverse(candles)}
    out = {"symbol": symbol, "candles": len(candles), "windows": max(0, len(candles) - WINDOW + 1)}
    for version, decide in (("v1", decide_v1), ("v2", decide_v2)):
        rows = {name: replay(series, symbol, decide) for name, series in sets.items()}
        out[version] = {
            "real": shares(rows["real"]),
            "mirror": shares(rows["mirror"]),
            "inverse": shares(rows["inverse"]),
            "real_by_regime": by_regime(rows["real"]),
            "decomposition": decomposition(shares(rows["real"]), shares(rows["mirror"])),
            "decomposition_inverse": decomposition(shares(rows["real"]), shares(rows["inverse"])),
            "bull_rates": argument_rates(rows["real"], "bull"),
            "bear_rates": argument_rates(rows["real"], "bear"),
            "bull_rates_mirror": argument_rates(rows["mirror"], "bull"),
            "bear_rates_mirror": argument_rates(rows["mirror"], "bear"),
            "decisive_long": decisive_arguments(rows["real"], "LONG"),
            "decisive_short": decisive_arguments(rows["real"], "SHORT"),
        }
        if version == "v2":
            out[version]["real_by_state"] = by_state(rows["real"])
    out["walk_forward"] = walk_forward(candles, symbol)
    first = candles[0]["c"] if candles else 0
    out["buy_and_hold_pct"] = round((candles[-1]["c"] / first - 1) * 100, 2) if candles else 0
    out["regime_windows"] = Counter(regime(candles[end - WINDOW : end]) for end in range(WINDOW, len(candles) + 1))
    return out


# ---- the ledger ---------------------------------------------------------------------------------------------------


def ledger_view(ledger: list[dict]) -> dict:
    v2 = [d for d in ledger if d["verdict"].get("rules_version") == "v2"]
    per_symbol: dict[str, dict] = {}
    for symbol in sorted({d["evidence"]["symbol"] for d in v2}):
        mine = [d for d in v2 if d["evidence"]["symbol"] == symbol]
        feats = [d["features"] for d in mine]
        per_symbol[symbol] = {
            **{a: sum(1 for d in mine if d["verdict"]["action"] == a) for a in ACTIONS},
            "n": len(mine),
            "mean_edge": round(sum(d["verdict"]["edge"] for d in mine) / len(mine), 2),
            "trend_up_pct": _pct(sum(1 for f in feats if f["ema_fast"] > f["ema_slow"]), len(mine)),
            "macd_positive_pct": _pct(sum(1 for f in feats if (f.get("macd_histogram") or 0) > 0), len(mine)),
            "rsi_mean": round(sum(f.get("rsi_14") or 0 for f in feats) / len(mine), 1),
            "return_24_mean": round(sum(f["return_24"] for f in feats) / len(mine), 2),
            "first_seq": mine[0]["seq"],
            "last_seq": mine[-1]["seq"],
        }
    # What funding and news (absent from the history) changed: recompute the verdict without them from the frozen packet.
    changed = 0
    for d in v2:
        bare = {**d["evidence"], "funding": {"rate": None}, "news": {"articles": []}}
        if agents_v2.decide(features_v2.compute(bare))["verdict"]["action"] != d["verdict"]["action"]:
            changed += 1
    live_states = by_state([{"action": d["verdict"]["action"], "bull": [a["claim"] for a in d["bull"]], "bear": [a["claim"] for a in d["bear"]]} for d in v2])
    bull_args = Counter(a["claim"] for d in v2 for a in d["bull"])
    bear_args = Counter(a["claim"] for d in v2 for a in d["bear"])
    return {
        "dossiers": len(v2),
        "actions": {a: sum(1 for d in v2 if d["verdict"]["action"] == a) for a in ACTIONS},
        "per_symbol": per_symbol,
        "changed_without_funding_and_news": changed,
        "by_state": live_states,
        "bull_rates": [(c, _pct(n, len(v2))) for c, n in bull_args.most_common(6)],
        "bear_rates": [(c, _pct(n, len(v2))) for c, n in bear_args.most_common(6)],
        "first_seq": v2[0]["seq"] if v2 else None,
        "last_seq": v2[-1]["seq"] if v2 else None,
    }


# ---- report -------------------------------------------------------------------------------------------------------


def _rate_lines(rates: list[tuple[str, float]]) -> str:
    return "; ".join(f"{claim} ({pct}%)" for claim, pct in rates) or "none"


def render(ledger_head: dict, live: dict, history: list[dict], series: dict[str, dict]) -> str:
    sources = ", ".join("{} {} closed hourly candles {} to {} UTC (sha256 {})".format(name, m["candles"], m["first"], m["last"], m["sha"][:16]) for name, m in series.items())
    lines = [
        "<!-- generated by `python3 -m verdict_desk.v2_diagnosis`; the tables below are counts, the text around them is written by hand -->",
        f"Ledger read: head seq {ledger_head['seq']} (hash {ledger_head['hash'][:16]}). History: {sources}. Window {WINDOW} candles, regime band {REGIME_BAND_PCT}% on the 72-candle return.",
        "",
        "## 1. What the v2 dossiers in the ledger decided",
        "",
        f"{live['dossiers']} dossiers carry `verdict.rules_version = \"v2\"` (seq {live['first_seq']} to {live['last_seq']}): "
        + ", ".join(f"{a} {live['actions'][a]}" for a in ACTIONS) + ".",
        "",
        "| Symbol | v2 dossiers | LONG | FLAT | SHORT | Mean edge | EMA12 above EMA48 | MACD histogram positive | Mean RSI | Mean 24-candle return |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for symbol, v in live["per_symbol"].items():
        lines.append(f"| {symbol} | {v['n']} | {v['LONG']} | {v['FLAT']} | {v['SHORT']} | {v['mean_edge']} | {v['trend_up_pct']}% | {v['macd_positive_pct']}% | {v['rsi_mean']} | {v['return_24_mean']}% |")
    lines += [
        "",
        f"Bull arguments that appear most often in these dossiers: {_rate_lines(live['bull_rates'])}.",
        "",
        f"Bear arguments: {_rate_lines(live['bear_rates'])}.",
        "",
        f"Recomputing each frozen packet without funding and news (which the history does not have) changes the verdict of {live['changed_without_funding_and_news']} of {live['dossiers']} dossiers, so the history replay below is a fair stand-in for the live rules.",
        "",
        "## 2. The same rules over a long history, hour by hour",
        "",
        "Share of hourly decisions (v1 is the baseline, v2 is what the workflow records now). Regime is the 72-candle return of the window, which is not one of the rule inputs.",
        "",
        "| Symbol | Rules | Windows | LONG | SHORT | FLAT | LONG in up regimes | SHORT in down regimes | FLAT in range | Buy and hold over the period |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in history:
        for version in ("v1", "v2"):
            v = m[version]
            r = v["real_by_regime"]
            lines.append(f"| {m['symbol']} | {version} | {v['real']['n']} | {v['real']['LONG_pct']}% | {v['real']['SHORT_pct']}% | {v['real']['FLAT_pct']}% | {r['up']['LONG_pct']}% (of {r['up']['n']}) | {r['down']['SHORT_pct']}% (of {r['down']['n']}) | {r['range']['FLAT_pct']}% (of {r['range']['n']}) | {m['buy_and_hold_pct']}% |")
    lines += [
        "",
        "### The same indicator state on both sides (v2)",
        "",
        "Windows grouped by the two signals each side weighs most: EMA12 against EMA48 and the sign of the MACD histogram. A rule set that treats both sides alike answers `both bullish` with LONG as often as it answers `both bearish` with SHORT. The last rows are the v2 dossiers in the ledger, with the counts in brackets.",
        "",
        "| Symbol | Indicator state | Windows | LONG | FLAT | SHORT |",
        "|---|---|---|---|---|---|",
    ]
    for m in history:
        for name in STATES:
            s = m["v2"]["real_by_state"][name]
            lines.append(f"| {m['symbol']} | {name} | {s['n']} | {s['LONG_pct']}% | {s['FLAT_pct']}% | {s['SHORT_pct']}% |")
    for name in STATES:
        s = live["by_state"][name]
        lines.append(f"| Ledger, v2 dossiers | {name} | {s['n']} | {s['LONG_pct']}% ({s['LONG']}) | {s['FLAT_pct']}% ({s['FLAT']}) | {s['SHORT_pct']}% ({s['SHORT']}) |")
    lines += [
        "",
        "## 3. Mirror test: regime or bias",
        "",
        "LONG share minus SHORT share, in percentage points of windows, on the real series and on the mirror (every move negated). `bias` is the part that stays when the market is mirrored, `regime` is the part that flips sign. Two mirrors are shown: negated arithmetic returns, and inverted prices (exact in log returns).",
        "",
        "| Symbol | Rules | Real: LONG, SHORT | Mirror: LONG, SHORT | Bias (pp) | Regime (pp) | Bias with the inverted-price mirror (pp) |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in history:
        for version in ("v1", "v2"):
            v = m[version]
            d, d2 = v["decomposition"], v["decomposition_inverse"]
            lines.append(f"| {m['symbol']} | {version} | {v['real']['LONG_pct']}%, {v['real']['SHORT_pct']}% | {v['mirror']['LONG_pct']}%, {v['mirror']['SHORT_pct']}% | {d['bias_pp']} | {d['regime_pp']} | {d2['bias_pp']} |")
    lines += ["", "### Which arguments carry the decisions (v2, real series)", ""]
    for m in history:
        v = m["v2"]
        lines += [
            f"**{m['symbol']}.** Arguments present in the most windows, bull: {_rate_lines(v['bull_rates'])}. Bear: {_rate_lines(v['bear_rates'])}.",
            "",
            f"Arguments whose removal alone would change a LONG decision: {_rate_lines(v['decisive_long'])}. A SHORT decision: {_rate_lines(v['decisive_short'])}.",
            "",
        ]
    lines += [
        "## 4. Context: what these decisions earned in a walk-forward backtest",
        "",
        "Same history, same protocol as `python3 -m verdict_desk backtest` (one position at a time, 48-candle time exit, 0.05% fee per side, start capital 10,000), with price and volume only. Past data, not live performance. It is here to put the direction mix in context, not as a verdict on the rules.",
        "",
        "| Symbol | Rules | Trades (long, short) | Net return | Return before fees | Net P&L of longs, of shorts | Fees paid | Profit factor | Win rate | Max drawdown | Buy and hold |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in history:
        for version in ("v1", "v2"):
            w = m["walk_forward"][version]
            sides = w["net_by_side"]
            before_fees = round((w["net_pnl"] + w["fees_paid"]) / w["start_capital"] * 100, 2)
            lines.append(f"| {m['symbol']} | {version} | {w['trades']} ({w['longs']}, {w['shorts']}) | {w['return_pct']}% | {before_fees}% | {sides['LONG']}, {sides['SHORT']} | {w['fees_paid']:.2f} | {w['profit_factor']} | {w['win_rate_pct']}% | {w['max_drawdown_pct']}% | {w['buy_and_hold_pct']}% |")
    return "\n".join(lines).rstrip() + "\n"


START_MARK = "<!-- v2-diagnosis:start -->"
END_MARK = "<!-- v2-diagnosis:end -->"


def write_block(path: Path, block: str) -> None:
    """Put the generated tables between the markers of `path`, keeping the hand-written text around them."""
    wrapped = f"{START_MARK}\n{block}{END_MARK}\n"
    if path.exists() and START_MARK in path.read_text(encoding="utf-8"):
        text = path.read_text(encoding="utf-8")
        head, _, rest = text.partition(START_MARK)
        _, _, tail = rest.partition(END_MARK)
        path.write_text(head + wrapped + tail.lstrip("\n"), encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wrapped, encoding="utf-8")


# ---- entry point --------------------------------------------------------------------------------------------------


def load_series(symbol: str, total: int, directory: Path | None, now_ms: int | None = None, until_ms: int | None = None) -> list[dict]:
    """Closed hourly candles. `until_ms` pins the end of the window (the same data again, whenever it is run)."""
    if directory is not None:
        candles = json.loads((directory / f"{symbol}.json").read_text())
    else:
        candles = backtest.fetch_history(symbol, "1h", total, until_ms=until_ms)
    limit = until_ms if until_ms is not None else now_ms if now_ms is not None else int(time.time() * 1000)
    return [c for c in candles if c["t"] + HOUR_MS <= limit]  # a candle that is still forming is not a decision input


def parse_until(text: str) -> int:
    """'2026-10-21T07:00:00Z' (or without the Z) to epoch milliseconds, UTC."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def stamp(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ms / 1000))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", type=Path, default=Path("ledger/ledger.jsonl"))
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--candles", type=int, default=4000)
    parser.add_argument("--candles-dir", type=Path, help="read DIR/SYMBOL.json instead of fetching (offline)")
    parser.add_argument("--until", help="end of the window as a UTC instant, e.g. 2026-10-21T07:00:00Z: only candles that closed by then (reproduces an earlier run)")
    parser.add_argument("--out", type=Path, help="write the tables between the v2-diagnosis markers of this markdown file (created if missing)")
    opts = parser.parse_args(argv)

    ledger = dossier.read_ledger(opts.ledger)
    problems = dossier.verify(ledger)
    if not ledger or problems:
        print("The ledger is empty or does not verify; refusing to measure it: " + "; ".join(problems), file=sys.stderr)
        return 1
    live = ledger_view(ledger)
    until_ms = parse_until(opts.until) if opts.until else None
    history, series = [], {}
    for symbol in opts.symbols:
        candles = load_series(symbol, opts.candles, opts.candles_dir, until_ms=until_ms)
        if len(candles) < WINDOW * 3:
            print(f"{symbol}: not enough history ({len(candles)} candles).", file=sys.stderr)
            return 1
        series[symbol] = {"candles": len(candles), "first": stamp(candles[0]["t"]), "last": stamp(candles[-1]["t"]), "sha": backtest.series_hash(candles)}
        history.append(measure(candles, symbol))
        print(f"{symbol}: measured {history[-1]['windows']} windows", file=sys.stderr)
    text = render({"seq": ledger[-1]["seq"], "hash": ledger[-1]["hash"]}, live, history, series)
    if opts.out:
        write_block(opts.out, text)
        print(f"Saved {opts.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
