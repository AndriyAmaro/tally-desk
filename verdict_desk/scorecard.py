"""Scorecard: score our own decisions against what the market did next.

Every other artifact in this project proves the record is intact. None of them proves the
decisions were any good. This one does, and it is published with the sample sizes attached,
because a scorecard without its denominators is marketing.

Method, exactly:
  * entry price is the last close in the frozen packet (the same price the judge sized at);
  * the forward price is the first candle at or after the target horizon, taken from later
    packets in the same ledger, with no external data, so the report reproduces from the ledger;
  * a LONG scores (forward / entry - 1), a SHORT scores the negative of that;
  * rows whose horizon has not yet elapsed are excluded, never estimated.

What it does not do: adjust for overlapping windows (an hourly decision scored at 24h shares
most of its price path with its neighbours), subtract fees, or claim significance. Those
limits are printed on the page next to the numbers.
"""

import html

HORIZONS = (1, 4, 24)
STATES = ("LONG", "SHORT", "FLAT", "WATCH_REVERSAL", "REDUCE_EXPOSURE", "BLOCKED_STALE")



def _money(value) -> str:
    """4499.9997 -> '+$4,500' (the simulated result on $10,000, to the dollar)."""
    if not isinstance(value, (int, float)):
        return str(value)
    return f"{'+' if value >= 0 else '-'}${abs(value):,.0f}"

def _price_map(ledger: list[dict]) -> dict[str, dict[int, float]]:
    """Todos os candles publicados por simbolo. Dossies mais novos completam os antigos."""
    prices: dict[str, dict[int, float]] = {}
    for row in ledger:
        ev = row.get("evidence") or {}
        symbol = ev.get("symbol")
        if not symbol:
            continue
        for candle in (ev.get("market") or {}).get("candles") or []:
            try:
                prices.setdefault(symbol, {})[int(candle["t"])] = float(
                    candle.get("close", candle.get("c")))
            except (KeyError, TypeError, ValueError):
                continue
    return prices


def _forward(prices: dict[int, float], start_ms: int, hours: int):
    target = start_ms + hours * 3_600_000
    later = [t for t in prices if t >= target]
    return prices[min(later)] if later else None


def compute(ledger: list[dict], horizons=HORIZONS) -> dict:
    prices = _price_map(ledger)
    buckets: dict[tuple[str, int], list[float]] = {}
    counts: dict[str, int] = {}
    skipped = 0

    for row in ledger:
        verdict = row.get("verdict") or {}
        action = verdict.get("action")
        counts[action] = counts.get(action, 0) + 1
        if action not in ("LONG", "SHORT"):
            continue
        ev = row.get("evidence") or {}
        candles = (ev.get("market") or {}).get("candles") or []
        if not candles:
            skipped += 1
            continue
        symbol = ev.get("symbol")
        if not symbol:
            skipped += 1
            continue
        try:
            start = int(candles[-1]["t"])
            entry = float(candles[-1].get("close", candles[-1].get("c")))
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        for hours in horizons:
            forward = _forward(prices.get(symbol, {}), start, hours)
            if forward is None or entry <= 0:
                continue
            move = (forward / entry - 1) * 100
            buckets.setdefault((action, hours), []).append(move if action == "LONG" else -move)

    table = []
    for state in ("LONG", "SHORT"):
        for hours in horizons:
            series = buckets.get((state, hours)) or []
            if not series:
                table.append({"state": state, "hours": hours, "n": 0})
                continue
            table.append({
                "state": state,
                "hours": hours,
                "n": len(series),
                "mean": round(sum(series) / len(series), 3),
                "hit_rate": round(100 * sum(1 for x in series if x > 0) / len(series), 1),
                "best": round(max(series), 2),
                "worst": round(min(series), 2),
            })
    return {"table": table, "counts": counts, "skipped": skipped,
            "decisions": len(ledger), "horizons": list(horizons)}


def _row(r: dict) -> str:
    if not r.get("n"):
        return (f"<tr><td>{html.escape(r['state'])}</td><td>+{r['hours']}h</td>"
                f"<td colspan=\"5\" class=\"small\">no position has reached this horizon yet</td></tr>")
    # Cor por significancia pratica, nao por sinal: uma media de +0.001% pintada de verde
    # com 29% de acerto engana. Abaixo de meio ponto percentual, neutro.
    if abs(r["mean"]) < 0.005:
        cls = "mut"
    else:
        cls = "ok" if r["mean"] > 0 else "bad"
    return (f"<tr><td><b>{html.escape(r['state'])}</b></td><td>+{r['hours']}h</td><td>{r['n']}</td>"
            f"<td class=\"{cls}\">{r['mean']:+.3f}%</td><td>{r['hit_rate']:.0f}%</td>"
            f"<td>{r['best']:+.2f}%</td><td>{r['worst']:+.2f}%</td></tr>")


def render(result: dict, portfolio_summary: dict | None = None) -> str:
    counts = " · ".join(f"{html.escape(k)}: {v}" for k, v in sorted(result["counts"].items()))
    rows = "".join(_row(r) for r in result["table"])
    port = ""
    if portfolio_summary:
        port = (f"<p class=\"lead\">Simulated portfolio across the same records: "
                f"<b>{html.escape(str(portfolio_summary.get('decisions')))}</b> decisions, "
                f"<b>{html.escape(str(len(portfolio_summary.get('positions') or [])))}</b> positions "
                f"({html.escape(str(portfolio_summary.get('closed')))} closed, "
                f"{html.escape(str(portfolio_summary.get('open')))} open), "
                f"win rate <b>{html.escape(str(portfolio_summary.get('win_rate_pct')))}%</b>, "
                f"max drawdown <b>{html.escape(str(portfolio_summary.get('max_drawdown_pct')))}%</b>. "
                f"Realized <b>{html.escape(_money(portfolio_summary.get('realized_pnl')))}</b> before fees and "
                f"<b>{html.escape(_money(portfolio_summary.get('realized_net')))}</b> after a "
                f"{html.escape(str(portfolio_summary.get('fee_pct_per_side')))}% taker fee per side on the entry and exit notional "
                "(the Binance USD-M futures base rate; slippage excluded). Positions are marked against later candles.</p>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verdict Desk · scorecard</title>
<style>
:root{{--fg:#111;--mut:#555;--line:#ccc;--ok:#0a7a3d;--bad:#b3261e;--warn:#8a5300}}
body{{font:14px/1.6 system-ui,sans-serif;color:var(--fg);background:#fff;max-width:860px;margin:0 auto;padding:26px 22px 60px}}
h1{{font-size:21px;margin:0 0 4px}}h2{{font-size:15px;margin:24px 0 6px;padding-bottom:3px;border-bottom:1px solid var(--line)}}
.lead{{margin:8px 0}}.small{{font-size:12px;color:var(--mut)}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}}
th,td{{border:1px solid var(--line);padding:6px 8px;text-align:left}}thead th{{background:#f3f3f3;font-size:11px;text-transform:uppercase}}
.ok{{color:var(--ok);font-weight:600}}.bad{{color:var(--bad);font-weight:600}}.mut{{color:var(--mut)}}
ul{{margin:8px 0 8px 18px}}
</style></head><body>
<h1>Scorecard: how the decisions actually performed</h1>
<p class="small">Generated from the published ledger alone. No external data, so this page reproduces.</p>
<p class="lead">Every other page in this project proves the record is intact. This one asks the uncomfortable question: <b>were the decisions any good?</b></p>

<h2>Outcome by state and horizon</h2>
<p class="small">Entry is the last close in the frozen packet. The forward price is the first candle at or after the horizon, taken from later packets of the same ledger. A LONG scores the price move; a SHORT scores its negative.</p>
<table><thead><tr><th>State</th><th>Horizon</th><th>n</th><th>Mean</th><th>Hit rate</th><th>Best</th><th>Worst</th></tr></thead>
<tbody>{rows}</tbody></table>
{port}
<p class="small">Records by state: {counts}.</p>

<h2>What this table does not tell you</h2>
<ul class="small">
  <li><b>Overlap.</b> An hourly decision scored at 24 hours shares most of its price path with its neighbours, so the effective sample is smaller than <code>n</code> suggests.</li>
  <li><b>Fees and slippage are excluded.</b> Real execution would eat into every row.</li>
  <li><b>No significance claim.</b> The columns are what happened, not a forecast of what will happen.</li>
  <li><b>Small n in some rows.</b> Where <code>n</code> is a handful, the mean is noise with a decimal point.</li>
</ul>
<p class="small">The backtest of the v1 rules, published in the repository, does not profit after fees. That result and this table are kept side by side on purpose.</p>
</body></html>
"""


def render_markdown(result: dict) -> str:
    lines = ["| State | Horizon | n | Mean | Hit rate | Best | Worst |", "|---|---|---|---|---|---|---|"]
    for r in result["table"]:
        if not r.get("n"):
            lines.append(f"| {r['state']} | +{r['hours']}h | 0 | · | · | · | · |")
        else:
            lines.append(f"| {r['state']} | +{r['hours']}h | {r['n']} | {r['mean']:+.3f}% | "
                         f"{r['hit_rate']:.0f}% | {r['best']:+.2f}% | {r['worst']:+.2f}% |")
    return "\n".join(lines)
