/* The "Try to cheat it" panel of verify.html: four single changes to a COPY of a record. Nothing here reads or writes anything outside the
 * object it is given; the original is never touched (apply() works on a JSON clone). The page then re-runs its own checks on the copy. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VerdictTamper = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const KINDS = ["cost", "verdict", "sentence", "prev"];
  const RECEIPTS = "receipts/1";
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const pathText = (p) => p.reduce((s, k) => (typeof k === "number" ? s + "[" + k + "]" : s ? s + "." + k : String(k)), "");
  const getAt = (o, p) => p.reduce((x, k) => (x == null ? undefined : x[k]), o);

  function walk(node, path, visit) {
    visit(node, path);
    if (Array.isArray(node)) node.forEach((v, i) => walk(v, path.concat(i), visit));
    else if (isObj(node)) Object.keys(node).forEach((k) => walk(node[k], path.concat(k), visit));
  }

  /* ---- 1. the cost of one model call ---- */
  function decimals(text) { const m = /\.(\d+)/.exec(String(text)); return m ? m[1].length : 0; }
  function cheaper(cost) {
    const n = Number(cost);
    const asText = typeof cost === "string";
    const digits = Math.max(decimals(cost), 6);
    const fmt = (x) => (asText ? x.toFixed(digits) : Math.round(x * 1e6) / 1e6);
    const half = fmt(n / 2);
    if (n > 0 && Number(half) !== n) return half;
    return fmt(n + 0.01);  // a call that cost nothing cannot look cheaper: it is made to look like it cost one cent
  }
  function planCost(d) {
    let best = null;
    walk(d, [], (node, path) => {
      if (isObj(node) && node.schema === "otari-receipt/1" && node.cost_usd != null && Number.isFinite(Number(node.cost_usd))) {
        if (!best || Number(node.cost_usd) > Number(best.before)) best = { path: path.concat("cost_usd"), before: node.cost_usd };
      }
    });
    if (!best) return { ok: false, why: "This record has no model-call receipt with a cost, so there is no price to rewrite." };
    return { ok: true, op: "set", what: "cost", path: best.path, before: best.before, after: cheaper(best.before) };
  }

  /* ---- 2. the verdict action ---- */
  const FLIP = { LONG: "SHORT", SHORT: "LONG" };
  function planVerdict(d) {
    const action = isObj(d.verdict) ? d.verdict.action : undefined;
    if (typeof action !== "string") {
      return { ok: false, why: d.kind === RECEIPTS ? "This is a receipts record: it has no verdict, so there is no action to flip." : "This record has no verdict action to flip." };
    }
    return { ok: true, op: "set", what: "action", path: ["verdict", "action"], before: action, after: FLIP[action] || "LONG" };
  }

  /* ---- 3. one sentence of one analyst claim (or, failing that, one field) ---- */
  // a full stop ends a sentence only before a space or the end, so "-22.0%" and "v1.5" stay whole
  const sentences = (text) => String(text).match(/[\s\S]*?[.!?]+(?=\s|$)\s*|[\s\S]+$/g) || [String(text)];
  function claimPaths(d) {
    const out = [];
    const take = (list, base) => { if (Array.isArray(list)) list.forEach((c, i) => { if (isObj(c) && typeof c.claim === "string" && c.claim.trim()) out.push(base.concat(i, "claim")); }); };
    ["bull", "bear"].forEach((side) => {
      take(isObj(d.llm) && isObj(d.llm[side]) ? d.llm[side].accepted : null, ["llm", side, "accepted"]);
      take(d[side], [side]);
    });
    return out;
  }
  function dropLast(path, text) {
    const parts = sentences(text);
    return { ok: true, op: "set", what: "sentence", path, before: text, after: parts.slice(0, -1).join("").trim(), removed: parts[parts.length - 1].trim() };
  }
  function planSentence(d) {
    const claims = claimPaths(d).map((p) => ({ path: p, text: getAt(d, p) })).sort((a, b) => b.text.length - a.text.length);
    const many = claims.find((c) => sentences(c.text).length > 1);
    if (many) return dropLast(many.path, many.text);
    const key = isObj(d.outputs) ? Object.keys(d.outputs).find((k) => typeof d.outputs[k] === "string" && sentences(d.outputs[k]).length > 1) : undefined;
    if (key !== undefined) return dropLast(["outputs", key], d.outputs[key]);
    // every claim is a single sentence: that sentence is the thing deleted
    if (claims.length) return { ok: true, op: "delete", what: "claim", path: claims[0].path, before: claims[0].text, removed: claims[0].text.trim() };
    const skip = { hash: 1, seq: 1, prev_hash: 1 };
    const field = Object.keys(d).filter((k) => !skip[k]).pop();
    if (field === undefined) return { ok: false, why: "This record has no field other than its hash and its link to delete." };
    return { ok: true, op: "delete", what: "field", path: [field], before: d[field], removed: field };
  }

  /* ---- 4. prev_hash swapped for another valid-looking hash ---- */
  function otherHash(h) {
    const s = String(h);
    const out = s.replace(/[0-9a-f]/g, (c) => ((parseInt(c, 16) + 8) % 16).toString(16));
    return out !== s ? out : "8".repeat(64);
  }
  function planPrev(d) {
    if (typeof d.prev_hash !== "string" || !d.prev_hash) return { ok: false, why: "This record names no previous hash, so there is no link to swap." };
    return { ok: true, op: "set", what: "link", path: ["prev_hash"], before: d.prev_hash, after: otherHash(d.prev_hash) };
  }

  const PLANNERS = { cost: planCost, verdict: planVerdict, sentence: planSentence, prev: planPrev };
  const plan = (d, kind) => (isObj(d) && PLANNERS[kind] ? PLANNERS[kind](d) : { ok: false, why: "There is no record to change." });

  /* availability(record) -> {cost: {ok, why?}, ...}: what each button can do on this record. */
  function availability(d) {
    const out = {};
    KINDS.forEach((k) => { const p = plan(d, k); out[k] = p.ok ? { ok: true } : { ok: false, why: p.why }; });
    return out;
  }

  /* apply(record, kind) -> {ok:true, copy, change} | {ok:false, why}. `copy` is a deep clone with the single change made; `record` is not touched. */
  function apply(d, kind) {
    const p = plan(d, kind);
    if (!p.ok) return p;
    const copy = clone(d);
    const parent = getAt(copy, p.path.slice(0, -1));
    const key = p.path[p.path.length - 1];
    if (p.op === "delete") delete parent[key]; else parent[key] = p.after;
    return { ok: true, copy, change: { kind, op: p.op, what: p.what, path: pathText(p.path), before: p.before, after: p.after, removed: p.removed } };
  }

  return { KINDS, plan, availability, apply, pathText };
});
