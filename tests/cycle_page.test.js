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
  assert.deepEqual(m.asked, ["../ledger/ledger.jsonl", "../config/otari.json"], "the paths that work from the repository root come first: nothing is requested that does not exist there");
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

test("served from web/ (the ledger beside the page) the second path is found", async () => {
  const m = mount({ "ledger/ledger.jsonl": ledger });
  await m.settle();
  assert.deepEqual(m.asked.slice(0, 2), ["../ledger/ledger.jsonl", "ledger/ledger.jsonl"]);
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
// The base of the fixtures is the newest dossier in which all nine roles answered: a cycle that skipped a tool role is a real
// outcome, so the newest dossier alone cannot be assumed to be complete (the ledger grows every hour).
const NINE = ["bull", "bear", "judge", "checker", "narrator", "searcher", "fetcher", "sandbox", "mcp"];
function rolesOf(d) {
  // the labels the page itself counts: a drift re-fetch carries the role "fetcher" but is labeled "drift", so it is not the page fetch
  return new Set(require("../web/cycle.js").callsOf(d).map((c) => c.label));
}

function clone(seq, edit) {
  const rows = ledger.trim().split("\n").map(JSON.parse);
  const complete = rows.filter((r) => r.llm && r.llm.otari && NINE.every((x) => rolesOf(r).has(x)));
  const base = complete.length ? complete[complete.length - 1] : rows[rows.length - 1];
  const d = JSON.parse(JSON.stringify(base));
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

function withCanary(seq, mutate) {
  const d = clone(seq, (r) => { delete r.refusal; });
  d.llm.otari.canary = { schema: "otari-canary/1", label: "failover canary (not a judgment: its answer is never read)", policy: "vd-judge-drill", passed: true,
    receipt: { schema: "otari-receipt/1", role: "judge", request_id: "canary-req", request_id_source: "otari", sent_model: "vd-judge-drill", served_by: "deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731",
      served_model: "deepseek-ai/DeepSeek-V4-Flash-0731", provider: "deepinfra", attempt_count: 2, fallback: true, duration_ms: 1700, cost_usd: "0.002104", started_offset_ms: 100000, end_user: "vd-canary" } };
  d.llm.otari.headroom = { schema: "otari-headroom/1", limit_usd: "0.10", max_fraction: 0.8, expected_budget_id: "52a154a1-a369-45b9-8922-6728c4fe53c6", judge_user: "vd-judge", judge_ok: true,
    reason: "the judge's end user has used USD 0.007204 of its USD 0.10 day (7%, under 80%): room for the hard route",
    users: { "vd-judge": { found: true, budget_id: "52a154a1-a369-45b9-8922-6728c4fe53c6", used_usd: "0.007204", fraction: "0.0720" }, "vd-ghost": { found: false } } };
  if (mutate) mutate(d);
  return d;
}

test("the canary is a labeled call, the headroom is a table, the roles are counted and the narrator batches are one collapsed group", async () => {
  const d = withCanary(9010);
  const m = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9010");
  await m.settle();
  const calls = m.els.calls.innerHTML;
  assert.match(calls, /class="role">canary</);
  assert.match(calls, /failover canary: its answer is never read/);
  assert.equal((calls.match(/<details class="group">/g) || []).length, 1, "one group for the narrator");
  assert.ok(calls.indexOf('class="role">narrator<') > calls.indexOf('<details class="group">'), "narrator calls only inside the group");
  assert.ok(calls.indexOf('class="role">bull<') < calls.indexOf('<details class="group">'), "the deciding roles come first");
  assert.match(calls, /Narrator<\/b>: \d+ batches of headlines/);
  assert.match(m.els.summary.innerHTML, /9 of 9<\/b>plus the failover canary/);
  assert.match(m.els.headroom.innerHTML, /room for the hard route/);
  assert.match(m.els.headroom.innerHTML, /vd-analyst/);
  assert.match(m.els.headroom.innerHTML, /not read/);
});

test("a call with no duration says n/a (replayed) and draws no bar; a hard route shows the model that was sent, not a policy chip", async () => {
  const d = withCanary(9011, (x) => {
    x.llm.bull.receipt.duration_ms = null;
    x.llm.judge.receipt.route_rule = { rule: "judge-difficulty/v2", hard: true, reason: "many claims: 8 validated claims", model: "deepseek/deepseek-v4-pro" };
    x.llm.judge.receipt.sent_model = "deepseek/deepseek-v4-pro";
    x.llm.judge.receipt.policy = null;
  });
  const m = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9011");
  await m.settle();
  const calls = m.els.calls.innerHTML;
  // only bull's own card: calls are ordered by start time, so the injected canary (offset 100000) can sit between bull and bear
  const from = calls.indexOf('class="role">bull<');
  const to = calls.indexOf('<article class="call', from);
  const bull = calls.slice(from, to < 0 ? undefined : to);
  assert.match(bull, /n\/a \(replayed\)/);
  assert.ok(!bull.includes('class="track"'), "no bar without a duration");
  assert.ok(!/hard case, larger model/.test(calls), "no policy-like chip for the hard route");
  assert.match(calls, /Model sent <\/span>deepseek\/deepseek-v4-pro/);
});

const CHECKS = ["receipts", "evidence_frozen", "verdict_replays", "checker_gate", "narrative_flagged", "headroom_lookups", "priced_within_cap", "narrative_complete"];
const closureOf = (failing) => ({ version: 1, outcome: failing ? "warning" : "pass", checks: CHECKS.map((name, i) => ({ name, class: i < 5 ? "hard" : "soft", pass: name !== failing, reason: "r" })) });

test("a dossier recorded before the closure gate and the drift seal renders as before and says it has neither", async () => {
  const d = clone(9020, (r) => { delete r.refusal; });
  delete d.closure;
  delete d.drift;
  const m = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9020");
  await m.settle();
  assert.equal(m.els.main.hidden, false);
  assert.match(m.els.gates.innerHTML, /Closure gate: not applied to this dossier/);
  assert.match(m.els.gates.innerHTML, /Source drift: not checked in this dossier/);
  assert.match(m.els.calls.innerHTML, /class="role">bull</);
});

test("a dossier with the closure gate and the drift seal shows one line each, and the drift fetch is a call of its own", async () => {
  const d = clone(9021, (r) => { delete r.refusal; });
  d.closure = closureOf("narrative_complete");
  d.drift = { schema: "otari-drift/1", status: "changed", source_dossier: 808, minutes_after_decision: 190,
    receipt: { schema: "otari-receipt/1", role: "fetcher", request_id: "drift-req", request_id_source: "otari", sent_model: "deepseek/deepseek-v4-flash-0731", served_by: "deepinfra:x", provider: "deepinfra",
      duration_ms: 3000, cost_usd: "0.010", started_offset_ms: 150000, end_user: "vd-fetcher" } };
  const m = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9021");
  await m.settle();
  assert.match(m.els.gates.innerHTML, /Closure gate: 7 of 8 checks passed \(1 warning: narrative_complete\)/);
  assert.match(m.els.gates.innerHTML, /Source drift: changed \(dossier 808, 190 min after the decision\)/);
  assert.match(m.els.calls.innerHTML, /class="role">drift</);
  assert.match(m.els.calls.innerHTML, /drift-req/);
  d.closure = closureOf(null);
  const m2 = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9021");
  await m2.settle();
  assert.match(m2.els.gates.innerHTML, /Closure gate: 8 of 8 checks passed<\/li>/);
});

test("closure and drift fields of the wrong shape never break the page", async () => {
  const d = clone(9022, (r) => { delete r.refusal; });
  d.closure = { checks: "nope" };
  d.drift = "nope";
  const m = mount({ "../ledger/ledger.jsonl": ledger + JSON.stringify(d) + "\n" }, "?seq=9022");
  await m.settle();
  assert.equal(m.els.main.hidden, false);
  assert.match(m.els.gates.innerHTML, /not applied/);
});
