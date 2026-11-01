import json
import tempfile
import unittest
from pathlib import Path

from verdict_desk import agents, dossier, features


def packet(trend: float, funding: float = 0.0001, titles: tuple = ()) -> dict:
    candles = []
    price = 100.0
    for i in range(72):
        price *= 1 + trend
        candles.append({"t": i, "o": price, "h": price * 1.004, "l": price * 0.996, "c": price, "v": 100 + (50 if i == 71 else 0)})
    return {
        "symbol": "BTCUSDT",
        "collected_at": "2026-10-12T00:00:00+00:00",
        "market": {"source": "test", "interval": "1h", "candles": candles},
        "funding": {"source": "test", "instrument": "BTC-USDT-SWAP", "rate": funding},
        "news": {"source": "test", "query": "bitcoin", "articles": [{"title": t, "url": "", "domain": "", "seen": ""} for t in titles]},
    }


class AgentsTest(unittest.TestCase):
    def test_uptrend_breakout_goes_long_with_risk_bounded_size(self):
        f = features.compute(packet(0.004, funding=-0.0001, titles=("Bitcoin rally hits record",)))
        verdict = agents.decide(f)["verdict"]
        self.assertEqual(verdict["action"], "LONG")
        self.assertTrue(verdict["simulated"])
        self.assertLess(verdict["stop"], verdict["entry"])
        risk = (verdict["entry"] - verdict["stop"]) * verdict["size"]
        self.assertAlmostEqual(risk, 10_000 * agents.MAX_RISK_PCT / 100, places=4)

    def test_downtrend_goes_short(self):
        f = features.compute(packet(-0.004, funding=0.001, titles=("Exchange hack triggers crash",)))
        self.assertEqual(agents.decide(f)["verdict"]["action"], "SHORT")

    def test_flat_market_stays_flat(self):
        self.assertEqual(agents.decide(features.compute(packet(0.0)))["verdict"]["action"], "FLAT")

    def test_every_argument_cites_its_evidence(self):
        debate = agents.decide(features.compute(packet(0.004, funding=-0.0001)))
        for arg in debate["bull"] + debate["bear"]:
            self.assertTrue(arg["evidence_ref"].startswith("features."))
            self.assertIsNotNone(arg["value"])


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        for trend in (0.004, -0.004, 0.0):
            dossier.append(packet(trend), self.path)

    def test_chain_links_and_verifies(self):
        ledger = dossier.read_ledger(self.path)
        self.assertEqual([d["seq"] for d in ledger], [1, 2, 3])
        self.assertEqual(ledger[0]["prev_hash"], dossier.GENESIS)
        self.assertEqual(ledger[1]["prev_hash"], ledger[0]["hash"])
        self.assertEqual(dossier.verify(ledger), [])

    def test_replay_from_disk_matches_every_decision(self):
        for d in dossier.read_ledger(self.path):
            self.assertTrue(dossier.replay(d)["matches"])

    def test_tampering_with_old_evidence_is_detected(self):
        ledger = dossier.read_ledger(self.path)
        ledger[0]["evidence"]["market"]["candles"][-1]["c"] = 1.0
        problems = dossier.verify(ledger)
        self.assertTrue(any("seq 1" in p and "altered" in p for p in problems))
        self.assertFalse(dossier.replay(ledger[0])["matches"])

    def test_rewriting_a_dossier_breaks_the_next_link(self):
        ledger = dossier.read_ledger(self.path)
        body = {k: v for k, v in ledger[0].items() if k != "hash"}
        body["verdict"] = {**body["verdict"], "action": "SHORT"}
        ledger[0] = {**body, "hash": dossier.digest(body)}
        self.assertTrue(any("seq 2" in p and "prev_hash" in p for p in dossier.verify(ledger)))


if __name__ == "__main__":
    unittest.main()


class HeadTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        for trend in (0.004, -0.004):
            dossier.append(packet(trend), self.path)

    def test_head_is_the_last_hash_in_the_shape_the_anchor_workflow_parses(self):
        import re

        ledger = dossier.read_ledger(self.path)
        head = dossier.head(ledger)
        self.assertEqual(head, {"seq": 2, "hash": ledger[-1]["hash"], "symbol": "BTCUSDT"})
        self.assertRegex(head["hash"], re.compile(r"^[0-9a-f]{64}$"))

    def test_head_refuses_an_empty_or_tampered_ledger(self):
        with self.assertRaises(ValueError):
            dossier.head([])
        ledger = dossier.read_ledger(self.path)
        ledger[0]["verdict"]["rationale"] = "edited"
        with self.assertRaises(ValueError):
            dossier.head(ledger)
