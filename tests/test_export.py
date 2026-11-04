"""The evidence pack: one dossier, self-contained, verifiable without trusting the exporter."""

import hashlib
import json
import unittest
from pathlib import Path

from verdict_desk import dossier
from verdict_desk.__main__ import export_pack
from verdict_desk.canonical import canonical


def ledger() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ExportPackTest(unittest.TestCase):
    def setUp(self):
        self.rows = ledger()

    def test_pack_covers_a_real_dossier_and_its_replay(self):
        pack = export_pack(self.rows, 80)
        self.assertEqual(pack["seq"], 80)
        self.assertEqual(pack["recorded_hash"], pack["dossier"]["hash"])
        self.assertEqual(pack["recomputed_hash"], pack["recorded_hash"])
        self.assertTrue(pack["replay"]["matches"])

    def test_pack_digest_recomputes_from_the_pack_itself(self):
        pack = export_pack(self.rows, 80)
        claimed = pack.pop("pack_digest")
        self.assertEqual(hashlib.sha256(canonical(pack)).hexdigest(), claimed)

    def test_pack_digest_changes_if_the_dossier_changes(self):
        """A tampered dossier cannot keep the same pack digest."""
        pack = export_pack(self.rows, 80)
        before = pack["pack_digest"]
        pack["dossier"]["verdict"]["action"] = "SHORT" if pack["dossier"]["verdict"]["action"] != "SHORT" else "LONG"
        body = {k: v for k, v in pack.items() if k != "pack_digest"}
        self.assertNotEqual(hashlib.sha256(canonical(body)).hexdigest(), before)

    def test_pack_links_to_its_neighbours(self):
        pack = export_pack(self.rows, 80)
        previous = next(d for d in self.rows if d["seq"] == 79)
        following = next(d for d in self.rows if d["seq"] == 81)
        self.assertEqual(pack["neighbors"]["prev_hash"], previous["hash"])
        self.assertEqual(pack["neighbors"]["next_hash"], following["hash"])
        self.assertFalse(pack["neighbors"]["is_head"])

    def test_head_dossier_is_marked_as_head(self):
        pack = export_pack(self.rows, self.rows[-1]["seq"])
        self.assertTrue(pack["neighbors"]["is_head"])
        self.assertIsNone(pack["neighbors"]["next_hash"])

    def test_pack_carries_verification_instructions(self):
        pack = export_pack(self.rows, 80)
        steps = pack["how_to_verify_this_file_without_trusting_us"]
        self.assertGreaterEqual(len(steps), 4)
        self.assertTrue(any("SHA-256" in s for s in steps))
        self.assertTrue(any("replay" in s for s in steps))

    def test_missing_sequence_raises(self):
        with self.assertRaises(ValueError):
            export_pack(self.rows, 999_999)


if __name__ == "__main__":
    unittest.main()
