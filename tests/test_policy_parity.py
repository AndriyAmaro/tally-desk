"""The browser playground must not drift from the judge that produced the ledger.

`web/policy.js` is a mirror of `verdict_desk/agents_v2.py` (rules v3). A mirror that is
never compared is a second implementation waiting to lie, so this test runs the JS over a
grid of inputs and fails when any state differs from the Python judge's answer.

Skipped where node is absent, which is honest: it proves parity where it runs, and it
never silently passes by checking nothing.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from verdict_desk import agents_v2

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "web" / "policy.js"

GRID = {
    "bull": [0, 0.5, 1.0, 1.4, 1.5, 1.6, 2.0, 2.5, 3.0],
    "bear": [0, 0.5, 1.0, 1.4, 1.5, 1.6, 2.0, 2.5, 3.0],
    "atr": [0.5, 1.5, 2.5],
    "age": [10, 76],
}


def js_states(cases):
    script = f"""
const P = require({json.dumps(str(POLICY))});
const input = require('fs').readFileSync(0, 'utf8');
const rows = JSON.parse(input);
process.stdout.write(JSON.stringify(rows.map((c) => P.evaluate(c).state)));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        out = subprocess.run([NODE, path], input=json.dumps(cases), capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise AssertionError(f"node failed: {out.stderr[:400]}")
        return json.loads(out.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


def py_state(bull, bear, atr, age):
    f = {
        "last_close": 100.0,
        "atr_14": atr,
        "freshness": {"ok": age <= 75, "reason": "" if age <= 75 else "stale", "window_minutes": 75},
    }
    a = [{"claim": "bull", "evidence_ref": "x", "value": 1.0, "weight": bull}]
    b = [{"claim": "bear", "evidence_ref": "x", "value": 1.0, "weight": bear}]
    return agents_v2.judge(f, a, b, rules=agents_v2.V3)["action"]


@unittest.skipUnless(NODE, "node is not installed here")
class PolicyParityTest(unittest.TestCase):
    def test_browser_policy_matches_the_python_judge_on_a_grid(self):
        cases, expected = [], []
        for bull in GRID["bull"]:
            for bear in GRID["bear"]:
                for atr in GRID["atr"]:
                    for age in GRID["age"]:
                        cases.append({"bull": bull, "bear": bear, "atrPct": atr, "ageMinutes": age})
                        expected.append(py_state(bull, bear, atr, age))
        got = js_states(cases)
        mismatches = [(c, e, g) for c, e, g in zip(cases, expected, got) if e != g]
        self.assertEqual(mismatches[:5], [], f"{len(mismatches)} of {len(cases)} states differ")
        self.assertGreater(len(cases), 400, "the grid must actually be wide")

    def test_the_grid_covers_every_state(self):
        seen = {py_state(b, r, a, g) for b in (0, 1.5, 3) for r in (0, 1.5, 2) for a in (1, 3) for g in (10, 90)}
        self.assertEqual(seen, {"LONG", "SHORT", "REDUCE_EXPOSURE", "WATCH_REVERSAL", "FLAT", "BLOCKED_STALE"})

    def test_risk_cap_matches_the_python_cap(self):
        script = f"const P=require({json.dumps(str(POLICY))});process.stdout.write(JSON.stringify(P.SYSTEM_MAX_RISK_PCT));"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
            self.assertEqual(json.loads(out.stdout), agents_v2.SYSTEM_MAX_RISK_PCT)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
