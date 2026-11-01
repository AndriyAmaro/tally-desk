"""The tamper battery: attack the ledger on purpose and count what the verifier misses.

A verifier is only worth trusting if someone tried hard to fool it and wrote down the result. The
battery mutates a copy of the published ledger in every way we can think of, runs the project's own
`verify` (and, for the two attacks a hash chain cannot see by itself, the timestamped heads), and
reports how many were caught and by what. The seed comes from the head hash, so anyone re-running
it gets the same attacks.
"""

import copy
import unittest
from pathlib import Path

from verdict_desk import battery, dossier

ROOT = Path(__file__).resolve().parents[1]
LEDGER = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")
STAMPS = ROOT / "ledger" / "timestamps"


class VerifyWindowTest(unittest.TestCase):
    def test_verify_accepts_the_hash_before_a_window(self):
        window = LEDGER[10:13]
        self.assertEqual(dossier.verify(window, prev=LEDGER[9]["hash"]), [])
        self.assertTrue(dossier.verify(window), "without the right predecessor a window is not a chain from genesis")

    def test_the_default_is_unchanged(self):
        self.assertEqual(dossier.verify(LEDGER), [])


class LeafTest(unittest.TestCase):
    def test_every_mutation_changes_the_value_it_touches(self):
        sample = {"a": 1.5, "b": 7, "c": "hello world", "d": True, "e": None, "f": [1, {"g": "x"}], "hash": "00"}
        found = battery.leaves(sample)
        self.assertIn(("a",), found)
        self.assertIn(("f", 1, "g"), found)
        for path in found:
            for kind, edited in battery.edits(sample, path):
                self.assertNotEqual(edited, sample, f"{kind} at {path} changed nothing")

    def test_a_homoglyph_swap_looks_the_same_but_is_not(self):
        sample = {"c": "approval"}
        kinds = dict(battery.edits(sample, ("c",)))
        self.assertIn("homoglyph", kinds)
        self.assertNotEqual(kinds["homoglyph"]["c"], "approval")
        self.assertEqual(len(kinds["homoglyph"]["c"]), len("approval"))


class BatteryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = battery.run(LEDGER, STAMPS, seed="test-seed", trials=60)

    def test_it_reports_every_attack_class_with_a_denominator(self):
        names = {row["attack"] for row in self.result["attacks"]}
        for expect in ("edit one value", "delete a dossier", "swap two neighbours", "duplicate a dossier",
                       "forge a new head", "truncate the tail", "rewrite history and re-hash it"):
            self.assertIn(expect, names)
        for row in self.result["attacks"]:
            self.assertGreater(row["trials"], 0, row["attack"])

    def test_the_chain_alone_catches_edits_deletions_swaps_duplicates_and_forgeries(self):
        for row in self.result["attacks"]:
            if row["caught_by"] == "hash chain":
                self.assertEqual(row["missed"], 0, row["attack"])

    def test_truncation_and_a_consistent_rewrite_pass_the_chain_and_are_caught_by_the_timestamped_heads(self):
        by_name = {row["attack"]: row for row in self.result["attacks"]}
        for name in ("truncate the tail", "rewrite history and re-hash it"):
            row = by_name[name]
            self.assertEqual(row["caught_by"], "timestamped heads")
            self.assertEqual(row["chain_alone_missed"], row["trials"], "the chain by itself cannot see this: that is the point of the anchors")
            self.assertEqual(row["missed"], 0)

    def test_a_history_rewrite_always_changes_the_ledger(self):
        """It re-hashes everything after the edit, so editing "hash" or "prev_hash" would change nothing and
        be counted as a miss that is no attack (three such false misses showed up at dossier 728)."""
        for d in (LEDGER[1], LEDGER[len(LEDGER) // 2], LEDGER[-1]):
            paths = battery.rewrite_paths(d)
            self.assertTrue(paths)
            self.assertNotIn(("hash",), paths)
            self.assertNotIn(("prev_hash",), paths)
        p = len(LEDGER) - 2
        for path in battery.rewrite_paths(LEDGER[p])[:40]:
            edited = copy.deepcopy(LEDGER)
            edited[p] = battery.edits(edited[p], path)[0][1]
            self.assertNotEqual(battery._rehash_from(edited, p), LEDGER, path)

    def test_the_same_seed_gives_the_same_attacks(self):
        again = battery.run(LEDGER, STAMPS, seed="test-seed", trials=60)
        self.assertEqual(self.result["attacks"], again["attacks"])

    def test_the_input_ledger_is_never_modified(self):
        before = copy.deepcopy(LEDGER)
        battery.run(LEDGER, STAMPS, seed="x", trials=10)
        self.assertEqual(LEDGER, before)

    def test_a_verifier_that_misses_something_would_be_reported_not_hidden(self):
        weak = battery.run(LEDGER, STAMPS, seed="weak", trials=20, verifier=lambda ledger, prev=dossier.GENESIS: [])
        edit = next(r for r in weak["attacks"] if r["attack"] == "edit one value")
        self.assertEqual(edit["missed"], edit["trials"])
        self.assertFalse(weak["all_caught"])


class WitnessTest(unittest.TestCase):
    def test_the_published_ledger_agrees_with_every_timestamped_head(self):
        heads = battery.timestamped_heads(STAMPS)
        self.assertGreaterEqual(len(heads), 5)
        self.assertEqual(battery.witness_problems(LEDGER, heads), [])

    def test_a_shorter_ledger_and_a_rewritten_one_both_disagree_with_a_timestamped_head(self):
        heads = battery.timestamped_heads(STAMPS)
        self.assertTrue(battery.witness_problems(LEDGER[:-3], heads))
        rewritten = copy.deepcopy(LEDGER)
        rewritten[5]["evidence"]["symbol"] = "XXX"
        prev = dossier.GENESIS
        for d in rewritten:
            d["prev_hash"] = prev
            d["hash"] = dossier.digest({k: v for k, v in d.items() if k != "hash"})
            prev = d["hash"]
        self.assertEqual(dossier.verify(rewritten), [], "a consistent rewrite passes the chain")
        self.assertTrue(battery.witness_problems(rewritten, heads))


class WitnessIndexTest(unittest.TestCase):
    """The list the browser page reads: which heads were stamped, where their proofs are, what is anchored."""

    def test_the_index_lists_every_stamped_head_with_files_that_exist(self):
        index = battery.witness_index(STAMPS, ROOT / "cre" / "deployment.sepolia.json")
        self.assertGreaterEqual(len(index["heads"]), 5)
        for head in index["heads"]:
            self.assertTrue((ROOT / head["snapshot"]).is_file(), head["snapshot"])
            self.assertTrue((ROOT / head["proof"]).is_file(), head["proof"])
        seqs = [h["seq"] for h in index["heads"]]
        self.assertEqual(seqs, sorted(seqs))

    def test_the_index_names_the_anchor_contract_the_page_will_read(self):
        anchor = battery.witness_index(STAMPS, ROOT / "cre" / "deployment.sepolia.json")["anchor"]
        self.assertRegex(anchor["address"], r"^0x[0-9a-fA-F]{40}$")
        self.assertEqual(anchor["chain_id"], 11155111)

    def test_the_anchor_history_is_read_from_a_node_that_keeps_old_blocks(self):
        """publicnode answers eth_getLogs over the contract's history with "pruned history unavailable" (26 Oct)."""
        anchor = battery.witness_index(STAMPS, ROOT / "cre" / "deployment.sepolia.json")["anchor"]
        self.assertEqual(anchor["deploy_block"], 11963992)
        self.assertTrue(anchor["history_rpc"].startswith("https://"))
        self.assertNotIn("publicnode", anchor["history_rpc"])

    def test_a_missing_deployment_file_leaves_the_anchor_out_instead_of_inventing_one(self):
        index = battery.witness_index(STAMPS, ROOT / "cre" / "nope.json")
        self.assertIsNone(index["anchor"])


if __name__ == "__main__":
    unittest.main()
