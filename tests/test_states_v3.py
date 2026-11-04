"""Rules v3: the same sizing as v2, but a declined entry says why.

The contract this test suite protects is narrow and important:
  * v3 never opens a position that v2 would not have opened, and never refuses one it
    would have taken. Only the label of a non-entry decision changes.
  * v2 decisions stay byte-identical, so the dossiers already in the public ledger keep
    replaying exactly as they were recorded.
"""

import unittest
from pathlib import Path
import json

from verdict_desk import agents_v2, agents_v3, dossier, portfolio


def arg(claim: str, weight: float) -> dict:
    return {"claim": claim, "evidence_ref": "market.candles[-1].c", "value": 100.0, "weight": weight}


def features(last_close: float = 100.0, atr: float = 1.0) -> dict:
    return {"last_close": last_close, "atr_14": atr}


class StatesTest(unittest.TestCase):
    def judge(self, bull, bear, rules, **kw):
        return agents_v2.judge(features(**kw), [arg(f"bull{i}", w) for i, w in enumerate(bull)],
                               [arg(f"bear{i}", w) for i, w in enumerate(bear)], rules=rules)

    def test_v2_calls_a_borderline_market_flat(self):
        v = self.judge([2.0], [1.6], agents_v2.V2)
        self.assertEqual(v["action"], "FLAT")
        self.assertNotIn("entry", v)

    def test_v3_names_the_argument_that_would_flip_it(self):
        v = self.judge([2.0], [1.6], agents_v2.V3)
        self.assertEqual(v["action"], "WATCH_REVERSAL")
        self.assertIn("would flip the verdict", v["rationale"])
        self.assertNotIn("entry", v, "a declined entry must not size a position")

    def test_v3_flags_a_strong_two_sided_market_with_high_volatility(self):
        v = self.judge([1.5], [1.5], agents_v2.V3, atr=2.0)
        self.assertEqual(v["action"], "REDUCE_EXPOSURE")
        self.assertIn("volatility is high", v["rationale"])

    def test_v3_still_says_flat_when_nothing_is_close(self):
        v = self.judge([0.5], [0.5], agents_v2.V3, atr=2.0)
        self.assertEqual(v["action"], "FLAT")

    def test_entry_decisions_are_identical_between_v2_and_v3(self):
        for bull, bear in (([3.0, 1.0], [0.5]), ([0.5], [3.0, 1.0])):
            a = self.judge(bull, bear, agents_v2.V2)
            b = self.judge(bull, bear, agents_v2.V3)
            self.assertEqual(a["action"], b["action"])
            for key in ("edge", "bull_score", "bear_score", "entry", "stop", "take_profit", "size"):
                self.assertEqual(a.get(key), b.get(key), key)

    def test_v3_marks_its_version(self):
        self.assertEqual(self.judge([3.0], [0.0], agents_v2.V3)["rules_version"], "v3")

    def test_every_verdict_freezes_the_exact_rules(self):
        v = self.judge([3.0], [0.0], agents_v2.V3)
        pol = v["policy"]
        self.assertEqual(pol["entry_threshold"], 1.5)
        self.assertEqual(pol["stop_atr"], 1.5)
        self.assertEqual(pol["take_atr"], 3.0)
        self.assertEqual(pol["system_max_risk_pct"], 25.0)
        self.assertEqual(pol["market_window_minutes"], 75)

    def test_the_frozen_rules_reflect_a_user_risk_limit(self):
        wide = agents_v2.with_risk_pct(agents_v2.V3, 5)
        v = self.judge([3.0], [0.0], wide)
        self.assertEqual(v["policy"]["max_risk_pct"], 5.0)
        self.assertEqual(v["risk_pct"], 5.0)

    def test_agent_module_defaults_to_v3(self):
        f = {
            "last_close": 100.0, "atr_14": 1.0, "ema_fast": 101.0, "ema_slow": 99.0,
            "return_24": 1.0, "range_high_24": 102.0, "range_low_24": 98.0, "volume_ratio": 1.0,
            "funding_rate": 0.0001, "news": {"bullish": 0, "bearish": 0, "total": 0},
            "rsi_14": 50.0, "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
            "bb_upper": 103.0, "bb_middle": 100.0, "bb_lower": 97.0, "bb_pct_b": 0.5,
        }
        self.assertEqual(agents_v3.decide(f)["verdict"]["rules_version"], "v3")


class PortfolioTest(unittest.TestCase):
    def test_non_position_states_are_not_treated_as_trades(self):
        ledger = [
            {"seq": 1, "evidence": {"symbol": "BTCUSDT", "market": {"candles": []}},
             "verdict": {"action": "WATCH_REVERSAL", "edge": 0.2}},
            {"seq": 2, "evidence": {"symbol": "BTCUSDT", "market": {"candles": []}},
             "verdict": {"action": "REDUCE_EXPOSURE", "edge": 0.0}},
            {"seq": 3, "evidence": {"symbol": "BTCUSDT", "market": {"candles": []}},
             "verdict": {"action": "FLAT", "edge": 0.0}},
        ]
        summary = portfolio.summarize(ledger)
        self.assertEqual(summary["positions"], [])
        self.assertEqual(summary["flat"], 3)


class ReplayTest(unittest.TestCase):
    def test_every_published_dossier_still_replays(self):
        path = Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for d in rows:
            rv = d.get("verdict", {}).get("rules_version", "v1")
            result = dossier.replay(d, rules_version=rv)
            self.assertTrue(result["matches"], f"seq {d['seq']} (rules {rv}) stopped replaying")


if __name__ == "__main__":
    unittest.main()
