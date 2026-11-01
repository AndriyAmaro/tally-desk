/* JSON canonico (RFC 8785) e verificacao do ledger do Verdict Desk.
 * Funciona no navegador (global VerdictDesk) e no Node (module.exports).
 * Mesmo texto canonico que verdict_desk/canonical.py, entao o hash confere nas duas pontas. */
(function (root) {
  "use strict";
  var GENESIS = "0".repeat(64);

  function canonical(v) {
    if (v === null || typeof v !== "object") {
      if (typeof v === "number" && !isFinite(v)) throw new Error("NaN and Infinity cannot be canonicalized");
      return JSON.stringify(v);
    }
    if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
    return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canonical(v[k]); }).join(",") + "}";
  }

  function hex(buffer) {
    return Array.from(new Uint8Array(buffer)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  /* sha256Hex(text) -> Promise<string>. No navegador use crypto.subtle; no Node, crypto.createHash. */
  function webSha256(text) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)).then(hex);
  }

  /* Confere o ledger inteiro sem confiar em quem o publicou. */
  async function verifyLedger(text, sha256Hex) {
    var dossiers = text.split("\n").filter(Boolean).map(function (line) { return JSON.parse(line); });
    var prev = GENESIS, rows = [], problems = [];
    for (var i = 0; i < dossiers.length; i++) {
      var d = dossiers[i], body = {};
      Object.keys(d).forEach(function (k) { if (k !== "hash") body[k] = d[k]; });
      var linked = d.prev_hash === prev;
      var recomputed = await sha256Hex(canonical(body));
      var intact = recomputed === d.hash;
      if (!linked) problems.push("seq " + d.seq + ": prev_hash does not match the previous dossier");
      if (!intact) problems.push("seq " + d.seq + ": content was altered after it was recorded");
      rows.push({ dossier: d, linked: linked, intact: intact, recomputed: recomputed });
      prev = d.hash;
    }
    return { rows: rows, problems: problems };
  }

  var api = { GENESIS: GENESIS, canonical: canonical, verifyLedger: verifyLedger, webSha256: webSha256 };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.VerdictDesk = api;
})(typeof self !== "undefined" ? self : this);
