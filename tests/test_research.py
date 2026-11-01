import random
import unittest

from verdict_desk import agents, backtest, research


def walk(n: int, seed: int, drift: float = 0.0, start_t: int = 0) -> list[dict]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for i in range(n):
        price *= 1 + drift + rng.gauss(0, 0.01)
        high, low = price * (1 + abs(rng.gauss(0, 0.004))), price * (1 - abs(rng.gauss(0, 0.004)))
        out.append({"t": start_t + i * 3_600_000, "o": price, "h": high, "l": low, "c": price, "v": 100 + rng.random() * 50})
    return out


class SplitTest(unittest.TestCase):
    def test_train_and_test_follow_time_and_test_only_borrows_warmup(self):
        candles = walk(1000, 1)
        train, test = research.split(candles)
        cut = int(1000 * research.TRAIN_FRACTION)
        self.assertEqual(train, candles[:cut])
        self.assertEqual(test[backtest.WINDOW :], candles[cut:])
        self.assertEqual(test[: backtest.WINDOW], candles[cut - backtest.WINDOW : cut])

    def test_grid_has_36_candidates_and_v1_values_are_inside_it(self):
        rules = research.configs()
        self.assertEqual(len(rules), 36)
        self.assertIn((2.0, 1.5, 3.0), {(r.entry_threshold, r.stop_atr, r.take_atr) for r in rules})
        self.assertTrue(all(r.version == "v2-candidate" for r in rules))


class StudyTest(unittest.TestCase):
    def test_choice_is_made_on_train_only(self):
        candles = walk(3000, 7)
        cut = int(len(candles) * research.TRAIN_FRACTION)
        altered = candles[:cut] + walk(len(candles) - cut, 99, drift=-0.003, start_t=candles[cut]["t"])
        first = research.study("TESTUSDT", candles)["v2"]["chosen"]
        second = research.study("TESTUSDT", altered)["v2"]["chosen"]
        self.assertEqual(first, second)

    def test_study_reports_protocol_and_both_rule_sets_on_the_same_test_segment(self):
        result = research.study("TESTUSDT", walk(3000, 3))
        self.assertEqual(result["protocol"]["configs_tried"], 36)
        self.assertEqual(result["data"]["candles"], 3000)
        self.assertIn("test", result["v2"])
        self.assertIn("net_pnl", result["v1_test"])
        self.assertTrue(result["label"].startswith("RESEARCH"))

    def test_v1_default_rules_are_unchanged(self):
        self.assertEqual((agents.V1.version, agents.V1.entry_threshold, agents.V1.stop_atr, agents.V1.take_atr), ("v1", 2.0, 1.5, 3.0))


class VerdictTest(unittest.TestCase):
    def study(self, beats: bool, positive: bool) -> dict:
        return {"symbol": "X", "v2": {"test_beats_v1": beats, "test_net_positive": positive}}

    def test_adoption_needs_both_conditions_on_most_symbols(self):
        self.assertFalse(research.verdict([self.study(True, False), self.study(True, False), self.study(False, False)])["adopt_v2_live"])
        self.assertFalse(research.verdict([self.study(False, True), self.study(False, True), self.study(True, True)])["adopt_v2_live"])
        self.assertTrue(research.verdict([self.study(True, True), self.study(True, True), self.study(False, False)])["adopt_v2_live"])

    def test_no_usable_study_never_adopts(self):
        self.assertFalse(research.verdict([{"symbol": "X", "v2": None}])["adopt_v2_live"])


if __name__ == "__main__":
    unittest.main()
