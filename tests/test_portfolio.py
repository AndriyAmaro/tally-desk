import unittest

from verdict_desk import portfolio


def dossier(seq, action, t_entry, candles, entry=100.0, stop=98.0, take=104.0, size=10.0):
    v = {"action": action}
    if action != "FLAT":
        v.update(entry=entry, stop=stop, take_profit=take, size=size)
    return {"seq": seq, "verdict": v, "evidence": {"symbol": "BTCUSDT", "market": {"candles": candles}}}


def c(t, lo, hi, close):
    return {"t": t, "o": close, "h": hi, "l": lo, "c": close, "v": 1}


class PortfolioTest(unittest.TestCase):
    def test_take_profit_hit(self):
        ledger = [dossier(1, "LONG", 1, [c(1, 99, 101, 100)]), dossier(2, "FLAT", 3, [c(1, 99, 101, 100), c(2, 100, 105, 104)])]
        s = portfolio.summarize(ledger)
        self.assertEqual(s["positions"][0]["status"], "TAKE")
        self.assertEqual(s["positions"][0]["pnl"], 40.0)
        self.assertEqual(s["win_rate_pct"], 100.0)

    def test_stop_wins_when_both_in_same_candle(self):
        ledger = [dossier(1, "LONG", 1, [c(1, 99, 101, 100)]), dossier(2, "FLAT", 2, [c(2, 97, 105, 101)])]
        p = portfolio.summarize(ledger)["positions"][0]
        self.assertEqual(p["status"], "STOP")
        self.assertEqual(p["pnl"], -20.0)

    def test_short_open_marked_to_last_close_and_not_realized(self):
        ledger = [dossier(1, "SHORT", 1, [c(1, 99, 101, 100)], stop=102, take=96), dossier(2, "FLAT", 2, [c(2, 98, 101, 99)])]
        s = portfolio.summarize(ledger)
        self.assertEqual(s["positions"][0]["status"], "OPEN")
        self.assertEqual(s["unrealized_pnl"], 10.0)
        self.assertEqual(s["realized_pnl"], 0)
        self.assertIsNone(s["win_rate_pct"])

    def test_no_later_candles_means_open_zero(self):
        s = portfolio.summarize([dossier(1, "LONG", 1, [c(1, 99, 101, 100)])])
        self.assertEqual(s["positions"][0]["pnl"], 0.0)


class FeesTest(unittest.TestCase):
    def test_each_closed_position_pays_the_taker_fee_on_its_entry_and_exit_notional(self):
        d = dossier(1, "LONG", 0, [c(0, 99, 101, 100)])
        later = dossier(2, "FLAT", 1, [c(1, 99, 105, 104)])
        s = portfolio.summarize([d, later])
        self.assertEqual(s["closed"], 1)
        expected = (100.0 * 10 + 104.0 * 10) * portfolio.FEE_PCT_PER_SIDE / 100
        self.assertAlmostEqual(s["fees"], expected, places=6)
        self.assertAlmostEqual(s["realized_net"], s["realized_pnl"] - expected, places=6)

    def test_open_positions_pay_no_fee_yet(self):
        s = portfolio.summarize([dossier(1, "LONG", 0, [c(0, 99, 101, 100)])])
        self.assertEqual(s["fees"], 0)
        self.assertEqual(s["realized_net"], s["realized_pnl"])


class MarketSeriesTest(unittest.TestCase):
    """The candles Ask the record replays "what happened next" on: every candle the ledger froze, per symbol, the
    first recorded version of each, so a position resolves on them exactly as portfolio.resolve does."""

    def test_each_candle_appears_once_in_its_first_recorded_version(self):
        ledger = [dossier(1, "FLAT", 0, [c(0, 99, 101, 100), c(1, 99, 102, 101)]),
                  dossier(2, "FLAT", 0, [c(1, 90, 110, 100), c(2, 99, 103, 102)])]
        series = portfolio.market_series(ledger)
        self.assertEqual([x["t"] for x in series["BTCUSDT"]["candles"]], [0, 1, 2])
        self.assertEqual(series["BTCUSDT"]["candles"][1]["l"], 99)
        self.assertEqual(series["BTCUSDT"]["records"], [1, 2])

    def test_every_position_of_the_real_ledger_resolves_the_same_on_the_series(self):
        from pathlib import Path

        from verdict_desk import dossier as ledger_io
        ledger = ledger_io.read_ledger(Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl")
        series = portfolio.market_series(ledger)
        checked = 0
        for i, d in enumerate(ledger):
            if d["verdict"]["action"] not in portfolio.POSITION_ACTIONS:
                continue
            t = d["evidence"]["market"]["candles"][-1]["t"]
            later = [x for x in series[d["evidence"]["symbol"]]["candles"] if x["t"] > t]
            self.assertEqual(portfolio.resolve(d, later), portfolio.resolve(d, portfolio._later_candles(ledger, i)), d["seq"])
            checked += 1
        self.assertGreater(checked, 150)
