"""Two capabilities the bounty names and that are easy to claim without building.

  * "user-defined risk limits": a limit the user sets, inside a ceiling the system owns,
    frozen into the dossier so the replay reapplies the same rule.
  * freshness by source class: price goes stale in minutes and blocks the decision;
    a headline goes stale in hours and only warns, because yesterday's news is still
    auditable while yesterday's price is not.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from verdict_desk import agents_v2, agents_v3, dossier, evidence


class RiskLimitTest(unittest.TestCase):
    def test_default_is_unchanged(self):
        self.assertEqual(agents_v2.V3.max_risk_pct, 1.0)
        self.assertEqual(agents_v3.DEFAULT.max_risk_pct, 1.0)

    def test_user_can_lower_the_limit(self):
        rules = agents_v2.with_risk_pct(agents_v2.V3, 0.25)
        self.assertEqual(rules.max_risk_pct, 0.25)
        self.assertTrue(rules.rich_states, "the chosen rule set must be preserved")

    def test_user_can_raise_it_under_the_cap(self):
        self.assertEqual(agents_v2.with_risk_pct(agents_v2.V3, 5).max_risk_pct, 5.0)

    def test_above_the_system_cap_is_refused_loudly(self):
        with self.assertRaises(ValueError) as ctx:
            agents_v2.with_risk_pct(agents_v2.V3, 30)
        self.assertIn("system cap", str(ctx.exception))

    def test_zero_and_negative_are_refused(self):
        for bad in (0, -1, "abc", None if False else 0.0):
            with self.assertRaises(ValueError):
                agents_v2.with_risk_pct(agents_v2.V3, bad)

    def test_none_keeps_the_default(self):
        self.assertEqual(agents_v2.with_risk_pct(agents_v2.V3, None).max_risk_pct, 1.0)

    def test_verdict_records_both_the_limit_and_the_cap(self):
        f = {"last_close": 100.0, "atr_14": 1.0, "ema_fast": 101.0, "ema_slow": 99.0, "return_24": 2.0,
             "range_high_24": 103.0, "range_low_24": 95.0, "volume_ratio": 1.5, "funding_rate": 0.0,
             "news": {"bullish": 2, "bearish": 0, "total": 2,
                      "bullish_titles": ["a", "b"], "bearish_titles": []}}
        v2 = agents_v2.judge(f, agents_v2.bull(f), agents_v2.bear(f), rules=agents_v2.V2)
        self.assertEqual(v2["system_max_risk_pct"], 25.0)
        wide = agents_v2.with_risk_pct(agents_v2.V3, 10)
        v3 = agents_v2.judge(f, agents_v2.bull(f), agents_v2.bear(f), rules=wide)
        self.assertEqual(v3["risk_pct"], 10.0)
        if "size" in v3 and v3["action"] in ("LONG", "SHORT"):
            self.assertGreater(v3["size"], v2.get("size") or 0)


class ReplayWithCustomRiskTest(unittest.TestCase):
    def test_replay_reapplies_the_frozen_limit_instead_of_the_default(self):
        """A dossier recorded at 5% must replay at 5%, or the trail is a lie."""
        def packet():
            t = 1700000000000
            return {
                "symbol": "BTCUSDT", "collected_at": "2026-10-22T20:00:00+00:00",
                "market": {"candles": [{"t": t + i * 3600_000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
                                       for i in range(40)]},
                "funding": {"rate": 0.0}, "news": {"articles": []},
            }
        wide = agents_v2.with_risk_pct(agents_v2.V3, 5)
        built = dossier.build(packet(), "genesis", 1, use_llm=False, rules_version="v3", rules=wide)
        result = dossier.replay(built, rules_version="v3")
        self.assertTrue(result["matches"], "a dossier recorded with --risk-pct must replay identically")
        self.assertEqual(result["replayed"]["risk_pct"], 5.0)


class FreshnessClassTest(unittest.TestCase):
    def base(self, collected, candle_start, news_age_h=None):
        candles = [{"t": int(candle_start.timestamp() * 1000)}]
        arts = []
        if news_age_h is not None:
            seen = collected - timedelta(hours=news_age_h)
            arts = [{"title": "t", "url": "u", "domain": "d", "seen": seen.strftime("%Y%m%dT%H%M%SZ")}]
        return {"market": {"candles": candles}, "collected_at": collected.isoformat(),
                "news": {"articles": arts}}

    def test_fresh_market_is_ok(self):
        now = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
        f = evidence.freshness(self.base(now, now - timedelta(minutes=20)))
        self.assertTrue(f["ok"])
        self.assertEqual(f["classes"]["market"]["blocking"], True)

    def test_stale_price_blocks(self):
        now = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
        f = evidence.freshness(self.base(now, now - timedelta(minutes=200)))
        self.assertFalse(f["ok"])
        self.assertIn("window is", f["reason"])

    def test_stale_news_only_warns_and_never_blocks(self):
        now = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
        f = evidence.freshness(self.base(now, now - timedelta(minutes=10), news_age_h=48))
        self.assertTrue(f["ok"], "old news must not block a decision made on fresh price")
        self.assertFalse(f["classes"]["news"]["ok"])
        self.assertEqual(f["classes"]["news"]["blocking"], False)
        self.assertTrue(f["warnings"])

    def test_news_from_yesterday_is_within_the_slow_window(self):
        now = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
        f = evidence.freshness(self.base(now, now - timedelta(minutes=10), news_age_h=20))
        self.assertTrue(f["classes"]["news"]["ok"])

    def test_old_shape_dossiers_still_have_the_keys_the_judge_reads(self):
        """Dossies gravados antes do frescor existir continuam reproduzindo: o juiz
        aceita a ausencia do bloco, e quem tem o bloco tem as chaves que ele le."""
        seen_old = seen_new = 0
        for d in (json.loads(l) for l in (Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl").read_text().splitlines() if l.strip()):
            f = d["evidence"].get("freshness")
            if f is None:
                seen_old += 1
                continue
            seen_new += 1
            self.assertIn("ok", f)
            self.assertIn("window_minutes", f)
        self.assertGreater(seen_old, 0, "the ledger is expected to contain pre-freshness dossiers")
        self.assertGreater(seen_new, 0)


if __name__ == "__main__":
    unittest.main()
