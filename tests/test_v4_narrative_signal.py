"""Rules v4: the multi-KOL convergence becomes a deterministic signal (the spotlight close).

The featured direction asks the agent to "create a practice position using transparent signal
rules". Before v4 a convergence was only *citable* by the LLM; now it is a deterministic
argument whose weight is a stated formula, so the narrative creates the position by a rule the
replay reproduces — and v1/v2/v3 dossiers stay byte-identical because the signal is gated on
`rules.narrative_signal`.
"""

import json
import unittest
from pathlib import Path

from verdict_desk import agents_v2, agents_v3, agents_v4, dossier


def conv(token, bias, voices, conviction="low", urgency="medium"):
    return {"token": token, "bias": bias, "voices": voices, "max_conviction": conviction, "max_urgency": urgency}


class ConvergenceWeightTest(unittest.TestCase):
    def test_two_voices_low_conviction_weight_one(self):
        bull, _ = agents_v2.convergence_args({"convergence": [conv("BTC", "bullish", ["A", "B"])]}, "BTCUSDT")
        self.assertEqual(bull[0]["weight"], 1.0)

    def test_three_voices_high_conviction_weight_two(self):
        bull, _ = agents_v2.convergence_args({"convergence": [conv("BTC", "bullish", ["A", "B", "C"], "high")]}, "BTCUSDT")
        self.assertEqual(bull[0]["weight"], 2.0)

    def test_symbol_suffix_is_stripped(self):
        for symbol in ("BTCUSDT", "BTCUSD", "BTCUSDC", "BTCBUSD"):
            bull, _ = agents_v2.convergence_args({"convergence": [conv("BTC", "bullish", ["A", "B"])]}, symbol)
            self.assertEqual(len(bull), 1, symbol)

    def test_non_matching_token_is_ignored(self):
        bull, bear = agents_v2.convergence_args({"convergence": [conv("ETH", "bearish", ["A", "B"])]}, "BTCUSDT")
        self.assertEqual(bull, [])
        self.assertEqual(bear, [])

    def test_bearish_goes_to_the_bear_side(self):
        _, bear = agents_v2.convergence_args({"convergence": [conv("BTC", "bearish", ["A", "B"])]}, "BTCUSDT")
        self.assertEqual(len(bear), 1)
        self.assertEqual(bear[0]["weight"], 1.0)


class DecideGatingTest(unittest.TestCase):
    def features(self):
        return {"last_close": 100.0, "atr_14": 1.0, "ema_fast": 99.0, "ema_slow": 101.0, "return_24": -1.0,
                "range_high_24": 102.0, "range_low_24": 98.0, "volume_ratio": 1.0, "funding_rate": 0.0,
                "news": {"bullish": 0, "bearish": 0, "total": 0, "bullish_titles": [], "bearish_titles": []},
                "rsi_14": 50.0, "macd_histogram": 0.0, "bb_pct_b": 0.5}

    def test_v4_adds_the_convergence_argument_and_v3_does_not(self):
        nar = {"convergence": [conv("BTC", "bullish", ["A", "B", "C"], "high")]}
        d4 = agents_v4.decide(self.features(), narrative=nar, symbol="BTCUSDT")
        d3 = agents_v3.decide(self.features(), narrative=nar, symbol="BTCUSDT")
        self.assertEqual(d4["verdict"]["rules_version"], "v4")
        self.assertEqual(d3["verdict"]["rules_version"], "v3")
        self.assertEqual(sum(a["weight"] for a in d4["bull"]),
                         sum(a["weight"] for a in d3["bull"]) + 2.0)
        self.assertTrue(any(a["evidence_ref"].startswith("narrative.convergence") for a in d4["bull"]))
        self.assertFalse(any(a["evidence_ref"].startswith("narrative.convergence") for a in d3["bull"]))

    def test_the_convergence_signal_appears_in_the_sensitivity_table(self):
        nar = {"convergence": [conv("BTC", "bullish", ["A", "B", "C"], "high")]}
        d4 = agents_v4.decide(self.features(), narrative=nar, symbol="BTCUSDT")
        drops = d4["sensitivity"]["drop_one"]
        self.assertTrue(any("voices converge" in x["claim"] for x in drops),
                        "the deciding argument must be attributable, including the narrative one")


class ReplayIntegrationTest(unittest.TestCase):
    def test_a_v4_dossier_replays(self):
        packet = {"symbol": "BTCUSDT", "collected_at": "2026-10-23T00:00:00+00:00",
                  "market": {"candles": [{"t": 1700000000000 + i * 3600000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10} for i in range(40)]},
                  "funding": {"rate": 0.0}, "news": {"articles": []},
                  "narrative": {"convergence": [conv("BTC", "bullish", ["A", "B", "C"], "high")]}}
        d = dossier.build(packet, "genesis", 1, use_llm=False, rules_version="v4")
        self.assertTrue(dossier.replay(d, rules_version="v4")["matches"])

    def test_the_published_ledger_still_replays_under_its_own_versions(self):
        path = Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for d in rows:
            rv = d.get("verdict", {}).get("rules_version", "v1")
            self.assertTrue(dossier.replay(d, rules_version=rv)["matches"], f"seq {d['seq']} ({rv}) no longer replays")


if __name__ == "__main__":
    unittest.main()
