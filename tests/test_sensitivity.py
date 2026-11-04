import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import agents, dossier, features


def arg(claim, weight):
    return {"claim": claim, "evidence_ref": "x", "value": 0, "weight": weight}


class SensitivityTest(unittest.TestCase):
    def test_flat_verdict_reports_how_far_each_entry_is(self):
        s = agents.sensitivity([arg("b1", 1.0)], [arg("r1", 2.0)])
        self.assertEqual(s["edge"], -1.0)
        self.assertEqual(s["needs_for_long"], 3.0)
        self.assertEqual(s["needs_for_short"], 1.0)

    def test_entry_already_reached_needs_nothing(self):
        s = agents.sensitivity([arg("b1", 1.5), arg("b2", 1.0)], [])
        self.assertEqual((s["edge"], s["needs_for_long"], s["needs_for_short"]), (2.5, 0.0, 4.5))

    def test_drop_one_marks_only_the_arguments_that_alone_flip_the_verdict(self):
        s = agents.sensitivity([arg("trend", 1.0), arg("breakout", 1.5)], [arg("funding", 0.5)])
        # edge 2.0 => LONG; without trend: 1.0 FLAT (flips), without breakout: 0.5 FLAT (flips), without funding: 2.5 LONG
        flips = {d["claim"]: d["flips"] for d in s["drop_one"]}
        self.assertEqual(flips, {"trend": True, "breakout": True, "funding": False})
        funding = next(d for d in s["drop_one"] if d["claim"] == "funding")
        self.assertEqual((funding["edge_without"], funding["action_without"]), (2.5, "LONG"))

    def test_it_uses_the_same_rules_as_the_judge(self):
        strict = agents.Rules(entry_threshold=3.0)
        s = agents.sensitivity([arg("a", 1.0), arg("b", 1.5)], [], strict)
        self.assertEqual(s["needs_for_long"], 0.5)
        self.assertEqual(s["drop_one"][0]["action_without"], "FLAT")

    def test_decide_still_gives_the_same_verdict_as_before(self):
        f = features.compute(packet(0.004, funding=-0.0001, titles=("Bitcoin rally hits record",)))
        d = agents.decide(f)
        self.assertEqual(d["verdict"], agents.judge(f, d["bull"], d["bear"]))
        self.assertEqual(d["sensitivity"]["edge"], d["verdict"]["edge"])

    def test_dossier_freezes_sensitivity_and_replay_detects_tampering(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        d = dossier.append(packet(0.004, funding=-0.0001), path)
        self.assertIn("sensitivity", d)
        self.assertTrue(dossier.replay(d)["matches"])
        # Tampering with the verdict action should be detected by replay
        d["verdict"]["action"] = "SHORT"
        self.assertFalse(dossier.replay(d)["matches"])


if __name__ == "__main__":
    unittest.main()
