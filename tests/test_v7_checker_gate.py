"""Rules v7: the citation checker has a consequence. A judge ruling that relies on a claim the checker marked unsupported carries no weight.

Under v1 to v6 the checker is advisory. Under v7 its marks, frozen in the dossier, are read by the rules: the replay recomputes the
consequence from them, a dossier whose checker marks are edited fails the replay, and a v6 dossier with the same marks is unchanged. No network.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from tests.test_v6_llm_judge import BEAR, BULL, ITEMS, ruling, stub
from verdict_desk import agents_v2, agents_v7, dossier, features_v2, llm

ROOT = Path(__file__).resolve().parents[1]


def layer(r, checks, checker=True):
    out = {"evidence_index": ITEMS, "bull": {"accepted": BULL, "rejected": []}, "bear": {"accepted": BEAR, "rejected": []}, "judge": {"ruling": r}}
    if checker:
        out["checker"] = {"advisory": True, "checks": checks, "checked": len(checks)}
    return out


def mark(label, supported, note=""):
    return {"label": label, "supported": supported, "note": note}


class GateTest(unittest.TestCase):
    def test_a_flagged_claim_the_judge_relies_on_voids_the_judges_lean(self):
        gate = agents_v2.checker_gate(layer(ruling(), [mark("S1", False), mark("B1", True)]))
        self.assertEqual(gate, {"flagged": ["S1"], "judge_relied_on_flagged": ["S1"], "judge_voided": True})

    def test_a_flag_on_a_claim_the_judge_did_not_rely_on_changes_nothing(self):
        gate = agents_v2.checker_gate(layer(ruling(), [mark("B1", False)]))
        self.assertEqual((gate["flagged"], gate["judge_voided"]), (["B1"], False))

    def test_no_checker_a_failed_checker_and_marks_on_unknown_labels_flag_nothing(self):
        for lay in (layer(ruling(), [], checker=False), {**layer(ruling(), []), "checker": {"advisory": True, "error": "boom"}},
                    layer(ruling(), [mark("S9", False), mark("X", False)]), layer(ruling(), [{"label": "S1", "supported": "no"}]), None):
            gate = agents_v2.checker_gate(lay)
            self.assertEqual((gate["flagged"], gate["judge_voided"]), ([], False), lay)

    def test_a_ruling_that_was_already_voided_has_nothing_to_void(self):
        gate = agents_v2.checker_gate(layer(ruling(relied_on=("S9",)), [mark("S1", False)]))
        self.assertEqual((gate["flagged"], gate["judge_relied_on_flagged"], gate["judge_voided"]), (["S1"], [], False))

    def test_the_judge_argument_disappears_only_when_voided(self):
        lay = layer(ruling(conviction="high"), [])
        self.assertEqual(len(agents_v2.llm_judge_args(lay)[1]), 1)
        self.assertEqual(agents_v2.llm_judge_args(lay, void=True), ([], []))


class DecideTest(unittest.TestCase):
    F = features_v2.compute(packet(0.0))

    def decide(self, lay, rules):
        return agents_v2.decide(self.F, rules, None, None, lay)

    def test_v7_records_the_gate_in_the_verdict_and_the_rule_in_the_policy(self):
        d = self.decide(layer(ruling(conviction="high"), [mark("S1", False)]), agents_v7.V7)
        self.assertEqual([a for a in d["bear"] if a["evidence_ref"] == "llm.judge"], [], "the judge's lean is gone")
        self.assertEqual(d["verdict"]["checker_gate"], {"flagged": ["S1"], "judge_relied_on_flagged": ["S1"], "judge_voided": True})
        self.assertIn("checker_gate", d["verdict"]["policy"])
        self.assertEqual(d["verdict"]["rules_version"], "v7")

    def test_v6_with_the_same_marks_is_unchanged_the_checker_stays_advisory_there(self):
        d = self.decide(layer(ruling(conviction="high"), [mark("S1", False)]), agents_v2.V6)
        self.assertEqual([a["evidence_ref"] for a in d["bear"] if a["evidence_ref"] == "llm.judge"], ["llm.judge"])
        self.assertNotIn("checker_gate", d["verdict"])
        self.assertNotIn("checker_gate", d["verdict"]["policy"])

    def test_v7_without_a_flag_decides_exactly_like_v6(self):
        lay = layer(ruling(conviction="high"), [mark("S1", True)])
        v6, v7 = self.decide(lay, agents_v2.V6), self.decide(lay, agents_v7.V7)
        for key in ("action", "edge", "bull_score", "bear_score"):
            self.assertEqual(v6["verdict"][key], v7["verdict"][key])
        self.assertEqual(v6["bear"], v7["bear"])

    def test_a_voided_lean_can_take_a_close_call_back_to_no_position_and_never_opens_one(self):
        f = self.F
        bear_args = [{"claim": "x", "evidence_ref": "e", "value": 1, "weight": 1.2}]
        lay = layer(ruling(conviction="high"), [mark("S1", False)])
        with_judge = agents_v2.judge(f, [], bear_args + agents_v2.llm_judge_args(lay)[1], rules=agents_v2.V6)
        without = agents_v2.judge(f, [], bear_args, rules=agents_v7.V7)
        self.assertEqual(with_judge["action"], "SHORT")
        self.assertNotIn(without["action"], ("LONG", "SHORT"))


class DossierTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.pkt = packet(0.0, titles=("Bitcoin rally hits record",))

    def build(self, flag=True, rules_version="v7"):
        answer = ruling(lean="bull", conviction="high", relied_on=("B1",), reason="B1 is the stronger case.")
        checks = {"checks": [mark("B1", not flag, "overstates")], "checked": 1, "supported": int(not flag), "claims": 1}
        with mock.patch.object(llm, "use_checker", lambda ask=None: True), mock.patch.object(llm, "check_claims", lambda labels, items, ask=None: dict(checks)):
            return dossier.append(self.pkt, self.path, ask=stub(answer), rules_version=rules_version)

    def test_a_flagged_claim_the_judge_relied_on_leaves_the_judge_out_of_the_verdict_and_the_replay_agrees(self):
        d = self.build(flag=True)
        self.assertEqual(d["llm"]["checker"]["checks"][0]["supported"], False)
        self.assertTrue(d["llm"]["judge"]["accepted"], "the ruling itself still held; the checker is what voids its weight")
        self.assertFalse([a for a in d["bull"] + d["bear"] if a["evidence_ref"] == "llm.judge"])
        self.assertTrue(d["verdict"]["checker_gate"]["judge_voided"])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_the_same_marks_under_v6_leave_the_judge_in(self):
        d = self.build(flag=True, rules_version="v6")
        self.assertEqual(len([a for a in d["bull"] if a["evidence_ref"] == "llm.judge"]), 1)
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_an_unflagged_claim_keeps_the_judge_in_under_v7(self):
        d = self.build(flag=False)
        self.assertEqual(len([a for a in d["bull"] if a["evidence_ref"] == "llm.judge"]), 1)
        self.assertFalse(d["verdict"]["checker_gate"]["judge_voided"])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_editing_the_checkers_marks_after_the_fact_fails_the_replay(self):
        d = self.build(flag=True)
        forged = copy.deepcopy(d)
        forged["llm"]["checker"]["checks"][0]["supported"] = True  # hide the flag so that the judge's lean would count again
        self.assertFalse(dossier.replay(forged, rules_version="auto")["matches"])

    def test_the_sealed_verdict_carries_the_rule_so_a_reader_does_not_need_the_code(self):
        d = self.build(flag=True)
        self.assertEqual(d["verdict"]["policy"]["checker_gate"], "a judge ruling that relies on a claim the citation checker marked unsupported carries no weight")

    def test_every_dossier_of_the_ledger_replays_and_only_v7_ones_carry_the_gate(self):
        ledger = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        for d in ledger[-30:]:
            self.assertTrue(dossier.replay(d, rules_version="auto")["matches"], d["seq"])
            v7 = d["verdict"].get("rules_version") == "v7"
            self.assertEqual("checker_gate" in d["verdict"], v7, f"dossier {d['seq']}: the gate belongs to v7 verdicts and to no other")
            if v7:
                self.assertEqual(d["verdict"]["checker_gate"], agents_v2.checker_gate(d["llm"]), d["seq"])


from tests.test_otari_tools import FlowEnv  # noqa: E402


class FullCycleTest(FlowEnv):
    """A whole cycle through the command line path (every Otari tool, the checker, the sandbox re-derivation), under rules v7."""

    def test_a_cycle_whose_checker_flags_the_claim_the_judge_relied_on_seals_the_gate_and_still_replays_and_re_derives(self):
        self.CHECKER_ANSWER = '[{"label": "B1", "supported": false, "note": "the claim overstates its evidence"}]'
        code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, len(ledger)), (0, 1))
        d = ledger[0]
        self.assertEqual(d["verdict"]["rules_version"], "v7")
        self.assertEqual(d["llm"]["checker"]["checks"][0]["supported"], False)
        self.assertEqual(d["verdict"]["checker_gate"], {"flagged": ["B1"], "judge_relied_on_flagged": ["B1"], "judge_voided": True})
        self.assertFalse([a for a in d["bull"] + d["bear"] if a["evidence_ref"] == "llm.judge"])
        result = dossier.replay(d, rules_version="auto")
        self.assertTrue(result["matches"], result)
        self.assertTrue(d["sandbox"]["equal"] and d["sandbox"]["proof_ok"], "the sandbox re-derives the verdict from the same sealed arguments")

    def test_a_cycle_whose_checker_supports_everything_keeps_the_judge_in(self):
        code, _, ledger = self.run_flow(rules_version="v7")
        d = ledger[0]
        self.assertEqual(code, 0)
        self.assertFalse(d["verdict"]["checker_gate"]["judge_voided"])
        self.assertEqual(len([a for a in d["bull"] if a["evidence_ref"] == "llm.judge"]), 1)
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_the_command_exists_and_runs_the_v7_flow(self):
        import subprocess
        import sys
        out = subprocess.run([sys.executable, "-m", "verdict_desk", "decide-v7", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("decide-v7", out.stdout)
        from verdict_desk import __main__ as cli
        with mock.patch.object(cli, "_decide_flow", return_value=0) as flow:
            self.assertEqual(cli.cmd_decide_v7(object()), 0)
        self.assertEqual(flow.call_args.args[1], "v7")


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
