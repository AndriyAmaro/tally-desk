"""Ask the record: the browser assistant answers only from the frozen dossier and checks its own numbers.

Runs the JavaScript unit tests, then sweeps the whole published ledger: for every dossier and every
question the page offers, the answer must be built without error and pass its own number check.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

SWEEP = """
const fs = require("node:fs");
const ask = require(process.argv[1]);
const rows = fs.readFileSync(process.argv[2], "utf8").split("\\n").filter(Boolean).map((l) => JSON.parse(l));
const failures = [];
let answers = 0;
for (const d of rows) {
  for (const intent of [...ask.INTENTS, null]) {
    try {
      const a = ask.answer(d, intent, { recomputedHash: d.hash });
      const c = ask.check(a);
      answers++;
      if (!c.ok) failures.push(`#${d.seq} ${intent}: stray ${c.stray.join(",")}`);
    } catch (err) {
      failures.push(`#${d.seq} ${intent}: ${err.message}`);
    }
  }
}
console.log(JSON.stringify({ dossiers: rows.length, answers, failures: failures.slice(0, 20), failed: failures.length }));
"""


OUTCOMES = """
const fs = require("node:fs");
const ask = require(process.argv[1]);
const rows = fs.readFileSync(process.argv[2], "utf8").split("\\n").filter(Boolean).map((l) => JSON.parse(l));
const market = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = [];
for (const d of rows) {
  const t = d.evidence.market.candles.at(-1).t;
  const o = ask.outcome(d, market[d.evidence.symbol].candles.filter((c) => c.t > t));
  if (o) { delete o.candle; out.push(o); }
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class AskTest(unittest.TestCase):
    def test_the_javascript_unit_tests_pass(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "ask.test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_the_chart_unit_tests_pass(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "chart.test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_what_happened_next_matches_the_scoreboard_for_every_position(self):
        """The page resolves a position on market.json in the browser; it must agree with portfolio.resolve, field for field."""
        import json
        import tempfile

        from verdict_desk import dossier, portfolio
        ledger = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            market = Path(tmp) / "market.json"
            market.write_text(json.dumps(portfolio.market_series(ledger)), encoding="utf-8")
            out = subprocess.run([NODE, "-e", OUTCOMES, str(ROOT / "web" / "ask.js"), str(ROOT / "ledger" / "ledger.jsonl"), str(market)],
                                 capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        js = {o["seq"]: o for o in json.loads(out.stdout)}
        expected = {p["seq"]: p for p in portfolio.summarize(ledger)["positions"]}
        self.assertEqual(set(js), set(expected))
        for seq, p in expected.items():
            self.assertEqual({k: js[seq][k] for k in p}, p, seq)

    def test_every_answer_on_the_whole_ledger_passes_its_own_number_check(self):
        import json

        out = subprocess.run([NODE, "-e", SWEEP, str(ROOT / "web" / "ask.js"), str(ROOT / "ledger" / "ledger.jsonl")],
                             capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertGreater(result["dossiers"], 300)
        self.assertEqual(result["failed"], 0, result["failures"])


if __name__ == "__main__":
    unittest.main()
