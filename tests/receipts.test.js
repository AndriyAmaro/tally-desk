// web/receipts.js: the generic checks of a `receipts/1` record (verdict_desk/seal.py), recomputed in the browser from the record alone.
// The fixture is a ledger sealed in a dry run: fake receipts, one recorded refusal. tests/test_seal.py compares these results with Python's.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const V = require("../web/canonical.js");
const R = require("../web/receipts.js");

const records = fs.readFileSync(path.join(__dirname, "fixtures", "receipts_ledger.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const digestOf = (value) => V.webSha256(V.canonical(value));
const copy = (d) => JSON.parse(JSON.stringify(d));

test("every record of the fixture ledger is a receipts record whose receipts and closure recompute", async () => {
  assert.equal(records.length, 3);
  for (const d of records) {
    assert.ok(R.isReceipts(d));
    const r = await R.check(d, digestOf);
    assert.deepEqual([r.schemaOk, r.closureOk, r.dryRun], [true, true, true], `#${d.seq}`);
    assert.equal(r.sealedOutcome, "warning", "a dry run is sealed with its warning");
  }
  assert.equal(R.isReceipts({ seq: 1, verdict: {} }), false, "a trading dossier is not one");
});

test("a recorded refusal counts as an accounted call", async () => {
  const refused = records[1];
  assert.ok(refused.calls[0].receipt.refusal);
  assert.equal((await R.check(refused, digestOf)).closureOk, true);
});

test("a receipt turned into a client id, a missing receipt, a wrong schema and a changed input each break the closure", async () => {
  const a = copy(records[0]); a.calls[0].receipt.request_id_source = "client";
  const b = copy(records[0]); b.calls[0].receipt = null;
  const c = copy(records[0]); c.calls[0].receipt.schema = "other/1";
  const e = copy(records[0]); e.input = { url: "https://example.test/other" };
  for (const [name, d] of [["client id", a], ["no receipt", b], ["schema", c], ["input", e]]) {
    const r = await R.check(d, digestOf);
    assert.equal(r.closureOk, false, name);
  }
  assert.equal((await R.check(c, digestOf)).schemaOk, false);
});

test("an unpriced call under a cap and a spend over the cap are refused; a failed call costs nothing", async () => {
  const unpriced = copy(records[0]); unpriced.calls[0].receipt.cost_usd = null;
  const over = copy(records[0]); over.calls[0].receipt.cost_usd = "0.5";
  const fine = copy(records[0]); fine.calls[0].receipt.cost_usd = "0.049999999999999999";
  const find = (r, name) => r.checks.find((c) => c.name === name);
  assert.equal(find(await R.check(unpriced, digestOf), "priced_within_cap").pass, false);
  assert.equal(find(await R.check(over, digestOf), "priced_within_cap").pass, false);
  assert.equal(find(await R.check(fine, digestOf), "priced_within_cap").pass, true);
});

test("a closure that was edited does not recompute to itself", async () => {
  const d = copy(records[0]);
  d.closure.checks.find((c) => c.name === "live_calls").pass = true;  // claims the dry run was live
  assert.equal((await R.check(d, digestOf)).closureOk, false);
  const gone = copy(records[0]); delete gone.closure;
  assert.equal((await R.check(gone, digestOf)).closureOk, false);
});
