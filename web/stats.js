/* Estatisticas do ledger calculadas no navegador (global VerdictStats) e no Node (module.exports).
 * portfolio() e a mesma regra de verdict_desk/portfolio.py: cada posicao so usa candles de dossies
 * registrados depois dela; se stop e alvo caem no mesmo candle, vale o stop; sem candle posterior, OPEN.
 * cadence() mostra os buracos reais do historico horario em vez de escondê-los. */
(function (root) {
  "use strict";
  var START_CAPITAL = 10000;
  var GAP_LIMIT_MS = 90 * 60 * 1000;
  var HOUR_MS = 3600000;
  var FIRST_LLM_SEQ = 46; // first dossier recorded after the analyst prompt and citation validator fixes

  function round(x, places) {
    var f = Math.pow(10, places);
    return Math.round((x + Number.EPSILON) * f) / f;
  }

  function round1(x) {
    return Math.floor(x * 10 + 0.5) / 10; // half up, same as verdict_desk/livestats.py
  }

  function lastCandleTime(d) {
    var c = d.evidence.market.candles;
    return c[c.length - 1].t;
  }

  function laterCandles(ledger, index) {
    var symbol = ledger[index].evidence.symbol, entry = lastCandleTime(ledger[index]), seen = {}, out = [];
    for (var i = index + 1; i < ledger.length; i++) {
      if (ledger[i].evidence.symbol !== symbol) continue;
      ledger[i].evidence.market.candles.forEach(function (c) {
        if (c.t > entry && !seen[c.t]) { seen[c.t] = true; out.push(c); }
      });
    }
    return out.sort(function (a, b) { return a.t - b.t; });
  }

  function resolve(d, candles) {
    var v = d.verdict, dir = v.action === "LONG" ? 1 : -1;
    var base = { seq: d.seq, symbol: d.evidence.symbol, action: v.action, entry: v.entry };
    for (var i = 0; i < candles.length; i++) {
      var c = candles[i];
      var hitStop = dir === 1 ? c.l <= v.stop : c.h >= v.stop;
      var hitTake = dir === 1 ? c.h >= v.take_profit : c.l <= v.take_profit;
      if (hitStop || hitTake) {
        var exit = hitStop ? v.stop : v.take_profit;
        return Object.assign(base, { status: hitStop ? "STOP" : "TAKE", exit: exit, pnl: round((exit - v.entry) * dir * v.size, 4), bars: i + 1 });
      }
    }
    if (candles.length) {
      var mark = candles[candles.length - 1].c;
      return Object.assign(base, { status: "OPEN", exit: mark, pnl: round((mark - v.entry) * dir * v.size, 4), bars: candles.length });
    }
    return Object.assign(base, { status: "OPEN", exit: v.entry, pnl: 0, bars: 0 });
  }

  function portfolio(ledger) {
    var positions = [];
    // Only a sized LONG or SHORT is a position. FLAT, WATCH_REVERSAL, REDUCE_EXPOSURE and BLOCKED_STALE record a
    // decision not to enter, with no entry or size; treating them as positions produced NaN (same rule as portfolio.py).
    ledger.forEach(function (d, i) { if (d.verdict.action === "LONG" || d.verdict.action === "SHORT") positions.push(resolve(d, laterCandles(ledger, i))); });
    var closed = positions.filter(function (p) { return p.status !== "OPEN"; });
    var wins = closed.filter(function (p) { return p.pnl > 0; });
    var equity = START_CAPITAL, peak = START_CAPITAL, maxDd = 0;
    closed.forEach(function (p) {
      equity += p.pnl;
      peak = Math.max(peak, equity);
      maxDd = Math.max(maxDd, (peak - equity) / peak * 100);
    });
    var sum = function (list) { return list.reduce(function (a, p) { return a + p.pnl; }, 0); };
    return {
      decisions: ledger.length,
      flat: ledger.length - positions.length,
      positions: positions,
      closed: closed.length,
      open: positions.length - closed.length,
      win_rate_pct: closed.length ? round(wins.length / closed.length * 100, 1) : null,
      realized_pnl: round(sum(closed), 4),
      unrealized_pnl: round(sum(positions.filter(function (p) { return p.status === "OPEN"; })), 4),
      max_drawdown_pct: round(maxDd, 2),
      start_capital: START_CAPITAL
    };
  }

  function cadence(ledger) {
    var bySymbol = {}, all = [];
    ledger.forEach(function (d) {
      var t = Date.parse(d.evidence.collected_at);
      if (isNaN(t)) return;
      (bySymbol[d.evidence.symbol] = bySymbol[d.evidence.symbol] || []).push(t);
      all.push(t);
    });
    var longest = 0, longestSymbol = null, gapsOver = 0, counts = {};
    Object.keys(bySymbol).forEach(function (sym) {
      var times = bySymbol[sym].sort(function (a, b) { return a - b; });
      counts[sym] = times.length;
      for (var i = 1; i < times.length; i++) {
        var gap = times[i] - times[i - 1];
        if (gap > GAP_LIMIT_MS) gapsOver++;
        if (gap > longest) { longest = gap; longestSymbol = sym; }
      }
    });
    all.sort(function (a, b) { return a - b; });
    var hours = {};
    all.forEach(function (t) { hours[Math.floor(t / HOUR_MS)] = true; });
    var withRecords = Object.keys(hours).length;
    var span = all.length ? Math.floor(all[all.length - 1] / HOUR_MS) - Math.floor(all[0] / HOUR_MS) + 1 : 0;
    return {
      total: ledger.length,
      per_symbol: counts,
      first: all.length ? new Date(all[0]).toISOString() : null,
      last: all.length ? new Date(all[all.length - 1]).toISOString() : null,
      window_hours: all.length ? round1((all[all.length - 1] - all[0]) / HOUR_MS) : 0,
      window_days: all.length ? round1((all[all.length - 1] - all[0]) / (24 * HOUR_MS)) : 0,
      longest_gap_minutes: Math.round(longest / 60000),
      longest_gap_symbol: longestSymbol,
      gaps_over_90_minutes: gapsOver,
      clock_hours_with_records: withRecords,
      clock_hours_in_window: span,
      coverage_pct: span ? round1(withRecords / span * 100) : 0
    };
  }

  /* What the citation validator did with the LLM analysts' claims from firstSeq on (same rule as livestats.py). */
  function llmEra(ledger, firstSeq, lastSeq) {
    firstSeq = firstSeq || FIRST_LLM_SEQ;
    lastSeq = lastSeq || null;
    var era = ledger.filter(function (d) { return d.seq >= firstSeq && (lastSeq === null || d.seq <= lastSeq); });
    var sides = { bull: { accepted: 0, rejected: 0, errors: 0 }, bear: { accepted: 0, rejected: 0, errors: 0 } };
    var reasons = {}, without = [], models = {};
    era.forEach(function (d) {
      var layer = d.llm;
      if (!layer || !Object.keys(layer).length) { without.push(d.seq); return; }
      if (layer.model) models[layer.model] = true;
      ["bull", "bear"].forEach(function (side) {
        var part = layer[side] || {};
        if (part.error) { sides[side].errors++; return; }
        sides[side].accepted += (part.accepted || []).length;
        (part.rejected || []).forEach(function (c) {
          sides[side].rejected++;
          var reason = String(c.rejected_because || "unknown").split(":")[0];
          reasons[reason] = (reasons[reason] || 0) + 1;
        });
      });
    });
    var accepted = sides.bull.accepted + sides.bear.accepted, rejected = sides.bull.rejected + sides.bear.rejected;
    return {
      first_seq: firstSeq,
      last_seq: lastSeq,
      dossiers: era.length,
      dossiers_with_analysts: era.length - without.length,
      dossiers_without_llm: without,
      models: Object.keys(models).sort(),
      accepted: accepted,
      rejected: rejected,
      accepted_pct: accepted + rejected ? round1(accepted / (accepted + rejected) * 100) : null,
      sides: sides,
      rejection_reasons: reasons
    };
  }

  function narrative(ledger) {
    var headlines = 0, voices = {}, convs = 0, tokens = {}, rejected = 0, withNarrative = 0;
    ledger.forEach(function (d) {
      var n = d.evidence && d.evidence.narrative;
      if (!n) return;
      withNarrative++;
      (n.voices || []).forEach(function (v) { headlines++; if (v.name) voices[v.name] = true; });
      (n.convergence || []).forEach(function (c) { convs++; if (c.token) tokens[c.token] = true; });
      rejected += (n.rejected_candidates || []).length;
    });
    return { dossiers: withNarrative, headlines: headlines, voices: Object.keys(voices).length,
             convergences: convs, tokens: Object.keys(tokens).sort(), rejected: rejected };
  }

  var api = { START_CAPITAL: START_CAPITAL, FIRST_LLM_SEQ: FIRST_LLM_SEQ, portfolio: portfolio, cadence: cadence, llmEra: llmEra, resolve: resolve, narrative: narrative };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.VerdictStats = api;
})(typeof self !== "undefined" ? self : this);
