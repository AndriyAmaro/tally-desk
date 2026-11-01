// web/cycle.html run against a minimal fake DOM and the real ledger file: it must render, offline, from the two places a static host
// may serve the ledger (beside the page, or one level up when the repository root is served), with relative paths only.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "web", "cycle.html"), "utf8");
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");

function mount(served, search) {
  const els = {};
  const make = (id) => {
    const e = { id, textContent: "", hidden: true, value: "", listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }, _html: "" };
    // a <select> takes its first option as its value when options are written into it, as a browser does
    Object.defineProperty(e, "innerHTML", { get() { return this._html; }, set(v) { this._html = v; if (id === "pick") { const m = /value="([^"]*)"/.exec(v); this.value = m ? m[1] : ""; } } });
    return e;
  };
  const el = (id) => (els[id] = els[id] || make(id));
  const asked = [];
  const document = { getElementById: el };
  const fetch = async (p) => { asked.push(p); return served[p] == null ? { ok: false, text: async () => "" } : { ok: true, text: async () => served[p] }; };
  const C = require("../web/cycle.js");
  const fn = new Function("document", "window", "location", "fetch", "URLSearchParams", "URL", inline);
  fn(document, { VerdictCycle: C }, { search: search || "" }, fetch, URLSearchParams, URL);
  return { els, asked, settle: () => new Promise((r) => setTimeout(r, 30)) };
}

const ledger = fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8");
const config = fs.readFileSync(path.join(ROOT, "config", "otari.json"), "utf8");

test("served from the repository root (the ledger one level up) it renders the newest cycle, offline, with relative paths only", async () => {
  const m = mount({ "../ledger/ledger.jsonl": ledger, "../config/otari.json": config });
  await m.settle();
  assert.deepEqual(m.asked.slice(0, 2), ["ledger/ledger.jsonl", "../ledger/ledger.jsonl"], "tries beside the page first, then one level up");
  assert.ok(m.asked.every((p) => !p.startsWith("/") && !/^https?:/.test(p)), "no absolute path and no host");
  assert.equal(m.els.main.hidden, false);
  const calls = m.els.calls.innerHTML;
  assert.ok((calls.match(/<article class="call/g) || []).length >= 8);
  assert.match(calls, /class="role">bull</);
  assert.match(calls, /Request id/);
  assert.match(m.els.who.innerHTML, /Gateway: <b>otari\.ai<\/b>/);
  assert.match(m.els.who.innerHTML, /deepinfra/);
  assert.match(m.els.board.innerHTML, /USD per claim/);
  assert.match(m.els.status.textContent, /Otari-era dossiers, read from the published ledger/);
});

test("served from web/ (the ledger beside the page) the first path is enough", async () => {
  const m = mount({ "ledger/ledger.jsonl": ledger });
  await m.settle();
  assert.equal(m.asked[0], "ledger/ledger.jsonl");
  assert.equal(m.els.main.hidden, false);
  assert.match(m.els.who.innerHTML, /unknown \(config not served here\)/, "without the config the page says it does not know, instead of guessing");
});

test("when the ledger is not served the page says so and offers the file button", async () => {
  const m = mount({});
  await m.settle();
  assert.equal(m.els.main, undefined, "nothing is drawn");
  assert.match(m.els.status.textContent, /Open a ledger file/);
});

// The newest real dossier changes with every live cycle, so these tests clone it and set the facts they assert, instead of trusting its contents.
function clone(seq, edit) {
  const rows = ledger.trim().split("\n").map(JSON.parse);
  const d = JSON.parse(JSON.stringify(rows[rows.length - 1]));
  d.seq = seq;
  (function walk(o) {
    if (Array.isArray(o)) o.forEach(walk);
    else if (o && typeof o === "object") {
      if (o.schema === "otari-receipt/1") edit(o);
      Object.values(o).forEach(walk);
    }
  })(d);
  return d;
}

test("a refusal is drawn with its plain-language remedy and a ?seq= picks the dossier", async () => {
  const d = clone(9001, (r) => { delete r.refusal; });
  d.llm.judge.receipt = { schema: "otari-receipt/1", role: "judge", request_id: "req-refused", request_id_source: "otari", sent_model: "vd-judge", end_user: "vd-judge",
    refusal: { status: 403, code: "budget_exceeded", budget_scope: "user", retry_after: "3600" }, duration_ms: 120 };
  const m = mount({ "ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9001");
  await m.settle();
  assert.equal(m.els.pick.value, "9001");
  assert.match(m.els.calls.innerHTML, /class="call fail"/);
  assert.match(m.els.calls.innerHTML, /money set aside for this role/);
  assert.match(m.els.calls.innerHTML, /Retry after 3600 s/);
  assert.match(m.els.calls.innerHTML, /Budget that refused: user/);
  assert.match(m.els.summary.innerHTML, /1 refused or failed/);
});

test("a dossier without start offsets says the bars are only an order", async () => {
  const d = clone(9003, (r) => { delete r.started_offset_ms; });
  const m = mount({ "ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9003");
  await m.settle();
  assert.match(m.els.timenote.textContent, /only an order/);
  assert.match(m.els.calls.innerHTML, /order only/);
});

test("request ids and model names from a ledger are escaped before they reach the page", async () => {
  const rows = ledger.trim().split("\n").map(JSON.parse);
  const d = JSON.parse(JSON.stringify(rows[rows.length - 1]));
  d.seq = 9002;
  d.llm.bull.receipt.request_id = "<img src=x onerror=alert(1)>";
  d.llm.bull.receipt.sent_model = "\"><script>alert(2)</script>";
  const m = mount({ "ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9002");
  await m.settle();
  assert.ok(!m.els.calls.innerHTML.includes("<img src=x"));
  assert.ok(!m.els.calls.innerHTML.includes("<script>alert"));
  assert.match(m.els.calls.innerHTML, /&lt;img src=x/);
});
