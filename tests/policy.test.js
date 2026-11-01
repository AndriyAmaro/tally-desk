// web/policy.js: the decision policy and the position a verdict becomes, in the visitor's browser.
// Run: node --test tests/policy.test.js   (the Python judge is compared in tests/test_policy_parity.py
// and tests/test_position_parity.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const policy = require("../web/policy.js");

test("an edge at the entry band opens a LONG, below minus the band a SHORT, inside it nothing", () => {
  assert.equal(policy.evaluate({ bull: 3, bear: 1.4, atrPct: 0.5, ageMinutes: 10 }).state, "LONG");
  assert.equal(policy.evaluate({ bull: 1, bear: 3, atrPct: 0.5, ageMinutes: 10 }).state, "SHORT");
  assert.equal(policy.evaluate({ bull: 1, bear: 1.2, atrPct: 0.5, ageMinutes: 10 }).state, "FLAT");
});

test("a stale price blocks the decision whatever the arguments say", () => {
  assert.equal(policy.evaluate({ bull: 9, bear: 0, atrPct: 0.5, ageMinutes: 200 }).state, "BLOCKED_STALE");
});

test("a LONG puts the stop 1.5 ATR below and the target 3 ATR above, sized so the stop loses exactly the risk", () => {
  const p = policy.position(100, 2, "LONG", 1, 10000);
  assert.equal(p.stop, 97);
  assert.equal(p.take_profit, 106);
  assert.equal(p.risk_cash, 100);
  assert.equal(p.size, round8(100 / 3));
  assert.ok(Math.abs(p.size * (p.entry - p.stop) - p.risk_cash) < 1e-5, "losing at the stop costs the risk cash");
});

test("a SHORT mirrors it", () => {
  const p = policy.position(100, 2, "SHORT", 2, 10000);
  assert.equal(p.stop, 103);
  assert.equal(p.take_profit, 94);
  assert.equal(p.risk_cash, 200);
});

test("the user may lower the risk, never raise it past the system ceiling", () => {
  assert.equal(policy.position(100, 2, "LONG", 25, 10000).risk_pct, 25);
  assert.throws(() => policy.position(100, 2, "LONG", 25.01, 10000), /exceeds the system cap of 25%/);
  assert.throws(() => policy.position(100, 2, "LONG", 0, 10000), /greater than 0/);
});

test("only a LONG or a SHORT has a position", () => {
  assert.throws(() => policy.position(100, 2, "FLAT", 1, 10000), /no position/);
});

test("a risk limit that was not asked for defaults to one percent", () => {
  assert.equal(policy.position(100, 2, "LONG", null, 10000).risk_pct, 1);
});

function round8(x) { return Math.round(x * 1e8) / 1e8; }

test("a freshness block sealed as not ok refuses the decision even when the candle is fresh, and says why", () => {
  const r = policy.evaluate({ bull: 9, bear: 0, atrPct: 0.5, ageMinutes: 10, freshOk: false, blockReason: "price sources diverge", divergent: true });
  assert.equal(r.state, "BLOCKED_STALE");
  assert.equal(r.stale, false);
  assert.equal(r.divergent, true);
  assert.match(r.rule, /price sources diverge/);
  assert.equal(policy.evaluate({ bull: 9, bear: 0, atrPct: 0.5, ageMinutes: 10, freshOk: true }).state, "LONG");
  assert.equal(policy.evaluate({ bull: 9, bear: 0, atrPct: 0.5, ageMinutes: 200, freshOk: true }).state, "BLOCKED_STALE");
});

test("checkDossier reads a dossier the way the verify page does and agrees with a divergence refusal", () => {
  const args = (w) => [{ weight: w }];
  const d = {
    bull: args(3), bear: args(0), features: { atr_14: 0.5, last_close: 100 }, verdict: { action: "BLOCKED_STALE", rules_version: "v6" },
    evidence: { freshness: { ok: false, reason: "x", window_minutes: 75, newest_candle_age_minutes: 20,
      classes: { market: { ok: true }, divergence: { ok: false, reason: "the sources diverge by 8%" } } } },
  };
  const r = policy.checkDossier(d);
  assert.equal(r.rederived.state, "BLOCKED_STALE");
  assert.equal(r.agrees, true);
  assert.match(r.rederived.rule, /diverge by 8%/);
  const lenient = JSON.parse(JSON.stringify(d));
  lenient.evidence.freshness.ok = true;
  assert.equal(policy.checkDossier(lenient).agrees, false);
  assert.equal(policy.family("BLOCKED_STALE"), "BLOCKED");
});
