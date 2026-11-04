// web/tamper.js: the four single changes of the "Try to cheat it" panel, on a real dossier and on a receipts/1 record. Each one must change
// exactly one thing in a COPY, never in the record it was given, and must say plainly when it does not apply.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const T = require("../web/tamper.js");

const ROOT = path.join(__dirname, "..");
const lines = fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8").split("\n").filter((l) => l.trim());
const dossier = JSON.parse(lines[lines.length - 1]);
const fixture = fs.readFileSync(path.join(ROOT, "tests", "fixtures", "receipts_ledger.jsonl"), "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));

function deepFreeze(o) { if (o && typeof o === "object" && !Object.isFrozen(o)) { Object.freeze(o); Object.values(o).forEach(deepFreeze); } return o; }
// Every leaf path and value of an object, so "exactly one thing changed" can be counted.
function leaves(o, p = "", out = {}) {
  if (o && typeof o === "object") for (const k of Object.keys(o)) leaves(o[k], p + "/" + k, out);
  else out[p] = o;
  return out;
}
function diff(a, b) {
  const x = leaves(a), y = leaves(b), keys = new Set([...Object.keys(x), ...Object.keys(y)]);
  return [...keys].filter((k) => x[k] !== y[k]);
}

test("the four changes are the four kinds the panel offers", () => {
  assert.deepEqual(T.KINDS, ["cost", "verdict", "sentence", "prev"]);
});

test("on a real dossier every change applies, touches exactly one leaf of a copy, and leaves a frozen original alone", () => {
  const frozen = deepFreeze(JSON.parse(JSON.stringify(dossier)));
  const before = JSON.stringify(frozen);
  for (const kind of T.KINDS) {
    const r = T.apply(frozen, kind);
    assert.ok(r.ok, kind + " should apply: " + r.why);
    assert.notEqual(r.copy, frozen);
    assert.equal(JSON.stringify(frozen), before, kind + " must not touch the original");
    assert.equal(diff(frozen, r.copy).length, 1, kind + " must change exactly one leaf, changed: " + diff(frozen, r.copy).join(", "));
    assert.equal(r.copy.hash, frozen.hash, "the stored hash is left as it was: the point is that it no longer matches");
  }
});

test("cost: the costliest receipt is made to look cheaper, and the path names where", () => {
  const r = T.apply(dossier, "cost");
  assert.match(r.change.path, /receipt\.cost_usd$|receipts\[\d+\]\.cost_usd$/);
  assert.ok(Number(r.change.after) < Number(r.change.before));
  const all = [];
  JSON.stringify(dossier, (k, v) => { if (v && v.schema === "otari-receipt/1" && v.cost_usd != null) all.push(Number(v.cost_usd)); return v; });
  assert.equal(Number(r.change.before), Math.max(...all), "the costliest receipt of the dossier is the one rewritten");
});

test("cost: a call that cost nothing is made to look like it cost a cent, and a string stays a string", () => {
  const r = T.apply(fixture[0], "cost");
  assert.equal(r.change.before, "0.000000");
  assert.equal(r.change.after, "0.010000");
  assert.equal(typeof r.change.after, "string");
});

test("verdict: LONG and SHORT swap, anything else becomes LONG, and a receipts record says it has no verdict", () => {
  const r = T.apply(dossier, "verdict");
  assert.equal(r.change.path, "verdict.action");
  assert.equal(r.copy.verdict.action, { LONG: "SHORT", SHORT: "LONG" }[dossier.verdict.action] || "LONG");
  const watch = { ...dossier, verdict: { ...dossier.verdict, action: "WATCH_REVERSAL" } };
  assert.equal(T.apply(watch, "verdict").copy.verdict.action, "LONG");
  const none = T.apply(fixture[0], "verdict");
  assert.equal(none.ok, false);
  assert.match(none.why, /receipts record.*no verdict/);
  assert.equal(T.availability(fixture[0]).verdict.ok, false);
});

test("sentence: the last sentence of a multi-sentence claim goes, the whole claim goes when it is one sentence", () => {
  const multi = JSON.parse(JSON.stringify(dossier));
  multi.bull = [{ claim: "Trend is up. Volume agrees. Funding is neutral.", weight: 1 }];
  multi.bear = [];
  delete multi.llm;
  const r = T.apply(multi, "sentence");
  assert.equal(r.change.path, "bull[0].claim");
  assert.equal(r.copy.bull[0].claim, "Trend is up. Volume agrees.");
  assert.equal(r.change.removed, "Funding is neutral.");
  const one = JSON.parse(JSON.stringify(multi));
  one.bull = [{ claim: "Trend is up", weight: 1 }, { claim: "Volume agrees with the move", weight: 1 }];
  const single = T.apply(one, "sentence");
  assert.equal(single.change.op, "delete");
  assert.equal(single.change.path, "bull[1].claim", "the longest claim is the one deleted");
  assert.match(single.change.path, /claim$/);
  assert.equal(getAt(single.copy, single.change.path), undefined);
});

test("sentence: a receipts record loses the last sentence of its output", () => {
  const r = T.apply(fixture[0], "sentence");
  assert.equal(r.change.path, "outputs.answer");
  assert.equal(r.copy.outputs.answer, "Dry-run answer. It reads nothing.");
});

test("sentence: a record with no analyst claim and no text loses one other field, never its hash or its link", () => {
  const bare = { seq: 5, prev_hash: "a".repeat(64), features: { atr_14: 1 }, hash: "b".repeat(64) };
  const r = T.apply(bare, "sentence");
  assert.ok(r.ok);
  assert.equal(r.change.what, "field");
  assert.deepEqual(Object.keys(r.copy), ["seq", "prev_hash", "hash"]);
  assert.equal(T.apply({ seq: 1, prev_hash: "a".repeat(64), hash: "b" }, "sentence").ok, false);
});

test("prev: another valid-looking hash, 64 hex digits, different at every position, and the genesis link can be swapped too", () => {
  const r = T.apply(dossier, "prev");
  assert.match(r.copy.prev_hash, /^[0-9a-f]{64}$/);
  assert.notEqual(r.copy.prev_hash, dossier.prev_hash);
  assert.ok([...r.copy.prev_hash].every((c, i) => c !== dossier.prev_hash[i]));
  assert.match(T.apply(fixture[0], "prev").copy.prev_hash, /^[0-9a-f]{64}$/);
  assert.equal(T.apply({ seq: 1, hash: "x" }, "prev").ok, false);
});

test("a record with no receipt cost says so, and availability reports a reason for each button that cannot work", () => {
  const stripped = { seq: 3, prev_hash: "a".repeat(64), verdict: { action: "FLAT" }, hash: "b".repeat(64) };
  const a = T.availability(stripped);
  assert.equal(a.cost.ok, false);
  assert.match(a.cost.why, /no model-call receipt with a cost/);
  assert.equal(a.verdict.ok, true);
  assert.equal(T.availability(null).cost.ok, false);
});

function getAt(o, p) { return p.replace(/\[(\d+)\]/g, ".$1").split(".").reduce((x, k) => (x == null ? undefined : x[k]), o); }
