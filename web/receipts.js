/* A record of kind `receipts/1` (verdict_desk/seal.py) is the sealed receipts of any workload that called models through Otari. It has no
 * verdict, so there is nothing to re-derive; what the browser can recompute is the hash and the link (verify.html), the receipts schema
 * and the generic closure checks below, the same ones `seal.evaluate` runs in Python (tests/test_seal.py compares the two on fixtures). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VerdictReceipts = api;
})(typeof self !== "undefined" ? self : this, function () {
  const KIND = "receipts/1";
  const FIELDS = ["schema", "role", "request_id", "request_id_source", "sent_model", "captured_at"];
  const isReceipts = (d) => !!d && d.kind === KIND;
  const calls = (d) => (Array.isArray(d.calls) ? d.calls.filter((c) => c && typeof c === "object") : []);
  const failedCall = (r) => !!(r && (r.refusal || r.error));
  const accounted = (r) => !!r && typeof r === "object" && (failedCall(r) || (r.request_id_source === "otari" && !!r.request_id));

  function receiptProblem(r) {
    if (!r || typeof r !== "object" || Array.isArray(r)) return "no receipt";
    const missing = FIELDS.filter((f) => typeof r[f] !== "string" || !r[f]);
    if (missing.length) return "missing " + missing.join(", ");
    if (r.schema !== "otari-receipt/1") return "schema '" + r.schema + "'";
    if (r.request_id_source !== "otari" && r.request_id_source !== "client") return "unknown request_id_source";
    if (!failedCall(r) && (!("cost_usd" in r) || !r.tokens || typeof r.tokens !== "object" || Array.isArray(r.tokens))) return "a successful call without cost_usd and tokens";
    return null;
  }

  /* USD strings as exact integers (18 decimals), so a sum and a cap compare without float error. null when the text is not a plain decimal. */
  function scaled(v) {
    const m = /^\s*\+?(\d+)(?:\.(\d{1,18}))?\s*$/.exec(String(v));
    return m ? BigInt(m[1] + (m[2] || "").padEnd(18, "0")) : null;
  }
  const usdText = (n) => { const s = n.toString().padStart(19, "0"); return (s.slice(0, -18) + "." + s.slice(-18)).replace(/\.?0+$/, "") || "0"; };

  function priced(d) {
    const cycle = d.cycle && typeof d.cycle === "object" ? d.cycle : {};
    const caps = { core: cycle.cap_usd == null ? null : scaled(cycle.cap_usd), tools: (cycle.tools || {}).cap_usd == null ? null : scaled(cycle.tools.cap_usd) };
    const problems = [];
    for (const pool of ["core", "tools"]) {
      const cap = caps[pool];
      const done = calls(d).filter((c) => (c.pool || "core") === pool && c.receipt && typeof c.receipt === "object" && !failedCall(c.receipt)).map((c) => c.receipt);
      const costs = done.map((r) => (r.cost_usd == null ? null : scaled(r.cost_usd)));
      const unpriced = costs.filter((c) => c === null).length;
      const spent = costs.reduce((a, c) => a + (c === null ? 0n : c), 0n);
      if (cap !== null && unpriced) problems.push(unpriced + " " + pool + " calls are unpriced");
      if (cap !== null && spent > cap) problems.push(pool + " spend USD " + usdText(spent) + " passed its cap of USD " + usdText(cap));
    }
    return problems.length ? [false, problems.join("; ")] : [true, "no unpriced call under a cap, and no pool over its cap"];
  }

  /* The checks as seal.py runs them. `digestOf(value)` -> Promise<sha256 hex of its canonical JSON>. */
  async function evaluate(d, frozen, digestOf) {
    const list = calls(d);
    const bad = list.filter((c) => !accounted(c.receipt));
    const schema = list.filter((c) => receiptProblem(c.receipt));
    const inputHash = await digestOf(d.input === undefined ? null : d.input);
    const inputOk = inputHash === d.input_sha256 && (frozen == null || d.input_sha256 === frozen);
    const [pricedOk, pricedWhy] = priced(d);
    const mode = (d.workload || {}).mode;
    return [
      ["calls_accounted", "hard", bad.length === 0, bad.length ? bad.length + " of " + list.length + " calls have no receipt with an Otari request id or a recorded failure" : list.length + " calls, each with an Otari request id or a recorded failure"],
      ["receipts_schema", "hard", schema.length === 0, schema.length ? schema.length + " receipts are not otari-receipt/1" : list.length + " receipts have the otari-receipt/1 fields"],
      ["input_frozen", "hard", inputOk, inputOk ? "the input hashes to input_sha256" : "the recorded input does not hash to input_sha256"],
      ["priced_within_cap", "hard", pricedOk, pricedWhy],
      ["has_calls", "soft", list.length > 0, list.length ? list.length + " calls" : "the record holds no Otari call"],
      ["live_calls", "soft", mode === "live", mode === "live" ? "live calls through the gateway" : "dry run: a fake transport answered, no Otari call was made"],
    ].map(([name, klass, pass, reason]) => ({ name, class: klass, pass, reason }));
  }

  /* check(d, digestOf) -> {schemaOk, closureOk, checks, sealedOutcome, dryRun, calls}: the receipts schema, and the closure recomputed from the
   * record alone (same pass and fail as the sealed one, check by check, every hard check passing). */
  async function check(d, digestOf) {
    const sealed = d.closure;
    const shape = Array.isArray(d.calls) && !!d.workload && typeof d.workload === "object" && !!sealed && sealed.version === 1 && Array.isArray(sealed.checks);
    const checks = shape ? await evaluate(d, sealed.input_sha256, digestOf) : [];
    const mine = shape ? Object.fromEntries(sealed.checks.map((c) => [c && c.name, c && c.pass])) : {};
    const same = shape && checks.length === Object.keys(mine).length && checks.every((c) => mine[c.name] === c.pass);
    const closureOk = same && checks.every((c) => c.class !== "hard" || c.pass);
    const schemaOk = shape && calls(d).every((c) => receiptProblem(c.receipt) === null);
    return { schemaOk, closureOk, checks, sealedOutcome: sealed && sealed.outcome, dryRun: (d.workload || {}).mode === "dry-run", calls: calls(d).length };
  }

  return { KIND, isReceipts, receiptProblem, evaluate, check };
});
