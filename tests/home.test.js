// web/index.html run against a minimal fake DOM and the real ledger: the figures it draws are counted in the browser code and must equal
// the ones scripts/otari_cards.py wrote to web/otari-figures.json (the same figures as the README card).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "web", "index.html"), "utf8");
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");
const ledgerText = fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8");
const payloadText = fs.readFileSync(path.join(ROOT, "web", "otari-figures.json"), "utf8");
const C = require("../web/cycle.js");

function mount(served) {
  const els = {};
  const el = (id) => (els[id] = els[id] || { id, textContent: "", innerHTML: "", hidden: true, href: "", dataset: {} });
  const rows = [
    { dataset: { res: "1" }, querySelector: () => badge1, ...{} },
  ];
  const badge1 = { className: "", textContent: "", remove() {} };
  const document = { getElementById: el, querySelectorAll: () => rows };
  const asked = [];
  const fetch = async (p) => { asked.push(p); return served[p] == null ? { ok: false, text: async () => "" } : { ok: true, text: async () => served[p] }; };
  new Function("document", "window", "fetch", inline)(document, { VerdictCycle: C }, fetch);
  return { els, asked, badge1, settle: () => new Promise((r) => setTimeout(r, 30)) };
}

test("the browser counts the same figures as the script that draws the README card", () => {
  const live = C.figures(ledgerText.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l)));
  const card = JSON.parse(payloadText).figures;
  assert.equal(String(live.dossiers), card[0].value);
  assert.equal(`${live.first} to ${live.last}`, card[0].sub);
  assert.equal(String(live.receipts), card[1].value);
  assert.equal(String(live.routes), card[2].value);
  assert.equal(String(live.backups), card[4].value);
});

test("the landing page draws the five figures, the latest cycle and the resource status from the ledger and the audit file", async () => {
  const m = mount({ "../ledger/ledger.jsonl": ledgerText, "otari-figures.json": payloadText });
  await m.settle();
  const card = JSON.parse(payloadText).figures;
  for (const f of card) assert.ok(m.els.figs.innerHTML.includes(`<span class="v">${f.value}</span>`), `figure ${f.label}`);
  assert.match(m.els.figs.innerHTML, /all from sealed receipts or drills/);
  assert.equal(m.els.peek.hidden, false);
  assert.equal(m.els.rcpt.hidden, false, "the hero shows one real receipt");
  assert.equal((m.els.proofs.innerHTML.match(/<a class="proof" href="cycle\.html\?rid=[0-9a-f-]{36}"/g) || []).length, 8, "one receipt link per audited feature");
  assert.equal(m.els["proofs-band"].hidden, false);
  assert.match(m.els["rcpt-rows"].innerHTML, /<dt>answered by<\/dt><dd>[^<]+:[^<]+<\/dd>/);
  assert.match(m.els["rcpt-foot"].innerHTML, /sealed in <b>#\d+<\/b> · hash [0-9a-f]{12}/);
  assert.match(m.els.peek["innerHTML"] + m.els["peek-strip"].innerHTML, /class="strip"/);
  assert.match(m.els.asof.textContent, /Counted in your browser/);
  assert.match(m.els["earlier-note"].textContent, /Dossiers 1 to 791/);
  assert.ok(m.asked.every((p) => !p.startsWith("/") && !/^https?:/.test(p)), "relative paths only");
});

test("without the ledger the page falls back to the audit file and says it did not recount", async () => {
  const m = mount({ "otari-figures.json": payloadText });
  await m.settle();
  assert.match(m.els.asof.textContent, /not recounted/);
  assert.equal(m.els.peek, undefined, "no latest cycle is drawn without a ledger");
});

test("without either file the page says what to serve", async () => {
  const m = mount({});
  await m.settle();
  assert.match(m.els.figs.innerHTML, /not served next to this page/);
});
