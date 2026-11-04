"""The browser's "your voices, your limit" calculator must agree with the Python judge.

The narrative page lets a visitor pick voices, set a risk limit and see the practice position that
results. That is only worth showing if the JavaScript sizes the position, and weighs the convergence,
exactly as the code that wrote the ledger does. Each case below is run through the real
`agents_v2.judge` / `convergence_args` and through `web/policy.js` / `web/trust.js` in Node.
"""

import json
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from verdict_desk import agents_v2

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

SCRIPT = """
const policy = require(process.argv[1]);
const trust = require(process.argv[2]);
const cases = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const out = cases.map((c) => c.kind === "position"
  ? policy.position(c.entry, c.atr, c.action, c.riskPct, c.capital)
  : { weight: trust.convergenceWeight(c.weights, c.high, c.trustWeighted) });
console.log(JSON.stringify(out));
"""


def run_js(cases):
    out = subprocess.run([NODE, "-e", SCRIPT, str(ROOT / "web" / "policy.js"), str(ROOT / "web" / "trust.js")],
                         input=json.dumps(cases), capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def python_position(entry, atr, action, risk_pct, capital):
    rules = agents_v2.with_risk_pct(agents_v2.V4, risk_pct)
    f = {"last_close": entry, "atr_14": atr}
    big = rules.entry_threshold + 1.0
    bull = [{"weight": big if action == "LONG" else 0.0}]
    bear = [{"weight": big if action == "SHORT" else 0.0}]
    verdict = agents_v2.judge(f, bull, bear, capital=capital, rules=rules)
    assert verdict["action"] == action, verdict
    return verdict


@unittest.skipUnless(NODE, "node is not installed")
class PositionParityTest(unittest.TestCase):
    def test_the_browser_sizes_a_position_like_the_judge_over_many_inputs(self):
        rng = random.Random(11)
        cases = [{"kind": "position", "entry": round(rng.uniform(0.5, 90000), 4), "atr": round(rng.uniform(0.01, 3000), 4),
                  "action": rng.choice(["LONG", "SHORT"]), "riskPct": round(rng.uniform(0.1, 25), 2), "capital": rng.choice([10000, 2500.5, 100000])}
                 for _ in range(300)]
        for case, got in zip(cases, run_js(cases)):
            want = python_position(case["entry"], case["atr"], case["action"], case["riskPct"], case["capital"])
            for key, js_key in (("stop", "stop"), ("take_profit", "take_profit"), ("size", "size")):
                self.assertAlmostEqual(want[key], got[js_key], delta=max(1e-6, abs(want[key]) * 1e-9), msg=f"{key} for {case}")

    def test_the_limit_above_the_system_ceiling_is_refused_by_both(self):
        with self.assertRaises(ValueError):
            agents_v2.with_risk_pct(agents_v2.V4, 25.01)
        out = subprocess.run([NODE, "-e", "console.log(require(process.argv[1]).riskPct(25.01))", str(ROOT / "web" / "policy.js")], capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("exceeds the system cap", out.stderr)


@unittest.skipUnless(NODE, "node is not installed")
class ConvergenceWeightParityTest(unittest.TestCase):
    def test_the_browser_weighs_a_convergence_like_rules_v4_and_v5(self):
        rng = random.Random(5)
        cases, wants = [], []
        for _ in range(400):
            n = rng.randint(2, 7)
            voices = [f"V{i}" for i in range(n)]
            weights = [round(rng.uniform(0.5, 1.5), 3) for _ in voices]
            high = rng.random() < 0.5
            tw = rng.random() < 0.5
            cases.append({"kind": "weight", "weights": weights, "high": high, "trustWeighted": tw})
            narrative = {"convergence": [{"token": "BTC", "bias": "bullish", "voices": voices, "max_conviction": "high" if high else "low", "max_urgency": "low"}],
                         "trust": {"voices": {v: {"weight": w} for v, w in zip(voices, weights)}}}
            bull, _ = agents_v2.convergence_args(narrative, "BTCUSDT", tw)
            wants.append(bull[0]["weight"])
        for case, want, got in zip(cases, wants, run_js(cases)):
            self.assertAlmostEqual(want, got["weight"], places=2, msg=str(case))


if __name__ == "__main__":
    unittest.main()
