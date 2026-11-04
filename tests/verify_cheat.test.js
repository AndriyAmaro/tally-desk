// The "Try to cheat it" panel of web/verify.html, run against the same minimal fake DOM as verify_page.test.js: each change must make the
// right check fail on a real dossier and on a receipts/1 record, Undo must bring the green back, and the record that was loaded must not change.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "web", "verify.html"), "utf8");
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");
const ledger = fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8");
const lines = ledger.split("\n").filter((l) => l.trim());
const fixture = fs.readFileSync(path.join(ROOT, "tests", "fixtures", "receipts_ledger.jsonl"), "utf8").split("\n").filter((l) => l.trim());
const KINDS = ["cost", "verdict", "sentence", "prev"];

function mount(served, loc) {
  const els = {};
  const make = (id) => ({ id, textContent: "", innerHTML: "", hidden: true, disabled: false, value: "", listeners: {}, attrs: {}, addEventListener(t, f) { this.listeners[t] = f; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, click() { return this.listeners.click(); }, focus() { this.focused = true; } });
  const el = (id) => (els[id] = els[id] || make(id));
  for (const id of ["detail", "file", "go", "input", "out", "sample", "status", "verdict", "cheat", "cheat-result", "cheat-undo", "cheat-target", ...KINDS.map((k) => "cheat-" + k)]) el(id);
  const asked = [];
  const fetch = async (p) => { asked.push(p); return served[p] == null ? { ok: false, text: async () => "" } : { ok: true, text: async () => served[p] }; };
  const window = { VerdictDesk: require("../web/canonical.js"), VerdictPolicy: require("../web/policy.js"), VerdictReceipts: require("../web/receipts.js"), VerdictTamper: require("../web/tamper.js") };
  new Function("document", "window", "location", "fetch", inline)({ getElementById: el }, window, loc || { search: "" }, fetch);
  const settle = () => new Promise((r) => setTimeout(r, 60));
  const after = () => { const s = els["cheat-result"].innerHTML; return s.slice(s.indexOf('data-col="after"')); };
  return { els, asked, settle, after };
}

async function loadNewest(served) {
  const m = mount(served || { "ledger/ledger.jsonl": ledger });
  m.els.input.value = lines[lines.length - 1];
  await m.els.go.click();
  await m.settle();
  return m;
}
async function press(m, kind) { await m.els["cheat-" + kind].click(); await m.settle(); }
const state = (html, check) => (new RegExp('data-check="' + check + '" data-state="(\\w+)"').exec(html) || [])[1];

test("the panel is hidden until a record is loaded, then every button is enabled for a real dossier and the original check stays green", async () => {
  const m = mount({ "ledger/ledger.jsonl": ledger });
  assert.equal(m.els.cheat.hidden, true);
  m.els.input.value = lines[lines.length - 1];
  await m.els.go.click();
  await m.settle();
  assert.equal(m.els.cheat.hidden, false);
  for (const k of KINDS) assert.equal(m.els["cheat-" + k].disabled, false, k);
  assert.match(m.els.verdict.innerHTML, /VERIFIED/);
  assert.doesNotMatch(m.els.verdict.innerHTML, /NOT VERIFIED/);
});

test("cost: integrity fails with the two hashes side by side, linkage and re-derivation still pass", async () => {
  const m = await loadNewest();
  const d = JSON.parse(lines[lines.length - 1]);
  await press(m, "cost");
  const a = m.after();
  assert.equal(state(a, "integrity"), "bad");
  assert.equal(state(a, "linkage"), "ok");
  assert.equal(state(a, "rederivation"), "ok");
  assert.ok(a.includes(d.hash.slice(0, 20)), "the stored hash is shown");
  assert.doesNotMatch(a.slice(a.indexOf('data-check="integrity"'), a.indexOf('data-check="linkage"')).replace(/&[a-z]+;/g, ""), new RegExp("recomputed[^<]*" + d.hash.slice(0, 20)), "the recomputed one is a different hash");
  assert.match(m.els["cheat-result"].innerHTML, /cost_usd/);
  assert.match(m.els["cheat-result"].innerHTML, /NOT VERIFIED/);
  assert.equal(m.els["cheat-cost"].attrs["aria-pressed"], "true");
  assert.equal(m.els["cheat-undo"].hidden, false);
});

test("verdict: integrity AND re-derivation fail, and the words say which action the rules produce", async () => {
  const m = await loadNewest();
  await press(m, "verdict");
  const a = m.after();
  assert.equal(state(a, "integrity"), "bad");
  assert.equal(state(a, "linkage"), "ok");
  assert.equal(state(a, "rederivation"), "bad");
  assert.match(a, /the rules now produce a different action/);
  assert.match(m.els["cheat-result"].innerHTML, /verdict\.action/);
});

test("sentence: integrity fails, and the rules cannot see a deleted sentence, which the page says", async () => {
  const m = await loadNewest();
  await press(m, "sentence");
  const a = m.after();
  assert.equal(state(a, "integrity"), "bad");
  assert.equal(state(a, "linkage"), "ok");
  assert.equal(state(a, "rederivation"), "ok");
  assert.match(m.els["cheat-result"].innerHTML, /only the hash/i);
});

test("prev: integrity and linkage both fail, naming the previous record", async () => {
  const m = await loadNewest();
  await press(m, "prev");
  const a = m.after();
  assert.equal(state(a, "integrity"), "bad");
  assert.equal(state(a, "linkage"), "bad");
  assert.match(a, /previous record \d+ carries a different hash/);
});

test("each button replaces the last change, so only one change is ever applied", async () => {
  const m = await loadNewest();
  await press(m, "cost");
  await press(m, "prev");
  assert.equal(m.els["cheat-cost"].attrs["aria-pressed"], "false");
  assert.equal(m.els["cheat-prev"].attrs["aria-pressed"], "true");
  assert.equal(state(m.after(), "linkage"), "bad");
  assert.doesNotMatch(m.els["cheat-result"].innerHTML, /cost_usd/);
});

test("Undo restores the original: it verifies again with the very hash it had, and the buttons are free again", async () => {
  const m = await loadNewest();
  const d = JSON.parse(lines[lines.length - 1]);
  await press(m, "verdict");
  await m.els["cheat-undo"].click();
  await m.settle();
  const r = m.els["cheat-result"].innerHTML;
  assert.match(r, /verifies again/i);
  assert.doesNotMatch(r, /data-state="bad"/);
  assert.ok(r.includes(d.hash.slice(0, 20)));
  assert.equal(m.els["cheat-undo"].hidden, true);
  for (const k of KINDS) assert.equal(m.els["cheat-" + k].attrs["aria-pressed"], "false");
});

test("nothing is mutated, uploaded or saved: the textarea is as it was, and the only fetch is the public ledger file", async () => {
  const m = await loadNewest();
  const typed = m.els.input.value;
  for (const k of KINDS) { await press(m, k); }
  await m.els["cheat-undo"].click();
  await m.settle();
  assert.equal(m.els.input.value, typed);
  assert.ok(m.asked.every((p) => p === "ledger/ledger.jsonl"), "asked: " + m.asked.join(", "));
  // the first verification is still what the page shows above the panel
  assert.match(m.els.verdict.innerHTML, /intact, linked/);
});

test("a receipts/1 record: verdict is disabled with a one-line reason, the other three each make the right check fail", async () => {
  const m = mount({});
  m.els.input.value = fixture[0];
  await m.els.go.click();
  await m.settle();
  assert.equal(m.els["cheat-verdict"].disabled, true);
  assert.match(m.els["cheat-verdict-why"].textContent, /no verdict/);
  for (const k of ["cost", "sentence", "prev"]) assert.equal(m.els["cheat-" + k].disabled, false, k);
  for (const k of ["cost", "sentence", "prev"]) {
    await press(m, k);
    const a = m.after();
    assert.equal(state(a, "integrity"), "bad", k);
    assert.match(a, /altered: recomputed hash differs/);
  }
  assert.deepEqual(m.asked, [], "the published ledger is not read for a receipts record");
});

test("a receipts/1 ledger: swapping prev_hash on a later record breaks the link to the one pasted before it, and the closure check is replayed", async () => {
  const m = mount({});
  m.els.input.value = fixture.join("\n");
  await m.els.go.click();
  await m.settle();
  assert.equal(m.els["cheat-target"].value, "0");
  m.els["cheat-target"].value = "1";
  await m.els["cheat-target"].listeners.change();
  await press(m, "prev");
  const a = m.after();
  assert.equal(state(a, "integrity"), "bad");
  assert.equal(state(a, "linkage"), "bad");
  assert.match(a, /previous record 1 carries a different hash/);
  assert.equal(state(a, "rederivation"), "ok", "the receipts schema and closure are not what a link swap touches");
});

test("an early record with no model receipt: the cost button is disabled with its reason, the other three still catch their change", async () => {
  const d = JSON.parse(lines[0]);
  assert.ok(!JSON.stringify(d).includes('"cost_usd"'), "the first ledger record carries no receipt");
  const m = mount({ "ledger/ledger.jsonl": ledger });
  m.els.input.value = JSON.stringify(d);
  await m.els.go.click();
  await m.settle();
  assert.equal(m.els["cheat-cost"].disabled, true);
  assert.match(m.els["cheat-cost-why"].textContent, /no model-call receipt with a cost/);
  for (const k of ["verdict", "sentence", "prev"]) {
    assert.equal(m.els["cheat-" + k].disabled, false, k);
    await press(m, k);
    assert.equal(state(m.after(), "integrity"), "bad", k);
  }
  assert.equal(state(m.after(), "linkage"), "bad", "the genesis claim is gone and the ledger has no record before it");
});

test("?real=1#cheat loads only the newest dossier, shows the panel, and brings it into view", async () => {
  const m = mount({ "ledger/ledger.jsonl": ledger }, { search: "?real=1", hash: "#cheat" });
  m.els.cheat.scrollIntoView = () => { m.scrolled = true; };
  await m.settle();
  await m.settle();
  assert.equal(m.els.input.value, lines[lines.length - 1]);
  assert.equal(m.els.cheat.hidden, false);
  assert.match(m.els.verdict.innerHTML, /intact, linked/);
});
