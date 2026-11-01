"""The anchor monitor: does the published dossier still carry the hash the chain anchored?

These tests never touch the network. The two RPC reads are replaced, so what is under
test is the comparison logic: the exact failure a tamperer would have to defeat.
"""

import unittest
from unittest import mock

from verdict_desk import anchor

HEAD = "04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283"


def ledger(hashes: dict) -> list[dict]:
    return [{"seq": seq, "hash": h, "prev_hash": "0" * 64} for seq, h in sorted(hashes.items())]


def onchain(seq: int, head_hash: str) -> dict:
    return {
        "seq": seq,
        "head_hash": "0x" + head_hash,
        "price": 8437812678601,
        "price_decimals": 8,
        "price_updated_at": 1761000000,
        "anchored_at": 1761000000,
    }


class CompareTest(unittest.TestCase):
    def test_same_hash_matches(self):
        r = anchor.compare({"seq": 80, "hash": HEAD}, onchain(80, HEAD))
        self.assertTrue(r["matches"])
        self.assertEqual(r["problems"], [])

    def test_different_hash_is_reported(self):
        r = anchor.compare({"seq": 80, "hash": "ab" * 32}, onchain(80, HEAD))
        self.assertFalse(r["matches"])
        self.assertIn("head hash differs", r["problems"][0])

    def test_different_seq_is_reported(self):
        r = anchor.compare({"seq": 79, "hash": HEAD}, onchain(80, HEAD))
        self.assertFalse(r["matches"])
        self.assertIn("seq differs", r["problems"][0])

    def test_hash_comparison_ignores_0x_prefix(self):
        r = anchor.compare({"seq": 80, "hash": "0x" + HEAD.upper()}, onchain(80, HEAD))
        self.assertTrue(r["matches"])


class MonitorTest(unittest.TestCase):
    def test_compares_against_the_anchored_dossier_not_the_head(self):
        """The published ledger is usually ahead of the anchor; that is not a mismatch."""
        book = ledger({79: "aa" * 32, 80: HEAD, 81: "bb" * 32})
        with mock.patch.object(anchor, "read_latest_seq", return_value=80), \
             mock.patch.object(anchor, "read_anchor", return_value=onchain(80, HEAD)):
            r = anchor.monitor(book)
        self.assertTrue(r["matches"])
        self.assertEqual(r["ledger_head_seq"], 81)
        self.assertEqual(r["ledger_length"], 3)

    def test_a_rewritten_history_is_caught(self):
        """This is the whole point: rewrite dossier 80 and the chain still catches it."""
        book = ledger({79: "aa" * 32, 80: "ff" * 32, 81: "bb" * 32})
        with mock.patch.object(anchor, "read_latest_seq", return_value=80), \
             mock.patch.object(anchor, "read_anchor", return_value=onchain(80, HEAD)):
            r = anchor.monitor(book)
        self.assertFalse(r["matches"])
        self.assertIn("head hash differs", r["problems"][0])

    def test_anchored_seq_missing_from_the_ledger_is_a_mismatch(self):
        book = ledger({79: "aa" * 32})
        with mock.patch.object(anchor, "read_latest_seq", return_value=80), \
             mock.patch.object(anchor, "read_anchor", return_value=onchain(80, HEAD)):
            r = anchor.monitor(book)
        self.assertFalse(r["matches"])
        self.assertIn("no dossier with seq 80", r["problems"][0])

    def test_explicit_seq_is_honoured(self):
        book = ledger({78: "cc" * 32, 79: "aa" * 32})
        with mock.patch.object(anchor, "read_latest_seq", side_effect=AssertionError("must not be called")), \
             mock.patch.object(anchor, "read_anchor", return_value=onchain(78, "cc" * 32)):
            r = anchor.monitor(book, seq=78)
        self.assertTrue(r["matches"])


class DecodingTest(unittest.TestCase):
    def test_anchor_word_decoding(self):
        payload = "0x" + (HEAD + f"{8437812678601:064x}" + f"{8:064x}" + f"{1761000000:064x}" + f"{1761000000:064x}")
        with mock.patch.object(anchor, "_rpc", return_value=payload):
            a = anchor.read_anchor("http://x", "0xabc", 80)
        self.assertEqual(a["head_hash"], "0x" + HEAD)
        self.assertEqual(a["price"], 8437812678601)
        self.assertEqual(a["price_decimals"], 8)

    def test_short_payload_raises(self):
        with mock.patch.object(anchor, "_rpc", return_value="0x" + HEAD):
            with self.assertRaises(RuntimeError):
                anchor.read_anchor("http://x", "0xabc", 80)

    def test_latest_seq_decoding(self):
        with mock.patch.object(anchor, "_rpc", return_value=hex(80)):
            self.assertEqual(anchor.read_latest_seq("http://x", "0xabc"), 80)


if __name__ == "__main__":
    unittest.main()
