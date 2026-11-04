"""docs/bring-your-own-agent.md: the sketch on the page really runs (with a fake transport), the page is plain, and the examples agent works."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from verdict_desk import dossier

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "bring-your-own-agent.md"
EXAMPLE = ROOT / "examples" / "summarize_url.py"


class DocTest(unittest.TestCase):
    def test_the_sketch_runs_and_its_ledger_verifies(self):
        text = DOC.read_text(encoding="utf-8")
        code = re.search(r"```python\n(.*?)```", text, re.S).group(1)
        self.assertTrue(10 <= len(code.strip().splitlines()) <= 25, "the page promises a sketch of 10 to 25 lines")
        cfg = json.loads((ROOT / "examples" / "otari_roles.json").read_text(encoding="utf-8"))
        cfg["roles"] = {"writer": cfg["roles"]["reader"]}
        cfg["default_role"] = "writer"
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "my_roles.json").write_text(json.dumps(cfg), encoding="utf-8")
            scope = {}
            exec(compile(code, "bring-your-own-agent.md", "exec"), scope)
            here = os.getcwd()
            with mock.patch.dict(os.environ):
                os.environ.pop("OTARI_API_KEY", None)
                os.chdir(tmp)  # the sketch names its role config by a relative path
                try:
                    record = scope["run"]("What is a hash chain?", Path(tmp, "ledger.jsonl"), transport=lambda payload: "A list where each entry names the one before.")
                finally:
                    os.chdir(here)
            ledger = dossier.read_ledger(Path(tmp, "ledger.jsonl"))
        self.assertEqual(dossier.verify(ledger), [])
        self.assertEqual((record["outputs"]["answer"], record["workload"]["name"]), ("A list where each entry names the one before.", "my-agent"))
        self.assertTrue(dossier.replay(ledger[0])["matches"])

    def test_the_page_names_the_limits_and_the_checks_it_runs(self):
        text = DOC.read_text(encoding="utf-8")
        for needle in ("No verdict replay", "Otari only", "The ledger is yours", "calls_accounted", "receipts_schema", "input_frozen", "priced_within_cap"):
            self.assertIn(needle, text)
        self.assertNotIn("—", text)
        self.assertNotIn("–", text)


class ExampleTest(unittest.TestCase):
    def run_example(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "OTARI_API_KEY"}
        return subprocess.run([sys.executable, str(EXAMPLE), *args], capture_output=True, text=True, env=env, timeout=60, cwd=tempfile.gettempdir())

    def test_the_dry_run_seals_a_ledger_that_verify_and_replay_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = str(Path(tmp, "mine.jsonl"))
            out = self.run_example("https://example.test/a", "--dry-run", "--ledger", ledger)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("sealed #1", out.stdout)
            for command in ("verify", "replay"):
                run = subprocess.run([sys.executable, "-m", "verdict_desk", "--ledger", ledger, command], capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            record = dossier.read_ledger(Path(ledger))[0]
            self.assertEqual((record["workload"]["name"], record["workload"]["mode"]), ("summarize-url", "dry-run"))
            self.assertIs(record["outputs"]["number_is_in_summary"], True)

    def test_a_real_run_without_a_key_stops_before_sending_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_example("https://example.test/a", "--ledger", str(Path(tmp, "mine.jsonl")))
            self.assertEqual(out.returncode, 2)
            self.assertIn("OTARI_API_KEY is not set", out.stdout)
            self.assertFalse(Path(tmp, "mine.jsonl").exists())

    def test_the_example_will_not_write_to_the_public_ledger(self):
        before = (ROOT / "ledger" / "ledger.jsonl").read_bytes()
        out = self.run_example("https://example.test/a", "--dry-run", "--ledger", str(ROOT / "ledger" / "ledger.jsonl"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("public trading ledger", out.stdout)
        self.assertEqual((ROOT / "ledger" / "ledger.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
