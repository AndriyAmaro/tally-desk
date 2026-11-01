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
