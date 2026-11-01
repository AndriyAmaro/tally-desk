"""The judged series: which model wrote each stretch of the ledger, and why it changed, read from the ledger itself.

Nothing is rewritten: consecutive dossiers are grouped by their configured model and by whether the LLM layer
answered, failures are named from the error each dossier recorded, and the models a router actually used are
listed from served_by.
"""

import unittest
from pathlib import Path

from verdict_desk import dossier, series

LEDGER = dossier.read_ledger(Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl")


def d(seq, llm=None, t="2026-10-20T00:00:00+00:00"):
    out = {"seq": seq, "evidence": {"collected_at": t}}
    if llm is not None:
        out["llm"] = llm
    return out


def ok(model, served=None):
    side = {"accepted": [{"claim": "x"}], "rejected": []}
    if served:
        side = {**side, "served_by": served}
    return {"model": model, "bull": side, "bear": side}


def failed(model, error):
    return {"model": model, "bull": {"accepted": [], "rejected": [], "error": error}, "bear": {"accepted": [], "rejected": [], "error": error}}


class SeriesTest(unittest.TestCase):
    def test_consecutive_records_with_the_same_model_and_status_form_one_era(self):
        eras = series.eras([d(1), d(2, ok("m1")), d(3, ok("m1")), d(4, failed("m1", "HTTP 402 Payment Required")), d(5, ok("m2"))])
        self.assertEqual([(e["from_seq"], e["to_seq"]) for e in eras], [(1, 1), (2, 3), (4, 4), (5, 5)])
        self.assertEqual([e["status"] for e in eras], ["no LLM layer", "answered", "failed", "answered"])

    def test_failures_are_named_from_the_recorded_error(self):
        cases = {"HTTP 402 Payment Required": "provider billing (HTTP 402)",
                 "Your credit balance is too low to access the Anthropic API": "provider credit exhausted",
                 "HTTP 429 Too Many Requests": "rate limited (HTTP 429)", "boom": "provider error"}
        for error, reason in cases.items():
            self.assertEqual(series.eras([d(1, failed("m", error))])[0]["reason"], reason)

    def test_the_models_a_router_really_used_are_counted_per_call(self):
        eras = series.eras([d(1, ok("auto", "a")), d(2, ok("auto", "b")), d(3, ok("auto", "a"))])
        self.assertEqual(eras[0]["served_by"], {"a": 4, "b": 2})  # two analyst calls per record

    def test_the_real_ledger_is_covered_once_with_no_gap(self):
        eras = series.eras(LEDGER)
        self.assertEqual(eras[0]["from_seq"], 1)
        self.assertEqual(eras[-1]["to_seq"], LEDGER[-1]["seq"])
        for a, b in zip(eras, eras[1:]):
            self.assertEqual(b["from_seq"], a["to_seq"] + 1)
        self.assertEqual(sum(e["records"] for e in eras), len(LEDGER))


if __name__ == "__main__":
    unittest.main()
