import unittest

from verdict_desk import backtest


def rising(n, step=0.005, start=100.0):
    out, price = [], start
    for k in range(n):
        nxt = price * (1 + step)
        out.append({"t": k * 3_600_000, "o": price, "h": nxt * 1.001, "l": price * 0.999, "c": nxt, "v": 10.0 + k})
        price = nxt
    return out


class BacktestTest(unittest.TestCase):
    def test_uptrend_opens_long_and_hits_take(self):
        trades = backtest.run(rising(200), "TESTUSDT")
        self.assertTrue(trades)
        first = trades[0]
        self.assertEqual(first["action"], "LONG")
        self.assertEqual(first["status"], "TAKE")
        self.assertGreater(first["pnl"], 0)

    def test_fees_come_off_both_sides(self):
        free = backtest.run(rising(200), "TESTUSDT", fee_pct=0)[0]
        paid = backtest.run(rising(200), "TESTUSDT", fee_pct=0.05)[0]
        self.assertEqual(free["net_pnl"], free["pnl"])
        notional = paid["entry"] * (paid["fees"] / (paid["entry"] * 0.0005 * 2)) if paid["fees"] else 0
        self.assertGreater(paid["fees"], 0)
        self.assertAlmostEqual(paid["net_pnl"], paid["pnl"] - paid["fees"], places=3)
        self.assertGreater(notional, 0)

    def test_decision_ignores_the_future(self):
        base = rising(200)
        first = backtest.run(base, "TESTUSDT")[0]
        cut = backtest.WINDOW + first["bars"] + 5
        changed = base[:cut] + [{**c, "c": c["c"] * 0.5, "h": c["h"] * 0.5, "l": c["l"] * 0.5} for c in base[cut:]]
        self.assertEqual(backtest.run(changed, "TESTUSDT")[0], first)

    def test_series_hash_is_stable_and_sensitive(self):
        a = rising(100)
        self.assertEqual(backtest.series_hash(a), backtest.series_hash(rising(100)))
        b = rising(100)
        b[50]["c"] += 0.01
        self.assertNotEqual(backtest.series_hash(a), backtest.series_hash(b))

    def test_report_is_labeled_and_segments_add_up(self):
        candles = rising(300)
        rep = backtest.report("TESTUSDT", "1h", candles)
        self.assertIn("not live", rep["label"])
        self.assertEqual(sum(s["trades"] for s in rep["segments"]), rep["summary"]["trades"])
        self.assertAlmostEqual(sum(s["net_pnl"] for s in rep["segments"]), rep["summary"]["net_pnl"], places=3)


if __name__ == "__main__":
    unittest.main()
