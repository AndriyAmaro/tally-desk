import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import __main__ as cli
from verdict_desk import dossier, evidence


class DecideCommandTest(unittest.TestCase):
    def run_decide(self, collect, symbols):
        path = Path(tempfile.mkdtemp()) / "ledger" / "ledger.jsonl"
        opts = argparse.Namespace(symbols=symbols, interval="1h", no_news=True, feed=None, default_feeds=False, ledger=path)
        original = evidence.collect
        evidence.collect = collect
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = cli.cmd_decide(opts)
        finally:
            evidence.collect = original
        return code, path, err.getvalue()

    def test_default_ledger_is_the_tracked_path(self):
        self.assertEqual(cli.DEFAULT_LEDGER, Path("ledger/ledger.jsonl"))

    def test_records_every_symbol_it_can_collect(self):
        code, path, _ = self.run_decide(lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(code, 0)
        ledger = dossier.read_ledger(path)
        self.assertEqual([d["evidence"]["symbol"] for d in ledger], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(dossier.verify(ledger), [])

    def test_a_failed_symbol_is_a_visible_gap_not_a_fake_decision(self):
        def collect(symbol, interval, with_news):
            if symbol == "ETHUSDT":
                raise OSError("exchange down")
            return {**packet(0.004), "symbol": symbol}

        code, path, err = self.run_decide(collect, ["ETHUSDT", "BTCUSDT"])
        self.assertEqual(code, 0)
        self.assertEqual([d["evidence"]["symbol"] for d in dossier.read_ledger(path)], ["BTCUSDT"])
        self.assertIn("ETHUSDT: no evidence collected, nothing recorded (exchange down)", err)

    def test_nothing_collected_exits_nonzero_and_writes_no_ledger(self):
        def collect(symbol, interval, with_news):
            raise OSError("down")

        code, path, _ = self.run_decide(collect, ["BTCUSDT"])
        self.assertEqual(code, 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
