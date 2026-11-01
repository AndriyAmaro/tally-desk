"""Tests of verdict_desk/learning.py, which entered the repository without any.

Fixtures are small hand-made dossiers: a LONG entered at 100 (stop 98, target 105, size 1) and a later FLAT dossier of
the same symbol whose candle settles it as TAKE (+5), STOP (-2) or still OPEN (marked at 101, +1 unrealized).
Two tests are characterization tests: they pin what the code does today, even where that is questionable, so a
change of behavior is a decision and not an accident. Their docstrings say so.
"""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import __main__ as cli
from verdict_desk import agents_v2, features_v2, learning

OUTCOME_CANDLE = {
    "TAKE": {"o": 100, "h": 106, "l": 100, "c": 105},
    "STOP": {"o": 100, "h": 100, "l": 97, "c": 98},
    "OPEN": {"o": 100, "h": 101, "l": 100, "c": 101},
}


def position(seq, symbol, bull=(), bear=(), feats=None):
    return {
        "seq": seq,
        "evidence": {"symbol": symbol, "market": {"candles": [{"t": 10, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1}]}},
        "verdict": {"action": "LONG", "edge": 2.0, "entry": 100.0, "stop": 98.0, "take_profit": 105.0, "size": 1.0},
        "bull": [{"claim": c, "weight": w} for c, w in bull],
        "bear": [{"claim": c, "weight": w} for c, w in bear],
        "features": feats or {},
    }


def follow_up(seq, symbol, outcome):
    return {
        "seq": seq,
        "evidence": {"symbol": symbol, "market": {"candles": [{"t": 11, "v": 1, **OUTCOME_CANDLE[outcome]}]}},
        "verdict": {"action": "FLAT", "edge": 0.0},
        "bull": [],
        "bear": [],
        "features": {},
    }


def ledger_of(*trades):
    """Each trade is (outcome, bull claims, bear claims, features); every trade becomes a position and its settling dossier."""
    out = []
    for n, (outcome, bull, bear, feats) in enumerate(trades, 1):
        symbol = f"S{n}"  # one symbol per trade, so the trades cannot settle each other
        out.append(position(len(out) + 1, symbol, bull, bear, feats))
        out.append(follow_up(len(out) + 1, symbol, outcome))
    return out


def write_ledger(path: Path, dossiers) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(d) + "\n" for d in dossiers), encoding="utf-8")
    return path


WEIGHTS_LEDGER = ledger_of(
    ("TAKE", [("WIN", 1.0), ("MIXED", 1.5)], [], None),
    ("TAKE", [("WIN", 1.0)], [("BEARISH", 0.5)], None),
    ("STOP", [("LOSE", 2.0), ("MIXED", 1.5)], [], None),
    ("STOP", [("LOSE", 2.0)], [("BEARISH", 0.5)], None),
    ("TAKE", [("ONCE", 1.0)], [], None),
)


class LoadLearnedWeightsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def learn(self, dossiers):
        return learning.load_learned_weights(write_ledger(self.dir / "ledger.jsonl", dossiers))

    def test_a_missing_ledger_teaches_nothing(self):
        self.assertEqual(learning.load_learned_weights(self.dir / "nope.jsonl"), {})

    def test_fewer_than_ten_dossiers_teach_nothing(self):
        self.assertEqual(self.learn(WEIGHTS_LEDGER[:9]), {})
        self.assertEqual(self.learn(WEIGHTS_LEDGER[:10])["dossiers_analyzed"], 10)

    def test_counts_dossiers_and_only_closed_positions(self):
        learned = self.learn(WEIGHTS_LEDGER)
        self.assertEqual(learned["dossiers_analyzed"], 10)
        self.assertEqual(learned["positions_analyzed"], 5)  # 3 TAKE + 2 STOP, the settling FLAT dossiers are not positions
        self.assertRegex(learned["generated_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_the_weight_follows_the_win_rate_within_twenty_percent(self):
        weights = self.learn(WEIGHTS_LEDGER)["adaptive_weights"]
        self.assertEqual(weights["WIN"], {"original_weight": 1.0, "adaptive_weight": 1.2, "win_rate": 1.0, "avg_pnl": 5.0, "sample_size": 2})
        self.assertEqual(weights["LOSE"], {"original_weight": 2.0, "adaptive_weight": 1.6, "win_rate": 0.0, "avg_pnl": -2.0, "sample_size": 2})
        self.assertEqual(weights["MIXED"], {"original_weight": 1.5, "adaptive_weight": 1.5, "win_rate": 0.5, "avg_pnl": 1.5, "sample_size": 2})

    def test_an_argument_seen_once_gets_no_adaptive_weight(self):
        self.assertNotIn("ONCE", self.learn(WEIGHTS_LEDGER)["adaptive_weights"])

    def test_bear_arguments_are_scored_by_the_same_outcome_as_bull_ones(self):
        """Characterization: an argument is credited with the P&L of every dossier it appears in, on either side.

        BEARISH sits in one winning LONG and one losing LONG, so it ends at 50%. It also means a bear argument that
        spoke against a winning LONG counts as a win; the code does not look at which side an argument took.
        """
        bearish = self.learn(WEIGHTS_LEDGER)["adaptive_weights"]["BEARISH"]
        self.assertEqual((bearish["win_rate"], bearish["adaptive_weight"], bearish["sample_size"]), (0.5, 0.5, 2))

    def test_open_positions_feed_the_argument_stats_with_their_unrealized_pnl(self):
        """Characterization: OPEN positions are marked at the last candle and counted as wins or losses in the argument
        stats, although `positions_analyzed` counts closed positions only."""
        dossiers = ledger_of(
            ("TAKE", [("A", 1.0)], [], None),
            ("STOP", [("A", 1.0)], [], None),
            ("TAKE", [("B", 1.0)], [], None),
            ("OPEN", [("FLOATING", 1.0)], [], None),
            ("OPEN", [("FLOATING", 1.0)], [], None),
        )
        learned = self.learn(dossiers)
        self.assertEqual(learned["positions_analyzed"], 3)
        floating = learned["adaptive_weights"]["FLOATING"]
        self.assertEqual((floating["win_rate"], floating["avg_pnl"], floating["sample_size"]), (1.0, 1.0, 2))

    def feature_ledger(self, values, outcomes):
        return ledger_of(*[(outcome, [], [], {"rsi_14": value}) for value, outcome in zip(values, outcomes)])

    def test_feature_insights_compare_the_pnl_above_and_below_the_average(self):
        ledger = self.feature_ledger([10, 20, 30, 40, 50, 60], ["STOP", "STOP", "STOP", "TAKE", "TAKE", "TAKE"])
        insight = self.learn(ledger)["feature_insights"]["rsi_14"]
        self.assertEqual(insight, {"avg_value": 35.0, "pnl_when_high": 5.0, "pnl_when_low": -2.0, "edge_signal": "bullish"})

    def test_a_feature_that_pays_when_low_reads_as_bearish(self):
        ledger = self.feature_ledger([10, 20, 30, 40, 50, 60], ["TAKE", "TAKE", "TAKE", "STOP", "STOP", "STOP"])
        self.assertEqual(self.learn(ledger)["feature_insights"]["rsi_14"]["edge_signal"], "bearish")

    def test_a_feature_needs_five_positions_to_count(self):
        ledger = self.feature_ledger([10, 20, 30, 40], ["STOP", "STOP", "TAKE", "TAKE"]) + [follow_up(100 + n, f"X{n}", "TAKE") for n in range(1, 4)]
        learned = self.learn(ledger)
        self.assertGreaterEqual(learned["dossiers_analyzed"], 10)
        self.assertNotIn("rsi_14", learned["feature_insights"])


class ReportTest(unittest.TestCase):
    def report(self, dossiers):
        path = write_ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl", dossiers)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            learning.print_learning_report(path)
        return out.getvalue()

    def test_the_report_shows_each_weight_with_its_direction(self):
        text = self.report(WEIGHTS_LEDGER)
        for expected in ("ADAPTIVE WEIGHTS", "Dossiers analyzed: 10", "Positions analyzed: 5", "weight: 1.0 → 1.2 (↑)", "weight: 2.0 → 1.6 (↓)", "weight: 1.5 → 1.5 (=)", "win: 100%", "n=2"):
            self.assertIn(expected, text)

    def test_too_little_data_says_so_instead_of_printing_an_empty_report(self):
        self.assertIn("Not enough data for learning", self.report(WEIGHTS_LEDGER[:4]))


class ApplyTest(unittest.TestCase):
    """`--apply` reads ledger/ledger.jsonl relative to the working directory and writes LEARNING_FILE."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.target = self.work / "data" / "learned_weights.json"
        self.previous = os.getcwd()
        os.chdir(self.work)
        patch = mock.patch.object(learning, "LEARNING_FILE", self.target)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(os.chdir, self.previous)

    def apply(self, dossiers):
        write_ledger(self.work / "ledger" / "ledger.jsonl", dossiers)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = learning.apply_learned_weights()
        return result, out.getvalue()

    def test_apply_writes_the_learned_weights_file(self):
        result, printed = self.apply(WEIGHTS_LEDGER)
        saved = json.loads(self.target.read_text())
        self.assertEqual(saved, json.loads(json.dumps(result)))
        self.assertEqual(saved["adaptive_weights"]["WIN"]["adaptive_weight"], 1.2)
        self.assertIn("Learned weights saved to", printed)
        self.assertIn(f"{len(saved['adaptive_weights'])} arguments analyzed", printed)

    def test_apply_without_enough_data_writes_nothing(self):
        result, printed = self.apply(WEIGHTS_LEDGER[:4])
        self.assertIsNone(result)
        self.assertFalse(self.target.exists())
        self.assertIn("No adaptive weights to apply", printed)

    def test_applied_weights_do_not_change_decisions_yet(self):
        """Characterization: nothing reads the learned weights file, so `--apply` cannot move a verdict.

        That also keeps `replay` deterministic. When agents_v2 starts to load these weights, this test fails on
        purpose: replay of old dossiers must then be decided explicitly (for example by freezing the weights used).
        """
        feats = features_v2.compute(packet(0.004, funding=-0.0001))
        before = agents_v2.decide(feats)
        self.apply(WEIGHTS_LEDGER)
        self.assertTrue(self.target.exists())
        self.assertEqual(agents_v2.decide(feats), before)


class LearnCommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ledger = write_ledger(self.dir / "ledger.jsonl", WEIGHTS_LEDGER)
        self.target = self.dir / "data" / "learned_weights.json"
        patch = mock.patch.object(learning, "LEARNING_FILE", self.target)
        patch.start()
        self.addCleanup(patch.stop)

    def run_learn(self, apply=False, reset=False):
        opts = argparse.Namespace(apply=apply, reset=reset, ledger=self.ledger)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.cmd_learn(opts)
        return code, out.getvalue()

    def test_plain_learn_prints_the_report_and_writes_nothing(self):
        code, printed = self.run_learn()
        self.assertEqual(code, 0)
        self.assertIn("ADAPTIVE WEIGHTS", printed)
        self.assertFalse(self.target.exists())

    def test_apply_saves_the_weights_computed_from_ledger_ledger_jsonl(self):
        write_ledger(self.dir / "ledger" / "ledger.jsonl", WEIGHTS_LEDGER)
        previous = os.getcwd()
        os.chdir(self.dir)
        try:
            code, printed = self.run_learn(apply=True)
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        self.assertTrue(self.target.exists())
        self.assertIn("Learned 4 weight adjustments", printed)  # WIN, LOSE, MIXED and BEARISH

    def test_reset_removes_the_saved_weights_and_is_quiet_when_there_are_none(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text("{}")
        code, printed = self.run_learn(reset=True)
        self.assertEqual(code, 0)
        self.assertFalse(self.target.exists())
        self.assertIn("Removed", printed)
        self.assertEqual(self.run_learn(reset=True), (0, ""))


if __name__ == "__main__":
    unittest.main()
