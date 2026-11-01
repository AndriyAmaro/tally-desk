"""The model bench and the routing policy: which free model answers is a measured, recorded decision.

The bench counts, from the ledger alone, how each model that answered did: calls, empty answers, claims and how
many passed the citation validator (with a Wilson lower bound so a small sample cannot look like a champion),
refusal reasons, and the judge's rulings kept or voided. The routing policy orders the models by that record under
a rule fixed in advance, and config/model_routing.json must be exactly what the rule gives on the ledger up to its
`as_of_seq`: nobody can hand-tune the order.
"""

import json
import math
import unittest
from pathlib import Path

from verdict_desk import dossier, models

ROOT = Path(__file__).resolve().parents[1]
LEDGER = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")
POLICY = ROOT / "config" / "model_routing.json"


def d(seq, bull=None, bear=None, judge=None, voices=None):
    llm = {}
    if bull is not None:
        llm["bull"] = bull
    if bear is not None:
        llm["bear"] = bear
    if judge is not None:
        llm["judge"] = judge
    out = {"seq": seq, "evidence": {"collected_at": "2026-10-20T00:00:00+00:00"}}
    if voices is not None:
        out["evidence"]["narrative"] = {"served_by": voices}
    if llm:
        out["llm"] = llm
    return out


def side(model, ok=0, refused=(), error=None):
    out = {"accepted": [{"claim": "c"}] * ok, "rejected": [{"claim": "r", "rejected_because": r} for r in refused]}
    if model:
        out["served_by"] = model
    if error:
        out["error"] = error
    return out


class WilsonTest(unittest.TestCase):
    def test_the_lower_bound_is_below_the_rate_and_tightens_with_the_sample(self):
        self.assertEqual(models.wilson_low(0, 0), 0.0)
        self.assertLess(models.wilson_low(4, 4), models.wilson_low(96, 96))
        self.assertLess(models.wilson_low(96, 96), 1.0)
        p, n, z = 109 / 115, 115, 1.96
        expected = (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)
        self.assertAlmostEqual(models.wilson_low(109, 115), round(expected, 4), places=4)


class BenchTest(unittest.TestCase):
    def test_each_call_is_counted_for_the_model_that_answered_it_under_its_routing_name(self):
        ledger = [d(1, side("openai/gpt-oss-120b", ok=3, refused=["number not in cited evidence: 24"]),
                    side("agnes-3.0-flash", ok=2), {"ruling": {"lean": "bear"}, "accepted": True, "served_by": "agnes-3.0-flash"}),
                  d(2, side("agnes-3.0-flash"), side(None, error="HTTP 429"), {"ruling": {"lean": "bull"}, "accepted": False, "served_by": "agnes-3.0-flash"},
                    voices=["agnes-3.0-flash"])]
        bench = {m["id"]: m for m in models.bench(ledger)["models"]}
        gpt = bench["gpt-oss-120b"]
        self.assertEqual((gpt["calls"], gpt["claims"], gpt["passed"]), (1, 4, 3))
        self.assertEqual(gpt["reasons"], {"number not in cited evidence": 1})
        agnes = bench["agnes-3.0-flash"]
        self.assertEqual((agnes["calls"], agnes["empty"], agnes["claims"], agnes["passed"]), (2, 1, 2, 2))
        self.assertEqual(agnes["judge"], {"rulings": 2, "kept": 1, "voided": 1})
        self.assertEqual(agnes["voice_batches"], 1)
        self.assertEqual((agnes["first_seq"], agnes["last_seq"]), (1, 2))

    def test_failed_calls_with_no_answering_model_are_not_credited_to_anyone(self):
        bench = models.bench([d(1, side(None, error="HTTP 402"), side(None, error="HTTP 402"))])
        self.assertEqual(bench["models"], [])
        self.assertEqual(bench["unanswered_calls"], 2)


class PolicyTest(unittest.TestCase):
    def test_the_rule_ranks_by_the_lower_bound_and_puts_small_samples_under_observation(self):
        ledger = [d(i, side("openai/gpt-oss-120b", ok=5), side("agnes-3.0-flash", ok=5, refused=["unknown evidence id"] if i == 1 else ())) for i in range(1, 13)]
        ledger.append(d(13, side("dots-studio/dots-3-note-preview:free", ok=2), side("dots-studio/dots-3-note-preview:free", ok=1)))
        policy = models.policy(ledger)
        self.assertEqual(policy["ranked"], ["gpt-oss-120b", "agnes-3.0-flash"])
        self.assertEqual(policy["observing"], ["dots3-note-preview"])
        self.assertEqual(policy["order"], policy["ranked"] + policy["observing"])
        self.assertEqual(policy["as_of_seq"], 13)

    def test_a_model_whose_lower_bound_falls_under_the_floor_is_dropped(self):
        ledger = [d(i, side("openai/gpt-oss-120b", ok=5), side("agnes-3.0-flash", ok=2, refused=["unknown evidence id"] * 3)) for i in range(1, 9)]
        policy = models.policy(ledger)
        self.assertIn("agnes-3.0-flash", policy["dropped"])
        self.assertNotIn("agnes-3.0-flash", policy["order"])

    def test_the_policy_is_frozen_for_the_judging_days(self):
        self.assertTrue(models.frozen("2026-10-30T12:00:00+00:00"))
        self.assertTrue(models.frozen("2026-10-31T23:00:00+00:00"))
        self.assertFalse(models.frozen("2026-10-29T23:59:00+00:00"))

    def test_the_committed_policy_is_exactly_what_the_rule_gives_on_the_ledger(self):
        committed = json.loads(POLICY.read_text(encoding="utf-8"))
        prefix = [x for x in LEDGER if x["seq"] <= committed["as_of_seq"]]
        self.assertEqual(committed, models.policy(prefix))
        self.assertTrue(committed["order"], "the gateway needs at least one model to call")

    def test_every_routed_model_maps_to_the_names_the_provider_answers_with(self):
        committed = json.loads(POLICY.read_text(encoding="utf-8"))
        for model in committed["order"]:
            self.assertIn(model, committed["aliases"])


if __name__ == "__main__":
    unittest.main()
