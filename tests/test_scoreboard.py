"""The scoreboard block for the build page: every number counted from the raw ledger, none typed.

The block cites a dossier ("as of dossier N"). The ledger is append-only, so the numbers at N can be
recomputed exactly at any later time; `--check` does that and fails on any difference. That is the
property worth protecting: a figure that is not reproduced does not get published.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

from verdict_desk import dossier, livestats, scoreboard

ROOT = Path(__file__).resolve().parents[1]
LEDGER = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")
AS_OF = 60  # a short prefix keeps the replay quick; the logic does not depend on the length


def build(seq=AS_OF, tests=111):
    return scoreboard.compute(LEDGER, seq, ROOT, tests=tests)


class NumbersTest(unittest.TestCase):
    def test_the_count_is_the_cited_head_not_the_current_one(self):
        data = build()
        self.assertEqual(data["seq"], AS_OF)
        self.assertEqual(data["dossiers"], AS_OF)
        self.assertEqual(data["hash"], LEDGER[AS_OF - 1]["hash"])

    def test_claims_are_counted_by_hand_here_not_by_the_function_under_test(self):
        data = build(seq=LEDGER[-1]["seq"])
        accepted = rejected = 0
        for d in LEDGER:
            for side in ("bull", "bear"):
                part = (d.get("llm") or {}).get(side) or {}
                if part.get("error"):
                    continue
                accepted += len(part.get("accepted") or [])
                rejected += len(part.get("rejected") or [])
        self.assertEqual((data["claims"]["accepted"], data["claims"]["rejected"]), (accepted, rejected))

    def test_every_refusal_has_a_reason_and_the_reasons_add_up(self):
        data = build(seq=LEDGER[-1]["seq"])
        self.assertEqual(sum(data["claims"]["reasons"].values()), data["claims"]["rejected"])
        self.assertTrue(all(reason for reason in data["claims"]["reasons"]))

    def test_replay_counts_the_dossiers_that_rederive_to_the_same_verdict(self):
        data = build()
        self.assertEqual(data["replay"]["total"], AS_OF)
        expected = sum(1 for d in LEDGER[:AS_OF]
                       if dossier.replay(d, rules_version=d.get("verdict", {}).get("rules_version", "v1"))["matches"])
        self.assertEqual(data["replay"]["matched"], expected)

    def test_the_test_count_is_the_one_given_never_invented(self):
        self.assertEqual(build(tests=999)["tests"], 999)

    def test_the_practice_book_is_settled_from_the_cited_prefix_only(self):
        data = build(seq=200)["practice"]
        positions = [d for d in LEDGER[:200] if d["verdict"]["action"] in ("LONG", "SHORT")]
        self.assertEqual(data["closed"] + data["open"], len(positions))
        self.assertEqual(data["take"] + data["stop"], data["closed"])
        # a position's outcome may only use candles recorded up to the cited dossier
        later = build(seq=201)["practice"]
        self.assertGreaterEqual(later["closed"], data["closed"])

    def test_the_net_result_is_the_gross_minus_the_declared_fees(self):
        data = build(seq=200)["practice"]
        self.assertGreater(data["fees"], 0)
        self.assertAlmostEqual(data["net"], data["realized"] - data["fees"], places=2)

    def test_the_practice_line_declares_the_fee_model(self):
        text = scoreboard.render(build(seq=200))
        line = next(l for l in text.splitlines() if "practice positions" in l)
        self.assertIn("after a 0.05% taker fee per side", line)
        self.assertIn("slippage excluded", line)

    def test_the_sepolia_executions_are_counted_apart_from_the_simulated_positions(self):
        data = build(seq=LEDGER[-1]["seq"])["sepolia"]
        rows = [d for d in LEDGER if d.get("execution")]
        self.assertEqual(data["executed"] + data["recorded"] + data["failed"], len(rows))
        self.assertEqual(data["first_seq"], rows[0]["seq"] if rows else None)
        positions = [d for d in LEDGER if d["verdict"]["action"] in ("LONG", "SHORT")]
        self.assertEqual(data["simulated_only"], len(positions) - data["executed"] - data["recorded"])

    def test_before_the_desk_existed_there_is_no_sepolia_line(self):
        self.assertNotIn("Sepolia testnet", scoreboard.render(build(seq=200)).split("Three witnesses")[0])

    def test_analyst_calls_are_split_at_the_first_dossier_that_records_a_route(self):
        calls = build(seq=LEDGER[-1]["seq"])["claims"]["calls"]
        sides = [(d["seq"], part) for d in LEDGER for side in ("bull", "bear") if (part := (d.get("llm") or {}).get(side))]
        routed_from = next((seq for seq, part in sides if "route" in part), None)
        self.assertEqual(calls["routed_from"], routed_from)
        errors = [seq for seq, part in sides if part.get("error")]
        after = [part for seq, part in sides if routed_from and seq >= routed_from]
        self.assertEqual(calls["failed_before"] + (len(after) - calls["answered"]), len(errors))
        self.assertEqual(calls["routed"], len(after))

    def test_before_routing_existed_the_line_does_not_mention_it(self):
        data = build(seq=200)
        self.assertIsNone(data["claims"]["calls"]["routed_from"])
        line = next(l for l in scoreboard.render(data).splitlines() if "analyst claims accepted" in l)
        self.assertNotIn("routing", line)
        self.assertIn("one provider at a time", line)

    def test_after_routing_the_line_gives_the_answered_share_up_to_the_cited_dossier(self):
        data = build(seq=LEDGER[-1]["seq"])
        calls = data["claims"]["calls"]
        if calls["routed_from"] is None:
            self.skipTest("the ledger has no routed dossier yet")
        line = next(l for l in scoreboard.render(data).splitlines() if "analyst claims accepted" in l)
        self.assertIn(f"(dossiers {calls['routed_from']} to {data['seq']}), {calls['answered']} of {calls['routed']} analyst calls were answered", line)
        self.assertIn(f"{calls['failed_before']} analyst calls failed before routing", line)

    def test_the_practice_result_is_the_sum_of_the_settled_positions(self):
        data = build(seq=200)["practice"]
        self.assertAlmostEqual(data["realized"], data["take"] * 200.0 - data["stop"] * 100.0, places=2)


class AnchorsTest(unittest.TestCase):
    def test_the_bitcoin_height_is_read_from_the_proof_file(self):
        # the same two archived proofs tests/ots.test.js uses; the heights come from the official client (`ots info`)
        self.assertEqual(scoreboard.bitcoin_height((ROOT / "ledger" / "timestamps" / "116.json.ots").read_bytes()), 972666)
        self.assertEqual(scoreboard.bitcoin_height((ROOT / "ledger" / "timestamps" / "170.json.ots").read_bytes()), 972852)

    def test_a_proof_with_no_block_yet_has_no_height(self):
        self.assertIsNone(scoreboard.bitcoin_height(b"\x00OpenTimestamps\x00\x00Proof\x00" + b"\x01" * 40))

    def test_the_anchors_point_to_files_that_exist(self):
        anchors = build(seq=LEDGER[-1]["seq"])["anchors"]
        if anchors["bitcoin"]:
            self.assertTrue((ROOT / anchors["bitcoin"]["proof"]).exists())
        if anchors["sigstore"]:
            self.assertTrue((ROOT / anchors["sigstore"]["bundle"]).exists())
        if anchors["sepolia"]:
            self.assertRegex(anchors["sepolia"]["tx"], r"^0x[0-9a-f]{64}$")

    def test_an_anchor_that_does_not_exist_yet_is_absent_not_invented(self):
        empty = Path(tempfile.mkdtemp())
        anchors = scoreboard.compute(LEDGER, AS_OF, empty, tests=1)["anchors"]
        self.assertEqual(anchors, {"bitcoin": None, "sigstore": None, "sepolia": None})
        self.assertEqual(scoreboard.render(scoreboard.compute(LEDGER, AS_OF, empty, tests=1)).lower().count(" yet"), 3)


    def test_an_anchor_from_the_github_route_is_never_called_a_cre_run(self):
        d = LEDGER[AS_OF - 1]
        for name, route, label in (("direct-x.txt", "direct", "outside the CRE runtime"), ("anchor-x.txt", "cre", "Chainlink CRE workflow")):
            root = Path(tempfile.mkdtemp())
            (root / "cre" / "evidence").mkdir(parents=True)
            (root / "cre" / "evidence" / name).write_text(f"Anchored seq {d['seq']} head {d['hash']} at tx 0x{'ab' * 32} (block 1)\n", encoding="utf-8")
            data = scoreboard.compute(LEDGER, AS_OF, root, tests=1)
            self.assertEqual(data["anchors"]["sepolia"]["route"], route)
            line = next(x for x in scoreboard.render(data).splitlines() if "Ethereum Sepolia" in x)
            self.assertIn(label, line)


class RenderAndCheckTest(unittest.TestCase):
    def setUp(self):
        self.data = build()
        self.text = scoreboard.render(self.data)

    def test_each_number_comes_with_what_it_proves_to_a_lawyer(self):
        for idea in ("integrity after recording", "custody", "time", "audit trail", "repeatab", "practice trades"):
            self.assertIn(idea, self.text.lower())

    def test_the_practice_line_says_simulated_before_fees_and_overlapping(self):
        line = next(l for l in self.text.splitlines() if "practice positions" in l)
        for words in ("simulated", "before fees", "overlap", "drawdown"):
            self.assertIn(words, line)

    def test_the_judge_path_is_three_steps_with_links_and_nothing_to_install(self):
        path = self.text[self.text.index("JUDGE PATH"):]
        self.assertEqual(len(re.findall(r"^\s*[123]\. ", path, flags=re.M)), 3)
        self.assertEqual(len(re.findall(r"https://", path)), 3)
        self.assertIn("nothing to install", path.lower())

    def test_it_cites_the_dossier_and_how_to_regenerate_it(self):
        self.assertIn(f"as of dossier {AS_OF}", self.text)
        self.assertIn("python3 -m verdict_desk scoreboard", self.text)

    def test_the_block_it_just_wrote_passes_its_own_check(self):
        self.assertEqual(scoreboard.check(self.text, LEDGER, ROOT, tests=111), [])

    def test_an_edited_number_fails_the_check(self):
        edited = self.text.replace(f"{self.data['claims']['accepted']} analyst claims accepted", f"{self.data['claims']['accepted'] + 1} analyst claims accepted")
        self.assertNotEqual(edited, self.text)
        self.assertTrue(scoreboard.check(edited, LEDGER, ROOT, tests=111))

    def test_a_block_whose_head_hash_is_not_the_ledgers_fails_the_check(self):
        forged = self.text.replace(self.data["hash"][:16], "0" * 16)
        self.assertTrue(any("head" in problem for problem in scoreboard.check(forged, LEDGER, ROOT, tests=111)))

    def test_the_check_still_passes_after_the_ledger_has_grown(self):
        # the block cites dossier AS_OF; later dossiers must not matter
        self.assertEqual(scoreboard.check(self.text, LEDGER, ROOT, tests=111), [])
        self.assertGreater(len(LEDGER), AS_OF)

    def test_a_different_test_count_fails_the_check(self):
        self.assertTrue(scoreboard.check(self.text, LEDGER, ROOT, tests=112))

    def test_without_a_live_count_the_cited_one_is_not_compared(self):
        self.assertEqual(scoreboard.check(self.text, LEDGER, ROOT, tests=None), [])


class PublishedBlockTest(unittest.TestCase):
    """The block committed for the build page must stay reproducible: it cites a dossier, and the ledger only grows."""

    def test_the_committed_block_passes_its_own_check(self):
        path = ROOT / "docs" / "scoreboard.txt"
        if not path.exists():
            self.skipTest("no block committed yet")
        self.assertEqual(scoreboard.check(path.read_text(encoding="utf-8"), LEDGER, ROOT, tests=None), [])


if __name__ == "__main__":
    unittest.main()
