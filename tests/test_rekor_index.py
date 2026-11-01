"""The Sigstore witness: which signed heads the witnesses page should ask the public log about.

The index only says where to look. The page asks Rekor itself and compares the digest in the log with
the head it downloaded, so a wrong index can hide a witness but never fake one. These tests pin that
the index lists a signed head only when the snapshot it signed is there, and refuses what it cannot read.
"""

import json
import tempfile
import unittest
from pathlib import Path

from verdict_desk import battery

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rekor"


def workspace(with_snapshot=True, bundle="bundle-legacy.json"):
    site = Path(tempfile.mkdtemp())
    root = site / "ledger"  # the page reads paths relative to the site, so they start with ledger/
    (root / "timestamps").mkdir(parents=True)
    (root / "rekor").mkdir()
    if with_snapshot:
        (root / "timestamps" / "7.json").write_text(json.dumps({"seq": 7, "hash": "abc"}), encoding="utf-8")
    (root / "rekor" / "7.bundle.json").write_text((FIXTURES / bundle).read_text(encoding="utf-8"), encoding="utf-8")
    return root


class RekorHeadsTest(unittest.TestCase):
    def test_a_signed_head_is_listed_with_its_snapshot_and_log_index(self):
        root = workspace()
        found = battery.rekor_heads(root / "rekor", root / "timestamps")
        self.assertEqual(found, [{"seq": 7, "hash": "abc", "log_index": 424242,
                                  "snapshot": "ledger/timestamps/7.json", "bundle": "ledger/rekor/7.bundle.json"}])

    def test_the_newer_bundle_format_is_read_too(self):
        root = workspace(bundle="bundle-new.json")
        self.assertEqual(battery.rekor_heads(root / "rekor", root / "timestamps")[0]["log_index"], 424242)

    def test_a_bundle_without_its_snapshot_is_not_listed(self):
        root = workspace(with_snapshot=False)
        self.assertEqual(battery.rekor_heads(root / "rekor", root / "timestamps"), [])

    def test_a_bundle_that_cannot_be_read_is_skipped_not_fatal(self):
        root = workspace()
        (root / "rekor" / "8.bundle.json").write_text("{not json", encoding="utf-8")
        (root / "rekor" / "9.bundle.json").write_text(json.dumps({"base64Signature": "x"}), encoding="utf-8")
        self.assertEqual([h["seq"] for h in battery.rekor_heads(root / "rekor", root / "timestamps")], [7])

    def test_a_missing_folder_is_an_empty_list(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(battery.rekor_heads(root / "none", root), [])

    def test_the_witness_index_carries_them(self):
        root = workspace()
        index = battery.witness_index(root / "timestamps", root / "nope.json", rekor=root / "rekor")
        self.assertEqual([h["seq"] for h in index["rekor"]], [7])
        self.assertEqual(battery.witness_index(root / "timestamps", root / "nope.json")["rekor"], [])


if __name__ == "__main__":
    unittest.main()
