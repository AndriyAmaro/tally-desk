"""The CRE evidence must stay tied to the record it claims to anchor.

`cre/evidence/simulate-live-head.txt` is a claim in front of judges: "the workflow read the
published head at this hour and encoded it against the deployed contract". A claim in a text
file rots the moment the ledger moves, so it is checked here against `ledger/ledger.jsonl`.

The anchoring script is also checked for the one behaviour that matters operationally: it
refuses to run without a key instead of quietly doing nothing.
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "cre" / "evidence" / "simulate-live-head.txt"
DEPLOYMENT = ROOT / "cre" / "deployment.sepolia.json"
SCRIPT = ROOT / "cre" / "scripts" / "anchor-existing.sh"


class CreEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.ledger = {d["seq"]: d for d in
                       (json.loads(l) for l in (ROOT / "ledger" / "ledger.jsonl").read_text().splitlines() if l.strip())}

    def test_the_simulated_head_matches_the_record_it_names(self):
        text = EVIDENCE.read_text()
        match = re.search(r"Anchored seq (\d+) head ([0-9a-f]{64})", text)
        self.assertIsNotNone(match, "the evidence file must name the sequence and hash it read")
        seq, head = int(match.group(1)), match.group(2)
        self.assertIn(seq, self.ledger, f"the simulated head names seq {seq}, which the ledger does not contain")
        self.assertEqual(head, self.ledger[seq]["hash"],
                         f"seq {seq}: the CRE workflow read a head that is not the published one")

    def test_the_evidence_shows_consensus_and_a_data_feed_read(self):
        text = EVIDENCE.read_text()
        self.assertIn("head.json", text)
        self.assertIn("Data Feed", text)
        self.assertIn("Binary hash", text, "the simulation must record which binary produced it")

    def test_the_two_source_simulation_names_a_head_the_ledger_really_has(self):
        text = (ROOT / "cre" / "evidence" / "simulate-two-sources.txt").read_text()
        match = re.search(r"Anchored seq (\d+) head ([0-9a-f]{64})", text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(2), self.ledger[int(match.group(1))]["hash"])
        self.assertIn("raw.githubusercontent.com", text)
        self.assertIn("andriyamaro.github.io", text, "the second, independent source must be named in the log")
        self.assertIn("Data Feed BTC / USD", text, "the feed must be named, not just its address")

    def test_the_workflow_config_names_two_different_sources_for_the_head(self):
        config = json.loads((ROOT / "cre" / "anchor" / "config.staging.json").read_text())
        self.assertNotEqual(config["headUrl"], config["headUrlMirror"])
        self.assertTrue(config["headUrl"].endswith("/ledger/head.json") and config["headUrlMirror"].endswith("/ledger/head.json"))

    def test_the_deployment_file_points_at_a_real_contract(self):
        dep = json.loads(DEPLOYMENT.read_text())
        self.assertRegex(dep["contract"], r"^0x[0-9a-fA-F]{40}$")
        self.assertEqual(dep["chainId"], 11155111)
        self.assertIn(dep["contract"], (ROOT / "verdict_desk" / "anchor.py").read_text(),
                      "the monitored address and the deployment must be the same contract")

    def test_the_anchoring_script_refuses_to_run_without_a_key(self):
        env = {k: v for k, v in os.environ.items() if k != "SEPOLIA_PRIVATE_KEY"}
        out = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("SEPOLIA_PRIVATE_KEY", out.stderr + out.stdout)

    def test_the_evidence_directory_holds_the_broadcast_proof(self):
        files = {p.name for p in (ROOT / "cre" / "evidence").iterdir()}
        self.assertIn("simulate-broadcast-output.txt", files)
        broadcast = (ROOT / "cre" / "evidence" / "simulate-broadcast-output.txt").read_text()
        tx = re.search(r"at tx (0x[0-9a-f]{64})", broadcast)
        self.assertIsNotNone(tx, "the broadcast run must record the transaction it sent")
        self.assertNotIn("<test-key>", broadcast.replace("<test-key>", ""), "the key must be masked before saving")


if __name__ == "__main__":
    unittest.main()
