"""The scorecard: the only page that asks whether the decisions were any good.

Guard rails worth testing, because a scorecard is easy to make flattering:
  * a horizon that has not elapsed is excluded, never estimated from a partial window;
  * a SHORT scores the negative of the price move, so a falling market cannot look like a win
    for both sides;
  * the caveats travel with the numbers (overlap, fees, small n), so the page cannot be
    screenshotted without them.
"""

import unittest

from verdict_desk import scorecard

HOUR_MS = 3_600_000


def candle(t_ms, close):
    return {"t": t_ms, "open": close, "high": close, "low": close, "close": close, "volume": 1.0}


def row(seq, action, candles, symbol="BTCUSDT"):
    return {"seq": seq, "verdict": {"action": action}, "evidence": {"symbol": symbol,
            "collected_at": "2026-10-22T00:00:00+00:00", "market": {"candles": candles}}}


class ComputeTest(unittest.TestCase):
    def test_a_long_that_rose_scores_positive(self):
        t0 = 1_700_000_000_000
        ledger = [row(1, "LONG", [candle(t0, 100.0)]), row(2, "FLAT", [candle(t0 + HOUR_MS, 110.0)])]
        result = scorecard.compute(ledger, horizons=(1,))
        long_row = next(r for r in result["table"] if r["state"] == "LONG")
        self.assertEqual(long_row["n"], 1)
        self.assertAlmostEqual(long_row["mean"], 10.0, places=3)
        self.assertEqual(long_row["hit_rate"], 100.0)

    def test_a_short_scores_the_negative_of_the_move(self):
        t0 = 1_700_000_000_000
        ledger = [row(1, "SHORT", [candle(t0, 100.0)]), row(2, "FLAT", [candle(t0 + HOUR_MS, 90.0)])]
        result = scorecard.compute(ledger, horizons=(1,))
        short_row = next(r for r in result["table"] if r["state"] == "SHORT")
        self.assertAlmostEqual(short_row["mean"], 10.0, places=3)

    def test_a_short_into_a_rising_market_scores_negative(self):
        t0 = 1_700_000_000_000
        ledger = [row(1, "SHORT", [candle(t0, 100.0)]), row(2, "FLAT", [candle(t0 + HOUR_MS, 105.0)])]
        result = scorecard.compute(ledger, horizons=(1,))
        short_row = next(r for r in result["table"] if r["state"] == "SHORT")
        self.assertAlmostEqual(short_row["mean"], -5.0, places=3)
        self.assertEqual(short_row["hit_rate"], 0.0)

    def test_a_horizon_that_has_not_elapsed_is_excluded(self):
        t0 = 1_700_000_000_000
        ledger = [row(1, "LONG", [candle(t0, 100.0)]), row(2, "FLAT", [candle(t0 + HOUR_MS, 101.0)])]
        result = scorecard.compute(ledger, horizons=(24,))
        long_row = next(r for r in result["table"] if r["state"] == "LONG")
        self.assertEqual(long_row["n"], 0, "a 24h horizon must not be scored from 1h of data")

    def test_flat_decisions_are_counted_but_not_scored(self):
        t0 = 1_700_000_000_000
        ledger = [row(1, "FLAT", [candle(t0, 100.0)]), row(2, "FLAT", [candle(t0 + HOUR_MS, 120.0)])]
        result = scorecard.compute(ledger, horizons=(1,))
        self.assertEqual(result["counts"]["FLAT"], 2)
        self.assertEqual([r for r in result["table"] if r.get("n")], [])

    def test_prices_are_filled_in_from_later_records_of_the_same_symbol(self):
        """The earlier packet has one candle; the later packet supplies the forward price."""
        t0 = 1_700_000_000_000
        ledger = [row(1, "LONG", [candle(t0, 100.0)]),
                  row(2, "LONG", [candle(t0 + 4 * HOUR_MS, 120.0)])]
        result = scorecard.compute(ledger, horizons=(24,))
        long_row = next(r for r in result["table"] if r["state"] == "LONG")
        self.assertEqual(long_row["n"], 0, "the 24h forward price does not exist yet for either record")


class RenderTest(unittest.TestCase):
    def test_the_caveats_are_always_on_the_page(self):
        result = {"table": [], "counts": {"FLAT": 1}, "skipped": 0, "decisions": 1, "horizons": [1]}
        page = scorecard.render(result)
        for caveat in ("Overlap", "Fees and slippage are excluded", "No significance claim", "Small n"):
            self.assertIn(caveat, page)

    def test_an_unreached_horizon_says_so_instead_of_showing_a_zero(self):
        result = {"table": [{"state": "LONG", "hours": 24, "n": 0}], "counts": {"LONG": 1},
                  "skipped": 0, "decisions": 1, "horizons": [24]}
        page = scorecard.render(result)
        self.assertIn("no position has reached this horizon yet", page)
        self.assertNotIn("+0.000%", page)


if __name__ == "__main__":
    unittest.main()
