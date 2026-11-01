"""The README cards are images a reader trusts at a glance, so they must parse, exist and match the ledger."""

import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ReadmeCardsTest(unittest.TestCase):
    def test_every_card_the_readme_shows_exists_and_parses(self):
        shown = re.findall(r'src="(docs/readme/[^"]+\.svg)"', README.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(shown), 8)
        for rel in shown:
            path = ROOT / rel
            self.assertTrue(path.exists(), f"README shows {rel}, which does not exist")
            ET.fromstring(path.read_text(encoding="utf-8"))

    def test_figures_card_and_table_cite_the_same_dossier(self):
        table = re.search(r"<!-- differentiators:start: generated from dossier (\d+)", README.read_text(encoding="utf-8"))
        card = re.search(r"As of dossier (\d+)", (ROOT / "docs" / "readme" / "figures.svg").read_text(encoding="utf-8"))
        self.assertIsNotNone(table)
        self.assertIsNotNone(card)
        self.assertEqual(table.group(1), card.group(1))

    def test_battery_snapshot_names_a_dossier_of_this_chain(self):
        snap = json.loads((ROOT / "docs" / "readme" / "battery.json").read_text(encoding="utf-8"))
        hashes = {}
        with (ROOT / "ledger" / "ledger.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    d = json.loads(line)
                    hashes[d["seq"]] = d["hash"]
        self.assertEqual(hashes.get(snap["head"]["seq"]), snap["head"]["hash"])
        self.assertTrue(snap["all_caught"])


if __name__ == "__main__":
    unittest.main()
