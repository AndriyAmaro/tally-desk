"""A dossier refused because two price sources diverge (the MCP check) must be VERIFIED by every verifier, for ever.

The fail-closed path used to be tied to the age of the newest candle in the browser verifier, so a dossier the Python judge refused for
another sealed reason (`freshness.ok` false) would have read NOT VERIFIED on the public page. This test builds such a dossier through the
real code path (Otari MCP block, divergence class, `dossier.build`) and runs it through two independent implementations of the verdict:

  1. Python: `dossier.verify` and `dossier.replay` (and the offline recheck of the MCP block);
  2. the browser: `web/policy.js` (`checkDossier`, the reader `web/verify.html` uses) and the canonical hash of `web/canonical.js`, run in node;
The earlier project had a third implementation, the Chainlink CRE workflow (`cre/anchor/verdict.ts`); it stays in that frozen repository.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from tests.test_otari_tools import GOOD_CODE, fresh_block, mcp_block
from verdict_desk import dossier, otari_mcp

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
TRADE_ARGS = (0.006, -0.0005, ("Bitcoin rally hits record", "ETF inflow surge", "adoption soar"))  # an uptrend whose edge alone opens a LONG under v6


def divergent_dossier():
    pkt = packet(*TRADE_ARGS)
    pkt["freshness"] = fresh_block()
    close = pkt["market"]["candles"][-1]["c"]
    otari_mcp.apply(pkt, mcp_block(round(close * 1.08, 4)))
    return dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")


def agreeing_dossier():
    pkt = packet(*TRADE_ARGS)
    pkt["freshness"] = fresh_block()
    otari_mcp.apply(pkt, mcp_block(round(pkt["market"]["candles"][-1]["c"] * 1.001, 4)))
    return dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")


def run_node(script: str, payload: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run([NODE, path], input=json.dumps(payload), capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise AssertionError(f"node failed: {out.stderr[:400]}")
        return json.loads(out.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


BROWSER = f"""
const P = require({json.dumps(str(ROOT / 'web' / 'policy.js'))});
const {{ verifyLedger }} = require({json.dumps(str(ROOT / 'web' / 'canonical.js'))});
const {{ createHash }} = require('node:crypto');
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const r = P.checkDossier(d);
verifyLedger(JSON.stringify(d), async (t) => createHash('sha256').update(t, 'utf8').digest('hex')).then((v) =>
  process.stdout.write(JSON.stringify({{ state: r.rederived && r.rederived.state, agrees: r.agrees, divergent: r.rederived && r.rederived.divergent, rule: r.rederived && r.rederived.rule, problems: v.problems }})));
"""


class BlockedByDivergenceParityTest(unittest.TestCase):
    def test_the_dossier_is_what_the_test_says_it_is(self):
        d = divergent_dossier()
        self.assertEqual(d["verdict"]["action"], "BLOCKED_STALE")
        self.assertNotIn("entry", d["verdict"])
        fresh = d["features"]["freshness"]
        self.assertIs(fresh["ok"], False)
        self.assertIs(fresh["classes"]["market"]["ok"], True, "the candle is fresh: the refusal is not about age")
        self.assertIs(fresh["classes"]["divergence"]["ok"], False)
        self.assertLess(fresh["newest_candle_age_minutes"], fresh["window_minutes"])
        self.assertGreaterEqual(abs(d["verdict"]["edge"]), d["verdict"]["threshold"], "the edge alone would have opened a position")

    def test_python_verifies_and_replays_it(self):
        d = divergent_dossier()
        self.assertEqual(dossier.verify([d]), [])
        r = dossier.replay(d, "auto")
        self.assertTrue(r["matches"])
        self.assertEqual((r["market_check_ok"], r["market_check_status"]), (True, "divergence"))

    @unittest.skipUnless(NODE, "node is not installed here")
    def test_the_browser_verifier_reads_the_sealed_refusal_and_verifies_it(self):
        out = run_node(BROWSER, divergent_dossier())
        self.assertEqual(out["state"], "BLOCKED_STALE")
        self.assertIs(out["agrees"], True)
        self.assertIs(out["divergent"], True)
        self.assertIn("diverge", out["rule"])
        self.assertEqual(out["problems"], [])

    @unittest.skipUnless(NODE, "node is not installed here")
    def test_the_browser_still_rejects_a_refusal_the_evidence_does_not_back(self):
        """Believing the sealed `ok` must not mean believing the verdict: a BLOCKED dossier whose freshness says ok, and a LONG whose freshness
        says not ok, are both disagreements."""
        d = divergent_dossier()
        lenient = json.loads(json.dumps(d))
        lenient["evidence"]["freshness"] = fresh_block()
        self.assertIs(run_node(BROWSER, lenient)["agrees"], False)
        a = agreeing_dossier()
        self.assertNotEqual(a["verdict"]["action"], "BLOCKED_STALE")
        self.assertIs(run_node(BROWSER, a)["agrees"], True)
        forged = json.loads(json.dumps(a))
        forged["evidence"]["freshness"] = {**forged["evidence"]["freshness"], "ok": False, "reason": "forged"}
        self.assertIs(run_node(BROWSER, forged)["agrees"], False)


class RefusalReasonsStayApartTest(unittest.TestCase):
    def test_a_divergence_does_not_overwrite_a_staleness_reason_and_both_classes_are_visible(self):
        pkt = packet(*TRADE_ARGS)
        pkt["freshness"] = fresh_block(age=200.0, ok=False, reason="the newest candle is 200 minutes old, window is 75")
        otari_mcp.apply(pkt, mcp_block(round(pkt["market"]["candles"][-1]["c"] * 1.08, 4)))
        d = dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")
        self.assertIn("200 minutes old", d["verdict"]["rationale"])
        classes = d["features"]["freshness"]["classes"]
        self.assertIs(classes["market"]["ok"], False)
        self.assertIs(classes["divergence"]["ok"], False)
        self.assertTrue(dossier.replay(d, "auto")["matches"])



if __name__ == "__main__":
    unittest.main()
