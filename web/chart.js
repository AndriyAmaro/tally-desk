/* The chart of Ask the record: drawn only from the candles frozen in the dossier, nothing fetched from a market.
 *
 * The averages and bands are recomputed from those candles with the same formulas as verdict_desk/features_v2.py
 * (EMA 12 and 48 over every close, Bollinger 20 with two population deviations), and tests/chart.test.js checks
 * on every v2+ record that their last points are the values the record itself computed. The levels drawn (entry,
 * stop, target, the 24-candle range, the last close) are the recorded numbers, never re-derived. A citation chip
 * lights the mark it names through `marksFor`.
 *
 * Pure data in, an SVG string out. No DOM, no network.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VerdictChart = api;
})(typeof self !== "undefined" ? self : this, function () {
  function emaSeries(values, period) {
    const k = 2 / (period + 1);
    const out = [];
    let current = values[0];
    for (let i = 0; i < values.length; i++) {
      if (i > 0) current = values[i] * k + current * (1 - k);
      out.push(current);
    }
    return out;
  }

  function bands(closes, period = 20, numStd = 2) {
    const upper = [], middle = [], lower = [];
    for (let i = 0; i < closes.length; i++) {
      if (i + 1 < period) { upper.push(null); middle.push(null); lower.push(null); continue; }
      const recent = closes.slice(i + 1 - period, i + 1);
      const mid = recent.reduce((a, b) => a + b, 0) / period;
      const std = Math.sqrt(recent.reduce((a, c) => a + (c - mid) ** 2, 0) / period);
      upper.push(mid + numStd * std); middle.push(mid); lower.push(mid - numStd * std);
    }
    return { upper, middle, lower };
  }

  /** The studies drawn over the candles, one value per candle (bands start at the 20th). */
  function studies(candles) {
    const closes = candles.map((c) => c.c);
    const bb = bands(closes);
    return { emaFast: emaSeries(closes, 12), emaSlow: emaSeries(closes, 48), bbUpper: bb.upper, bbMiddle: bb.middle, bbLower: bb.lower };
  }

  const LEVEL_LABEL = {
    entry: "entry", stop: "stop", take_profit: "target",
    range_high_24: "24h high", range_low_24: "24h low", last_close: "close",
  };

  /** Pixel geometry for one dossier, optionally with the candles that came after it and how the position ended. */
  function geometry(d, opts) {
    const o = opts || {};
    const width = o.width || 640, height = o.height || 400;
    const pad = { top: 14, right: 96, bottom: 26, left: 8 };
    const candles = d.evidence.market.candles;
    const later = o.later || [];
    const v = d.verdict || {}, f = d.features || {};
    const all = candles.concat(later);

    const levels = [];
    const level = (key, price) => { if (typeof price === "number" && Number.isFinite(price)) levels.push({ key, label: LEVEL_LABEL[key], price }); };
    if (typeof v.entry === "number") { level("entry", v.entry); level("stop", v.stop); level("take_profit", v.take_profit); }
    level("range_high_24", f.range_high_24);
    level("range_low_24", f.range_low_24);
    level("last_close", typeof f.last_close === "number" ? f.last_close : candles[candles.length - 1].c);

    let lo = Math.min(...all.map((c) => c.l), ...levels.map((l) => l.price));
    let hi = Math.max(...all.map((c) => c.h), ...levels.map((l) => l.price));
    const margin = (hi - lo) * 0.04 || 1;
    lo -= margin; hi += margin;
    const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
    const slot = plotW / all.length;
    const x = (i) => pad.left + slot * (i + 0.5);
    const y = (p) => pad.top + (hi - p) / (hi - lo) * plotH;
    const bar = (c, i) => ({ t: c.t, x: x(i), w: Math.max(1, slot * 0.62), yOpen: y(c.o), yClose: y(c.c), yHigh: y(c.h), yLow: y(c.l), up: c.c >= c.o });

    const s = studies(candles);
    const path = (series) => series.map((p, i) => (p === null ? null : `${x(i).toFixed(2)},${y(p).toFixed(2)}`)).filter(Boolean).join(" ");
    for (const l of levels) l.y = y(l.price);
    // Labels sit at their line unless two would overlap; then the lower one moves down just enough to be read.
    const gap = 11;
    const byY = levels.slice().sort((a, b) => a.y - b.y);
    byY.forEach((l, i) => { l.ly = i === 0 ? l.y : Math.max(l.y, byY[i - 1].ly + gap); });

    let exit = null;
    if (o.outcome && o.outcome.status !== "OPEN" && o.outcome.bars > 0 && later.length >= o.outcome.bars) {
      const i = candles.length + o.outcome.bars - 1;
      exit = { status: o.outcome.status, price: o.outcome.exit, x: x(i), y: y(o.outcome.exit) };
    }
    const ticks = [];
    const step = (hi - lo) / 5;
    for (let k = 1; k < 5; k++) ticks.push({ price: lo + step * k, y: y(lo + step * k) });
    const times = [];
    for (let i = 0; i < all.length; i += 12) times.push({ x: x(i), label: new Date(all[i].t).toISOString().slice(5, 16).replace("T", " ") });

    return {
      width, height, pad,
      candles: candles.map(bar),
      later: later.map((c, k) => bar(c, candles.length + k)),
      paths: { ema_fast: path(s.emaFast), ema_slow: path(s.emaSlow), bb_upper: path(s.bbUpper), bb_lower: path(s.bbLower) },
      levels, exit, ticks, times,
      split: later.length ? pad.left + slot * candles.length : null,
    };
  }

  const FEATURE_MARKS = { ema_fast: "ema_fast", ema_slow: "ema_slow", last_close: "last_close", range_high_24: "range_high_24", range_low_24: "range_low_24",
    bb_upper: "bands", bb_lower: "bands", bb_middle: "bands", bb_pct_b: "bands" };
  const VERDICT_MARKS = { "verdict.entry": "entry", "verdict.stop": "stop", "verdict.take_profit": "take_profit" };

  /** The chart marks a list of citation refs points at; an argument ref lights what its evidence reference names. */
  function marksFor(refs, d) {
    const out = [];
    const add = (m) => { if (m && !out.includes(m)) out.push(m); };
    for (const ref of refs || []) {
      if (VERDICT_MARKS[ref]) add(VERDICT_MARKS[ref]);
      else if (ref.startsWith("features.")) add(FEATURE_MARKS[ref.slice(9)]);
      else if (ref === "outcome.exit") add("exit");
      else if (ref === "outcome.later") add("later");
      else {
        const m = /^(bull|bear)\[(\d+)\]$/.exec(ref);
        const arg = m && d && (d[m[1]] || [])[Number(m[2])];
        if (arg && typeof arg.evidence_ref === "string") {
          for (const [token, mark] of Object.entries(FEATURE_MARKS)) if (arg.evidence_ref.includes(token)) add(mark);
        }
      }
    }
    return out;
  }

  const num = (p) => (Math.abs(p) >= 1000 ? p.toFixed(1) : Math.abs(p) >= 10 ? p.toFixed(2) : p.toFixed(4));

  /** The SVG markup. Every mark carries data-mark, so the page lights one by adding a class. */
  function render(g) {
    const out = [`<svg viewBox="0 0 ${g.width} ${g.height}" class="vchart" role="img" aria-label="Candles frozen in the record, with its levels">`];
    for (const t of g.ticks) out.push(`<line class="grid" x1="${g.pad.left}" x2="${g.width - g.pad.right}" y1="${t.y.toFixed(2)}" y2="${t.y.toFixed(2)}"/><text class="tick" x="${g.pad.left + 2}" y="${(t.y - 3).toFixed(2)}">${num(t.price)}</text>`);
    for (const t of g.times) out.push(`<text class="tick" x="${t.x.toFixed(2)}" y="${g.height - 8}" text-anchor="middle">${t.label}</text>`);
    if (g.split !== null) out.push(`<g data-mark="later"><line class="split" x1="${g.split.toFixed(2)}" x2="${g.split.toFixed(2)}" y1="${g.pad.top}" y2="${g.height - g.pad.bottom}"/><text class="tick" x="${(g.split + 4).toFixed(2)}" y="${g.pad.top + 8}">after the decision</text></g>`);
    out.push(`<polyline data-mark="bands" class="band" points="${g.paths.bb_upper}"/><polyline data-mark="bands" class="band" points="${g.paths.bb_lower}"/>`);
    out.push(`<polyline data-mark="ema_slow" class="ema slow" points="${g.paths.ema_slow}"/><polyline data-mark="ema_fast" class="ema fast" points="${g.paths.ema_fast}"/>`);
    const candle = (c, cls) => {
      const top = Math.min(c.yOpen, c.yClose), h = Math.max(1, Math.abs(c.yClose - c.yOpen));
      return `<g class="${cls} ${c.up ? "up" : "down"}"><line x1="${c.x.toFixed(2)}" x2="${c.x.toFixed(2)}" y1="${c.yHigh.toFixed(2)}" y2="${c.yLow.toFixed(2)}"/><rect x="${(c.x - c.w / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${c.w.toFixed(2)}" height="${h.toFixed(2)}"/></g>`;
    };
    for (const c of g.candles) out.push(candle(c, "candle"));
    if (g.later.length) out.push(`<g data-mark="later">${g.later.map((c) => candle(c, "candle later")).join("")}</g>`);
    for (const l of g.levels) {
      out.push(`<g data-mark="${l.key}" class="level ${l.key}"><line x1="${g.pad.left}" x2="${g.width - g.pad.right}" y1="${l.y.toFixed(2)}" y2="${l.y.toFixed(2)}"/><text x="${g.width - g.pad.right + 4}" y="${(l.ly + 3).toFixed(2)}">${l.label} ${num(l.price)}</text></g>`);
    }
    if (g.exit) out.push(`<g data-mark="exit" class="exit ${g.exit.status.toLowerCase()}"><circle cx="${g.exit.x.toFixed(2)}" cy="${g.exit.y.toFixed(2)}" r="5"/></g>`);
    out.push("</svg>");
    return out.join("");
  }

  return { studies, geometry, marksFor, render };
});
