"""Practice trades executed on Sepolia, bound to the dossier both ways.

The transaction carries a digest of the decision; the dossier records the transaction before it is sealed. A
position is executed once when it opens and once when its stop or target is hit. Without a key nothing changes:
the dossier is exactly what it was before, and the position stays simulated. A failed send is recorded as a failure.
"""

import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import dossier, practice


def opened(trend=-0.004, symbol="ETHUSDT"):
    p = packet(trend, funding=0.001, titles=("Exchange hack triggers crash",))
    p["symbol"] = symbol
    return p


class FakeChain:
    def __init__(self, fail=False):
        self.requests, self.fail = [], fail

    def __call__(self, request):
        self.requests.append(request)
        if self.fail:
            return {"status": "failed", "error": "nonce too low"}
        kind = "executed" if request.get("market") else "recorded"
        return {"status": kind, "tx": "0x" + "ab" * 32, "block": 123, "amount_in": "2000000000000000", "amount_out": "92000000"}


class DigestTest(unittest.TestCase):
    def test_the_digest_names_the_decision_and_changes_with_any_of_its_numbers(self):
        p = opened()
        d = dossier.build(p, dossier.GENESIS, 1, use_llm=False, rules_version="v6")
        a = practice.decision_digest(1, p["symbol"], d["verdict"], p)
        self.assertRegex(a, r"^0x[0-9a-f]{64}$")
        v = dict(d["verdict"], stop=d["verdict"]["stop"] + 1)
        self.assertNotEqual(a, practice.decision_digest(1, p["symbol"], v, p))
        self.assertNotEqual(a, practice.decision_digest(2, p["symbol"], d["verdict"], p))


class OpenTest(unittest.TestCase):
    def test_a_new_position_is_executed_and_the_transaction_sealed_inside_the_dossier(self):
        chain = FakeChain()
        d = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=chain)
        self.assertIn(d["verdict"]["action"], ("LONG", "SHORT"))
        ex = d["execution"]
        self.assertEqual(ex["status"], "executed")
        self.assertEqual(ex["tx"], "0x" + "ab" * 32)
        self.assertEqual(ex["label"], "practice trade executed on Sepolia testnet")
        self.assertEqual(ex["decision_digest"], practice.decision_digest(1, "ETHUSDT", d["verdict"], d["evidence"]))
        self.assertEqual(chain.requests[0]["kind"], "open")
        self.assertEqual(chain.requests[0]["market"], "WETH/USDC")
        self.assertEqual(dossier.verify([d]), [])  # the execution is inside the hashed body
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])  # and changes no verdict

    def test_a_market_the_testnet_lacks_is_recorded_not_executed(self):
        chain = FakeChain()
        d = dossier.build(opened(symbol="BTCUSDT"), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=chain)
        self.assertEqual(d["execution"]["status"], "recorded")
        self.assertIsNone(chain.requests[0]["market"])
        self.assertIn("not executed", d["execution"]["label"])

    def test_a_declined_entry_sends_nothing(self):
        chain = FakeChain()
        ledger = dossier.read_ledger(Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl")
        declined = next(x for x in ledger if x["verdict"].get("rules_version") == "v5" and x["verdict"]["action"] not in ("LONG", "SHORT"))
        d = dossier.build(declined["evidence"], dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=chain)
        self.assertNotIn(d["verdict"]["action"], ("LONG", "SHORT"))
        self.assertEqual(chain.requests, [])
        self.assertNotIn("execution", d)

    def test_without_a_key_the_dossier_is_exactly_what_it_was(self):
        a = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6")
        b = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=None)
        self.assertNotIn("execution", a)
        self.assertEqual(a["hash"], b["hash"])

    def test_a_failed_send_is_recorded_and_the_position_stays_simulated(self):
        d = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=FakeChain(fail=True))
        self.assertEqual(d["execution"]["status"], "failed")
        self.assertIn("nonce", d["execution"]["error"])
        self.assertIn("simulated", d["execution"]["label"])

    def test_a_send_recovered_after_a_push_race_says_so_and_counts_as_done(self):
        class Recovered(FakeChain):
            def __call__(self, request):
                self.requests.append(request)
                return {"status": "executed", "recovered": True, "tx": "0x" + "cd" * 32, "block": 99,
                        "amount_in": "2000000000000000", "amount_out": "92000000"}

        d = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=Recovered())
        self.assertEqual(d["execution"]["status"], "executed")
        self.assertIn("earlier attempt", d["execution"]["label"])
        self.assertEqual(d["execution"]["tx"], "0x" + "cd" * 32)

    def test_the_on_chain_size_is_the_position_scaled_down_and_declared(self):
        chain = FakeChain()
        d = dossier.build(opened(), dossier.GENESIS, 1, use_llm=False, rules_version="v6", execute=chain)
        req = chain.requests[0]
        self.assertAlmostEqual(req["weth"], round(d["verdict"]["size"] * practice.SCALE, 6))
        self.assertEqual(d["execution"]["scale"], practice.SCALE)


class CloseTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"

    def test_a_position_whose_stop_or_target_is_hit_is_closed_once_citing_the_opening(self):
        chain = FakeChain()
        first = dossier.append(opened(), self.path, rules_version="v6", execute=chain)
        side = first["verdict"]["action"]
        # the next packet for the same symbol moves hard through the target (a fall for a SHORT)
        later = opened(trend=-0.02 if side == "SHORT" else 0.02)
        for i, c in enumerate(later["market"]["candles"]):
            c["t"] = first["evidence"]["market"]["candles"][-1]["t"] + i + 1
        closes = practice.plan_closes(dossier.read_ledger(self.path), "ETHUSDT", later)
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["kind"], "close")
        self.assertEqual(closes[0]["opened_seq"], first["seq"])
        self.assertEqual(closes[0]["decision"], first["execution"]["decision_digest"])
        second = dossier.append(later, self.path, rules_version="v6", execute=chain)
        self.assertEqual(second["executions_closed"][0]["opened_seq"], first["seq"])
        self.assertEqual(second["executions_closed"][0]["status"], "executed")
        self.assertEqual(practice.plan_closes(dossier.read_ledger(self.path), "ETHUSDT", later), [])  # closed once

    def test_positions_that_were_never_executed_are_not_closed_on_chain(self):
        dossier.append(opened(), self.path, rules_version="v6")
        later = opened(trend=-0.02)
        self.assertEqual(practice.plan_closes(dossier.read_ledger(self.path), "ETHUSDT", later), [])


if __name__ == "__main__":
    unittest.main()
