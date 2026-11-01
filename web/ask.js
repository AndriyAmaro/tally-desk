/* Ask the record: a conversation with one recorded decision that cannot make things up.
 *
 * No model runs here. Every answer is built from the frozen dossier: each sentence is a template filled with
 * its fields, and each field used is cited with the exact text it came from. Words a model wrote when the
 * decision was recorded (analyst claims, the judge's reason) are shown as quotes, labelled, never rewritten.
 * `check` then applies the citation validator's rule to the answer itself: every number in the answer's own
 * text must appear in one of its cited sources, or the answer is refused. tests/test_ask.py runs that check on
 * every question for every dossier of the published ledger.
 *
 * Pure data in, pure data out. No DOM, no network.
 */
(function (root, factory) {
  const node = typeof module === "object" && module.exports;
  const api = factory(node ? require("./chart.js") : root.VerdictChart);
  if (node) module.exports = api;
  else root.VerdictAsk = api;
})(typeof self !== "undefined" ? self : this, function (chart) {
  const NUMBER = /-?\d+(?:\.\d+)?/g;

  const SUGGESTIONS = [
    { intent: "decision", label: "What did you decide?" },
    { intent: "why", label: "Why?" },
    { intent: "chart", label: "What does the chart show?" },
    { intent: "flip", label: "What would change your mind?" },
    { intent: "risk", label: "How much could it lose?" },
    { intent: "outcome", label: "What happened next?" },
    { intent: "analysts", label: "What did the AI analysts claim?" },
    { intent: "refused", label: "What did you refuse?" },
    { intent: "judge", label: "What did the AI judge think?" },
    { intent: "model", label: "Which model wrote this, and why that one?" },
    { intent: "voices", label: "What did the market voices say?" },
    { intent: "saw", label: "What data did you see?" },
    { intent: "executed", label: "Was it sent on-chain?" },
    { intent: "proof", label: "Can I trust this record?" },
    { intent: "help", label: "What can I ask?" },
  ];
  const INTENTS = SUGGESTIONS.map((s) => s.intent);
  const TITLE = Object.fromEntries(SUGGESTIONS.map((s) => [s.intent, s.label]));

  // Most specific first: "why did the judge lean bear" is about the judge, not a generic why.
  const ROUTES = [
    ["refused", /\b(refus|reject|discard|threw|thrown|invalid|recus|rejeit|descart)/i],
    ["judge", /\b(judge|ruling|lean|conviction|juiz|veredito do juiz)/i],
    ["analysts", /\b(analyst|claim|bull case|bear case|llm|ai said|analista|afirma)/i],
    ["model", /(which model|what model|who wrote|routing|routed|\bmodel (wrote|answered)|qual modelo|que modelo)/i],
    ["executed", /(execut|on-?chain|sepolia|transaction|\btx\b|swap|testnet|blockchain|transa[cç])/i],
    ["flip", /\b(flip|change (your|its) mind|would change|what if|without|sensitiv|removed|mudaria|mudar de ideia|virar)/i],
    ["outcome", /\b(happen|next|afterwards|did it work|work out|result|outcome|hit the|profit|pnl|won\b|aconteceu|depois|deu certo|resultado)/i],
    ["chart", /(chart|graph|candle|\bema\b|moving average|bollinger|band|\brsi\b|indicator|gr[aá]fico|vela)/i],
    ["voices", /\b(voice|telegram|news|people|influencer|kol|saying|sentiment|narrative|vozes|not[ií]cia)/i],
    ["saw", /\b(see|saw|source|data|evidence|where does|fresh|stale|gap|fonte|dados|viu)/i],
    ["risk", /\b(risk|stop|target|take profit|size|lose|loss|position|entry|risco|perd|alvo|tamanho|posi[cç])/i],
    ["proof", /\b(trust|proof|prove|verify|hash|fingerprint|tamper|changed|edited|real\b|genuine|confi|prova|verific|adulter)/i],
    ["why", /\b(why|reason|because|argument|por ?qu[eê]|motivo|raz[aã]o)/i],
    ["decision", /\b(decide|decision|verdict|action|what did (you|it) do|long or short|decid|decis[aã]o|a[cç][aã]o)/i],
    ["help", /\b(help|what can (i|you)|options|ajuda|o que posso)/i],
  ];

  function route(question) {
    const q = String(question || "");
    for (const [intent, pattern] of ROUTES) if (pattern.test(q)) return intent;
    return null;
  }

  // ---------------------------------------------------------------- the self-check
  function key(n) {
    const x = Number.parseFloat(n);
    if (!Number.isFinite(x)) return n;
    return Object.is(x, -0) ? "-0" : String(Math.round(x * 1e6) / 1e6);
  }

  /** The citation validator's rule, applied to the answer: every number in its own text must be in a cited source. */
  function check(answer) {
    const allowed = new Set();
    for (const c of answer.cites || []) for (const n of String(c.source).match(NUMBER) || []) allowed.add(key(n));
    const stray = [];
    for (const p of answer.parts || []) {
      if (p.kind === "quote") continue; // a model's own words, recorded and validated when the decision was made
      for (const n of String(p.text).match(NUMBER) || []) if (!allowed.has(key(n))) stray.push(n);
    }
    return { ok: stray.length === 0, stray };
  }

  // ---------------------------------------------------------------- building an answer
  function builder(d, intent) {
    const parts = [];
    const cites = [];
    const cite = (ref, label, value) => {
      const source = typeof value === "string" ? value : JSON.stringify(value);
      if (!cites.some((c) => c.ref === ref)) cites.push({ ref, label, source });
      return ref;
    };
    return {
      cite,
      say(text, refs) { parts.push({ kind: "text", text, refs: refs || [] }); },
      quote(text, label, refs) { parts.push({ kind: "quote", text: String(text), label, refs: refs || [] }); },
      done() {
        const marks = chart.marksFor(parts.flatMap((p) => p.refs || []), d);
        return { intent, title: TITLE[intent] || "", seq: d.seq, parts, cites, marks, suggestions: SUGGESTIONS.filter((s) => s.intent !== intent) };
      },
    };
  }

  const has = (x) => x !== undefined && x !== null;

  /** "2026-10-26 at 18:20 UTC" from the recorded ISO time; any other shape is shown as recorded. */
  function when(iso) {
    const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|\+00:00)$/.exec(String(iso));
    return m ? `${m[1]} at ${m[2]} UTC` : String(iso);
  }
  const ACTION_WORDS = {
    LONG: "went LONG (a simulated buy)",
    SHORT: "went SHORT (a simulated sale, betting on a fall)",
    WATCH_REVERSAL: "did not open a position (WATCH_REVERSAL: the sides were close and one argument could flip it)",
    REDUCE_EXPOSURE: "did not open a position (REDUCE_EXPOSURE: both sides strong while volatility was high)",
    FLAT: "did not open a position (FLAT: neither side was strong enough)",
    BLOCKED_STALE: "refused to decide (BLOCKED_STALE: the newest price was too old to trust)",
  };

  function decision(d, b) {
    const v = d.verdict || {};
    const ev = d.evidence || {};
    b.say(`Record ${d.seq}: on ${when(ev.collected_at)} the desk looked at ${ev.symbol} and ${ACTION_WORDS[v.action] || `recorded ${v.action}`}.`,
      [b.cite("seq", "record number", String(d.seq)), b.cite("evidence.collected_at", "when it looked", String(ev.collected_at))]);
    if (has(v.bull_score) && has(v.bear_score) && has(v.edge)) {
      const refs = [b.cite("verdict.bull_score", "bull score", v.bull_score), b.cite("verdict.bear_score", "bear score", v.bear_score), b.cite("verdict.edge", "edge", v.edge)];
      let line = `The arguments for a rise weighed ${v.bull_score} and those for a fall ${v.bear_score}, an edge of ${v.edge}`;
      if (has(v.threshold)) {
        refs.push(b.cite("verdict.threshold", "entry band", v.threshold));
        line += `; a position needs at least ${v.threshold} either way`;
      }
      b.say(line + ".", refs);
    }
    if (has(v.rules_version)) b.say(`It decided under rules ${v.rules_version}, frozen inside the record.`, [b.cite("verdict.rules_version", "rules", String(v.rules_version))]);
    b.say("Simulated only: no real order was placed.");
  }

  function arguments_(d) {
    return [...(d.bull || []).map((a, i) => ({ ...a, side: "bull", i })), ...(d.bear || []).map((a, i) => ({ ...a, side: "bear", i }))];
  }

  function why(d, b) {
    const v = d.verdict || {};
    const args = arguments_(d).sort((x, y) => y.weight - x.weight);
    if (!args.length) {
      b.say("No argument fired on either side in this record.");
    } else {
      b.say(`It ${ACTION_WORDS[v.action] || `recorded ${v.action}`}. The heaviest arguments:`);
      for (const a of args.slice(0, 4)) {
        const ref = b.cite(`${a.side}[${a.i}]`, `${a.side} argument`, `${a.claim} ${a.weight}`);
        b.say(`${a.side}: "${a.claim}", weight ${a.weight}.`, [ref]);
      }
    }
    if (has(v.bull_score) && has(v.bear_score)) {
      b.say(`In total, bull ${v.bull_score} against bear ${v.bear_score}.`, [b.cite("verdict.bull_score", "bull score", v.bull_score), b.cite("verdict.bear_score", "bear score", v.bear_score)]);
    }
    const sens = d.sensitivity;
    if (!sens || !sens.drop_one) {
      b.say("This record predates the sensitivity table, so it does not say which single argument decided it.");
      return;
    }
    const flips = sens.drop_one.filter((x) => x.flips);
    if (!flips.length) {
      b.say(`No single argument decides it: removing any one of them still leaves ${v.action}.`);
    } else {
      const f = flips[0];
      b.say(`The one that decided it: "${f.claim}" (weight ${f.weight}). Without it the edge would be ${f.edge_without} and the verdict ${f.action_without}.`,
        [b.cite("sensitivity.flip", "deciding argument", `${f.claim} ${f.weight} ${f.edge_without} ${f.action_without}`)]);
    }
  }

  function flip(d, b) {
    const sens = d.sensitivity;
    const v = d.verdict || {};
    if (!sens || !has(sens.needs_for_long)) {
      b.say("This record predates the sensitivity table (no sensitivity data), so it cannot say what would have changed the verdict.");
      return;
    }
    const refs = [b.cite("sensitivity.needs_for_long", "needed for a LONG", sens.needs_for_long), b.cite("sensitivity.needs_for_short", "needed for a SHORT", sens.needs_for_short)];
    b.say(`To open a LONG the bull side would have needed ${sens.needs_for_long} more net weight; to open a SHORT the bear side ${sens.needs_for_short} more (zero means it was already there).`, refs);
    const flips = (sens.drop_one || []).filter((x) => x.flips);
    if (!flips.length) b.say(`No single argument, removed on its own, changes ${v.action}.`);
    for (const f of flips.slice(0, 4)) {
      b.say(`Remove "${f.claim}" (${f.side}, weight ${f.weight}) and the edge becomes ${f.edge_without}: ${f.action_without}.`,
        [b.cite(`drop_one:${f.side}:${f.claim}`, "argument removed", `${f.claim} ${f.weight} ${f.edge_without} ${f.action_without}`)]);
    }
  }

  function risk(d, b) {
    const v = d.verdict || {};
    const p = v.policy || {};
    if (!has(v.entry)) {
      const refs = [];
      let line = `No position was opened (${v.action}), so there is no entry, stop, target or size`;
      if (has(p.max_risk_pct)) {
        refs.push(b.cite("verdict.policy.max_risk_pct", "risk limit", p.max_risk_pct));
        line += `. The risk limit in force was ${p.max_risk_pct}% of the simulated capital per position`;
      }
      b.say(line + ".", refs);
      return;
    }
    b.say(`Entry ${v.entry}, stop ${v.stop}, target ${v.take_profit}, size ${v.size} ${d.evidence.symbol}.`,
      [b.cite("verdict.entry", "entry", v.entry), b.cite("verdict.stop", "stop", v.stop), b.cite("verdict.take_profit", "target", v.take_profit), b.cite("verdict.size", "size", v.size)]);
    if (has(v.risk_pct)) {
      const refs = [b.cite("verdict.risk_pct", "risk per position", v.risk_pct)];
      let line = `The size is set so that if the stop is hit the simulated loss is exactly ${v.risk_pct}% of the simulated capital`;
      if (has(v.system_max_risk_pct)) {
        refs.push(b.cite("verdict.system_max_risk_pct", "system cap", v.system_max_risk_pct));
        line += `; a user may lower that limit, never raise it above ${v.system_max_risk_pct}%`;
      }
      b.say(line + ".", refs);
    }
    if (has(p.stop_atr) && has(p.take_atr)) {
      b.say(`The stop sits ${p.stop_atr} ATR from the entry and the target ${p.take_atr} ATR away, by the rules frozen in the record.`,
        [b.cite("verdict.policy.stop_atr", "stop rule", p.stop_atr), b.cite("verdict.policy.take_atr", "target rule", p.take_atr)]);
    }
    b.say("Simulated only: no real order was placed.");
  }

  function analysts(d, b) {
    const llm = d.llm;
    if (!llm) {
      b.say("This record has no AI analyst layer: the decision rests on the fixed rules alone.");
      return;
    }
    const items = llm.evidence_index || {};
    for (const side of ["bull", "bear"]) {
      const block = llm[side] || {};
      if (block.error) {
        b.say(`The ${side} analyst failed for this record, so it made no claim. The provider's error is recorded:`);
        b.quote(block.error, "provider error, recorded");
        continue;
      }
      const kept = block.accepted || [];
      const refused = block.rejected || [];
      const who = block.served_by ? [b.cite(`llm.${side}.served_by`, "model that answered", block.served_by)] : [];
      b.say(`The ${side} analyst made ${kept.length + refused.length} claims: ${kept.length} passed the citation check and ${refused.length} were refused${block.served_by ? ` (answered by ${block.served_by})` : ""}.`,
        [b.cite(`llm.${side}.counts`, `${side} claims kept and refused`, `${kept.length + refused.length} ${kept.length} ${refused.length}`), ...who]);
      for (const c of kept.slice(0, 4)) {
        const refs = (c.cites || []).filter((e) => typeof e === "string" && items[e]).map((e) => b.cite(e, `evidence ${e}`, items[e]));
        b.quote(c.claim, `${side} analyst (LLM), passed the check`, refs);
      }
    }
  }

  function refused(d, b) {
    const llm = d.llm;
    if (!llm) {
      b.say("This record has no AI analyst layer, so nothing could be refused.");
      return;
    }
    let any = false;
    for (const side of ["bull", "bear"]) {
      for (const r of ((llm[side] || {}).rejected || []).slice(0, 6)) {
        any = true;
        const reason = r.rejected_because || r.reason || "no valid citation";
        b.quote(r.claim, `${side} analyst (LLM), refused`);
        b.say(`Refused because: ${reason}.`, [b.cite(`refused:${side}:${r.claim}`, "refusal reason", reason)]);
      }
    }
    const j = llm.judge;
    if (j && j.ruling && !j.accepted && j.rejected_because) {
      any = true;
      b.say(`The AI judge's ruling was voided too: ${j.rejected_because}.`, [b.cite("llm.judge.rejected_because", "why the ruling was voided", j.rejected_because)]);
    }
    if (!any) b.say("Nothing was refused in this record: every claim the analysts made passed the citation check.");
  }

  function judge(d, b) {
    const v = d.verdict || {};
    const j = d.llm && d.llm.judge;
    if (!j) {
      if (v.rules_version === "v6") {
        b.say("This record has no AI judge ruling: the AI layer was off or failed for it, so the fixed rules decided alone.",
          [b.cite("verdict.rules_version", "rules", String(v.rules_version))]);
      } else {
        b.say(`This record has no AI judge: it was decided under rules ${v.rules_version}, before the judge existed (rules v6), so the fixed rules decided alone.`,
          [b.cite("verdict.rules_version", "rules", `${v.rules_version} v6`)]);
      }
      return;
    }
    const weights = (v.policy || {}).llm_judge_weights;
    const cap = weights ? Math.max(...Object.values(weights)) : null;
    const who = j.served_by ? [b.cite("llm.judge.served_by", "model that answered", j.served_by)] : [];
    const r = j.ruling;
    if (!r) {
      const why = j.rejected_because || j.error || "no ruling";
      b.say(`The AI judge did not rule on this record (${why}), so it added nothing.`, [b.cite("llm.judge.why", "why there is no ruling", why)]);
      return;
    }
    const relied = r.relied_on || [];
    const base = [b.cite("llm.judge.ruling.relied_on", "claims it relied on", relied), ...who];
    if (j.accepted && j.weight > 0) {
      const refs = [...base, b.cite("llm.judge.weight", "weight of the vote", j.weight)];
      let line = `The AI judge read only the claims that passed and leaned ${r.lean} with ${r.conviction} conviction, relying on ${relied.join(", ")}. Under rules v6 that adds ${j.weight} to the ${r.lean} side`;
      if (cap !== null && has(v.threshold)) {
        refs.push(b.cite("verdict.policy.llm_judge_weights", "judge weight table", weights), b.cite("verdict.threshold", "entry band", v.threshold), b.cite("verdict.rules_version", "rules", String(v.rules_version)));
        line += `; its vote is capped at ${cap}, below the entry band of ${v.threshold}, so it can never open a position on its own`;
      }
      b.say(line + (j.served_by ? ` (answered by ${j.served_by}).` : "."), refs);
    } else if (j.accepted) {
      b.say(`The AI judge read the claims that passed, found the two cases balanced (${r.lean}), and added nothing.`, base);
    } else {
      const why = j.rejected_because || "it did not hold";
      b.say(`The AI judge leaned ${r.lean}, but its ruling was voided (${why}), so it added nothing.`, [...base, b.cite("llm.judge.rejected_because", "why the ruling was voided", why)]);
    }
    if (r.reason) b.quote(r.reason, `the judge's own words (LLM)${j.accepted ? ", checked when recorded" : ", voided"}`);
  }

  function proof(d, b, ctx) {
    const recomputed = ctx && ctx.recomputedHash;
    const refs = [b.cite("hash", "recorded fingerprint", d.hash)];
    if (!recomputed) {
      b.say(`The recorded fingerprint is ${d.hash}. The page has not recomputed it yet.`, refs);
    } else if (recomputed === d.hash) {
      b.say(`The fingerprint recomputed in your browser from this record's contents just now matches the recorded one: ${d.hash}. Nobody has changed this record since it was written.`, refs);
    } else {
      refs.push(b.cite("recomputed", "recomputed fingerprint", recomputed));
      b.say(`The fingerprint recomputed in your browser, ${recomputed}, does NOT match the recorded one, ${d.hash}: this copy was changed after it was written. Do not rely on it.`, refs);
    }
    if (d.prev_hash) b.say(`It is chained to the record before it, whose fingerprint is ${d.prev_hash}; changing any earlier record breaks that link.`, [b.cite("prev_hash", "previous fingerprint", d.prev_hash)]);
    b.say("To check that this is the record we published, and when: the Witnesses page compares the chain's head with Bitcoin, the Sigstore log and the Sepolia anchor, none of which we control.");
  }

  function help(d, b) {
    b.say("I answer only from this record: what it decided, why, what the chart shows, what would have changed it, the risk it took, what happened next, what the AI analysts claimed, what was refused, what the AI judge thought, what the market voices said, what data it saw, whether it was sent to Sepolia testnet, and whether you can trust the record. Every number I give points to the field it came from, and this page checks it before showing it.");
  }

  // ---------------------------------------------------------------- phase 2: chart, what happened next, data, voices, Sepolia

  /** Python's round() (web/trust.js and cre/anchor/verdict.ts carry the same port). */
  function pyRound(x, places) {
    const long = x.toFixed(places + 20);
    const dot = long.indexOf(".");
    if (/^50*$/.test(long.slice(dot + 1 + places))) {
      const kept = long.slice(0, dot + 1 + places);
      if (Number(kept.replace(/[^0-9]/g, "").slice(-1)) % 2 === 0) return Number(kept);
      return Number((Number(kept) + (x < 0 ? -1 : 1) * 10 ** -places).toFixed(places));
    }
    return Number(x.toFixed(places));
  }

  /** portfolio.resolve, line for line: the first later candle touching the stop or the target closes it; the stop wins a tie. */
  function outcome(d, later) {
    const v = d.verdict || {};
    if (!["LONG", "SHORT"].includes(v.action) || !has(v.entry)) return null;
    const dir = v.action === "LONG" ? 1 : -1;
    const base = { seq: d.seq, symbol: d.evidence.symbol, action: v.action, entry: v.entry };
    for (let i = 0; i < later.length; i++) {
      const c = later[i];
      const hitStop = dir === 1 ? c.l <= v.stop : c.h >= v.stop;
      const hitTake = dir === 1 ? c.h >= v.take_profit : c.l <= v.take_profit;
      if (hitStop || hitTake) {
        const exit = hitStop ? v.stop : v.take_profit;
        return { ...base, status: hitStop ? "STOP" : "TAKE", exit, pnl: pyRound((exit - v.entry) * dir * v.size, 4), bars: i + 1, candle: c };
      }
    }
    if (later.length) {
      const mark = later[later.length - 1].c;
      return { ...base, status: "OPEN", exit: mark, pnl: pyRound((mark - v.entry) * dir * v.size, 4), bars: later.length, candle: later[later.length - 1] };
    }
    return { ...base, status: "OPEN", exit: v.entry, pnl: 0, bars: 0, candle: null };
  }

  const signed = (x) => (x >= 0 ? `+${x}` : String(x));

  function outcome_(d, b, ctx) {
    const v = d.verdict || {};
    const o = outcome(d, ctx.later || []);
    if (!o) {
      b.say(`No position was opened (${v.action}), so there is nothing to follow.`);
      return;
    }
    if (!ctx.later) {
      b.say("The candles recorded after this decision have not been loaded on this page yet, so I cannot say.");
      return;
    }
    if (o.bars === 0) {
      b.say(`No later candle of ${d.evidence.symbol} has been recorded yet: the position is still open, at its entry.`);
      return;
    }
    const levels = [b.cite("verdict.entry", "entry", v.entry), b.cite("verdict.stop", "stop", v.stop), b.cite("verdict.take_profit", "target", v.take_profit)];
    const candle = b.cite("outcome.exit", o.status === "OPEN" ? "last later candle" : "candle that closed it",
      `bars ${o.bars} ${when(new Date(o.candle.t).toISOString())} ${JSON.stringify(o.candle)} exit ${o.exit}`);
    const result = b.cite("outcome.result", "simulated result, computed", `(${o.exit} - ${v.entry}) x ${v.size} ${v.action} = ${o.pnl}`);
    const later = b.cite("outcome.later", "candles after the decision", `${ctx.later.length} later candles of ${d.evidence.symbol}, frozen in later records`);
    if (o.status === "OPEN") {
      b.say(`In the ${o.bars} candles recorded since, the price touched neither the stop ${v.stop} nor the target ${v.take_profit}. Marked at the last close, ${o.exit}, the open simulated result is ${signed(o.pnl)} before fees.`, [...levels, candle, result, later]);
    } else {
      const what = o.status === "TAKE" ? `reached the target ${v.take_profit}` : `hit the stop ${v.stop}`;
      b.say(`${o.bars} candle${o.bars === 1 ? "" : "s"} later, on ${when(new Date(o.candle.t).toISOString())}, the price ${what}. The simulated position closed at ${o.exit}, a result of ${signed(o.pnl)} before fees.`, [...levels, candle, result, later]);
    }
    b.say("The candles after the decision are the ones later records froze, the same the scoreboard uses; when a candle touches both levels, the stop is counted first.");
  }

  // The constants of verdict_desk/features_v2.py, so every number the chart answer names has a source.
  const CHART_METHOD = "EMA 12 and EMA 48 over every close; Bollinger 20 candles, 2 standard deviations, %B 0 = lower band, 1 = upper band; range of the previous 24 candles; return over 24 candles; RSI 14; ATR 14";

  function chart_(d, b) {
    const f = d.features || {};
    const candles = d.evidence.market.candles;
    const first = when(new Date(candles[0].t).toISOString());
    b.say(`The chart shows the ${candles.length} candles of ${d.evidence.symbol} frozen in the record (${d.evidence.market.interval}), the last one opening on ${when(new Date(candles[candles.length - 1].t).toISOString())}.`,
      [b.cite("evidence.market.candles", "frozen candles", `${candles.length} ${d.evidence.market.interval} ${first} ${when(new Date(candles[candles.length - 1].t).toISOString())}`),
        b.cite("method", "how the indicators are defined", CHART_METHOD)]);
    if (has(f.last_close)) {
      const refs = [b.cite("features.last_close", "last close", f.last_close)];
      let line = `Last close ${f.last_close}`;
      if (has(f.range_high_24) && has(f.range_low_24)) {
        refs.push(b.cite("features.range_high_24", "24-candle high", f.range_high_24), b.cite("features.range_low_24", "24-candle low", f.range_low_24));
        line += `, inside the range of the previous 24 candles, ${f.range_low_24} to ${f.range_high_24}`;
      }
      b.say(line + ".", refs);
    }
    if (has(f.ema_fast) && has(f.ema_slow)) {
      b.say(`The fast average (EMA 12) is at ${f.ema_fast} and the slow one (EMA 48) at ${f.ema_slow}: the short-term trend is ${f.ema_fast > f.ema_slow ? "above" : "below"} the longer one.`,
        [b.cite("features.ema_fast", "fast average", f.ema_fast), b.cite("features.ema_slow", "slow average", f.ema_slow)]);
    }
    if (has(f.bb_upper) && has(f.bb_lower)) {
      const refs = [b.cite("features.bb_upper", "upper band", f.bb_upper), b.cite("features.bb_lower", "lower band", f.bb_lower)];
      let line = `The Bollinger bands run from ${f.bb_lower} to ${f.bb_upper}`;
      if (has(f.bb_pct_b)) { refs.push(b.cite("features.bb_pct_b", "position in the bands", f.bb_pct_b)); line += `; the close sits at ${f.bb_pct_b} of that width (0 is the lower band, 1 the upper)`; }
      b.say(line + ".", refs);
    }
    const extra = [];
    const refs = [];
    if (has(f.rsi_14)) { extra.push(`RSI ${f.rsi_14}`); refs.push(b.cite("features.rsi_14", "RSI", f.rsi_14)); }
    if (has(f.atr_14)) { extra.push(`average true range ${f.atr_14}`); refs.push(b.cite("features.atr_14", "ATR", f.atr_14)); }
    if (has(f.return_24)) { extra.push(`${f.return_24}% over 24 candles`); refs.push(b.cite("features.return_24", "24-candle return", f.return_24)); }
    if (extra.length) b.say(`Also recorded: ${extra.join(", ")}.`, refs);
    b.say("The averages and bands are redrawn from the candles in your browser; their last points equal the values above, which the record computed when it decided.");
  }

  function saw(d, b) {
    const ev = d.evidence || {};
    const m = ev.market || {};
    b.say(`Prices: ${(m.candles || []).length} candles of ${ev.symbol} (${m.interval}) from ${m.source}.`,
      [b.cite("evidence.market.source", "price source", `${m.source} ${(m.candles || []).length} ${m.interval}`)]);
    const fr = ev.freshness;
    if (fr && has(fr.newest_candle_age_minutes)) {
      b.say(`The newest candle was ${fr.newest_candle_age_minutes} minutes old, against a limit of ${fr.window_minutes}: ${fr.ok ? "fresh enough to decide" : `too old (${fr.reason})`}.`,
        [b.cite("evidence.freshness", "freshness check", fr)]);
    }
    if (ev.funding) {
      b.say(`Funding: the perpetual funding rate ${ev.funding.rate} on ${ev.funding.instrument}, from ${ev.funding.source}.`, [b.cite("evidence.funding", "funding", ev.funding)]);
    }
    if (ev.news) {
      const n = ev.news;
      if (n.error) b.say(`News: the news source answered with an error, recorded as such (${n.error}), so no headline counted.`, [b.cite("evidence.news.error", "news error", n.error)]);
      else b.say(`News: ${(n.articles || []).length} headlines from ${n.source} for "${n.query}".`, [b.cite("evidence.news", "news", `${(n.articles || []).length} ${n.source} ${n.query}`)]);
    }
    const nar = ev.narrative;
    if (nar && Array.isArray(nar.voices)) {
      b.say(`Market voices: ${nar.voices.length} recent posts and articles were read (ask about the voices for what they said).`, [b.cite("evidence.narrative.voices", "voices read", `${nar.voices.length}`)]);
    }
    const gaps = nar && nar.config && nar.config.gaps;
    if (gaps && Object.keys(gaps).length) b.say(`Gaps recorded: ${Object.keys(gaps).join(", ")}.`, [b.cite("evidence.narrative.config.gaps", "gaps", gaps)]);
    b.say(`All of it was collected on ${when(ev.collected_at)} and frozen in the record before the decision.`, [b.cite("evidence.collected_at", "when it looked", String(ev.collected_at))]);
  }

  function voices(d, b) {
    const nar = d.evidence && d.evidence.narrative;
    if (!nar || !Array.isArray(nar.voices)) {
      b.say("This record has no market voices: it was written before the desk read news and Telegram channels.");
      return;
    }
    b.say(`${nar.voices.length} posts and articles from news sites and Telegram channels were read${nar.method ? ` (read by ${nar.method === "llm" ? "a language model, with its extractions checked" : nar.method})` : ""}.`,
      [b.cite("evidence.narrative.voices", "voices read", `${nar.voices.length}`)]);
    const conv = nar.convergence || [];
    if (!conv.length) {
      b.say("No token drew the same view from several independent voices, so the voices added no argument.");
    }
    const trust = (nar.trust && nar.trust.voices) || {};
    for (const c of conv.slice(0, 4)) {
      const names = c.voices || [];
      const weighed = names.filter((n) => trust[n]).map((n) => `${n} ${trust[n].hits}/${trust[n].n}`);
      const refs = [b.cite(`convergence:${c.token}:${c.bias}`, `${c.token} ${c.bias}`, `${names.length} voices ${JSON.stringify(c)}`)];
      let line = `${c.token}: ${names.length} voices ${c.bias} (${names.join(", ")})${c.max_conviction ? `, conviction up to ${c.max_conviction}` : ""}`;
      if (weighed.length) {
        refs.push(b.cite(`trust:${c.token}:${c.bias}`, "their track record", weighed.join(" ")));
        line += `; their past calls that came true: ${weighed.join(", ")}`;
      }
      b.say(line + ".", refs);
    }
    const rejected = (nar.rejected || []).length;
    if (rejected) b.say(`${rejected} extractions were refused because the post did not support them.`, [b.cite("evidence.narrative.rejected", "extractions refused", `${rejected}`)]);
    if (nar.llm_error) {
      b.say("The model that reads the voices failed for this record; its error is recorded:");
      b.quote(nar.llm_error, "provider error, recorded");
    }
  }

  // The test tokens of cre/practice.sepolia.json: an on-chain amount is an integer count of the token's smallest unit.
  const TOKENS = { "0xfff9976782d46cc05630d1f6ebab18b2324d6b14": ["WETH", 18], "0x1c7d4b196cb0c7b01d743fbc6116a902379c7238": ["USDC", 6] };
  function units(raw, token) {
    const known = TOKENS[String(token).toLowerCase()];
    if (!known) return { text: `${raw} (raw units of ${token})`, source: `${raw} ${token}` };
    const [sym, dec] = known;
    const digits = BigInt(raw).toString().padStart(dec + 1, "0");
    const value = `${digits.slice(0, -dec)}.${digits.slice(-dec)}`.replace(/\.?0+$/, "");
    return { text: `${value} ${sym}`, source: `${raw} ${token} = ${value} ${sym} (${dec} decimals)` };
  }

  const STATUS = (s) => (s === "empty" ? "an empty answer" : s === 200 ? "an answer" : `HTTP ${s}`);

  function model_(d, b) {
    const llm = d.llm;
    if (!llm) {
      b.say("This record has no AI analyst layer: no model wrote any part of it, and the fixed rules decided alone.");
      return;
    }
    let policy = null;
    const names = { bull: "bull analyst", bear: "bear analyst", judge: "AI judge" };
    for (const part of ["bull", "bear", "judge"]) {
      const block = llm[part];
      if (!block || (!block.served_by && !block.route)) continue;
      const route = Array.isArray(block.route) ? block.route : null;
      if (!route) {
        b.say(`The ${names[part]} was answered by ${block.served_by}, recorded before the gateway logged its route, so the record does not say which models were tried first.`,
          [b.cite(`llm.${part}.served_by`, "model that answered", block.served_by)]);
        continue;
      }
      const steps = route.map((a) => `${a.model} gave ${STATUS(a.status)} in ${a.ms} ms`).join("; then ");
      const refs = [b.cite(`llm.${part}.route`, `${names[part]} route`, route.map((a) => `${a.model} ${a.status} ${a.ms}`).join(" "))];
      if (block.routing_policy) {
        policy = block.routing_policy;
        refs.push(b.cite(`llm.${part}.routing_policy`, "routing policy", `sha256 ${policy.sha256} as_of_seq ${policy.as_of_seq}`));
      }
      if (block.served_by) refs.push(b.cite(`llm.${part}.served_by`, "model that answered", block.served_by));
      b.say(`The ${names[part]}: ${steps}${block.served_by ? `; the reply named ${block.served_by}` : ""}.`, refs);
    }
    if (policy) {
      b.say(`The gateway tried the models in the order of the routing policy computed from the ledger up to record ${policy.as_of_seq} (sha256 ${policy.sha256}), ranked by the lower confidence bound of the share of each model's claims that passed the citation check; it moves on only when a model is unavailable or gives an empty answer. The judge page shows that ranking.`,
        [b.cite("routing_policy", "routing policy", `sha256 ${policy.sha256} as_of_seq ${policy.as_of_seq}`)]);
    }
    b.say("Whichever model writes the arguments, the decision rule is the same: every claim is citation-checked and the AI judge can never open a position alone.");
  }

  function executed(d, b) {
    const v = d.verdict || {};
    const ex = d.execution;
    const closes = d.executions_closed || [];
    if (!ex) {
      if (!["LONG", "SHORT"].includes(v.action)) b.say(`No position was opened (${v.action}), so no new trade was sent to Sepolia testnet.`);
      else b.say("No Sepolia transaction is recorded for this position: it was decided before the Sepolia practice desk was switched on, so it stays simulated.");
    } else if (ex.status === "failed" || !ex.tx) {
      b.say(`The send of this position to Sepolia testnet failed, so it stays simulated: ${ex.label || "the send failed"}.`, [b.cite("execution.label", "what happened", ex.label || "failed")]);
      if (ex.error) b.quote(ex.error, "error, recorded");
    } else {
      b.say(`This position went to Sepolia testnet: ${ex.label}. Transaction ${ex.tx}${has(ex.block) ? `, block ${ex.block}` : ""}.`,
        [b.cite("execution.label", "what was done", ex.label), b.cite("execution.tx", "Sepolia transaction", `${ex.tx} block ${ex.block}`)]);
    }
    if (ex && ex.tx && ex.status === "executed" && has(ex.amount_in) && has(ex.amount_out)) {
      const sent = units(ex.amount_in, ex.token_in), got = units(ex.amount_out, ex.token_out);
      b.say(`The swap sent ${sent.text} and received ${got.text} on the Uniswap test pool, whose price is not the market price.`,
        [b.cite("execution.amounts", "swap amounts, raw on-chain units", `${sent.source}; ${got.source}`)]);
    }
    if (ex && ex.tx && ex.status !== "failed" && ex.decision_digest) {
      b.say(`The transaction carries a digest of this decision, ${ex.decision_digest}, and the record holds the transaction: it was written into the record before the record was sealed.`,
        [b.cite("execution.decision_digest", "decision digest", ex.decision_digest)]);
    }
    for (const c of closes) {
      b.say(`In this record the desk also ${c.tx ? "closed" : "tried to close"} an earlier on-chain position (record ${c.opened_seq}): ${c.label || c.status}${c.tx ? `, transaction ${c.tx}` : ""}.`,
        [b.cite(`closed:${c.opened_seq}`, "on-chain close", `${c.opened_seq} ${c.tx || ""} ${c.label || c.status}`)]);
    }
    b.say("Test tokens only: no real money moved.");
  }

  const ANSWERS = { decision, why, chart: chart_, flip, risk, outcome: outcome_, analysts, refused, judge, model: model_, voices, saw, executed, proof, help };

  function answer(dossier, intent, ctx) {
    if (!intent || !ANSWERS[intent]) {
      const b = builder(dossier, "unknown");
      b.say("I can only answer from this record, and that question is outside it. Try one of these:");
      const out = b.done();
      out.suggestions = SUGGESTIONS;
      return out;
    }
    const b = builder(dossier, intent);
    ANSWERS[intent](dossier, b, ctx || {});
    return b.done();
  }

  return { route, answer, check, outcome, INTENTS, SUGGESTIONS };
});
