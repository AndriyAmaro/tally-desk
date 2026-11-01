/* Confianca por voz calculada no navegador (global VerdictTrust) e no Node (module.exports).
 *
 * E o mesmo calculo de verdict_desk/trust.py, escrito de novo para que quem abre a pagina nao
 * precise acreditar num numero do servidor: o navegador le o ledger bruto e refaz a nota de cada
 * voz. tests/test_trust_parity.py compara os dois lados no ledger publicado e num mundo sintetico.
 *
 * Cada chamada e uma extracao direcional aceita (voz, citacao literal), contada na PRIMEIRA vez que o
 * ledger a viu. Resultado: retorno do par entre a abertura da primeira vela depois da chamada e a
 * abertura da vela HORIZON_HOURS depois, lido das velas congeladas. O acerto e comparado com o acerto
 * esperado so por sorte (vies da propria voz x para onde o mercado foi); o excesso e encolhido para
 * zero em amostra pequena. Nenhuma funcao aqui le nada alem da lista de dossiers recebida.
 */
(function (root) {
  "use strict";
  const HORIZON_HOURS = 4;
  const MIN_CALLS = 8;
  const PRIOR_CALLS = 4;
  const WEIGHT_MIN = 0.5;
  const WEIGHT_MAX = 1.5;
  const PAIRS = { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT" };

  const squash = (text) => String(text).replace(/\s+/g, " ").trim().toLowerCase();
  const round = (x, places) => { const f = 10 ** places; return Math.round(x * f) / f; };

  function calls(ledger) {
    const seen = new Set();
    const out = [];
    for (const d of ledger) {
      const ev = d.evidence || {};
      const nar = ev.narrative || {};
      if (!ev.collected_at) continue;
      const at = Date.parse(ev.collected_at);
      for (const x of nar.extractions || []) {
        const name = x.name, quote = x.quote;
        if (!name || !quote || (x.stance !== "bullish" && x.stance !== "bearish")) continue;
        const key = name + "\u0000" + squash(quote);
        if (seen.has(key)) continue;
        seen.add(key);
        for (const token of x.tokens || []) {
          if (Object.prototype.hasOwnProperty.call(PAIRS, token)) out.push({ voice: name, token, stance: x.stance, at });
        }
      }
    }
    return out;
  }

  function priceBook(ledger) {
    const raw = {};
    for (const sym of Object.values(PAIRS)) raw[sym] = new Map();
    for (const d of ledger) {
      const ev = d.evidence || {};
      const sym = ev.symbol;
      if (!raw[sym]) continue;
      for (const candle of (ev.market && ev.market.candles) || []) raw[sym].set(Number(candle.t), Number(candle.o));
    }
    const book = {};
    for (const [sym, byTime] of Object.entries(raw)) {
      const times = [...byTime.keys()].sort((a, b) => a - b);
      book[sym] = { times, opens: times.map((t) => byTime.get(t)) };
    }
    return book;
  }

  function openAtOrAfter(book, symbol, ms) {
    const { times, opens } = book[symbol];
    let lo = 0, hi = times.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < ms) lo = mid + 1; else hi = mid; }
    return lo < times.length ? opens[lo] : null;
  }

  function settled(ledger) {
    const book = priceBook(ledger);
    const horizon = HORIZON_HOURS * 3600000;
    const done = [];
    let pending = 0;
    for (const call of calls(ledger)) {
      const sym = PAIRS[call.token];
      const entry = openAtOrAfter(book, sym, call.at);
      const out = openAtOrAfter(book, sym, call.at + horizon);
      if (entry === null || out === null || entry <= 0) { pending++; continue; }
      const ret = out / entry - 1;
      const sign = call.stance === "bullish" ? 1 : -1;
      done.push({ ...call, ret, hit: ret * sign > 0, signed: ret * sign });
    }
    return { done, pending };
  }

  function weightFor(n, excess) {
    if (n < MIN_CALLS) return 1.0;
    const skill = excess / (n + PRIOR_CALLS);
    return round(Math.min(WEIGHT_MAX, Math.max(WEIGHT_MIN, 1.0 + 2.0 * skill)), 3);
  }

  function compute(ledger) {
    const { done, pending } = settled(ledger);
    const byVoice = {};
    for (const c of done) (byVoice[c.voice] = byVoice[c.voice] || []).push(c);
    const voices = {};
    for (const name of Object.keys(byVoice).sort()) {
      const rows = byVoice[name];
      const n = rows.length;
      const hits = rows.filter((r) => r.hit).length;
      const bullShare = rows.filter((r) => r.stance === "bullish").length / n;
      const expected = rows.reduce((acc, r) => acc + (r.ret > 0 ? bullShare : 1 - bullShare), 0);
      const excess = hits - expected;
      voices[name] = {
        n, hits,
        hit_rate: round(hits / n, 4),
        chance_rate: round(expected / n, 4),
        skill: round(excess / (n + PRIOR_CALLS), 4),
        bull_share: round(bullShare, 4),
        mean_signed_bps: round(rows.reduce((acc, r) => acc + r.signed, 0) / n * 10000, 2),
        weight: weightFor(n, excess),
        proven: n >= MIN_CALLS,
      };
    }
    return {
      horizon_hours: HORIZON_HOURS,
      min_calls: MIN_CALLS,
      as_of_seq: ledger.length ? ledger[ledger.length - 1].seq : 0,
      pending_calls: pending,
      voices,
    };
  }

  /* The weight a convergence carries as an argument. Rules v4: 0.5 per distinct voice, at most four.
   * Rules v5 (trustWeighted): 0.5 x each voice's track-record weight, the four heaviest. Plus 0.5 when
   * the top conviction is high. A mirror of `agents_v2.convergence_args`. */
  function convergenceWeight(weights, highConviction, trustWeighted) {
    const list = trustWeighted ? fsum([...weights].sort((a, b) => b - a).slice(0, 4)) : Math.min(weights.length, 4);
    return roundHalfEven(list * 0.5 + (highConviction ? 0.5 : 0), 2);
  }

  /* Exactly rounded sum (Shewchuk), the same as Python's math.fsum: the order and the width of the
   * floats cannot change the result, so the browser and the Python judge cannot disagree by an ulp. */
  function fsum(values) {
    const partials = [];
    for (let x of values) {
      let i = 0;
      for (let y of partials) {
        if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x];
        const hi = x + y;
        const lo = y - (hi - x);
        if (lo) partials[i++] = lo;
        x = hi;
      }
      partials.length = i;
      partials.push(x);
    }
    return partials.reduce((a, b) => a + b, 0);
  }

  /* Python's round(): the exact binary value decides, and an exact tie goes to the even digit. A plain
   * Math.round would put 1.835 (really 1.83499...) at 1.84 and 0.625 (an exact tie) at 0.63. */
  function roundHalfEven(x, places) {
    const long = x.toFixed(places + 20);
    const dot = long.indexOf(".");
    const tail = long.slice(dot + 1 + places);
    if (/^50*$/.test(tail)) {
      const kept = long.slice(0, dot + 1 + places);
      const lastDigit = Number(kept.replace(/[^0-9]/g, "").slice(-1));
      if (lastDigit % 2 === 0) return Number(kept);
      return Number((Number(kept) + (x < 0 ? -1 : 1) * 10 ** -places).toFixed(places));
    }
    return Number(x.toFixed(places));
  }

  const api = { compute, calls, settled, weightFor, convergenceWeight, HORIZON_HOURS, MIN_CALLS };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.VerdictTrust = api;
})(typeof window !== "undefined" ? window : globalThis);
