"""Per-voice track record, and rules v5 that weight a convergence by it.

"Trusted voices converge" only means something if trust is measured, measured fairly and measured
without peeking at the future. These tests pin: a call settles only when its exit candle exists;
a voice that merely says "up" in a rising market earns no credit; a small sample stays neutral; the
number frozen in a dossier is exactly what the ledger BEFORE it supports; and v5 reduces to v4
whenever nobody has a record.
"""

import copy
import random
import unittest
from datetime import datetime, timedelta, timezone

from verdict_desk import agents_v2, agents_v5, dossier, trust

HOUR = 3_600_000
T0 = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
T0_MS = int(T0.timestamp() * 1000)


def random_walk(hours: int) -> list[float]:
    """A seeded walk: windows go both ways and none ends exactly flat."""
    rng = random.Random(7)
    price, out = 100.0, []
    for _ in range(hours):
        out.append(round(price, 6))
        price += rng.uniform(-1.0, 1.0)
    return out


PATH = random_walk(200)


def make(seq: int, hour: int, extractions=(), symbol="BTCUSDT", trust_table=None) -> dict:
    first = max(0, hour - 5)
    narrative = {"extractions": list(extractions)}
    if trust_table is not None:
        narrative["trust"] = copy.deepcopy(trust_table)
    return {
        "seq": seq,
        "evidence": {
            "symbol": symbol,
            "collected_at": (T0 + timedelta(hours=hour)).isoformat(),
            "market": {"candles": [{"t": T0_MS + h * HOUR, "o": PATH[h], "h": PATH[h], "l": PATH[h], "c": PATH[h], "v": 1} for h in range(first, hour + 1)]},
            "narrative": narrative,
        },
    }


def call(name, stance, quote, token="BTC"):
    return {"voice": "V1", "name": name, "tokens": [token], "stance": stance, "conviction": "medium", "urgency": "low", "quote": quote}


def up_after(hour: int) -> bool:
    return PATH[hour + 4] > PATH[hour]


def world(calls_by_hour: dict[int, list[dict]], last_hour: int = 120) -> list[dict]:
    return [make(h + 1, h, calls_by_hour.get(h, [])) for h in range(last_hour + 1)]


class SettlementTest(unittest.TestCase):
    def test_a_call_settles_four_hours_later_and_not_before(self):
        extraction = [call("Oracle", "bullish", "btc will rise")]
        early = [make(1, 2, extraction), make(2, 3), make(3, 4), make(4, 5)]
        self.assertEqual(len(trust.settled(early)[0]), 0, "the exit candle (hour 6) is not in the data yet")
        self.assertEqual(trust.settled(early)[1], 1)
        later = early + [make(5, 6)]
        done, pending = trust.settled(later)
        self.assertEqual((len(done), pending), (1, 0))
        self.assertAlmostEqual(done[0]["ret"], PATH[6] / PATH[2] - 1.0)

    def test_the_same_headline_seen_again_is_one_call(self):
        extraction = [call("Oracle", "bullish", "BTC   will rise")]
        ledger = [make(i + 1, i, extraction if i < 3 else [call("Oracle", "bullish", "btc will rise")]) for i in range(12)]
        self.assertEqual(len(trust.calls(ledger)), 1)

    def test_neutral_calls_and_untracked_tokens_are_not_calls(self):
        ledger = [make(1, 0, [call("Oracle", "neutral", "x"), call("Oracle", "bullish", "bnb up", token="BNB")])]
        self.assertEqual(trust.calls(ledger), [])


class SkillTest(unittest.TestCase):
    def build(self, stance_for):
        """One voice, a call every 2 hours, direction chosen by `stance_for(hour)`."""
        by_hour = {h: [call("Voice", stance_for(h), f"call at hour {h}")] for h in range(0, 100, 2)}
        return trust.compute(world(by_hour))["voices"]["Voice"]

    def test_a_voice_that_foresees_the_direction_earns_weight_above_one(self):
        v = self.build(lambda h: "bullish" if up_after(h) else "bearish")
        self.assertEqual(v["hit_rate"], 1.0)
        self.assertGreater(v["weight"], 1.2)

    def test_a_voice_that_is_always_wrong_is_weighted_down_but_not_to_zero(self):
        v = self.build(lambda h: "bearish" if up_after(h) else "bullish")
        self.assertEqual(v["hit_rate"], 0.0)
        self.assertLess(v["weight"], 0.9)
        self.assertGreaterEqual(v["weight"], trust.WEIGHT_MIN)

    def test_a_voice_that_only_says_up_earns_nothing_even_in_a_rising_window(self):
        v = self.build(lambda h: "bullish")
        self.assertEqual(v["bull_share"], 1.0)
        self.assertEqual(v["skill"], 0.0, "always-up has exactly the chance-level hit rate, so no credit")
        self.assertEqual(v["weight"], 1.0)

    def test_a_small_sample_stays_neutral(self):
        by_hour = {h: [call("Rookie", "bullish" if up_after(h) else "bearish", f"c{h}")] for h in range(0, 8, 2)}
        v = trust.compute(world(by_hour))["voices"]["Rookie"]
        self.assertLess(v["n"], trust.MIN_CALLS)
        self.assertEqual(v["weight"], 1.0)
        self.assertFalse(v["proven"])


class FrozenTableTest(unittest.TestCase):
    def ledger_with_frozen_table(self):
        by_hour = {h: [call("Voice", "bullish" if up_after(h) else "bearish", f"call at hour {h}")] for h in range(0, 80, 2)}
        base = world(by_hour, last_hour=100)
        cut = 90  # the recorded cut: everything the run could know before it started
        table = trust.record_for(base[:cut], {"Voice", "Newcomer"})
        run = [make(cut + 1 + i, 95, trust_table=table, symbol=sym) for i, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT"))]
        return base[:cut] + run, table

    def test_the_three_dossiers_of_one_run_share_one_table_and_it_checks_out(self):
        ledger, table = self.ledger_with_frozen_table()
        self.assertEqual(table["voices"]["Newcomer"], {"n": 0, "hits": 0, "weight": 1.0})
        self.assertEqual(table["as_of_seq"], 90)
        self.assertEqual(trust.check(ledger), [])

    def test_a_table_edited_after_the_fact_is_caught(self):
        ledger, _ = self.ledger_with_frozen_table()
        table = ledger[-1]["evidence"]["narrative"]["trust"]["voices"]["Voice"]
        self.assertEqual(table["weight"], trust.WEIGHT_MAX, "this voice is perfect, so it sits at the ceiling")
        table["weight"] = 0.5
        self.assertEqual(len(trust.check(ledger)), 1)

    def test_a_table_built_from_the_future_is_caught(self):
        ledger, _ = self.ledger_with_frozen_table()
        peeking = trust.record_for(ledger, {"Voice"})  # includes the dossiers of its own run
        peeking["as_of_seq"] = ledger[-1]["seq"]
        ledger[-1]["evidence"]["narrative"]["trust"] = peeking
        self.assertTrue(any("not before it" in p for p in trust.check(ledger)))

    def test_a_past_price_rewritten_everywhere_it_appears_is_caught(self):
        """A single edited dossier is the hash chain's job; this is the consistent rewrite of one candle.

        A candle appears in several dossiers and the newest copy wins, so rewriting one hour in every
        dossier that carries it is the only edit that survives the lookup. The tables computed from
        that history must stop matching.
        """
        ledger, _ = self.ledger_with_frozen_table()
        hour = T0_MS + 20 * HOUR
        for d in ledger:
            for candle in d["evidence"]["market"]["candles"]:
                if candle["t"] == hour:
                    candle["o"] = candle["o"] * 1.5
        self.assertTrue(trust.check(ledger), "changing the history a table was computed from must break the check")

    def test_the_score_of_a_prefix_does_not_depend_on_what_came_after(self):
        ledger, _ = self.ledger_with_frozen_table()
        prefix = copy.deepcopy(ledger[:60])
        wrecked = copy.deepcopy(ledger)
        for d in wrecked[60:]:
            for candle in d["evidence"]["market"]["candles"]:
                candle["o"] = 1.0
        self.assertEqual(trust.compute(prefix), trust.compute(wrecked[:60]))


class RulesV5Test(unittest.TestCase):
    def conv(self, voices):
        return {"token": "BTC", "bias": "bullish", "voices": voices, "max_conviction": "low", "max_urgency": "low"}

    def table(self, **weights):
        return {"voices": {name: {"n": 20, "hits": 12, "weight": w} for name, w in weights.items()}}

    def weight(self, narrative, trust_weighted):
        bull, _ = agents_v2.convergence_args(narrative, "BTCUSDT", trust_weighted)
        return bull[0]["weight"]

    def test_without_a_track_record_v5_equals_v4(self):
        for voices in (["A", "B"], ["A", "B", "C", "D", "E"]):
            plain = {"convergence": [self.conv(voices)]}
            self.assertEqual(self.weight(plain, True), self.weight(plain, False))

    def test_a_trusted_pair_outweighs_a_distrusted_pair(self):
        nar = {"convergence": [self.conv(["A", "B"])], "trust": self.table(A=1.5, B=1.5)}
        bad = {"convergence": [self.conv(["A", "B"])], "trust": self.table(A=0.5, B=0.5)}
        self.assertEqual(self.weight(nar, True), 1.5)
        self.assertEqual(self.weight(bad, True), 0.5)
        self.assertEqual(self.weight(nar, False), 1.0, "v4 ignores the table")

    def test_an_unknown_voice_counts_as_neutral_and_only_the_best_four_count(self):
        nar = {"convergence": [self.conv(["A", "B", "C", "D", "E", "Z"])], "trust": self.table(A=1.5, B=1.5, C=1.5, D=1.5, E=0.5)}
        self.assertEqual(self.weight(nar, True), 3.0)

    def test_the_claim_states_the_weights_it_used(self):
        nar = {"convergence": [self.conv(["A", "B"])], "trust": self.table(A=1.25, B=0.75)}
        bull, _ = agents_v2.convergence_args(nar, "BTCUSDT", True)
        self.assertIn("A 1.25", bull[0]["claim"])
        self.assertIn("B 0.75", bull[0]["claim"])

    def test_a_v5_dossier_replays_and_says_it_is_v5(self):
        packet = {"symbol": "BTCUSDT", "collected_at": "2026-10-23T00:00:00+00:00",
                  "market": {"candles": [{"t": 1700000000000 + i * HOUR, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10} for i in range(40)]},
                  "funding": {"rate": 0.0}, "news": {"articles": []},
                  "narrative": {"convergence": [self.conv(["A", "B"])], "trust": self.table(A=1.4, B=1.2)}}
        d = dossier.build(packet, "genesis", 1, use_llm=False, rules_version="v5")
        self.assertEqual(d["verdict"]["rules_version"], "v5")
        self.assertTrue(dossier.replay(d, rules_version="v5")["matches"])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_v5_is_the_only_version_that_reads_the_table(self):
        self.assertTrue(agents_v5.V5.trust_weighted)
        self.assertFalse(agents_v2.V4.trust_weighted)


class PublishedLedgerTest(unittest.TestCase):
    def test_every_frozen_table_in_the_published_ledger_is_supported_by_the_ledger_before_it(self):
        from pathlib import Path
        ledger = dossier.read_ledger(Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl")
        self.assertEqual(trust.check(ledger), [])


if __name__ == "__main__":
    unittest.main()
