"""The source drift seal: one page an earlier dossier cited, read again through the fetch path, sealed as unchanged, changed or unavailable.
A fake fetch transport (an `ask` that plays Otari's reply) and fake ledgers: no network, no key, no money."""

import copy
import datetime
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from tests.test_otari_tools import FETCH_CALL, PAGE_TEXT, FlowEnv, _Page
from verdict_desk import closure, dossier, llm, otari_report as rep, source_drift as drift

NOW = datetime.datetime(2026, 11, 4, 18, 0, tzinfo=datetime.timezone.utc)
URL = FETCH_CALL["url"]


def old_dossier(seq, hours_ago, url=URL, excerpt=PAGE_TEXT, era=True, page=True):
    web = {"items": [{"url": url, "title": "Bitcoin ETF Flows Today"}]}
    if page:
        web["page"] = {"url": url, "title": "Bitcoin ETF Flows Today", "excerpt": excerpt}
    when = (NOW - datetime.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return {"seq": seq, "hash": f"h{seq}", "evidence": {"symbol": "BTCUSDT", "collected_at": when, "web": web}, "llm": {"otari": {}} if era else {}}


def fetcher(text=PAGE_TEXT, calls=(FETCH_CALL,), fail=None):
    """An `ask` that plays Otari's fetch reply and leaves the receipt where llm.served_receipt() reads it."""
    seen = []

    def ask(system, user, **kw):
        seen.append((kw["role"], user, kw["extra"]["tools"], kw.get("end_user")))
        if fail:
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "fetcher", "request_id": "req-refused", "refusal": {"status": 403, "code": "budget_exceeded"}}
            raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded")
        llm._served.receipt = {"schema": "otari-receipt/1", "role": "fetcher", "request_id": "req-drift", "request_id_source": "otari", "captured_at": "2026-11-04T18:00:30Z",
                               "cost_usd": "0.010100", "tool_calls": list(calls)}
        return text if text.startswith("not json") else json.dumps({"excerpt": text})
    ask.seen = seen
    return ask


class CollectTest(unittest.TestCase):
    def collect(self, ledger, **kw):
        ask = kw.pop("ask", None) or fetcher()
        return drift.collect(ledger, NOW, ask=ask, opener=lambda request, timeout=None: _Page("<p>" + PAGE_TEXT + "</p>")), ask

    def test_the_same_passage_is_unchanged_whatever_the_markup_the_case_or_the_punctuation(self):
        block, ask = self.collect([old_dossier(5, 3)], ask=fetcher("<b>BITCOIN ETFs</b> recorded their strongest weekly inflows in ten months, as the price surged past its October high!"))
        self.assertEqual(block["status"], "unchanged")
        self.assertEqual(block["new_sha256"], block["old_sha256"])
        self.assertEqual((block["url"], block["source_dossier"], block["minutes_after_decision"]), (URL, 5, 180))
        self.assertEqual(block["receipt"]["request_id"], "req-drift")
        self.assertTrue(block["otari_reported_fetch"])
        self.assertEqual([c[0] for c in ask.seen], ["fetcher"], "one fetch, by the fetcher role, with the fetch tool")
        self.assertEqual(ask.seen[0][2], [{"type": "otari_web_fetch"}])
        self.assertEqual(ask.seen[0][3], "vd-drift", "its own end user, so the day budget of vd-fetcher is not shared")
        self.assertIn("begins with: Bitcoin ETFs recorded their strongest", ask.seen[0][1])

    def test_a_different_passage_is_changed_and_our_own_get_says_whether_the_new_text_is_on_the_page(self):
        new = "Bitcoin ETFs recorded their strongest weekly outflows in ten months as the price fell below its October low."
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher(new))
        self.assertEqual(block["status"], "changed")
        self.assertNotEqual(block["new_sha256"], block["old_sha256"])
        self.assertIs(block["new_excerpt_verified"], False, "the page still says inflows: the relay is not on it")
        self.assertEqual(block["excerpt"], new)

    def test_a_passage_the_page_no_longer_has_is_changed_with_its_reason(self):
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher(""))
        self.assertEqual((block["status"], block["new_sha256"]), ("changed", hashlib.sha256(b"").hexdigest()))
        self.assertIn("no passage", block["reason"])

    def test_a_failed_call_is_unavailable_with_its_receipt_and_never_an_exception(self):
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher(fail=True))
        self.assertEqual(block["status"], "unavailable")
        self.assertIn("budget_exceeded", block["reason"])
        self.assertEqual(block["receipt"]["refusal"]["code"], "budget_exceeded")
        self.assertNotIn("new_sha256", block)

    def test_a_fetch_otari_did_not_report_as_completed_and_a_reply_that_is_not_json_are_unavailable(self):
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher(calls=()))
        self.assertEqual((block["status"], block["reason"]), ("unavailable", "Otari did not report a completed fetch of the page"))
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher("not json at all"))
        self.assertEqual(block["status"], "unavailable")
        other = [{**FETCH_CALL, "url": "https://elsewhere.example/x"}]
        block, _ = self.collect([old_dossier(5, 3)], ask=fetcher(calls=other))
        self.assertEqual(block["status"], "unavailable", "a fetch of another page proves nothing about this one")

    def test_no_eligible_source_means_no_call_at_all(self):
        for ledger in ([], [old_dossier(5, 1)], [old_dossier(5, 5, era=False)], [old_dossier(5, 5, page=False)], [old_dossier(5, 5, url="http://insecure.example/a")],
                       [old_dossier(5, 5, excerpt="... !!")]):
            ask = fetcher()
            block = drift.collect(ledger, NOW, ask=ask)
            self.assertEqual(block["status"], "none", ledger)
            self.assertEqual(ask.seen, [], "nothing was fetched")
            self.assertNotIn("receipt", block)

    def test_the_source_is_the_page_of_the_newest_dossier_that_is_old_enough(self):
        ledger = [old_dossier(3, 9, url="https://a.example/old-news"), old_dossier(4, 4, url="https://b.example/the-pick"), old_dossier(5, 0.5, url="https://c.example/too-new")]
        source = drift.pick(ledger, NOW)
        self.assertEqual((source["seq"], source["url"]), (4, "https://b.example/the-pick"))
        self.assertEqual(drift.pick(ledger, NOW, min_age_minutes=20)["seq"], 5)
        self.assertEqual(drift.pick(ledger, NOW), drift.pick(ledger, NOW), "deterministic")
        skipped = [old_dossier(3, 9), old_dossier(4, 4, page=False)]
        self.assertEqual(drift.pick(skipped, NOW)["seq"], 3, "a dossier whose page was never read has nothing to compare, the next one is taken")


class RecheckTest(unittest.TestCase):
    def sealed(self):
        block, _ = CollectTest().collect([old_dossier(5, 3)], ask=fetcher("Bitcoin ETFs recorded their strongest weekly outflows in ten months as the price fell."))
        return {"drift": block}

    def test_the_hashes_and_the_status_follow_from_the_sealed_passage_and_edits_are_caught(self):
        d = self.sealed()
        self.assertEqual(drift.recheck(d), {"drift_ok": True})
        for edit in (lambda b: b.update(excerpt="something else entirely here"), lambda b: b.update(status="unchanged"), lambda b: b.update(new_sha256=b["old_sha256"]),
                     lambda b: b.update(compared_chars=3), lambda b: b.pop("excerpt")):
            forged = copy.deepcopy(d)
            edit(forged["drift"])
            self.assertEqual(drift.recheck(forged), {"drift_ok": False})
        self.assertEqual(drift.recheck({"drift": {"status": "none"}}), {})
        self.assertEqual(drift.recheck({"drift": {"status": "unavailable"}}), {})
        self.assertEqual(drift.recheck({}), {})

    def test_the_report_line_says_what_the_block_says(self):
        d = self.sealed()
        self.assertEqual(rep.drift_line(d), "Source drift: changed (dossier 5, 180 min after the decision)")
        self.assertEqual(rep.drift_line({"drift": {"status": "none"}}), "Source drift: no earlier page old enough to read again")
        self.assertEqual(rep.drift_line({"drift": {"status": "unavailable", "source_dossier": 5, "reason": "boom"}}), "Source drift: unavailable (dossier 5): boom")
        self.assertEqual(rep.drift_line({}), "Source drift: not checked in this dossier (recorded before the seal existed)")

    def test_the_drift_receipt_is_a_call_of_the_cycle_with_its_own_label(self):
        d = {"seq": 1, "evidence": {}, "llm": {"otari": {}}, "drift": {"status": "unchanged", "receipt": {"request_id": "x", "role": "fetcher", "started_offset_ms": 5}}}
        self.assertEqual([c["label"] for c in rep.calls_of(d)], ["drift"])


class CycleTest(FlowEnv):
    """The whole decide flow with a fake Otari: the drift fetch happens once, inside the tool cap, and the verdict does not move."""

    def prior(self, hours=5):
        when = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        d = old_dossier(1, 0)
        d["evidence"]["collected_at"] = when
        self.ledger.write_text(json.dumps(d) + "\n", encoding="utf-8")

    def test_a_cycle_with_an_eligible_page_adds_one_fetch_inside_the_tool_cap_and_seals_it(self):
        self.prior()
        code, out, ledger = self.run_flow(rules_version="v7")
        d = ledger[-1]
        self.assertEqual((code, d["seq"]), (0, 2))
        self.assertEqual(d["drift"]["status"], "unchanged")
        self.assertEqual(d["drift"]["receipt"]["role"], "fetcher")
        self.assertIn("source drift via Otari: unchanged", out)
        cycle = d["llm"]["otari"]["cycle"]
        self.assertEqual(cycle["tools"]["calls"], 5)  # search, page fetch, drift fetch, mcp, sandbox: one more than before
        self.assertLessEqual(llm.decimal.Decimal(cycle["tools"]["spent_usd"]), llm.decimal.Decimal("0.06"))
        self.assertEqual([c["label"] for c in rep.calls_of(d)].count("drift"), 1)
        self.assertEqual(d["closure"]["outcome"], "pass")
        self.assertEqual(next(c for c in d["closure"]["checks"] if c["name"] == "receipts")["reason"].split(",")[0], "10 calls")
        self.assertTrue(dossier.replay(d, "auto")["matches"])
        self.assertIs(dossier.replay(d, "auto")["drift_ok"], True)

    def test_with_nothing_to_compare_there_is_no_extra_call(self):
        code, out, ledger = self.run_flow(rules_version="v7")
        d = ledger[-1]
        self.assertEqual(d["drift"]["status"], "none")
        self.assertEqual(d["llm"]["otari"]["cycle"]["tools"]["calls"], 4)
        self.assertIn("source drift via Otari: none", out)
        self.assertEqual([c["label"] for c in rep.calls_of(d)].count("drift"), 0)

    def test_the_drift_never_moves_the_verdict(self):
        _, _, plain = self.run_flow({"OTARI_SOURCE_DRIFT": "0"}, rules_version="v7")
        self.ledger = self.ledger.parent / "second.jsonl"
        self.n = 0
        self.prior()
        _, _, with_drift = self.run_flow(rules_version="v7")
        self.assertNotIn("drift", plain[-1])
        keep = lambda v: {k: v[k] for k in ("action", "edge", "bull_score", "bear_score", "rationale")}  # noqa: E731
        self.assertEqual(keep(plain[-1]["verdict"]), keep(with_drift[-1]["verdict"]))

    def test_a_drift_fetch_that_fails_is_recorded_and_the_cycle_still_records(self):
        self.prior()
        real = drift.collect
        with mock.patch.object(drift, "collect", lambda ledger, **kw: real(ledger, ask=fetcher(fail=True))):
            code, _, ledger = self.run_flow(rules_version="v7")
        d = ledger[-1]
        self.assertEqual((code, d["drift"]["status"]), (0, "unavailable"))
        self.assertEqual(d["closure"]["outcome"], "pass", "a recorded refusal is an accounted call")



class TickerNoiseTest(FlowEnv):
    """A live price ticker inside a news page changes on every read; it must not make an unchanged article look changed."""

    ARTICLE = "Bitcoin rebounded to around 83,000 on Monday after dipping to 80,500 on Sunday, a recovery that ran alongside rising US equity futures."

    def test_two_readings_that_differ_only_by_a_ticker_are_the_same_text(self):
        a = f"Bitcoin (BTC▲$80,401.00) rebounded. {self.ARTICLE}"
        b = f"Bitcoin (BTC▼$80,977.50) rebounded. {self.ARTICLE}"
        self.assertEqual(drift.normalize(a), drift.normalize(b))
        self.assertNotEqual(drift.normalize(a, 1), drift.normalize(b, 1), "version 1 still sees them differ, which is why sealed blocks record their version")

    def test_a_real_wording_change_is_still_a_change(self):
        a = f"Bitcoin (BTC▲$80,401.00) rebounded. {self.ARTICLE}"
        b = a.replace("rebounded", "collapsed")
        self.assertNotEqual(drift.normalize(a), drift.normalize(b))

    def test_numbers_of_the_article_are_not_dropped(self):
        self.assertIn("83", drift.normalize(f"BTC▲$80,401.00 {self.ARTICLE}").split())

    def test_collect_seals_unchanged_when_only_the_ticker_moved(self):
        old = f"Bitcoin (BTC▲$80,401.00) rebounded. {self.ARTICLE}"
        new = f"Bitcoin (BTC▼$80,977.50) rebounded. {self.ARTICLE}"
        ledger = [old_dossier(1, 4, excerpt=old)]
        block = drift.collect(ledger, now=NOW, ask=fetcher(text=new))
        self.assertEqual(block["status"], "unchanged")
        self.assertEqual(block["norm"], 2)
        self.assertTrue(drift.recheck({"drift": block})["drift_ok"])

    def test_a_block_sealed_under_version_one_still_replays(self):
        text = "Plain words of an article with no ticker at all."
        old = drift.normalize(text, 1)
        block = {"schema": "otari-drift/1", "status": "unchanged", "excerpt": text, "compared_chars": len(old),
                 "old_sha256": drift.sha(old), "new_sha256": drift.sha(old)}  # no `norm`: sealed before version 2
        self.assertTrue(drift.recheck({"drift": block})["drift_ok"])

    def test_the_sealed_blocks_of_the_real_ledger_still_replay(self):
        ledger = [json.loads(line) for line in (Path(__file__).resolve().parents[1] / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        sealed = [d for d in ledger if isinstance(d.get("drift"), dict) and d["drift"].get("status") in ("unchanged", "changed")]
        self.assertTrue(sealed, "the ledger holds sealed drift blocks")
        for d in sealed:
            self.assertTrue(drift.recheck(d)["drift_ok"], f"dossier {d['seq']}")


if __name__ == "__main__":
    unittest.main()
