// web/trust.js: the per-voice track record computed in the visitor's browser.
// Run: node --test tests/trust.test.js   (also run by tests/test_trust_parity.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const trust = require("../web/trust.js");

const HOUR = 3600000;
const T0 = Date.UTC(2026, 9, 20, 0, 0, 0);

// A deterministic path with no flat 4-hour windows.
const PATH = Array.from({ length: 200 }, (_, h) => 100 + Math.sin(h * 0.9) * 3 + h * 0.01);
const up = (h) => PATH[h + 4] > PATH[h];

function dossier(seq, hour, extractions = []) {
  const first = Math.max(0, hour - 5);
  const candles = [];
  for (let h = first; h <= hour; h++) candles.push({ t: T0 + h * HOUR, o: PATH[h], h: PATH[h], l: PATH[h], c: PATH[h], v: 1 });
  return {
    seq,
    evidence: { symbol: "BTCUSDT", collected_at: new Date(T0 + hour * HOUR).toISOString(), market: { candles }, narrative: { extractions } },
  };
}
const call = (name, stance, quote, token = "BTC") => ({ name, tokens: [token], stance, quote });
const world = (byHour, last) => Array.from({ length: last + 1 }, (_, h) => dossier(h + 1, h, byHour[h] || []));

test("a call settles four hours later and not before", () => {
  const early = [dossier(1, 2, [call("Oracle", "bullish", "btc up")]), dossier(2, 3), dossier(3, 4), dossier(4, 5)];
  assert.equal(trust.settled(early).done.length, 0);
  assert.equal(trust.settled(early).pending, 1);
  const later = [...early, dossier(5, 6)];
  assert.equal(trust.settled(later).done.length, 1);
  assert.ok(Math.abs(trust.settled(later).done[0].ret - (PATH[6] / PATH[2] - 1)) < 1e-12);
});

test("the same headline seen again is one call", () => {
  const ledger = Array.from({ length: 12 }, (_, i) => dossier(i + 1, i, [call("Oracle", "bullish", i < 3 ? "BTC   will rise" : "btc will rise")]));
  assert.equal(trust.calls(ledger).length, 1);
});

test("neutral calls and untracked tokens are not calls", () => {
  const ledger = [dossier(1, 0, [call("O", "neutral", "x"), call("O", "bullish", "bnb up", "BNB")])];
  assert.equal(trust.calls(ledger).length, 0);
});

test("a voice that only says up earns exactly nothing", () => {
  const byHour = {};
  for (let h = 0; h < 100; h += 2) byHour[h] = [call("Sheep", "bullish", `sheep ${h}`)];
  const v = trust.compute(world(byHour, 120)).voices.Sheep;
  assert.equal(v.bull_share, 1);
  assert.equal(v.skill, 0);
  assert.equal(v.weight, 1);
});

test("a voice that foresees the direction is weighted above one, a contrarian below", () => {
  const byHour = {};
  for (let h = 0; h < 100; h += 2) byHour[h] = [call("Oracle", up(h) ? "bullish" : "bearish", `o${h}`), call("Contra", up(h) ? "bearish" : "bullish", `c${h}`)];
  const { Oracle, Contra } = trust.compute(world(byHour, 120)).voices;
  assert.equal(Oracle.hit_rate, 1);
  assert.ok(Oracle.weight > 1.2 && Oracle.weight <= 1.5);
  assert.equal(Contra.hit_rate, 0);
  assert.ok(Contra.weight < 0.9 && Contra.weight >= 0.5);
});

test("a small sample stays neutral and unproven", () => {
  const byHour = {};
  for (let h = 0; h < 8; h += 2) byHour[h] = [call("Rookie", up(h) ? "bullish" : "bearish", `r${h}`)];
  const v = trust.compute(world(byHour, 40)).voices.Rookie;
  assert.ok(v.n < trust.MIN_CALLS);
  assert.equal(v.weight, 1);
  assert.equal(v.proven, false);
});

test("the score of a prefix never depends on what came after", () => {
  const byHour = {};
  for (let h = 0; h < 60; h += 2) byHour[h] = [call("Oracle", up(h) ? "bullish" : "bearish", `o${h}`)];
  const full = world(byHour, 100);
  const prefix = full.slice(0, 60);
  const wrecked = JSON.parse(JSON.stringify(full));
  for (const d of wrecked.slice(60)) for (const c of d.evidence.market.candles) c.o = 1;
  assert.deepEqual(trust.compute(prefix), trust.compute(wrecked.slice(0, 60)));
});
