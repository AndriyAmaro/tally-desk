"""A dossier refused because two price sources diverge (the MCP check) must be VERIFIED by every verifier, for ever.

The fail-closed path used to be tied to the age of the newest candle in the browser verifier, so a dossier the Python judge refused for
another sealed reason (`freshness.ok` false) would have read NOT VERIFIED on the public page. This test builds such a dossier through the
real code path (Otari MCP block, divergence class, `dossier.build`) and runs it through three independent implementations of the verdict:

  1. Python: `dossier.verify` and `dossier.replay` (and the offline recheck of the MCP block);
  2. the browser: `web/policy.js` (`checkDossier`, the reader `web/verify.html` uses) and the canonical hash of `web/canonical.js`, run in node;
  3. the CRE workflow: `replayDossier` of `cre/anchor/verdict.ts`, run with bun and a minimal stand-in for the two viem functions it uses, so it
     runs in a plain checkout (skipped only where bun is missing). Another test checks that the stand-in hashes exactly like the ledger does.
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
NODE, BUN = shutil.which("node"), shutil.which("bun")
CRE = ROOT / "cre" / "anchor"
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

    @unittest.skipUnless(BUN, "bun is not installed here")
    def test_the_cre_workflow_replays_it(self):
        """Runs the workflow's own `verdict.ts` with bun. It imports `sha256` and `toBytes` from viem and nothing else, so a minimal stand-in of
        those two (node's own sha256) is put next to a copy of the file: no `bun install` and no network are needed, and the verdict logic
        under test is the real one."""
        work = Path(tempfile.mkdtemp())
        (work / "node_modules" / "viem").mkdir(parents=True)
        (work / "node_modules" / "viem" / "package.json").write_text(json.dumps({"name": "viem", "type": "module", "main": "index.js"}))
        (work / "node_modules" / "viem" / "index.js").write_text(
            "import { createHash } from 'node:crypto'\n"
            "export const toBytes = (s) => new TextEncoder().encode(s)\n"
            "export const sha256 = (bytes) => '0x' + createHash('sha256').update(bytes).digest('hex')\n")
        shutil.copy(CRE / "verdict.ts", work / "verdict.ts")
        (work / "run.ts").write_text(
            "import { replayDossier } from './verdict'\n"
            "const d = JSON.parse(await Bun.stdin.text())\n"
            "process.stdout.write(JSON.stringify(replayDossier(d)))\n")
        for d, action in ((divergent_dossier(), "BLOCKED_STALE"), (agreeing_dossier(), None)):
            out = subprocess.run([BUN, "run", str(work / "run.ts")], input=json.dumps(d), capture_output=True, text=True, timeout=120, cwd=str(work))
            self.assertEqual(out.returncode, 0, out.stderr[:400])
            replay = json.loads(out.stdout)
            self.assertTrue(replay["matches"], replay)
            self.assertEqual(replay["action"], action or d["verdict"]["action"])

    def test_the_stand_in_is_a_faithful_sha256_of_what_the_workflow_hashes(self):
        """The shim must not be what makes the test pass: the hash of a dossier body, as the workflow computes it, equals Python's."""
        if not BUN:
            self.skipTest("bun is not installed here")
        work = Path(tempfile.mkdtemp())
        (work / "node_modules" / "viem").mkdir(parents=True)
        (work / "node_modules" / "viem" / "package.json").write_text(json.dumps({"name": "viem", "type": "module", "main": "index.js"}))
        (work / "node_modules" / "viem" / "index.js").write_text(
            "import { createHash } from 'node:crypto'\nexport const toBytes = (s) => new TextEncoder().encode(s)\n"
            "export const sha256 = (bytes) => '0x' + createHash('sha256').update(bytes).digest('hex')\n")
        shutil.copy(CRE / "verdict.ts", work / "verdict.ts")
        (work / "run.ts").write_text("import { dossierHash } from './verdict'\nconst d = JSON.parse(await Bun.stdin.text())\nprocess.stdout.write(dossierHash(d))\n")
        d = divergent_dossier()
        out = subprocess.run([BUN, "run", str(work / "run.ts")], input=json.dumps(d), capture_output=True, text=True, timeout=120, cwd=str(work))
        self.assertEqual(out.stdout.strip(), d["hash"])


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


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
