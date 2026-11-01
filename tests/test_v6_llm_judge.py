"""Rules v6: an LLM judge may tip a close call, but only on claims that passed the citation validator.

The judge reads the validated claims of both analysts (never the refused ones) and leans bull, bear or
neutral. The lean becomes one argument whose weight comes from a fixed table, capped below the entry
band, so the LLM can never open a position on its own. The ruling is checked deterministically from what
the dossier froze: an unknown claim id, a lean that rests on no claim of its own side, or a number the
relied-on evidence does not contain voids it, and the replay re-runs that check.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import agents_v2, agents_v6, citations, dossier, llm

ITEMS = {"E1": "BTCUSDT last close 101.74 at t=1, volume 52527.499", "E2": "Return over the last 24 candles: -2.5 percent"}
BULL = [{"claim": "Price holds at 101.74", "cites": ["E1"]}]
BEAR = [{"claim": "The last 24 candles lost -2.5 percent", "cites": ["E2"]}]


def ruling(lean="bear", conviction="medium", relied_on=("S1",), reason="The decline of -2.5 percent outweighs the hold."):
    return {"lean": lean, "conviction": conviction, "relied_on": list(relied_on), "reason": reason}


def check(r):
    return citations.validate_ruling(r, BULL, BEAR, ITEMS)


class RulingValidationTest(unittest.TestCase):
    def test_a_lean_resting_on_its_own_validated_claim_counts_with_the_table_weight(self):
        for conviction, weight in (("low", 0.5), ("medium", 0.75), ("high", 1.0)):
            v = check(ruling(conviction=conviction))
            self.assertTrue(v["accepted"], v)
            self.assertEqual(v["weight"], weight)

    def test_the_weight_can_never_reach_the_entry_band(self):
        self.assertLess(max(citations.JUDGE_WEIGHT.values()), agents_v6.V6.entry_threshold)

    def test_neutral_is_a_valid_ruling_that_moves_nothing(self):
        v = check(ruling(lean="neutral", relied_on=()))
        self.assertTrue(v["accepted"])
        self.assertEqual(v["weight"], 0.0)

    def test_rulings_that_do_not_hold_are_voided_with_their_reason(self):
        cases = {
            "unknown lean": ruling(lean="up"),
            "unknown conviction": ruling(conviction="certain"),
            "no validated claim relied on": ruling(relied_on=()),
            "unknown claim id": ruling(relied_on=("S9",)),
            "lean not supported by its own side": ruling(lean="bull", relied_on=("S1",)),
            "number not in relied-on evidence": ruling(reason="Price will fall to 95 next."),
        }
        for reason, r in cases.items():
            v = check(r)
            self.assertFalse(v["accepted"], reason)
            self.assertEqual(v["weight"], 0.0, reason)
            self.assertTrue(v["rejected_because"].startswith(reason), (reason, v["rejected_because"]))

    def test_claim_ids_in_the_reason_are_references_not_numbers(self):
        self.assertTrue(check(ruling(reason="S1 shows -2.5 percent; B1 only holds."))["accepted"])

    def test_a_ruling_that_is_not_an_object_is_voided(self):
        self.assertFalse(citations.validate_ruling([], BULL, BEAR, ITEMS)["accepted"])


class JudgeArgumentTest(unittest.TestCase):
    def layer(self, r):
        return {"evidence_index": ITEMS, "bull": {"accepted": BULL, "rejected": []},
                "bear": {"accepted": BEAR, "rejected": []}, "judge": {"ruling": r}}

    def test_an_accepted_lean_becomes_one_argument_on_its_side(self):
        bull, bear = agents_v2.llm_judge_args(self.layer(ruling(conviction="high")))
        self.assertEqual(bull, [])
        self.assertEqual(len(bear), 1)
        self.assertEqual(bear[0]["evidence_ref"], "llm.judge")
        self.assertEqual(bear[0]["weight"], 1.0)
        self.assertEqual(bear[0]["value"], ["S1"])

    def test_the_weight_is_recomputed_from_the_ruling_not_read_from_a_stored_field(self):
        layer = self.layer(ruling(conviction="low"))
        layer["judge"]["weight"] = 9.0
        _, bear = agents_v2.llm_judge_args(layer)
        self.assertEqual(bear[0]["weight"], 0.5)

    def test_a_voided_ruling_or_a_missing_layer_adds_nothing(self):
        self.assertEqual(agents_v2.llm_judge_args(self.layer(ruling(relied_on=("S9",)))), ([], []))
        self.assertEqual(agents_v2.llm_judge_args(None), ([], []))
        self.assertEqual(agents_v2.llm_judge_args({"bull": {}, "bear": {}}), ([], []))

    def test_the_judge_alone_never_opens_a_position(self):
        f = {"last_close": 100.0, "atr_14": 1.0}
        _, bear = agents_v2.llm_judge_args(self.layer(ruling(conviction="high")))
        verdict = agents_v2.judge(f, [], bear, rules=agents_v6.V6)
        self.assertNotIn(verdict["action"], ("LONG", "SHORT"))


def stub(judge_answer):
    """Analysts that each make one checkable claim, and a judge that answers `judge_answer`."""
    calls = []

    def ask(system, user):
        calls.append((system, user))
        if system.startswith("You are the judge"):
            return judge_answer if isinstance(judge_answer, str) else json.dumps(judge_answer)
        if "bull analyst" in system:
            return json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}, {"claim": "Moon at 999999", "cites": ["E4"]}])
        return json.dumps([{"claim": "Rally is old news", "cites": ["E4"]}])
    ask.calls = calls
    return ask


class DossierV6Test(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.pkt = packet(0.0, titles=("Bitcoin rally hits record",))

    def build(self, answer):
        ask = stub(answer)
        return dossier.append(self.pkt, self.path, ask=ask, rules_version="v6"), ask

    def test_the_judge_reads_only_validated_claims_and_its_lean_enters_the_debate(self):
        d, ask = self.build(ruling(lean="bull", conviction="high", relied_on=("B1",), reason="B1 is the stronger case."))
        system, user = ask.calls[-1]
        self.assertTrue(system.startswith("You are the judge"))
        self.assertIn("B1: Bitcoin rally hits record", user)
        self.assertNotIn("999999", user)  # the refused claim never reaches the judge
        self.assertTrue(d["llm"]["judge"]["accepted"])
        judged = [a for a in d["bull"] if a["evidence_ref"] == "llm.judge"]
        self.assertEqual([a["weight"] for a in judged], [1.0])
        self.assertEqual(d["verdict"]["rules_version"], "v6")
        self.assertEqual(d["verdict"]["bull_score"] - d["verdict"]["bear_score"], d["verdict"]["edge"])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_without_an_llm_v6_decides_exactly_like_v5(self):
        v5 = dossier.build(self.pkt, dossier.GENESIS, 1, use_llm=False, rules_version="v5")
        v6 = dossier.build(self.pkt, dossier.GENESIS, 1, use_llm=False, rules_version="v6")
        for key in ("action", "edge", "bull_score", "bear_score"):
            self.assertEqual(v5["verdict"][key], v6["verdict"][key])
        self.assertTrue(dossier.replay(v6, rules_version="auto")["matches"])

    def test_a_judge_that_fails_is_recorded_and_moves_nothing(self):
        d, _ = self.build("no json here")
        self.assertFalse(d["llm"]["judge"]["accepted"])
        self.assertFalse([a for a in d["bull"] + d["bear"] if a["evidence_ref"] == "llm.judge"])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_a_ruling_edited_after_recording_fails_the_replay(self):
        d, _ = self.build(ruling(lean="bull", conviction="high", relied_on=("B1",), reason="B1 is the stronger case."))
        d["llm"]["judge"]["ruling"]["lean"] = "bear"
        d["llm"]["judge"]["ruling"]["relied_on"] = ["S1"]
        self.assertFalse(dossier.replay(d, rules_version="auto")["matches"])

    def test_older_versions_never_call_the_judge(self):
        ask = stub(ruling())
        dossier.build(self.pkt, dossier.GENESIS, 1, ask=ask, rules_version="v5")
        self.assertFalse([s for s, _ in ask.calls if s.startswith("You are the judge")])


class ParseRulingTest(unittest.TestCase):
    def test_the_object_is_found_inside_prose_and_junk_becomes_empty(self):
        self.assertEqual(llm.parse_ruling('Ruling: {"lean": "bull"} done')["lean"], "bull")
        self.assertEqual(llm.parse_ruling("nothing"), {})
        self.assertEqual(llm.parse_ruling("[1, 2]"), {})
        self.assertEqual(llm.parse_ruling(None), {})


JUDGE_PAGE = Path(__file__).resolve().parents[1] / "web" / "judge.html"


class JudgePageStatesTheMoatTest(unittest.TestCase):
    """The judge path must say what the bounty is judged on, with the numbers the judge actually runs."""

    def test_the_page_says_the_verdict_is_code_and_quotes_the_band_it_enforces(self):
        html = JUDGE_PAGE.read_text(encoding="utf-8")
        self.assertIn("The verdict is deterministic code, not an LLM\u2019s call", html)
        cap = max(citations.JUDGE_WEIGHT.values())
        band = agents_v6.V6.entry_threshold
        self.assertIn(f"capped at {cap:.1f} against an entry band of {band:.1f}", html)


if __name__ == "__main__":
    unittest.main()
