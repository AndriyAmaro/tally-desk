"""The browser must compute the same per-voice track record as Python.

The page recomputes each voice's score from the raw ledger in the visitor's own browser, so the
JavaScript is only believable if it agrees with the Python on the published ledger and on a
synthetic world with calls that win, lose, repeat and are still pending.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_trust import PATH, call, make, world
from verdict_desk import dossier, trust

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
INTS = ("n", "hits")
FLOATS = ("hit_rate", "chance_rate", "skill", "bull_share", "mean_signed_bps", "weight")


SCRIPT = """
const fs = require("node:fs");
const trust = require(process.argv[1]);
const ledger = fs.readFileSync(process.argv[2], "utf8").split("\\n").filter(Boolean).map((line) => JSON.parse(line));
console.log(JSON.stringify(trust.compute(ledger)));
"""


def run_node(ledger: list[dict]) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(d) for d in ledger) + "\n")
    out = subprocess.run([NODE, "-e", SCRIPT, str(ROOT / "web" / "trust.js"), fh.name], capture_output=True, text=True, timeout=120)
    Path(fh.name).unlink(missing_ok=True)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class TrustJsSuiteTest(unittest.TestCase):
    def test_the_javascript_unit_tests_pass(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "trust.test.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


@unittest.skipUnless(NODE, "node is not installed")
class TrustParityTest(unittest.TestCase):
    def assert_same(self, ledger):
        py, js = trust.compute(ledger), run_node(ledger)
        for key in ("horizon_hours", "min_calls", "as_of_seq", "pending_calls"):
            self.assertEqual(py[key], js[key], key)
        self.assertEqual(sorted(py["voices"]), sorted(js["voices"]))
        for name, voice in py["voices"].items():
            for key in INTS:
                self.assertEqual(voice[key], js["voices"][name][key], f"{name}.{key}")
            for key in FLOATS:
                # both sides round to a fixed number of places, half-even vs half-up: allow one unit in the last place
                self.assertAlmostEqual(voice[key], js["voices"][name][key], delta=1.5e-4 if key != "mean_signed_bps" else 0.015, msg=f"{name}.{key}")
            self.assertEqual(voice["proven"], js["voices"][name]["proven"])

    def test_the_published_ledger_scores_the_same_in_python_and_in_the_browser_code(self):
        self.assert_same(dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl"))

    def test_a_synthetic_world_with_winners_losers_repeats_and_pending_calls(self):
        up = lambda h: PATH[h + 4] > PATH[h]
        by_hour = {}
        for h in range(0, 100, 2):
            by_hour.setdefault(h, []).append(call("Oracle", "bullish" if up(h) else "bearish", f"oracle {h}"))
            by_hour[h].append(call("Contrarian", "bearish" if up(h) else "bullish", f"contra {h}"))
            by_hour[h].append(call("Sheep", "bullish", f"sheep {h}", token="ETH" if h % 4 == 0 else "BTC"))
        by_hour[0].append(call("Sheep", "bullish", "sheep 4"))
        by_hour[2].append(call("Sheep", "bullish", "sheep 4"))  # the same headline again: one call
        ledger = world(by_hour, last_hour=104)
        ledger += [make(len(ledger) + 1, 104, [call("Late", "bearish", "just now")])]  # cannot settle yet
        self.assert_same(ledger)


if __name__ == "__main__":
    unittest.main()
