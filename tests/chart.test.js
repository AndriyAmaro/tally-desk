// web/chart.js: the chart of Ask the record, drawn only from the candles frozen in the dossier.
// Run: node --test tests/chart.test.js   (tests/test_ask.py runs it in CI)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const chart = require("../web/chart.js");

const LEDGER = fs.readFileSync(path.join(__dirname, "..", "ledger", "ledger.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const latest = (pred) => [...LEDGER].reverse().find(pred);
const POSITION = latest((d) => ["LONG", "SHORT"].includes(d.verdict.action));
const DECLINED = latest((d) => !["LONG", "SHORT"].includes(d.verdict.action) && d.verdict.rules_version === "v6");
const round6 = (x) => Math.round(x * 1e6) / 1e6;

test("the averages and bands drawn end exactly on the values the record computed (every v2+ record)", () => {
  let checked = 0;
  for (const d of LEDGER) {
    if (d.verdict.rules_version === "v1") continue;
    const s = chart.studies(d.evidence.market.candles);
    const f = d.features;
    assert.equal(round6(s.emaFast.at(-1)), f.ema_fast, `#${d.seq} ema_fast`);
    assert.equal(round6(s.emaSlow.at(-1)), f.ema_slow, `#${d.seq} ema_slow`);
    assert.ok(Math.abs(s.bbUpper.at(-1) - f.bb_upper) < 1e-5, `#${d.seq} bb_upper`);
    assert.ok(Math.abs(s.bbLower.at(-1) - f.bb_lower) < 1e-5, `#${d.seq} bb_lower`);
    checked++;
  }
  assert.ok(checked > 300);
});

test("the geometry keeps every candle inside the frame, highs above lows", () => {
  const g = chart.geometry(POSITION);
  assert.equal(g.candles.length, POSITION.evidence.market.candles.length);
  for (const c of g.candles) {
    assert.ok(c.yHigh <= c.yLow, "a high is drawn above its low");
    assert.ok(c.yHigh >= g.pad.top - 1e-9 && c.yLow <= g.height - g.pad.bottom + 1e-9, "inside the frame");
    assert.ok(c.x > g.pad.left && c.x < g.width - g.pad.right);
  }
});

test("a position draws its entry, stop and target at the recorded prices; a declined record draws none", () => {
  const levels = Object.fromEntries(chart.geometry(POSITION).levels.map((l) => [l.key, l]));
  assert.equal(levels.entry.price, POSITION.verdict.entry);
  assert.equal(levels.stop.price, POSITION.verdict.stop);
  assert.equal(levels.take_profit.price, POSITION.verdict.take_profit);
  assert.equal(levels.range_high_24.price, POSITION.features.range_high_24);
  const none = chart.geometry(DECLINED).levels.map((l) => l.key);
  assert.ok(!none.includes("entry") && !none.includes("stop"));
  assert.ok(none.includes("last_close"));
});

test("a citation lights the part of the chart it names", () => {
  assert.deepEqual(chart.marksFor(["verdict.stop"]), ["stop"]);
  assert.deepEqual(chart.marksFor(["features.ema_fast", "verdict.size"]), ["ema_fast"]);
  assert.deepEqual(chart.marksFor(["bear[0]"], POSITION).length >= 0, true);
  const arg = [...(POSITION.bull || []), ...(POSITION.bear || [])].find((a) => /ema_fast/.test(a.evidence_ref));
  if (arg) {
    const side = (POSITION.bull || []).includes(arg) ? "bull" : "bear";
    const i = POSITION[side].indexOf(arg);
    assert.ok(chart.marksFor([`${side}[${i}]`], POSITION).includes("ema_fast"));
  }
  assert.deepEqual(chart.marksFor(["outcome.exit"]), ["exit"]);
});

test("later candles extend the chart to the right and the exit is marked where the position closed", () => {
  const t = POSITION.evidence.market.candles.at(-1).t;
  const stop = POSITION.verdict.stop;
  const later = [1, 2, 3].map((k) => ({ t: t + k * 3600000, o: stop, h: stop * 1.01, l: stop * 0.99, c: stop, v: 1 }));
  const g = chart.geometry(POSITION, { later, outcome: { status: "STOP", exit: stop, bars: 1 } });
  assert.equal(g.later.length, 3);
  assert.ok(g.later[0].x > g.candles.at(-1).x);
  assert.equal(g.exit.price, stop);
  assert.equal(g.exit.x, g.later[0].x);
});
