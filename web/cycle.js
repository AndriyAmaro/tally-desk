/* One decision cycle as a timeline of its calls, the open-model scoreboard and "who saw this prompt", all read from the receipts sealed in
 * the dossiers. Pure data in, pure data out: no DOM, no network. `verdict_desk/otari_report.py` computes the same scoreboard and the same
 * who-saw view, and tests/test_otari_report.py fails when the two differ. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VerdictCycle = api;
})(typeof self !== "undefined" ? self : this, function () {
  const REGION_NOTE = {
    eu: "EU (the host starts with eu.)",
    default: "Otari's default region (the host has no eu. prefix)",
  };

  /* What a failure means and what to do about it, in plain words. Keyed by the code Otari sent, or by the kind we recorded when no answer came. */
  const REMEDY = {
    budget_exceeded: ["The money set aside for this role (or for the whole organization) for today is used up. Nothing was sent to the model.", "Wait for the budget to reset (the dashboard budgets reset daily, UTC), or raise the budget in the Otari dashboard."],
    insufficient_funds: ["The Otari wallet has no credit left. Nothing was sent to the model.", "Add credit in the Otari dashboard, then run the cycle again."],
    cycle_cap_exceeded: ["Our own spending cap for this cycle stopped the call before it was sent.", "Raise cycle_cap_usd or tool_cap_usd in config/otari.json if that cap is too low, or wait for the next cycle."],
    rate_limited: ["Otari asked us to slow down.", "Wait for the time in Retry-After, then try again."],
    upstream_rate_limited: ["The provider behind the model asked for a pause.", "Wait for Retry-After; a routing policy with a backup model would hand the request over."],
    model_not_allowed: ["This key may not use that model.", "Allow the model for the key in the dashboard, or choose another model in config/otari.json."],
    model_not_serving: ["The model is switched off for this organization.", "Switch it on in the dashboard (Providers), or choose another model."],
    provider_not_configured: ["No credential exists for that provider on this gateway.", "Use a model of a provider the gateway serves, or ask the operator to add the provider."],
    provider_credential_rejected: ["The provider rejected the credential the gateway holds.", "Nothing to fix on our side: tell the gateway operator."],
    pricing_required: ["The gateway cannot bill this model because it has no price.", "Choose a priced model; check the id for a typo."],
    end_user_budget_not_allowed: ["This key may not put end users on that budget.", "Send the id of a budget the key lists (the header takes the id, not the name)."],
    end_user_limit_reached: ["The key already has as many end users as it may have.", "Delete end users you do not need, or raise max_end_users."],
    guardrail_violation: ["A guardrail in block mode flagged the input.", "Review the input; nothing was sent to the model."],
    all_candidates_rejected: ["Every model in the plan refused the request as invalid.", "Fix the request (size, parameters); a different model will not help."],
    context_length_exceeded: ["The prompt is longer than the model's context.", "Shorten the prompt or use a model with a longer context."],
    timeout: ["No answer came before our timeout.", "Try again; the request is safe to repeat because it carries an Idempotency-Key."],
    connection: ["The connection to the gateway failed.", "Check the network and try again; the request is safe to repeat."],
    "409": ["Otari was still working on the first attempt of this request and kept answering 'in progress'.", "The retry waits longer each time; a slow model with a long answer is the usual cause: lower max_tokens or choose a faster model."],
    "504": ["The edge timed out before the gateway answered (about 30 seconds).", "The request is safe to repeat with the same Idempotency-Key; a shorter answer or a faster model avoids it."],
    "403": ["The gateway refused the request.", "Read the error code on the receipt for the reason."],
    "402": ["The gateway cannot bill the request.", "Check the wallet and the model's price."],
  };

  function failureOf(r) {
    const bad = r.refusal || r.error;
    if (!bad) return null;
    const code = String(bad.code || bad.kind || bad.status);
    const hit = REMEDY[code] || REMEDY[String(bad.status)] || ["The call failed.", "See the receipt for the details."];
    return { code, scope: bad.budget_scope || null, retryAfter: bad.retry_after || null, source: bad.source || null, what: hit[0], fix: hit[1] };
  }

  const era = (ledger) => ledger.filter((d) => d && d.llm && d.llm.otari && typeof d.llm.otari === "object");
  const failed = (r) => !!(r.refusal || r.error);

  /* What a receipt says about the app's own role fallback chain (`role_fallback`), or null when the first model it asked for served the call.
   * It is the app moving to the next catalog id of a role, not Otari's provider failover (`fallback`, `attempt_count`); verdict_desk/otari_report.py
   * `role_fallback_of` writes the same line. */
  function roleFallbackOf(r) {
    const note = r && r.role_fallback;
    if (!note || typeof note !== "object" || !Array.isArray(note.skipped)) return null;
    const skipped = note.skipped.filter((x) => x && typeof x === "object");
    if (!skipped.length) return null;
    const total = Array.isArray(note.chain) ? note.chain.length : 0;
    const why = skipped.map((x) => `${x.model} (${x.reason}${x.status ? ", HTTP " + x.status : ""})`).join(", ");
    let kind, line;
    if (note.answered) {
      kind = "answered";
      line = `Role fallback: answered by ${note.answered}, entry ${skipped.length + 1} of ${total || "?"}, after skipping ${why}`;
    } else if (note.exhausted) {
      kind = "exhausted";
      line = `Role fallback: all ${total || "?"} models of the chain failed; skipped ${why}`;
    } else {
      kind = "stopped";
      line = `Role fallback: stopped after skipping ${why}; ${note.stopped_by} does not move to another model`;
    }
    return { kind, skipped: skipped.length, total, answered: note.answered || null, stoppedBy: note.stopped_by || null, line };
  }
  const rid = (r) => (r.request_id_source === "otari" ? r.request_id : null);

  function callsOf(d) {
    const ev = d.evidence || {}, llm = d.llm || {}, found = [];
    for (const r of (ev.narrative || {}).receipts || []) found.push(["narrator", r]);
    const web = ev.web || {};
    if (web.receipt) found.push(["searcher", web.receipt]);
    if ((web.page || {}).receipt) found.push(["fetcher", web.page.receipt]);
    if ((d.drift || {}).receipt) found.push(["drift", d.drift.receipt]); // the source drift seal: one more fetch of the cycle
    if ((ev.market_mcp || {}).receipt) found.push(["mcp", ev.market_mcp.receipt]);
    for (const part of ["bull", "bear", "checker", "judge"]) if ((llm[part] || {}).receipt) found.push([part, llm[part].receipt]);
    const canary = ((llm.otari || {}).canary) || {};
    if (canary.receipt) found.push(["canary", canary.receipt]);
    if ((d.sandbox || {}).receipt) found.push(["sandbox", d.sandbox.receipt]);
    let calls = found.map(([label, receipt], n) => ({ label, receipt, n }));
    if (calls.some((c) => Number.isInteger(c.receipt.started_offset_ms))) {
      const key = (c) => (Number.isInteger(c.receipt.started_offset_ms) ? c.receipt.started_offset_ms : 1e12);
      calls = calls.slice().sort((a, b) => key(a) - key(b) || a.n - b.n);
    }
    return calls;
  }

  /* Bars for the timeline. With the start offset of each call (dossiers recorded from F4) a bar sits where the call began; without it the
   * bars are laid end to end in the order the cycle makes the calls, and the page says the position is only an order. A call with no
   * duration (Otari replayed a stored answer, or no answer came) has no bar: it says "n/a" and draws nothing. */
  function layout(calls) {
    const timed = calls.length > 0 && calls.every((c) => Number.isInteger(c.receipt.started_offset_ms));
    let cursor = 0;
    const spans = calls.map((c) => {
      const has = Number.isInteger(c.receipt.duration_ms);
      const dur = has ? c.receipt.duration_ms : 0;
      const start = timed ? c.receipt.started_offset_ms : cursor;
      cursor += dur;
      return { start, dur, has };
    });
    const total = Math.max(1, ...spans.map((s) => s.start + s.dur));
    return { timed, totalMs: total, bars: spans.map((s) => (s.has
      ? { leftPct: +(s.start / total * 100).toFixed(3), widthPct: +Math.max(s.dur / total * 100, 0.6).toFixed(3), startMs: s.start, durationMs: s.dur }
      : { leftPct: 0, widthPct: 0, startMs: s.start, durationMs: null })) };
  }

  /* The calls that decide (bull, bear, judge, checker, the tools, the canary) come first, in time order; the narrator's batches of
   * headlines are many and alike, so they are one group. Each entry keeps its index into `calls`, so the bars still line up. */
  function groups(calls) {
    const core = [], narrator = [];
    calls.forEach((c, i) => (c.label === "narrator" ? narrator : core).push({ call: c, i }));
    return { core, narrator };
  }

  function microsOf(text) {
    const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(text == null ? "" : text).trim());
    if (!m) return null;
    return BigInt(m[2]) * 1000000n + BigInt(((m[3] || "") + "000000").slice(0, 6));
  }
  function microsText(n) {
    const sign = n < 0n ? "-" : "";
    const a = n < 0n ? -n : n;
    return `${sign}${a / 1000000n}.${String(a % 1000000n).padStart(6, "0")}`;
  }
  function percentile(values, p) {
    if (!values.length) return null;
    const ordered = values.slice().sort((a, b) => a - b);
    return ordered[Math.max(0, Math.ceil(p * ordered.length) - 1)];
  }
  const sortKeys = (obj) => Object.fromEntries(Object.keys(obj).sort().map((k) => [k, obj[k]]));

  function board(ledger) {
    const rows = new Map();
    for (const d of era(ledger)) {
      const acc = { bull: ((d.llm.bull || {}).accepted || []).length, bear: ((d.llm.bear || {}).accepted || []).length };
      for (const c of callsOf(d)) {
        const r = c.receipt;
        const key = r.served_by || `(no answer) ${r.sent_model}`;
        if (!rows.has(key)) rows.set(key, { model: key, calls: 0, ok: 0, refusals: {}, latencies: [], fallbacks: 0, claims: 0, claimCost: 0n });
        const row = rows.get(key);
        row.calls += 1;
        if (failed(r)) {
          const bad = r.refusal || r.error || {};
          const code = String(bad.code || bad.kind || bad.status);
          row.refusals[code] = (row.refusals[code] || 0) + 1;
          continue;
        }
        row.ok += 1;
        if (Number.isInteger(r.duration_ms)) row.latencies.push(r.duration_ms);
        if (r.fallback) row.fallbacks += 1;
        if (c.label === "bull" || c.label === "bear") {
          row.claims += acc[c.label];
          row.claimCost += microsOf(r.cost_usd) || 0n;
        }
      }
    }
    const out = [...rows.values()].map((row) => {
      let perClaim = null;
      if (row.claims) { // round half to even, as Python's Decimal does
        const n = BigInt(row.claims);
        let q = row.claimCost / n;
        const rem = row.claimCost % n;
        if (rem * 2n > n || (rem * 2n === n && q % 2n === 1n)) q += 1n;
        perClaim = microsText(q);
      }
      return { model: row.model, calls: row.calls, ok: row.ok, refusals: sortKeys(row.refusals), p50_ms: percentile(row.latencies, 0.5), p95_ms: percentile(row.latencies, 0.95),
               fallbacks: row.fallbacks, claims: row.claims, cost_per_claim_usd: perClaim };
    });
    return out.sort((a, b) => b.calls - a.calls || (a.model < b.model ? -1 : a.model > b.model ? 1 : 0));
  }

  function whoSaw(d, cfg) {
    const flags = Object.assign({}, (cfg || {}).models_open_weights || {});
    for (const spec of Object.values((cfg || {}).roles || {})) flags[spec.model] = !!spec.open_weights;
    const base = (((d.llm || {}).otari) || {}).base_url || "";
    let host = "";
    try { host = new URL(base).hostname; } catch (e) { host = ""; }
    const gateway = { host, region: host ? (host.startsWith("eu.") ? REGION_NOTE.eu : REGION_NOTE.default) : "unknown" };
    const providers = new Map();
    for (const c of callsOf(d)) {
      const r = c.receipt;
      if (!r.provider) continue;
      if (!providers.has(r.provider)) providers.set(r.provider, { provider: r.provider, models: new Set(), roles: new Set(), open: [], calls: 0, request_ids: [] });
      const p = providers.get(r.provider);
      if (r.served_model) p.models.add(r.served_model);
      p.roles.add(c.label);
      p.calls += 1;
      if (r.sent_model != null) p.open.push(Object.prototype.hasOwnProperty.call(flags, r.sent_model) ? flags[r.sent_model] : null);
      if (rid(r)) p.request_ids.push(rid(r));
    }
    const rows = [...providers.values()].map((p) => ({
      provider: p.provider, models: [...p.models].sort(), roles: [...p.roles].sort(), calls: p.calls,
      open_weights: p.open.length && p.open.every((v) => v === true) ? "yes" : (p.open.some((v) => v === false) ? "no" : "unknown"), request_ids: p.request_ids,
    }));
    rows.sort((a, b) => b.calls - a.calls || (a.provider < b.provider ? -1 : 1));
    return { seq: d.seq, gateway, providers: rows };
  }

  /* Role -> the class that carries its color (web/theme.css: --role-*). The role name is always printed next to the color. */
  const ROLE_CLASS = { bull: "r-bull", bear: "r-bear", judge: "r-judge", checker: "r-checker", narrator: "r-narrator", searcher: "r-tool", fetcher: "r-tool", drift: "r-tool", mcp: "r-tool", sandbox: "r-tool", canary: "r-canary" };
  const roleClass = (label) => ROLE_CLASS[label] || "r-narrator";
  const escText = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* The whole cycle as one strip: each call is a bar in the color of its role, placed where it began (or in order when the dossier predates
   * start offsets). Used on the landing page and at the top of the timeline. A call with no duration draws nothing. */
  function stripHtml(calls, lay) {
    const bars = calls.map((c, i) => ({ c, b: lay.bars[i] })).filter((e) => e.b.durationMs != null);
    const spans = bars.map((e) => `<i class="${roleClass(e.c.label)}" style="left:${e.b.leftPct}%;width:${e.b.widthPct}%" title="${escText(e.c.label)}: ${e.b.durationMs} ms"></i>`).join("");
    const roles = [...new Set(calls.map((c) => c.label))].join(", ");
    return `<div class="strip" role="img" aria-label="${calls.length} calls over ${lay.timed ? (lay.totalMs / 1000).toFixed(1) + " seconds" : "one cycle"}: ${escText(roles)}">${spans}</div>`;
  }

  /* The two lines of the cycle page about the closure gate and the source drift seal; verdict_desk/otari_report.py writes the same words. A
   * dossier recorded before either existed has neither field, and says so. */
  function closureLine(d) {
    const c = d && d.closure;
    if (!c || !Array.isArray(c.checks)) return "Closure gate: not applied to this dossier (recorded before the gate)";
    const checks = c.checks.filter((x) => x && typeof x === "object");
    const warned = checks.filter((x) => !x.pass && x.class === "soft").map((x) => String(x.name));
    return `Closure gate: ${checks.filter((x) => x.pass).length} of ${checks.length} checks passed` + (warned.length ? ` (${warned.length} warning${warned.length === 1 ? "" : "s"}: ${warned.join(", ")})` : "");
  }
  function driftLine(d) {
    const b = d && d.drift;
    if (!b || typeof b !== "object") return "Source drift: not checked in this dossier (recorded before the seal existed)";
    if (b.status === "none") return "Source drift: no earlier page old enough to read again";
    const head = `dossier ${b.source_dossier}` + (Number.isInteger(b.minutes_after_decision) ? `, ${b.minutes_after_decision} min after the decision` : "");
    if (b.status === "unavailable") return `Source drift: unavailable (${head}): ${b.reason}`;
    return `Source drift: ${b.status} (${head})`;
  }

  /* The four figures of the README card that the ledger alone can give; the same counts as scripts/otari_cards.py (tests/home.test.js checks it). */
  function figures(ledger) {
    const dossiers = era(ledger);
    const rows = board(ledger);
    return {
      dossiers: dossiers.length,
      first: dossiers.length ? dossiers[0].seq : null,
      last: dossiers.length ? dossiers[dossiers.length - 1].seq : null,
      receipts: dossiers.reduce((t, d) => t + callsOf(d).length, 0),
      routes: rows.filter((r) => r.ok && !r.model.startsWith("(no answer)")).length,
      backups: rows.reduce((t, r) => t + r.fallbacks, 0),
    };
  }

  return { REMEDY, failureOf, roleFallbackOf, era, callsOf, layout, groups, board, whoSaw, percentile, microsOf, microsText, roleClass, stripHtml, figures, closureLine, driftLine };
});
