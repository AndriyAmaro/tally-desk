import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import dossier, portfolio

HERE = Path(__file__).parent
NODE = shutil.which("node")


def shifted(pkt: dict, start_t: int, scale: float = 1.0, collected_at: str | None = None, symbol: str | None = None) -> dict:
    """Copia o pacote com candles em outro intervalo de tempo (para haver candles posteriores)."""
    out = copy.deepcopy(pkt)
    for i, c in enumerate(out["market"]["candles"]):
        c["t"] = start_t + i
        for k in ("o", "h", "l", "c"):
            c[k] = c[k] * scale
    if collected_at:
        out["collected_at"] = collected_at
    if symbol:
        out["symbol"] = symbol
    return out


def run_node(path: Path) -> dict:
    out = subprocess.run([NODE, str(HERE / "node_stats.js"), str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def build_ledger(path: Path) -> None:
    long_pkt = packet(0.004, funding=-0.0001, titles=("Bitcoin rally hits record",))
    short_pkt = packet(-0.004, funding=0.001, titles=("Exchange hack triggers crash",))
    dossier.append(long_pkt, path)  # 1: LONG, resolvido por alvo logo abaixo
    long_v = dossier.read_ledger(path)[0]["verdict"]
    assert long_v["action"] == "LONG"
    # 2: candles posteriores onde a maxima passa do alvo no primeiro candle (TAKE para a posicao 1)
    up = shifted(long_pkt, 1000, scale=1.0)
    first = up["market"]["candles"][0]
    first.update({"o": long_v["entry"], "c": long_v["entry"], "l": long_v["entry"], "h": long_v["take_profit"] * 1.01})
    for c in up["market"]["candles"][1:]:  # o resto fica entre stop e alvo e nao muda nada
        c.update({"o": long_v["entry"], "c": long_v["entry"], "l": long_v["entry"], "h": long_v["entry"]})
    dossier.append(up, path)
    dossier.append(short_pkt, path)  # 3: SHORT, ainda sem candles posteriores no mesmo simbolo (OPEN no fim)
    dossier.append(packet(0.0), path)  # 4: FLAT


@unittest.skipUnless(NODE, "node not installed")
class StatsCrossLanguageTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        build_ledger(self.path)
        self.node = run_node(self.path)["portfolio"]
        self.py = portfolio.summarize(dossier.read_ledger(self.path))

    def test_counts_and_rates_match_python(self):
        for key in ("decisions", "flat", "closed", "open", "win_rate_pct", "start_capital"):
            self.assertEqual(self.node[key], self.py[key], key)

    def test_money_matches_python_within_rounding(self):
        for key in ("realized_pnl", "unrealized_pnl", "max_drawdown_pct"):
            self.assertAlmostEqual(self.node[key], self.py[key], places=2, msg=key)

    def test_each_position_matches(self):
        self.assertEqual(len(self.node["positions"]), len(self.py["positions"]))
        for n, p in zip(self.node["positions"], self.py["positions"]):
            self.assertEqual((n["seq"], n["symbol"], n["action"], n["status"], n["bars"]), (p["seq"], p["symbol"], p["action"], p["status"], p["bars"]))
            self.assertAlmostEqual(n["pnl"], p["pnl"], places=2)

    def test_the_fixture_covers_take_and_open(self):
        statuses = {p["status"] for p in self.py["positions"]}
        self.assertIn("TAKE", statuses)
        self.assertIn("OPEN", statuses)


@unittest.skipUnless(NODE, "node not installed")
class CadenceTest(unittest.TestCase):
    def test_gaps_are_reported_not_hidden(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        base = packet(0.0)
        for symbol, stamp in [
            ("BTCUSDT", "2026-10-17T00:00:00+00:00"),
            ("ETHUSDT", "2026-10-17T00:05:00+00:00"),
            ("BTCUSDT", "2026-10-17T01:00:00+00:00"),
            ("ETHUSDT", "2026-10-17T01:05:00+00:00"),
            ("BTCUSDT", "2026-10-17T03:30:00+00:00"),  # buraco de 2h30 no BTC
        ]:
            dossier.append(shifted(base, 0, collected_at=stamp, symbol=symbol), path)
        c = run_node(path)["cadence"]
        self.assertEqual(c["total"], 5)
        self.assertEqual(c["per_symbol"], {"BTCUSDT": 3, "ETHUSDT": 2})
        self.assertEqual(c["first"], "2026-10-17T00:00:00.000Z")
        self.assertEqual(c["last"], "2026-10-17T03:30:00.000Z")
        self.assertEqual((c["longest_gap_minutes"], c["longest_gap_symbol"]), (150, "BTCUSDT"))
        self.assertEqual(c["gaps_over_90_minutes"], 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node not installed")
class StatsOnTheRealLedgerTest(unittest.TestCase):
    """The fixture above has no declined entries, and the dashboard shows what the JavaScript computes on the
    published ledger: a WATCH_REVERSAL counted as a position gave 'unrealized NaN' and 215 positions beside
    '163 sized LONG or SHORT'. The two implementations must agree on the real file, not only on a fixture."""

    @classmethod
    def setUpClass(cls):
        cls.path = HERE.parent / "ledger" / "ledger.jsonl"
        cls.node = run_node(cls.path)["portfolio"]
        cls.py = portfolio.summarize(dossier.read_ledger(cls.path))

    def test_a_declined_entry_is_not_a_position(self):
        actions = {p["action"] for p in self.node["positions"]}
        self.assertEqual(actions - {"LONG", "SHORT"}, set())

    def test_the_same_positions_and_money_on_the_published_ledger(self):
        self.assertEqual(len(self.node["positions"]), len(self.py["positions"]))
        for key in ("closed", "open", "flat", "win_rate_pct"):
            self.assertEqual(self.node[key], self.py[key], key)
        for key in ("realized_pnl", "unrealized_pnl", "max_drawdown_pct"):
            self.assertIsNotNone(self.node[key], key)
            self.assertAlmostEqual(self.node[key], self.py[key], places=2, msg=key)
