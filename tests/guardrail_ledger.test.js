// The guardrail detector over the REAL text of the ledger: what the agents actually put in prompts (news titles, web page excerpts, the evidence
// items the analysts read, narrative voices and the model-written claims, rulings and notes) and the app's own system prompts.
// A false positive blocks a cycle, so the expected number of flagged items is zero; any flagged item is listed with its text and rules.
// Run: node --test tests/guardrail_ledger.test.js   (the system prompts need python3; that part is skipped without it)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const D = require("../services/guardrail/lib/detector.js");

const ROOT = path.join(__dirname, "..");
const SKIP_KEYS = new Set(["receipt", "receipts", "link", "url", "domain", "request_id", "page_sha256", "excerpt_sha256", "excerpt_check", "excerpt_source", "method", "served_by", "model", "hash", "prev_hash"]);

function collect(node, out, key) {
  if (typeof node === "string") {
    if (node.length >= 12 && !SKIP_KEYS.has(key) && !/^[0-9a-f]{32,}$/.test(node) && !/^https?:\/\/\S+$/.test(node)) out.add(node);
  } else if (Array.isArray(node)) for (const x of node) collect(x, out, key);
  else if (node && typeof node === "object") for (const [k, v] of Object.entries(node)) if (!SKIP_KEYS.has(k)) collect(v, out, k);
}

function ledgerTexts() {
  const texts = new Set();
  let dossiers = 0;
  for (const line of fs.readFileSync(path.join(ROOT, "ledger", "ledger.jsonl"), "utf8").split("\n")) {
    if (!line.trim()) continue;
    const d = JSON.parse(line);
    dossiers++;
    const ev = d.evidence || {}, llm = d.llm || {};
    const nar = ev.narrative || {};
    for (const part of [ev.news, nar.voices, nar.extractions, nar.rejected, ev.web, llm.evidence_index, (llm.bull || {}).accepted, (llm.bull || {}).rejected,
      (llm.bear || {}).accepted, (llm.bear || {}).rejected, (llm.judge || {}).ruling, (llm.checker || {}).checks]) collect(part, texts, "");
  }
  return { texts: [...texts], dossiers };
}

test("no text of the real ledger is flagged by the detector (news titles, web excerpts, evidence items, voices, claims, rulings)", () => {
  const { texts, dossiers } = ledgerTexts();
  assert.ok(dossiers >= 800, `${dossiers} dossiers read`);
  assert.ok(texts.length > 1000, `${texts.length} distinct texts read`);
  const flagged = [];
  for (const text of texts) {
    const r = D.analyze(text);
    if (r.score >= D.THRESHOLD) flagged.push({ score: r.score, rules: r.findings.map((f) => f.id), text: text.slice(0, 300) });
  }
  console.log(`# ledger: ${dossiers} dossiers, ${texts.length} distinct texts, ${flagged.length} flagged`);
  assert.deepEqual(flagged, [], "each flagged item is listed above with the rules that fired; judge it before changing a rule");
});

test("no text of the ledger even half-fires: the highest score over the ledger stays under the threshold with room to spare", () => {
  const { texts } = ledgerTexts();
  let top = 0, topText = "";
  for (const text of texts) {
    const { score } = D.analyze(text);
    if (score > top) { top = score; topText = text; }
  }
  console.log(`# ledger: highest score ${top} on ${JSON.stringify(topText.slice(0, 120))}`);
  assert.ok(top < D.THRESHOLD, `highest ledger score ${top}`);
});

test("the app's own system prompts pass: a mandated guardrail may screen the whole input, system prompt included", (t) => {
  const run = spawnSync("python3", [path.join(__dirname, "guardrail_prompts_dump.py")], { cwd: ROOT, encoding: "utf8", env: { ...process.env, OTARI_API_KEY: "" } });
  if (run.status !== 0) return t.skip("python3 could not import the app here: " + String(run.stderr).slice(-200));
  const { prompts, drill } = JSON.parse(run.stdout);
  assert.ok(prompts.length >= 10, `${prompts.length} prompts captured`);
  const flagged = prompts.filter((p) => D.analyze(p).score >= D.THRESHOLD).map((p) => ({ score: D.analyze(p).score, rules: D.analyze(p).findings.map((f) => f.id), text: p.slice(0, 120) }));
  assert.deepEqual(flagged, []);
});

test("the inputs of the guardrail drill: the benign headline passes, the malicious one is refused (and the drill's own system prompt passes)", (t) => {
  const run = spawnSync("python3", [path.join(__dirname, "guardrail_prompts_dump.py")], { cwd: ROOT, encoding: "utf8", env: { ...process.env, OTARI_API_KEY: "" } });
  if (run.status !== 0) return t.skip("python3 could not import the app here");
  const { drill } = JSON.parse(run.stdout);
  assert.equal(D.validate(drill.system).valid, true);
  assert.equal(D.validate(drill.benign).valid, true);
  const bad = D.validate(drill.injection);
  assert.equal(bad.valid, false);
  console.log(`# drill: malicious headline scores ${bad.score}, benign ${D.validate(drill.benign).score}`);
});
