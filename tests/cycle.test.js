// web/cycle.js: the timeline layout, the plain-language remedies, the scoreboard arithmetic.
// Run: node --test tests/cycle.test.js   (the Python side is compared in tests/test_otari_report.py)
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../web/cycle.js");

const rec = (role, id, extra) => Object.assign({ role, request_id: id, request_id_source: "otari", sent_model: "m/" + role, provider: "deepinfra", served_model: "s-" + role,
  served_by: "deepinfra:s-" + role, duration_ms: 1000, cost_usd: "0.000100", tokens: { prompt: 1, completion: 1 } }, extra || {});

test("bars sit where each call began when every receipt says so, on one scale", () => {
  const calls = [{ label: "a", receipt: rec("a", "1", { started_offset_ms: 0, duration_ms: 1000 }) }, { label: "b", receipt: rec("b", "2", { started_offset_ms: 4000, duration_ms: 1000 }) }];
  const lay = C.layout(calls);
  assert.equal(lay.timed, true);
  assert.equal(lay.totalMs, 5000);
  assert.deepEqual(lay.bars.map((b) => [b.leftPct, b.widthPct]), [[0, 20], [80, 20]]);
});

test("without offsets the bars are laid end to end and the layout says the position is only an order", () => {
  const calls = [{ label: "a", receipt: rec("a", "1") }, { label: "b", receipt: rec("b", "2", { duration_ms: 3000 }) }];
  const lay = C.layout(calls);
  assert.equal(lay.timed, false);
  assert.deepEqual(lay.bars.map((b) => b.startMs), [0, 1000]);
  assert.ok(lay.bars.every((b) => b.widthPct >= 0.6));
});

test("every failure code we record has a plain what-happened and a what-to-do", () => {
  for (const [code, bad] of [["budget_exceeded", { refusal: { status: 403, code: "budget_exceeded", budget_scope: "user", retry_after: "40" } }],
    ["insufficient_funds", { refusal: { status: 402, code: "insufficient_funds" } }], ["cycle_cap_exceeded", { refusal: { status: null, code: "cycle_cap_exceeded", source: "client", budget_scope: "cycle" } }],
    ["timeout", { error: { kind: "timeout" } }], ["409", { refusal: { status: 409, code: null } }], ["504", { error: { status: 504, code: null } }]]) {
    const f = C.failureOf(rec("x", "9", bad));
    assert.equal(f.code, code);
    assert.ok(f.what.length > 20 && f.fix.length > 20, code);
    assert.ok(!/—/.test(f.what + f.fix), "no em dash");
  }
  assert.equal(C.failureOf(rec("x", "9")), null);
  const unknown = C.failureOf(rec("x", "9", { refusal: { status: 418, code: "teapot" } }));
  assert.equal(unknown.what, "The call failed.");
  const f = C.failureOf(rec("x", "9", { refusal: { status: 403, code: "budget_exceeded", budget_scope: "user", retry_after: "40" } }));
  assert.deepEqual([f.scope, f.retryAfter], ["user", "40"]);
});

test("micro-dollar arithmetic is exact and rounds half to even like Python's Decimal", () => {
  assert.equal(C.microsOf("0.010207"), 10207n);
  assert.equal(C.microsOf("1"), 1000000n);
  assert.equal(C.microsOf("abc"), null);
  assert.equal(C.microsText(10207n), "0.010207");
  const ledger = (cost, claims) => [{ seq: 1, llm: { otari: {}, bull: { accepted: new Array(claims).fill(0), receipt: rec("bull", "b", { cost_usd: cost }) } }, evidence: {} }];
  assert.equal(C.board(ledger("0.000005", 2))[0].cost_per_claim_usd, "0.000002", "2.5 micro-dollars rounds to the even 2");
  assert.equal(C.board(ledger("0.000007", 2))[0].cost_per_claim_usd, "0.000004", "3.5 rounds to the even 4");
  assert.equal(C.board(ledger("0.000003", 0))[0].cost_per_claim_usd, null);
});

test("nearest-rank percentiles", () => {
  assert.deepEqual([0.5, 0.95, 0.2].map((p) => C.percentile([5, 1, 3, 2, 4], p)), [3, 5, 1]);
  assert.equal(C.percentile([], 0.5), null);
});

test("the gateway region is read from the base URL and nothing more is claimed", () => {
  const d = { seq: 1, llm: { otari: { base_url: "https://otari.ai/api/v1" } }, evidence: {} };
  assert.match(C.whoSaw(d, {}).gateway.region, /default region/);
  d.llm.otari.base_url = "https://eu.api.otari.ai/api/v1";
  assert.match(C.whoSaw(d, {}).gateway.region, /^EU/);
  d.llm.otari.base_url = "";
  assert.equal(C.whoSaw(d, {}).gateway.region, "unknown");
});

test("a call without a duration has no bar, and the narrator batches are split from the deciding calls with their index kept", () => {
  const calls = [{ label: "narrator", receipt: rec("narrator", "1", { started_offset_ms: 0 }) }, { label: "bull", receipt: rec("bull", "2", { started_offset_ms: 500, duration_ms: null }) },
    { label: "canary", receipt: rec("canary", "3", { started_offset_ms: 900 }) }, { label: "narrator", receipt: rec("narrator", "4", { started_offset_ms: 100 }) }];
  const lay = C.layout(calls);
  assert.equal(lay.bars[1].durationMs, null);
  assert.equal(lay.bars[1].widthPct, 0);
  const g = C.groups(calls);
  assert.deepEqual(g.core.map((e) => [e.call.label, e.i]), [["bull", 1], ["canary", 2]]);
  assert.deepEqual(g.narrator.map((e) => e.i), [0, 3]);
});

test("the failover canary of a dossier is one of its calls, after the four roles and before the sandbox", () => {
  const d = { seq: 1, evidence: {}, llm: { otari: { canary: { receipt: rec("judge", "c") } }, bull: { receipt: rec("bull", "b") }, judge: { receipt: rec("judge", "j") } }, sandbox: { receipt: rec("sandbox", "s") } };
  assert.deepEqual(C.callsOf(d).map((c) => c.label), ["bull", "judge", "canary", "sandbox"]);
});

test("the drift fetch is one of the calls of a dossier, right after the page fetch, and the two lines of the cycle page say what the dossier has", () => {
  const d = { seq: 1, evidence: { web: { receipt: rec("searcher", "s"), page: { receipt: rec("fetcher", "f") } } }, llm: { otari: {}, bull: { receipt: rec("bull", "b") } },
    drift: { status: "unchanged", source_dossier: 7, minutes_after_decision: 185, receipt: rec("fetcher", "d") } };
  assert.deepEqual(C.callsOf(d).map((c) => c.label + ":" + c.receipt.request_id), ["searcher:s", "fetcher:f", "drift:d", "bull:b"]);
  assert.equal(C.roleClass("drift"), "r-tool");
  assert.equal(C.driftLine(d), "Source drift: unchanged (dossier 7, 185 min after the decision)");
  assert.equal(C.driftLine({ drift: { status: "none" } }), "Source drift: no earlier page old enough to read again");
  assert.equal(C.driftLine({ drift: { status: "unavailable", source_dossier: 7, reason: "boom" } }), "Source drift: unavailable (dossier 7): boom");
  assert.match(C.driftLine({}), /not checked in this dossier/);
  assert.match(C.closureLine({}), /^Closure gate: not applied to this dossier/);
  const checks = [{ name: "receipts", class: "hard", pass: true }, { name: "narrative_complete", class: "soft", pass: false }, { name: "priced_within_cap", class: "soft", pass: true }];
  assert.equal(C.closureLine({ closure: { checks } }), "Closure gate: 2 of 3 checks passed (1 warning: narrative_complete)");
  assert.equal(C.closureLine({ closure: { checks: checks.map((c) => ({ ...c, pass: true })) } }), "Closure gate: 3 of 3 checks passed");
  assert.match(C.closureLine({ closure: "garbage" }), /not applied/, "a field of the wrong shape is read as absent, never thrown on");
  assert.match(C.driftLine({ drift: 5 }), /not checked/);
});

test("a receipt that went down the app's own role chain says so, apart from Otari's provider failover", () => {
  assert.equal(C.roleFallbackOf(rec("bull", "1")), null);
  assert.equal(C.roleFallbackOf(rec("bull", "1", { role_fallback: { chain: ["a", "b"], skipped: [], answered: "a", answered_index: 0 } })), null);
  const skipped = [{ model: "a", reason: "pricing_required", status: 402, code: "pricing_required", request_id: "r1", request_id_source: "otari" }];
  const answered = C.roleFallbackOf(rec("bull", "1", { fallback: false, role_fallback: { chain: ["a", "b", "c"], skipped, answered: "b", answered_index: 1 } }));
  assert.deepEqual([answered.kind, answered.skipped, answered.total, answered.answered], ["answered", 1, 3, "b"]);
  assert.equal(answered.line, "Role fallback: answered by b, entry 2 of 3, after skipping a (pricing_required, HTTP 402)");
  const exhausted = C.roleFallbackOf(rec("bull", "1", { role_fallback: { chain: ["a", "b"], skipped, answered: null, answered_index: null, exhausted: true } }));
  assert.equal(exhausted.kind, "exhausted");
  assert.match(exhausted.line, /all 2 models of the chain failed/);
  const stopped = C.roleFallbackOf(rec("bull", "1", { role_fallback: { chain: ["a", "b"], skipped, answered: null, answered_index: null, stopped_by: "budget_exceeded" } }));
  assert.deepEqual([stopped.kind, stopped.stoppedBy], ["stopped", "budget_exceeded"]);
  assert.ok(!/—/.test(answered.line + exhausted.line + stopped.line), "no em dash");
});

test("a call served by a role fallback still counts in the model table under the model that answered it", () => {
  const skipped = [{ model: "a", reason: "pricing_required", status: 402 }];
  const led = [{ seq: 1, llm: { otari: {}, bull: { accepted: [1], receipt: rec("bull", "b", { sent_model: "b", role_fallback: { chain: ["a", "b"], skipped, answered: "b", answered_index: 1 } }) } }, evidence: {} }];
  const rows = C.board(led);
  assert.deepEqual([rows[0].model, rows[0].calls, rows[0].ok, rows[0].fallbacks], ["deepinfra:s-bull", 1, 1, 0], "Otari's own failover counter is not touched by the app's chain");
});
