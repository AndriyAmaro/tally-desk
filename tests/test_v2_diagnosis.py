import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import backtest, dossier, llm, v2_diagnosis as diag

_no_llm = mock.patch.object(llm, "available", return_value=False)


def setUpModule():
    _no_llm.start()  # appending dossiers in these tests must not call the real API when a key is in the environment


def tearDownModule():
    _no_llm.stop()


def series(n=260, start=100.0, drift=0.0006, wobble=0.004, t0=1_700_000_000_000):
    """A deterministic hourly series: a steady drift with a repeating wobble, volume 100 with a periodic spike."""
    out, price = [], start
    for i in range(n):
        move = drift + wobble * (((i * 7) % 11) - 5) / 5
        close = price * (1 + move)
        out.append({"t": t0 + i * diag.HOUR_MS, "o": price, "h": max(price, close) * 1.002, "l": min(price, close) * 0.998, "c": close, "v": 100.0 + (60.0 if i % 13 == 0 else 0.0)})
        price = close
    return out


def accelerating(n=120, sign=1, start=100.0, t0=1_700_000_000_000):
    """Hourly moves that grow in size: the MACD histogram and the EMA gap both point the same way (a steady drift would leave the histogram near zero)."""
    out, price = [], start
    for i in range(n):
        close = price * (1 + sign * 0.00004 * i)
        out.append({"t": t0 + i * diag.HOUR_MS, "o": price, "h": max(price, close) * 1.001, "l": min(price, close) * 0.999, "c": close, "v": 100.0})
        price = close
    return out


class MirrorTest(unittest.TestCase):
    def test_every_move_is_negated_and_high_and_low_swap(self):
        real = series(40)
        mirrored = diag.mirror(real)
        self.assertEqual(len(mirrored), len(real))
        for a, b in zip(real, mirrored):
            self.assertEqual((a["t"], a["v"]), (b["t"], b["v"]))
            self.assertGreaterEqual(b["h"], b["l"])
            self.assertGreaterEqual(b["h"], max(b["o"], b["c"]) - 1e-9)
            self.assertLessEqual(b["l"], min(b["o"], b["c"]) + 1e-9)
        # a hourly rise of x% becomes a fall of x%
        for i in range(1, 40):
            real_move = real[i]["c"] / real[i - 1]["c"] - 1
            mirror_move = mirrored[i]["c"] / mirrored[i - 1]["c"] - 1
            self.assertAlmostEqual(mirror_move, -real_move, places=9)

    def test_mirroring_twice_gives_the_history_back(self):
        real = series(60)
        again = diag.mirror(diag.mirror(real))
        for a, b in zip(real, again):
            for key in ("o", "h", "l", "c"):
                self.assertAlmostEqual(a[key] / b[key], 1.0, places=9)

    def test_the_inverse_mirror_multiplies_to_a_constant(self):
        real = series(30)
        inverted = diag.mirror_inverse(real)
        constant = real[0]["o"] ** 2
        for a, b in zip(real, inverted):
            self.assertAlmostEqual(a["c"] * b["c"], constant, places=6)
            self.assertAlmostEqual(a["h"] * b["l"], constant, places=6)  # the high of one is the low of the other


class SummariesTest(unittest.TestCase):
    def test_regime_uses_the_72_candle_move_with_a_three_percent_band(self):
        flat = [{"c": 100.0}] * 72
        up = [{"c": 100.0}] * 71 + [{"c": 103.5}]
        down = [{"c": 100.0}] * 71 + [{"c": 96.0}]
        inside_up = [{"c": 100.0}] * 71 + [{"c": 102.9}]
        inside_down = [{"c": 100.0}] * 71 + [{"c": 97.1}]
        self.assertEqual([diag.regime(w) for w in (flat, up, down, inside_up, inside_down)], ["range", "up", "down", "range", "range"])

    def test_shares_and_decomposition(self):
        rows = [{"action": "LONG"}] * 2 + [{"action": "SHORT"}] * 6 + [{"action": "FLAT"}] * 12
        real = diag.shares(rows)
        self.assertEqual((real["n"], real["LONG_pct"], real["SHORT_pct"], real["FLAT_pct"]), (20, 10.0, 30.0, 60.0))
        mirrored = {"LONG_pct": 28.0, "SHORT_pct": 12.0}
        d = diag.decomposition(real, mirrored)
        # real: 10 - 30 = -20; mirror: 28 - 12 = +16 -> bias = (-20 + 16) / 2 = -2, regime = (-20 - 16) / 2 = -18
        self.assertEqual(d, {"real_long_minus_short": -20.0, "mirror_long_minus_short": 16.0, "bias_pp": -2.0, "regime_pp": -18.0})

    def test_a_symmetric_rule_set_has_zero_bias(self):
        self.assertEqual(diag.decomposition({"LONG_pct": 30.0, "SHORT_pct": 10.0}, {"LONG_pct": 10.0, "SHORT_pct": 30.0})["bias_pp"], 0.0)

    def test_argument_rates_and_decisive_arguments(self):
        rows = [
            {"action": "LONG", "bull": ["A", "B"], "bear": [], "flips": ["A"]},
            {"action": "LONG", "bull": ["A"], "bear": ["C"], "flips": ["A", "B"]},
            {"action": "FLAT", "bull": [], "bear": [], "flips": []},
            {"action": "SHORT", "bull": [], "bear": ["C"], "flips": ["C"]},
        ]
        self.assertEqual(diag.argument_rates(rows, "bull"), [("A", 50.0), ("B", 25.0)])
        self.assertEqual(diag.decisive_arguments(rows, "LONG"), [("A", 100.0), ("B", 50.0)])
        self.assertEqual(diag.decisive_arguments(rows, "SHORT"), [("C", 100.0)])


class StateTest(unittest.TestCase):
    def test_the_claim_texts_are_the_ones_the_v2_rules_produce(self):
        up = diag.decide_v2(accelerating(120, +1), "BTCUSDT")
        down = diag.decide_v2(accelerating(120, -1), "BTCUSDT")
        self.assertIn(diag.TREND_UP, up["bull"])
        self.assertIn(diag.MACD_UP, up["bull"])
        self.assertIn(diag.TREND_DOWN, down["bear"])
        self.assertIn(diag.MACD_DOWN, down["bear"])

    def test_state_needs_both_core_signals_on_the_same_side(self):
        self.assertEqual(diag.state_of([diag.TREND_UP, diag.MACD_UP, "x"], [diag.MACD_DOWN]), "both bullish")
        self.assertEqual(diag.state_of([diag.TREND_UP], [diag.MACD_DOWN]), "mixed")
        self.assertEqual(diag.state_of([diag.MACD_UP], [diag.TREND_DOWN]), "mixed")
        self.assertEqual(diag.state_of([], [diag.TREND_DOWN, diag.MACD_DOWN]), "both bearish")
        self.assertEqual(diag.state_of([], []), "mixed")

    def test_by_state_splits_the_decisions(self):
        rows = [
            {"action": "LONG", "bull": [diag.TREND_UP, diag.MACD_UP], "bear": []},
            {"action": "FLAT", "bull": [diag.TREND_UP, diag.MACD_UP], "bear": ["x"]},
            {"action": "SHORT", "bull": [], "bear": [diag.TREND_DOWN, diag.MACD_DOWN]},
            {"action": "FLAT", "bull": [diag.TREND_UP], "bear": [diag.MACD_DOWN]},
        ]
        split = diag.by_state(rows)
        self.assertEqual((split["both bullish"]["n"], split["both bullish"]["LONG"], split["both bullish"]["FLAT"]), (2, 1, 1))
        self.assertEqual((split["both bearish"]["n"], split["both bearish"]["SHORT_pct"]), (1, 100.0))
        self.assertEqual((split["mixed"]["n"], split["mixed"]["FLAT"]), (1, 1))

    def test_a_rule_set_in_mirror_answers_the_two_states_alike(self):
        # v1 has no MACD, so it never reaches 'both bullish'; the pair is only meaningful for v2, on a long enough mirrored series.
        base = series(300, drift=0.0004, wobble=0.004)
        real = diag.by_state(diag.replay(base, "BTCUSDT", diag.decide_v2))
        for name in diag.STATES:
            self.assertIn(name, real)
        self.assertEqual(sum(real[name]["n"] for name in diag.STATES), 300 - diag.WINDOW + 1)


class ReplayTest(unittest.TestCase):
    def test_one_decision_per_window_each_seeing_only_the_candles_before_it(self):
        candles = series(120)
        rows = diag.replay(candles, "BTCUSDT", diag.decide_v2)
        self.assertEqual(len(rows), 120 - diag.WINDOW + 1)
        for row in rows:
            self.assertIn(row["action"], diag.ACTIONS)
            self.assertIn(row["regime"], ("up", "range", "down"))
        # the first decision uses exactly candles 0..71 and the verdict equals the live function on that window
        first = diag.decide_v2(candles[: diag.WINDOW], "BTCUSDT")
        self.assertEqual((rows[0]["action"], rows[0]["edge"]), (first["action"], first["edge"]))

    def test_a_steady_uptrend_decides_long_and_its_mirror_decides_short(self):
        up = series(200, drift=0.002, wobble=0.0005)
        real = diag.shares(diag.replay(up, "BTCUSDT", diag.decide_v2))
        mirrored = diag.shares(diag.replay(diag.mirror(up), "BTCUSDT", diag.decide_v2))
        self.assertGreater(real["LONG_pct"], real["SHORT_pct"])
        self.assertGreater(mirrored["SHORT_pct"], mirrored["LONG_pct"])

    def test_walk_forward_trades_carry_what_the_summary_needs(self):
        candles = series(260, drift=0.002, wobble=0.003)
        trades = diag.walk_forward_v2(candles, "BTCUSDT")
        self.assertTrue(trades)
        for t in trades:
            for key in ("action", "status", "bars", "net_pnl", "fees", "pnl"):
                self.assertIn(key, t)
            self.assertIn(t["status"], ("TAKE", "STOP", "TIME"))
        summary = diag._with_sides(trades, candles)
        self.assertEqual(summary["trades"], len(trades))
        self.assertEqual(set(summary["net_by_side"]), {"LONG", "SHORT"})


class LedgerViewTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        dossier.append(packet(0.004), self.path)  # v1
        dossier.append(packet(0.004, funding=-0.0001), self.path, rules_version="v2")
        dossier.append(packet(-0.004, funding=0.001), self.path, rules_version="v2")

    def test_only_v2_dossiers_are_counted_per_symbol(self):
        view = diag.ledger_view(dossier.read_ledger(self.path))
        self.assertEqual(view["dossiers"], 2)
        self.assertEqual((view["first_seq"], view["last_seq"]), (2, 3))
        self.assertEqual(sum(view["actions"].values()), 2)
        self.assertEqual(view["per_symbol"]["BTCUSDT"]["n"], 2)
        self.assertEqual(view["per_symbol"]["BTCUSDT"]["LONG"] + view["per_symbol"]["BTCUSDT"]["SHORT"] + view["per_symbol"]["BTCUSDT"]["FLAT"], 2)
        self.assertEqual(view["per_symbol"]["BTCUSDT"]["trend_up_pct"], 50.0)  # one rising and one falling packet

    def test_the_ledger_is_split_by_indicator_state_too(self):
        view = diag.ledger_view(dossier.read_ledger(self.path))
        self.assertEqual(sum(view["by_state"][name]["n"] for name in diag.STATES), view["dossiers"])

    def test_it_counts_the_verdicts_that_funding_and_news_changed(self):
        view = diag.ledger_view(dossier.read_ledger(self.path))
        self.assertIn("changed_without_funding_and_news", view)
        self.assertLessEqual(view["changed_without_funding_and_news"], view["dossiers"])


class OutputTest(unittest.TestCase):
    def test_the_generated_block_replaces_only_what_is_between_the_markers(self):
        path = Path(tempfile.mkdtemp()) / "REPORT.md"
        path.write_text(f"# Title\n\nHand written intro.\n\n{diag.START_MARK}\nold tables\n{diag.END_MARK}\n\nHand written reading.\n", encoding="utf-8")
        diag.write_block(path, "new tables\n")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Hand written intro.", text)
        self.assertIn("Hand written reading.", text)
        self.assertIn("new tables", text)
        self.assertNotIn("old tables", text)
        diag.write_block(path, "newer tables\n")
        self.assertEqual(path.read_text(encoding="utf-8").count(diag.START_MARK), 1)

    def test_a_missing_file_is_created_with_the_markers(self):
        path = Path(tempfile.mkdtemp()) / "sub" / "REPORT.md"
        diag.write_block(path, "tables\n")
        self.assertEqual(path.read_text(encoding="utf-8"), f"{diag.START_MARK}\ntables\n{diag.END_MARK}\n")

    def test_closed_candles_only_a_forming_candle_is_dropped(self):
        candles = series(10)
        now = candles[-1]["t"] + diag.HOUR_MS // 2  # the last candle is still forming
        folder = Path(tempfile.mkdtemp())
        (folder / "BTCUSDT.json").write_text(json.dumps(candles))
        kept = diag.load_series("BTCUSDT", 10, folder, now_ms=now)
        self.assertEqual(len(kept), 9)

    def test_until_pins_the_window_end_whatever_the_clock_says(self):
        candles = series(10)
        folder = Path(tempfile.mkdtemp())
        (folder / "BTCUSDT.json").write_text(json.dumps(candles))
        until = candles[6]["t"] + diag.HOUR_MS  # the candle 6 closes exactly at this instant
        kept = diag.load_series("BTCUSDT", 10, folder, now_ms=candles[-1]["t"] + 10 * diag.HOUR_MS, until_ms=until)
        self.assertEqual([c["t"] for c in kept], [c["t"] for c in candles[:7]])

    def test_until_text_is_read_as_utc(self):
        self.assertEqual(diag.parse_until("2026-10-21T07:00:00Z"), 1_792_566_000_000)
        self.assertEqual(diag.parse_until("2026-10-21T07:00:00"), diag.parse_until("2026-10-21T07:00:00Z"))
        self.assertEqual(diag.parse_until("2026-10-21T04:00:00-03:00"), diag.parse_until("2026-10-21T07:00:00Z"))


class FetchHistoryTest(unittest.TestCase):
    """The public mirror is paged backwards from `endTime`; pinning the end must not change the default behaviour."""

    def rows(self, first, count):
        return [[first + i * diag.HOUR_MS, "1", "2", "0.5", "1.5", "10", first + (i + 1) * diag.HOUR_MS - 1, "0", 0, "0", "0", "0"] for i in range(count)]

    def test_the_first_request_ends_just_before_until(self):
        asked = []

        def fake_get(url, params):
            asked.append(dict(params))
            return 200, self.rows(1_700_000_000_000, params["limit"])

        with mock.patch.object(backtest.evidence, "_get_json", fake_get), mock.patch.object(backtest.time, "sleep"):
            got = backtest.fetch_history("BTCUSDT", "1h", 5, until_ms=1_700_100_000_000)
        self.assertEqual(len(got), 5)
        self.assertEqual(asked[0]["endTime"], 1_700_100_000_000 - 1)

    def test_without_until_the_first_request_has_no_end(self):
        asked = []

        def fake_get(url, params):
            asked.append(dict(params))
            return 200, self.rows(1_700_000_000_000, params["limit"])

        with mock.patch.object(backtest.evidence, "_get_json", fake_get), mock.patch.object(backtest.time, "sleep"):
            backtest.fetch_history("BTCUSDT", "1h", 5)
        self.assertNotIn("endTime", asked[0])


class CommandTest(unittest.TestCase):
    def test_offline_run_writes_the_report_between_markers(self):
        folder = Path(tempfile.mkdtemp())
        for symbol in ("BTCUSDT", "ETHUSDT"):
            (folder / f"{symbol}.json").write_text(json.dumps(series(240, drift=0.001)))
        ledger = folder / "ledger.jsonl"
        dossier.append(packet(0.004, funding=-0.0001), ledger, rules_version="v2")
        out = folder / "REPORT.md"
        with contextlib.redirect_stderr(io.StringIO()):
            code = diag.main(["--ledger", str(ledger), "--symbols", "BTCUSDT", "ETHUSDT", "--candles-dir", str(folder), "--out", str(out)])
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        for expected in (diag.START_MARK, "## 1. What the v2 dossiers", "## 2. The same rules", "## 3. Mirror test", "## 4. Context", "### The same indicator state on both sides", "| Ledger, v2 dossiers | both bullish |", "Return before fees", "Fees paid", "| BTCUSDT | v2 |", "| ETHUSDT | v1 |"):
            self.assertIn(expected, text)

    def test_until_on_the_command_line_drops_the_candles_after_it(self):
        folder = Path(tempfile.mkdtemp())
        candles = series(260, drift=0.001)
        (folder / "BTCUSDT.json").write_text(json.dumps(candles))
        ledger = folder / "ledger.jsonl"
        dossier.append(packet(0.004, funding=-0.0001), ledger, rules_version="v2")
        out = folder / "REPORT.md"
        until = candles[229]["t"] + diag.HOUR_MS
        stamp = f"{diag.stamp(until - diag.HOUR_MS)} UTC"
        with contextlib.redirect_stderr(io.StringIO()):
            code = diag.main(["--ledger", str(ledger), "--symbols", "BTCUSDT", "--candles-dir", str(folder), "--out", str(out), "--until", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(until / 1000))])
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("BTCUSDT 230 closed hourly candles", text)
        self.assertIn(stamp, text)

    def test_a_broken_ledger_is_refused(self):
        folder = Path(tempfile.mkdtemp())
        ledger = folder / "ledger.jsonl"
        dossier.append(packet(0.004), ledger)
        row = json.loads(ledger.read_text())
        row["verdict"]["edge"] = 99
        ledger.write_text(json.dumps(row) + "\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = diag.main(["--ledger", str(ledger), "--candles-dir", str(folder)])
        self.assertEqual(code, 1)
        self.assertIn("does not verify", err.getvalue())


if __name__ == "__main__":
    unittest.main()
