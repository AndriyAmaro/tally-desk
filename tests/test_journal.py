import json
import os
import unittest
from datetime import datetime, timezone

from verdict_desk import dossier, journal

HOUR = 3_600_000
NOW_MS = journal.to_ms("2026-10-20T00:00:00Z")


def price(t: int) -> float:
    return 100 + (t // HOUR % 50) * 0.1


def make_get():
    calls = []

    def get(url, params):
        calls.append(dict(params))
        width = journal.INTERVAL_MS[params["interval"]]
        limit = params["limit"]
        if "endTime" in params:
            last = params["endTime"] - params["endTime"] % width
            opens = [last - width * k for k in range(limit - 1, -1, -1)]
        else:
            opens = [t for t in (params["startTime"] + width * k for k in range(limit)) if t < NOW_MS]
        rows = []
        for t in opens:
            o, c = price(t), price(t + width)
            rows.append([t, str(o), str(max(o, c) + 0.2), str(min(o, c) - 0.2), str(c), "10"])
        return url, rows

    get.calls = calls
    return get


def candle_text(t: int, width: int, volume: str = "10") -> str:
    o, c = price(t), price(t + width)
    iso = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return f"{iso} o={o} h={max(o, c) + 0.2} l={min(o, c) - 0.2} c={c} v={volume}"


def turn(turn_id, at, symbol="BTCUSDT", decision="analysis", interval="4h", reasoning="", tweak=None):
    end = journal.to_ms(at)
    width = journal.INTERVAL_MS[interval]
    last_open = end - end % width
    texts = [candle_text(last_open - width * k, width) for k in (2, 1)]
    forming = candle_text(last_open, width, volume="1")  # ainda se formando: volume parcial
    candles = texts + [forming]
    if tweak:
        candles[0] = candles[0].replace("o=", "o=9")
    stamp = at.replace("T", " ").replace("Z", "")[:19]
    return {
        "turn_id": turn_id,
        "at": at,
        "decision": decision,
        "orders": [],
        "reasoning": reasoning,
        "evidence": [{"at": stamp, "symbol": symbol, "interval": interval, "summary": f"{symbol} price {price(end)}, funding 0.0100%", "candles": candles}],
        "_line_sha256": "x" * 64,
    }


class ParsingTest(unittest.TestCase):
    def test_summary_and_candle_and_time(self):
        self.assertEqual(journal.parse_summary("BTCUSDT price 84192.6, funding -0.0044%"), {"symbol": "BTCUSDT", "price": 84192.6, "funding_rate": -0.000044})
        self.assertIsNone(journal.parse_summary("nothing useful"))
        c = journal.parse_candle("2026-10-05T08:00:00.000Z o=1 h=2 l=0.5 c=1.5 v=9")
        self.assertEqual((c["o"], c["h"], c["l"], c["c"], c["v"]), (1.0, 2.0, 0.5, 1.5, 9.0))
        self.assertEqual(journal.to_ms("2026-10-05 08:00:00"), journal.to_ms("2026-10-05T08:00:00.000Z"))

    def test_closed_only_drops_the_forming_candle(self):
        end = journal.to_ms("2026-10-07T05:42:54Z")
        fetched = journal.fetch_as_of("BTCUSDT", "1h", end, 5, make_get())
        closed = journal.closed_only(fetched, "1h", end)
        self.assertEqual(len(closed), len(fetched) - 1)
        self.assertTrue(all(c["t"] + HOUR <= end for c in closed))


class CrosscheckTest(unittest.TestCase):
    def check(self, row):
        item = row["evidence"][0]
        end = journal.to_ms(item["at"])
        public = journal.fetch_as_of("BTCUSDT", "4h", end, 24, make_get())
        return journal.crosscheck(item, public, end, "4h")

    def test_closed_candles_match_and_forming_one_is_not_counted(self):
        r = self.check(turn("T1", "2026-10-07T05:42:54.735Z"))
        self.assertEqual((r["candles_recorded"], r["candles_closed"], r["closed_found"], r["closed_within_basis"]), (3, 2, 2, 2))
        self.assertTrue(r["price_in_candle_range"])

    def test_a_small_futures_spot_basis_is_tolerated_a_large_gap_is_not(self):
        row = turn("T1", "2026-10-07T05:10:00.000Z")
        end = journal.to_ms(row["evidence"][0]["at"])
        public = journal.fetch_as_of("BTCUSDT", "4h", end, 24, make_get())
        shifted = [{**c, **{k: c[k] * 1.0005 for k in ("o", "h", "l", "c")}} for c in public]
        r = journal.crosscheck(row["evidence"][0], shifted, end, "4h")
        self.assertEqual(r["closed_within_basis"], 2)
        far = [{**c, **{k: c[k] * 1.02 for k in ("o", "h", "l", "c")}} for c in public]
        self.assertEqual(journal.crosscheck(row["evidence"][0], far, end, "4h")["closed_within_basis"], 0)

    def test_an_altered_recorded_candle_is_reported(self):
        r = self.check(turn("T1", "2026-10-07T05:42:54.735Z", tweak=True))
        self.assertEqual((r["closed_found"], r["closed_within_basis"]), (2, 1))


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            turn("A", "2026-10-07T05:10:00.000Z", reasoning="PRIVATE NOTE do agente"),
            turn("B", "2026-10-07T05:50:00.000Z"),  # mesma hora, simbolo e tipo: nao duplica
            turn("C", "2026-10-07T09:20:00.000Z", symbol="ETHUSDT", decision="trade", interval="1h"),
        ]

    def run_audit(self):
        os.environ["MISTRAL_API_KEY"] = "must-not-be-used"
        try:
            return journal.audit(self.rows, get=make_get())
        finally:
            del os.environ["MISTRAL_API_KEY"]

    def test_one_shadow_decision_per_symbol_hour_and_chain_verifies(self):
        result = self.run_audit()
        self.assertEqual([e["turn"]["turn_id"] for e in result["shadow"]], ["A", "C"])
        self.assertEqual(dossier.verify(result["ledger"]), [])
        self.assertTrue(all("llm" not in d for d in result["ledger"]))

    def test_no_candle_from_the_future_enters_a_packet(self):
        for entry in self.run_audit()["shadow"]:
            end = journal.to_ms(entry["item"]["at"])
            candles = entry["record"]["evidence"]["market"]["candles"]
            self.assertEqual(len(candles), journal.SHADOW_CANDLES)
            self.assertTrue(all(c["t"] + HOUR <= end for c in candles))

    def test_packet_records_provenance_and_the_journal_funding(self):
        packet = self.run_audit()["shadow"][0]["record"]["evidence"]
        self.assertEqual(packet["origin"]["turn_id"], "A")
        self.assertEqual(packet["origin"]["kind"], "third-party journal, shadow replay")
        self.assertEqual(packet["funding"]["rate"], 0.0001)

    def test_free_text_reasoning_never_reaches_the_output(self):
        result = self.run_audit()
        outcomes = journal.mark_outcomes(result, get=make_get())
        blob = json.dumps({"ledger": result["ledger"], "outcomes": outcomes, "summary": journal.summarize(result, outcomes)})
        self.assertNotIn("PRIVATE NOTE", blob)

    def test_summary_counts_and_label(self):
        result = self.run_audit()
        outcomes = journal.mark_outcomes(result, get=make_get())
        s = journal.summarize(result, outcomes)
        self.assertEqual(s["shadow_decisions"], 2)
        self.assertEqual(s["journal_check"]["closed_within_basis_pct"], 100.0)
        self.assertEqual(sum(s["desk_actions"].values()), 2)
        self.assertIn("not a trade result", s["label"])

    def test_symbol_missing_from_the_public_mirror_is_skipped_and_listed(self):
        def get(url, params):
            if params["symbol"] == "1000PEPEUSDT":
                raise OSError("HTTP Error 400: Bad Request")
            return make_get()(url, params)

        rows = [turn("P", "2026-10-07T05:10:00.000Z", symbol="1000PEPEUSDT"), turn("Q", "2026-10-07T05:10:00.000Z")]
        result = journal.audit(rows, get=get)
        self.assertEqual([e["turn"]["turn_id"] for e in result["shadow"]], ["Q"])
        self.assertEqual(result["skipped"][0]["turn_id"], "P")
        self.assertIn("HTTP Error 400", result["skipped"][0]["why"])

    def test_unparsable_summary_is_skipped_and_listed(self):
        bad = turn("Z", "2026-10-07T05:10:00.000Z")
        bad["evidence"][0]["summary"] = "garbage"
        result = journal.audit([bad], get=make_get())
        self.assertEqual(result["shadow"], [])
        self.assertEqual(result["skipped"][0]["turn_id"], "Z")


if __name__ == "__main__":
    unittest.main()
