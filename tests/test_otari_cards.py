"""The Otari README cards: they parse, the figures card is computed from the ledger (not typed), and the README shows only cards that exist."""

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import otari_cards  # noqa: E402
from verdict_desk import otari_report  # noqa: E402

LEDGER, EVIDENCE = otari_cards.load()


class OtariCardsTest(unittest.TestCase):
    def test_every_card_the_readme_shows_exists_and_parses(self):
        shown = re.findall(r'src="(docs/readme/[^"]+\.svg)"', (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(shown), 5)
        for rel in shown:
            self.assertTrue((ROOT / rel).exists(), f"README shows {rel}, which does not exist")
            ET.fromstring((ROOT / rel).read_text(encoding="utf-8"))

    def test_the_committed_figures_card_matches_the_ledger(self):
        self.assertEqual(otari_cards.FIGURES.read_text(encoding="utf-8"), otari_cards.figures_svg(LEDGER, EVIDENCE),
                         "run scripts/otari_cards.py (the runner redraws it every cycle)")

    def test_figures_are_counts_over_the_ledger(self):
        values = {label: value for value, label, _ in otari_cards.figures(LEDGER, EVIDENCE)}
        era = otari_report.era(LEDGER)
        self.assertEqual(values["Otari-era dossiers"], str(len(era)))
        self.assertEqual(values["sealed receipts"], str(sum(len(otari_report.calls_of(d)) for d in era)))
        rows = otari_report.audit(LEDGER, EVIDENCE)
        self.assertEqual(values["resources proven"], f"{sum(r['status'] == 'PASS' for r in rows)}/{len(rows)}")

    def test_the_card_changes_when_the_ledger_changes(self):
        self.assertNotEqual(otari_cards.figures_svg(LEDGER, EVIDENCE), otari_cards.figures_svg(LEDGER[:-1], EVIDENCE))

    def test_the_header_names_the_product(self):
        self.assertIn("Verdict Desk on Otari", otari_cards.header_svg())

    def test_the_cycle_diagram_is_committed_current_and_shown_in_the_readme(self):
        self.assertEqual(otari_cards.FLOW.read_text(encoding="utf-8"), otari_cards.flow_svg(), "run scripts/otari_cards.py")
        self.assertIn("docs/readme/otari-flow.svg", (ROOT / "README.md").read_text(encoding="utf-8"))
        for step in ("Evidence", "Bull and bear", "Citation checker", "Judge", "Rules engine", "Sealed dossier", "Outside witnesses"):
            self.assertIn(step, otari_cards.flow_svg())

    def test_no_dash_in_the_cards_or_the_readme(self):
        for path in [ROOT / "README.md", otari_cards.HEADER, otari_cards.FIGURES, otari_cards.FLOW]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("—", text, path.name)
            self.assertNotIn("–", text, path.name)



if __name__ == "__main__":
    unittest.main()
