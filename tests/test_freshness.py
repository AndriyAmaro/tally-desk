"""Fail-closed freshness gate: stale evidence must block the decision, not be ignored.

Two properties matter here:
  1. A stale market packet produces BLOCKED_STALE and no simulated position.
  2. Dossiers recorded before this feature existed keep replaying unchanged.
"""

import unittest
from datetime import datetime, timedelta, timezone

from verdict_desk import agents_v2, dossier, evidence, features_v2


def packet(newest_candle_age_minutes: float = 1.0, collected_at: datetime | None = None) -> dict:
    now = collected_at or datetime.now(timezone.utc)
    newest = int((now - timedelta(minutes=newest_candle_age_minutes)).timestamp() * 1000)
    candles = []
    for i in range(72, 0, -1):
        t = newest - (i - 1) * 3_600_000
        price = 100.0 + (72 - i) * 0.4
        candles.append({"t": t, "o": price, "h": price * 1.004, "l": price * 0.996, "c": price, "v": 100.0})
    p = {
        "symbol": "BTCUSDT",
        "collected_at": now.isoformat(timespec="seconds"),
        "market": {"source": "test", "interval": "1h", "candles": candles},
        "funding": {"source": "test", "instrument": "BTC-USDT-SWAP", "rate": 0.0001},
        "news": {"source": "test", "query": "bitcoin", "articles": []},
    }
    p["freshness"] = evidence.freshness(p)
    return p


class FreshnessTest(unittest.TestCase):
    def test_recent_candle_is_within_the_window(self):
        f = evidence.freshness(packet(1.0))
        self.assertTrue(f["ok"])
        self.assertLess(f["newest_candle_age_minutes"], 2)

    def test_stale_candle_is_outside_the_window(self):
        f = evidence.freshness(packet(300.0))
        self.assertFalse(f["ok"])
        self.assertIn("window", f["reason"])

    def test_freshness_never_reads_the_clock_at_decision_time(self):
        """Same packet, computed twice, must give the same answer: replay depends on it."""
        p = packet(300.0)
        self.assertEqual(evidence.freshness(p), evidence.freshness(p))

    def test_stale_evidence_blocks_instead_of_deciding(self):
        f = features_v2.compute(packet(300.0))
        verdict = agents_v2.decide(f)["verdict"]
        self.assertEqual(verdict["action"], "BLOCKED_STALE")
        self.assertIn("Refused to decide", verdict["rationale"])
        self.assertNotIn("entry", verdict, "a blocked decision must not size a position")
        self.assertNotIn("size", verdict)

    def test_stale_block_is_reproducible_from_the_frozen_packet(self):
        p = packet(300.0)
        first = agents_v2.decide(features_v2.compute(p))["verdict"]
        second = agents_v2.decide(features_v2.compute(p))["verdict"]
        self.assertEqual(first, second)

    def test_packet_without_market_data_fails_closed(self):
        f = evidence.freshness({"collected_at": "2026-10-22T00:00:00+00:00", "market": {"candles": []}})
        self.assertFalse(f["ok"])

    def test_old_dossier_without_freshness_key_still_replays(self):
        """Backward compatibility: 95 dossiers were recorded before this gate existed."""
        import json
        from pathlib import Path

        ledger_path = Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl"
        ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        old = [d for d in ledger if "freshness" not in d.get("evidence", {})]
        self.assertTrue(old, "the fixture ledger should still contain pre-gate dossiers")
        for d in old[:5]:
            result = dossier.replay(d, rules_version=d.get("verdict", {}).get("rules_version", "v1"))
            self.assertTrue(result["matches"], f"seq {d['seq']} stopped replaying after the freshness gate")

    def test_fresh_packet_is_not_blocked(self):
        f = features_v2.compute(packet(1.0))
        verdict = agents_v2.decide(f)["verdict"]
        self.assertNotEqual(verdict["action"], "BLOCKED_STALE")


if __name__ == "__main__":
    unittest.main()
