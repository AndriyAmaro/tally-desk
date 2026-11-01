// web/ask.js: "Ask the record", a conversation with one recorded decision that cannot make things up.
// Run: node --test tests/ask.test.js   (tests/test_ask.py runs it in CI, plus a sweep over the whole ledger)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ask = require("../web/ask.js");

const LEDGER = fs.readFileSync(path.join(__dirname, "..", "ledger", "ledger.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const latest = (pred) => [...LEDGER].reverse().find(pred);
const POSITION = latest((d) => ["LONG", "SHORT"].includes(d.verdict.action) && d.llm && d.llm.judge && d.llm.judge.ruling);
const DECLINED = latest((d) => d.verdict.action === "WATCH_REVERSAL");
const JUDGED = latest((d) => d.llm && d.llm.judge && d.llm.judge.accepted && d.llm.judge.weight > 0);
const VOIDED = latest((d) => d.llm && d.llm.judge && d.llm.judge.ruling && !d.llm.judge.accepted);
const OLD = LEDGER.find((d) => !d.llm);
const NO_SENSITIVITY = { ...OLD, sensitivity: undefined };
const textOf = (a) => a.parts.map((p) => p.text).join(" ");

test("free questions are routed to the intent they ask about, in English or Portuguese", () => {
  const cases = {
    "Why did you go short?": "why", "por que vendeu?": "why",
    "What would change your mind?": "flip", "what if one argument was removed": "flip",
    "How much could it lose?": "risk", "where is the stop?": "risk", "qual o risco?": "risk",
    "What did the AI judge think?": "judge", "o que o juiz achou": "judge",
    "What did you refuse?": "refused", "which claims were rejected": "refused",
    "What did the analysts claim?": "analysts",
    "Can I trust this record?": "proof", "was it tampered with?": "proof",
    "What did you decide?": "decision",
    "help": "help",
  };
  for (const [q, intent] of Object.entries(cases)) assert.equal(ask.route(q), intent, q);
});

test("a question outside the repertoire is declined, never improvised", () => {
  assert.equal(ask.route("will bitcoin hit 200k tomorrow?"), null);
  const a = ask.answer(POSITION, null);
  assert.equal(a.intent, "unknown");
  assert.match(textOf(a), /only answer from this record/);
  assert.ok(a.suggestions.length >= 5);
});

test("the time is shown in plain words and still checks against the recorded one", () => {
  const a = ask.answer(POSITION, "decision");
  assert.match(textOf(a), / at \d{2}:\d{2} UTC/);
  assert.ok(ask.check(a).ok);
});

test("the decision answer states the action, the edge and the band, each number cited", () => {
  const a = ask.answer(POSITION, "decision");
  const v = POSITION.verdict;
  assert.match(textOf(a), new RegExp(v.action));
  assert.ok(textOf(a).includes(String(v.edge)));
  assert.ok(a.cites.some((c) => c.ref === "verdict.edge"));
  assert.ok(ask.check(a).ok);
});

test("why names the heaviest arguments with their weights and the one that decided it, if any", () => {
  const a = ask.answer(POSITION, "why");
  const heaviest = [...POSITION.bull, ...POSITION.bear].sort((x, y) => y.weight - x.weight)[0];
  assert.ok(textOf(a).includes(heaviest.claim));
  assert.ok(ask.check(a).ok);
  const flips = (POSITION.sensitivity.drop_one || []).filter((d) => d.flips);
  if (!flips.length) assert.match(textOf(a), /No single argument/);
});

test("flip says how far each side is from the band", () => {
  const a = ask.answer(DECLINED, "flip");
  assert.ok(textOf(a).includes(String(DECLINED.sensitivity.needs_for_long)) || textOf(a).includes(String(DECLINED.sensitivity.needs_for_short)));
  assert.ok(ask.check(a).ok);
});

test("risk gives entry, stop, target, size and the most the stop can lose; a declined entry says no position", () => {
  const a = ask.answer(POSITION, "risk");
  const v = POSITION.verdict;
  for (const n of [v.entry, v.stop, v.take_profit, v.size]) assert.ok(textOf(a).includes(String(n)), String(n));
  assert.ok(ask.check(a).ok);
  assert.match(textOf(ask.answer(DECLINED, "risk")), /no position/i);
});

test("a counted judge vote says its weight and that it can never open a position alone, quoting the model", () => {
  const a = ask.answer(JUDGED, "judge");
  const j = JUDGED.llm.judge;
  assert.ok(a.parts.some((p) => p.kind === "quote" && p.text.includes(j.ruling.reason.slice(0, 20))));
  assert.ok(textOf(a).includes(String(j.weight)));
  assert.match(textOf(a), /never open a position on its own/);
  assert.ok(ask.check(a).ok);
});

test("a voided judge ruling says why and that it added nothing", () => {
  const a = ask.answer(VOIDED, "judge");
  assert.match(textOf(a), /voided/);
  assert.match(textOf(a), /added nothing/);
  assert.ok(ask.check(a).ok);
});

test("analysts and refusals show the model's claims as quotes, with the evidence each one cites", () => {
  const a = ask.answer(POSITION, "analysts");
  assert.ok(a.parts.some((p) => p.kind === "quote"));
  assert.ok(a.cites.some((c) => /^E\d+$/.test(c.ref)));
  assert.ok(ask.check(a).ok);
  assert.ok(ask.check(ask.answer(POSITION, "refused")).ok);
});

test("proof uses the fingerprint the page recomputed and says plainly when it does not match", () => {
  const ok = ask.answer(POSITION, "proof", { recomputedHash: POSITION.hash });
  assert.match(textOf(ok), /matches/);
  assert.ok(ask.check(ok).ok);
  const bad = ask.answer(POSITION, "proof", { recomputedHash: "0".repeat(64) });
  assert.match(textOf(bad), /does NOT match/);
});

test("the self-check refuses a number that no cited source contains", () => {
  const a = ask.answer(POSITION, "decision");
  const forged = { ...a, parts: [...a.parts, { kind: "text", text: "It expected a 37.5 percent move." }] };
  const result = ask.check(forged);
  assert.equal(result.ok, false);
  assert.ok(result.stray.includes("37.5"));
});

test("quotes are excluded from the number check but cited references are not", () => {
  const a = { parts: [{ kind: "quote", text: "claims 999" }, { kind: "text", text: "weight 1.5" }], cites: [{ ref: "x", label: "x", source: "1.5" }] };
  assert.ok(ask.check(a).ok);
});

test("records without an LLM layer or a sensitivity table answer plainly instead of breaking", () => {
  assert.match(textOf(ask.answer(OLD, "judge")), /no AI judge/i);
  assert.match(textOf(ask.answer(OLD, "analysts")), /no AI analyst/i);
  assert.match(textOf(ask.answer(NO_SENSITIVITY, "flip")), /predates/i);
  assert.match(textOf(ask.answer(NO_SENSITIVITY, "why")), /predates/i);
  for (const intent of ask.INTENTS) assert.ok(ask.check(ask.answer(OLD, intent, { recomputedHash: OLD.hash })).ok, intent);
});

// ---------------------------------------------------------------- phase 2: the chart, what happened next, sources, voices
const EXECUTED = latest((d) => d.execution && d.execution.tx); // the newest send that reached the chain
const FAILED_SEND = latest((d) => d.execution && !d.execution.tx); // a send the chain refused is answered from the record too
const VOICED = latest((d) => d.evidence.narrative && (d.evidence.narrative.convergence || []).length);
const later = (d, rows) => rows.map((r, k) => ({ t: d.evidence.market.candles.at(-1).t + (k + 1) * 3600000, o: r[2], h: r[0], l: r[1], c: r[2], v: 1 }));

test("the new questions are routed too", () => {
  const cases = {
    "What happened next?": "outcome", "did it work?": "outcome", "o que aconteceu depois?": "outcome",
    "What does the chart show?": "chart", "explain the chart": "chart", "o gráfico": "chart",
    "What did you see?": "saw", "where does the data come from?": "saw",
    "What did the voices say?": "voices", "what were people saying on telegram?": "voices",
    "Was it executed on-chain?": "executed", "is there a sepolia transaction?": "executed",
  };
  for (const [q, intent] of Object.entries(cases)) assert.equal(ask.route(q), intent, q);
});

test("outcome resolves a position like portfolio.resolve: the stop wins a candle that touches both", () => {
  const v = POSITION.verdict;
  const dir = v.action === "LONG" ? 1 : -1;
  const both = later(POSITION, [[Math.max(v.stop, v.take_profit) + 1, Math.min(v.stop, v.take_profit) - 1, v.entry]]);
  const o = ask.outcome(POSITION, both);
  assert.equal(o.status, "STOP");
  assert.equal(o.exit, v.stop);
  assert.equal(o.bars, 1);
  assert.equal(o.pnl, Math.round((v.stop - v.entry) * dir * v.size * 1e4) / 1e4);
  assert.equal(ask.outcome(POSITION, []).status, "OPEN");
  assert.equal(ask.outcome(DECLINED, both), null);
});

test("what happened next is answered, cited and checked, with or without later candles", () => {
  const v = POSITION.verdict;
  const hit = later(POSITION, [[v.entry, v.entry, v.entry], v.action === "LONG" ? [v.take_profit + 1, v.entry, v.take_profit] : [v.entry, v.take_profit - 1, v.take_profit]]);
  const a = ask.answer(POSITION, "outcome", { later: hit });
  assert.match(textOf(a), /target/);
  assert.ok(ask.check(a).ok, JSON.stringify(ask.check(a)));
  assert.ok(a.marks.includes("exit"));
  const none = ask.answer(POSITION, "outcome", { later: [] });
  assert.match(textOf(none), /no later candle|still open/i);
  assert.ok(ask.check(none).ok);
  assert.match(textOf(ask.answer(DECLINED, "outcome", { later: hit })), /no position/i);
});

test("the chart answer reads the record's own indicators and lights them", () => {
  const a = ask.answer(POSITION, "chart");
  assert.ok(ask.check(a).ok, JSON.stringify(ask.check(a)));
  assert.ok(a.cites.some((c) => c.ref === "features.last_close"));
  assert.ok(a.marks.includes("ema_fast") && a.marks.includes("range_high_24"));
});

test("sources, voices and the Sepolia execution are answered from the record, and old records say what they lack", () => {
  for (const intent of ["saw", "voices", "executed"]) {
    for (const d of [POSITION, DECLINED, OLD, VOICED, EXECUTED, FAILED_SEND].filter(Boolean)) {
      const a = ask.answer(d, intent);
      assert.ok(a.parts.length > 0, `${intent} #${d.seq}`);
      assert.ok(ask.check(a).ok, `${intent} #${d.seq} ${JSON.stringify(ask.check(a))}`);
    }
  }
  assert.match(textOf(ask.answer(EXECUTED, "executed")), /Sepolia/);
  assert.ok(ask.answer(EXECUTED, "executed").cites.some((c) => c.ref === "execution.tx"));
  assert.match(textOf(ask.answer(OLD, "executed")), /before|simulated|no new trade was sent/i);
  const NO_VOICES = LEDGER.find((d) => !d.evidence.narrative);
  assert.match(textOf(ask.answer(NO_VOICES, "voices")), /no market voices/i);
  assert.ok(ask.check(ask.answer(NO_VOICES, "saw")).ok);
});

test("an executed swap is told in token units, with the raw on-chain amounts cited", () => {
  const swap = latest((d) => d.execution && d.execution.status === "executed");
  if (!swap) return;
  const a = ask.answer(swap, "executed");
  assert.match(textOf(a), /sent \d+(\.\d+)? WETH and received \d+(\.\d+)? USDC|sent \d+(\.\d+)? USDC and received \d+(\.\d+)? WETH/);
  assert.ok(ask.check(a).ok, JSON.stringify(ask.check(a)));
  assert.ok(a.cites.find((c) => c.ref === "execution.amounts").source.includes(String(swap.execution.amount_in)));
  // the venue's name must not carry a number no cited field holds (dossier 394: a digest with no lone "3")
  const plain = { ...swap, execution: { ...swap.execution, decision_digest: "0x" + "ab".repeat(32), tx: "0x" + "cd".repeat(32), block: 1 } };
  assert.ok(ask.check(ask.answer(plain, "executed")).ok, JSON.stringify(ask.check(ask.answer(plain, "executed"))));
  for (const d of LEDGER.filter((x) => x.execution)) assert.ok(ask.check(ask.answer(d, "executed")).ok, `#${d.seq}`);
});

test("which model wrote this: the route the gateway took, or what an older record can say", () => {
  assert.equal(ask.route("Which model wrote this, and why that one?"), "model");
  assert.equal(ask.route("qual modelo respondeu?"), "model");
  const routed = JSON.parse(JSON.stringify(POSITION));
  routed.llm = routed.llm || {};
  routed.llm.bull = { accepted: [], rejected: [], served_by: "agnes-3.0-flash",
    route: [{ model: "gpt-oss-120b", status: 429, ms: 812 }, { model: "agnes-3.0-flash", status: 200, ms: 9400 }],
    // a real policy fingerprint: digit runs inside the hash must not trip the number check
    routing_policy: { sha256: "90eeb7083371980696a476741bb6d6df64a2b8203dfa99262610c3144c1c8783", as_of_seq: 383 } };
  const a = ask.answer(routed, "model");
  assert.match(textOf(a), /gpt-oss-120b.*429.*agnes-3\.0-flash/);
  assert.ok(ask.check(a).ok, JSON.stringify(ask.check(a)));
  assert.ok(a.cites.some((c) => c.ref === "llm.bull.routing_policy"));
  // the reply can name the model with digits the route alias lacks (dossier 408: "...-550b-a55b"); that name is cited too
  routed.llm.bull.served_by = "nvidia/nemotron-3-ultra-550b-a55b";
  routed.llm.bull.route[1].model = "nemotron-3-ultra-550b";
  assert.ok(ask.check(ask.answer(routed, "model")).ok, JSON.stringify(ask.check(ask.answer(routed, "model"))));
  for (const d of LEDGER.filter((x) => x.llm && x.llm.bull && x.llm.bull.route)) assert.ok(ask.check(ask.answer(d, "model")).ok, `#${d.seq}`);
  const old = LEDGER.find((d) => d.llm && d.llm.bull && d.llm.bull.served_by && !d.llm.bull.route);
  assert.match(textOf(ask.answer(old, "model")), /before the gateway logged its route/);
  assert.match(textOf(ask.answer(OLD, "model")), /no AI analyst layer/);
});

test("edge cases found in review: no 'undefined' in text, failed sends, close-only records, missing amounts, v6 without LLM", () => {
  for (const d of LEDGER) for (const intent of ask.INTENTS) assert.doesNotMatch(textOf(ask.answer(d, intent, { later: [] })), /undefined|NaN|\[object Object\]/, `#${d.seq} ${intent}`);
  const failed = { ...POSITION, execution: { status: "failed", error: "nonce too low", label: "the Sepolia send failed; this position stays simulated" } };
  const f = ask.answer(failed, "executed");
  assert.match(textOf(f), /failed/);
  assert.doesNotMatch(textOf(f), /Transaction|holds the transaction/);
  assert.ok(ask.check(f).ok);
  const closeOnly = { ...DECLINED, execution: undefined, executions_closed: [{ opened_seq: 379, status: "executed", tx: "0x" + "ef".repeat(32), label: "practice position closed on Sepolia testnet" }] };
  const c = ask.answer(closeOnly, "executed");
  assert.match(textOf(c), /record 379/);
  assert.ok(ask.check(c).ok, JSON.stringify(ask.check(c)));
  const noOut = { ...POSITION, execution: { status: "executed", tx: "0x" + "ab".repeat(32), block: 1, label: "practice trade executed on Sepolia testnet", amount_in: 5, token_in: "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14" } };
  assert.doesNotThrow(() => ask.answer(noOut, "executed"));
  const v6NoLlm = { ...POSITION, llm: undefined };
  assert.doesNotMatch(textOf(ask.answer(v6NoLlm, "judge")), /before the judge existed \(rules v6\)/);
  const routed = { ...POSITION, llm: { bull: { served_by: "m", route: [{ model: "m", status: 200, ms: 5 }], routing_policy: { sha256: "ab".repeat(32), as_of_seq: 383 } } } };
  for (const cite of ask.answer(routed, "model").cites) assert.doesNotMatch(cite.source, / 12$/);
});

test("every answer names the chart marks its citations point at", () => {
  const a = ask.answer(POSITION, "risk");
  assert.ok(a.marks.includes("stop") && a.marks.includes("entry") && a.marks.includes("take_profit"));
});
