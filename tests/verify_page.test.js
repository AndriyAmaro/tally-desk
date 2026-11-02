// web/verify.html run against a minimal fake DOM and the real ledger: the linkage check must read the ledger from the two places a
// static host may serve it (beside the page, or one level up when the repository root is served) and must say the same thing in the
// title and in the Linkage row, whichever layout it runs in.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "web", "verify.html"), "utf8");
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");
const ledger = fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8");
const lines = ledger.split("\n").filter((l) => l.trim());

function mount(served) {
  const els = {};
  const make = (id) => ({ id, textContent: "", innerHTML: "", hidden: true, value: "", listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }, click() { return this.listeners.click(); } });
  const el = (id) => (els[id] = els[id] || make(id));
  for (const id of ["detail", "file", "go", "input", "out", "sample", "status", "verdict"]) el(id);
  const asked = [];
  const fetch = async (p) => { asked.push(p); return served[p] == null ? { ok: false, text: async () => "" } : { ok: true, text: async () => served[p] }; };
  const window = { VerdictDesk: require("../web/canonical.js"), VerdictPolicy: require("../web/policy.js") };
  new Function("document", "window", "location", "fetch", inline)({ getElementById: el }, window, { search: "" }, fetch);
  const settle = () => new Promise((r) => setTimeout(r, 60));
  return { els, asked, settle };
}

async function verifyNewest(m) {
  m.els.input.value = lines[lines.length - 1];
  await m.els.go.click();
  await m.settle();
}

test("served from web/ (the ledger beside the page) the newest dossier is linked, in the title and in the row", async () => {
  const m = mount({ "ledger/ledger.jsonl": ledger });
  await verifyNewest(m);
  assert.deepEqual(m.asked, ["ledger/ledger.jsonl"], "found at the first place, nothing else asked");
  assert.match(m.els.verdict.innerHTML, /intact, linked/);
  assert.match(m.els.detail.innerHTML, /<span class="ok">linked<\/span>/);
  assert.doesNotMatch(m.els.detail.innerHTML, /broken link/);
});

test("served from the repository root (the ledger one level up) it is linked too, with relative paths only", async () => {
  const m = mount({ "../ledger/ledger.jsonl": ledger });
  await verifyNewest(m);
  assert.deepEqual(m.asked, ["ledger/ledger.jsonl", "../ledger/ledger.jsonl"]);
  assert.ok(m.asked.every((p) => !p.startsWith("/") && !/^https?:/.test(p)));
  assert.match(m.els.verdict.innerHTML, /intact, linked/);
  assert.match(m.els.detail.innerHTML, /<span class="ok">linked<\/span>/);
  assert.doesNotMatch(m.els.detail.innerHTML, /broken link/);
});

test("a host that answers a missing ledger with an HTML page does not count as the ledger", async () => {
  const m = mount({ "ledger/ledger.jsonl": "<!doctype html><title>404</title>", "../ledger/ledger.jsonl": ledger });
  await verifyNewest(m);
  assert.match(m.els.detail.innerHTML, /<span class="ok">linked<\/span>/);
});

test("when no ledger is served the title and the row agree: linkage was not checked, nothing is called linked or broken", async () => {
  const m = mount({});
  await verifyNewest(m);
  assert.doesNotMatch(m.els.verdict.innerHTML, /intact, linked/);
  assert.match(m.els.verdict.innerHTML, /linkage not checked/);
  assert.match(m.els.detail.innerHTML, /<span class="mut">not checked<\/span>/);
  assert.doesNotMatch(m.els.detail.innerHTML, /broken link/);
});

test("a record that really does not follow its predecessor is still a broken link in both layouts", async () => {
  const d = JSON.parse(lines[lines.length - 1]);
  const other = lines.map(JSON.parse);
  other[other.length - 2] = { ...other[other.length - 2], hash: "f".repeat(64) };  // the ledger copy names a different previous hash
  const text = other.map((r) => JSON.stringify(r)).join("\n");
  for (const served of [{ "ledger/ledger.jsonl": text }, { "../ledger/ledger.jsonl": text }]) {
    const m = mount(served);
    m.els.input.value = JSON.stringify(d);
    await m.els.go.click();
    await m.settle();
    assert.match(m.els.verdict.innerHTML, /NOT VERIFIED/);
    assert.match(m.els.detail.innerHTML, /<span class="bad">broken link<\/span>/);
  }
});

test("the sample button reads the ledger from either layout", async () => {
  for (const served of [{ "ledger/ledger.jsonl": ledger }, { "../ledger/ledger.jsonl": ledger }]) {
    const m = mount(served);
    await m.els.sample.click();
    await m.settle();
    await m.settle();  // the click starts the check without waiting for it
    assert.equal(m.els.input.value.split("\n").length, 2, "two real records were loaded");
    assert.match(m.els.verdict.innerHTML, /intact, linked/, "and checked against the same ledger");
  }
});
