"""Fixes from the independent review: a failed budget read is told apart from a missing end user, the narrator waits out an in-flight
idempotency key, the audit cites real evidence. No network."""

import io
import json
import os
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request
from unittest import mock

from tests.test_otari import FakeResponse, message, plain_config
from tests.test_otari_canary import ENV, headroom
from verdict_desk import llm
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

ROW = {"external_id": "vd-judge", "budget_id": "bud", "blocked": False, "spend": 0.002, "reserved": 0.0, "next_budget_reset_at": "2026-11-03T00:00:00+00:00"}


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause"), plain_config()):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()


def http_error(code, headers=None, body=b"{}"):
    return urllib.error.HTTPError("https://otari.example/api/v1/end-users", code, "x", message(headers or {}), io.BytesIO(body))


class EndUserLookupTest(Env):
    def lookup(self, *answers):
        queue = list(answers)

        def urlopen(request, timeout=None):
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            return llm.end_user_lookup("vd-judge"), len(answers) - len(queue)

    def test_an_answer_without_the_user_is_absent_and_an_answer_with_it_is_found(self):
        got, calls = self.lookup(FakeResponse([], {}))
        self.assertEqual((got["lookup"], got["status"], got["row"], calls), ("absent", 200, None, 1))
        got, _ = self.lookup(FakeResponse([ROW], {}))
        self.assertEqual((got["lookup"], got["status"], got["row"]["budget_id"]), ("found", 200, "bud"))

    def test_an_http_error_is_an_error_with_its_status_never_a_missing_user(self):
        for code in (401, 403, 404):
            got, calls = self.lookup(http_error(code))
            self.assertEqual((got["lookup"], got["status"], got["error"], calls), ("error", code, f"HTTP {code}", 1), "no retry for a status a retry cannot change")

    def test_a_429_is_asked_once_more_after_the_wait_the_server_names_and_the_second_answer_counts(self):
        got, calls = self.lookup(http_error(429, {"Retry-After": "3"}), FakeResponse([ROW], {}))
        self.assertEqual((got["lookup"], got["attempts"], calls), ("found", 2, 2))
        self.assertEqual([c.args[0] for c in llm._pause.call_args_list], [3.0])

    def test_a_429_that_stays_a_429_is_an_error_with_the_status_and_both_tries(self):
        got, calls = self.lookup(http_error(429, {"Retry-After": "99"}), http_error(429))
        self.assertEqual((got["lookup"], got["status"], got["attempts"], calls), ("error", 429, 2, 2))
        self.assertLessEqual(llm._pause.call_args_list[0].args[0], 10.0, "never holds the cycle for the full Retry-After")

    def test_a_timeout_and_an_unreadable_body_are_errors_too(self):
        got, _ = self.lookup(urllib.error.URLError(TimeoutError()), urllib.error.URLError(TimeoutError()))
        self.assertEqual((got["lookup"], got["error"], got["status"]), ("error", "timeout", None))

        class Garbage(FakeResponse):
            def read(self):
                return b"<html>"
        got, _ = self.lookup(Garbage([], {}))
        self.assertEqual((got["lookup"], got["error"]), ("error", "unreadable_body"))

    def test_end_user_info_still_returns_the_row_or_none_and_leaves_how_it_went(self):
        with mock.patch.object(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse([ROW], {})):
            self.assertEqual(llm.end_user_info("vd-judge")["budget_id"], "bud")
        self.assertEqual(llm.take_lookup()["lookup"], "found")
        self.assertIsNone(llm.take_lookup(), "read once")
        with mock.patch.object(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse([], {})):
            self.assertIsNone(llm.end_user_info("vd-judge"))
        self.assertEqual(llm.take_lookup()["lookup"], "absent")


class HeadroomReadTest(Env):
    CFG = {"end_user_prefix": "vd-", "end_user_limit_usd": "0.10", "headroom_max_fraction": 0.8, "end_user_budget": "bud", "canary_user": "vd-canary", "send_user": True}

    def read(self, answers):
        """answers: name -> HTTP error code, or a row, or None for an answer with no row."""
        def urlopen(request, timeout=None):
            name = request.full_url.rsplit("=", 1)[1]
            got = answers.get(name)
            if isinstance(got, int):
                raise http_error(got)
            return FakeResponse([got] if got else [], {})
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            return llm.read_headroom(cfg={**llm.otari_config(), **self.CFG})

    def rows(self, **over):
        rows = {f"vd-{r}": {"external_id": f"vd-{r}", "budget_id": "bud", "spend": 0.001, "reserved": 0.0} for r in llm.otari_config()["roles"]}
        rows["vd-canary"] = {"external_id": "vd-canary", "budget_id": "bud", "spend": 0.002, "reserved": 0.0}
        rows.update(over)
        return rows

    def test_a_judge_that_this_key_has_not_created_yet_has_room_and_the_read_says_why(self):
        h = self.read(self.rows(**{"vd-judge": None, "vd-sandbox": None}))
        mine = h["users"]["vd-judge"]
        self.assertEqual((mine["found"], mine["lookup"], mine["status"]), (False, "absent", 200))
        self.assertTrue(h["judge_ok"], h["reason"])
        self.assertIn("no record of the judge's end user", h["reason"])
        self.assertEqual(h["read"], {"asked": 10, "found": 8, "absent": 2, "failed": 0})

    def test_a_judge_lookup_that_failed_keeps_the_standard_route_and_names_the_status(self):
        h = self.read(self.rows(**{"vd-judge": 403}))
        self.assertEqual((h["users"]["vd-judge"]["lookup"], h["users"]["vd-judge"]["status"]), ("error", 403))
        self.assertFalse(h["judge_ok"])
        self.assertIn("HTTP 403", h["reason"])
        self.assertEqual(h["read"], {"asked": 10, "found": 9, "absent": 0, "failed": 1})

    def test_the_sealed_numbers_alone_give_the_same_answer_on_replay(self):
        for judge, expected in (({"found": False, "lookup": "absent", "status": 200}, True), ({"found": False, "lookup": "error", "status": 429}, False), ({"found": False}, False)):
            h = {"limit_usd": "0.10", "max_fraction": 0.8, "judge_user": "vd-judge", "users": {"vd-judge": judge}}
            self.assertEqual(llm.headroom_ok(h)[0], expected, judge)
        self.assertTrue(llm.headroom_ok(headroom("0.01"))[0])


class AuditBudgetRowsTest(unittest.TestCase):
    """Rows 3 and 4 cite a read that worked, and say how many end users were found out of the ones asked."""

    @staticmethod
    def ledger():
        from tests.test_otari_report import LEDGER
        return json.loads(json.dumps(LEDGER))

    def break_judge(self, d, lookup=None):
        h = d["llm"]["otari"]["headroom"]
        h["users"][h["judge_user"]] = {"found": False, **({"lookup": lookup, "status": 200 if lookup == "absent" else 429} if lookup else {})}
        h["judge_ok"] = False

    def rows(self, ledger):
        from verdict_desk import otari_report
        return {r["n"]: r for r in otari_report.audit(ledger, [])}

    def test_a_newest_dossier_whose_judge_read_failed_is_not_cited_for_the_budget(self):
        ledger = self.ledger()
        self.break_judge(ledger[-1], "error")
        rows = self.rows(ledger)
        self.assertEqual(rows[3]["dossier"], ledger[-2]["seq"])
        self.assertNotIn("None", rows[3]["note"])
        self.assertRegex(rows[3]["note"], r"USD 0\.\d+ of USD 0\.10")

    def test_an_absent_judge_is_not_a_read_of_its_spend_either(self):
        ledger = self.ledger()
        self.break_judge(ledger[-1], "absent")
        self.assertEqual(self.rows(ledger)[3]["dossier"], ledger[-2]["seq"])

    def test_row_four_prints_found_out_of_asked_and_prefers_the_newest_complete_read(self):
        ledger = self.ledger()
        for name in ("vd-sandbox", "vd-mcp"):
            ledger[-1]["llm"]["otari"]["headroom"]["users"][name] = {"found": False, "lookup": "absent", "status": 200}
        rows = self.rows(ledger)
        self.assertEqual(rows[4]["dossier"], ledger[-2]["seq"])
        self.assertIn("10 of 10 asked were found", rows[4]["note"])

    def test_when_no_read_is_complete_row_four_names_the_gap_instead_of_hiding_it(self):
        ledger = self.ledger()
        for d in ledger:
            h = ((d.get("llm") or {}).get("otari") or {}).get("headroom")
            if h:
                h["users"]["vd-sandbox"] = {"found": False, "lookup": "absent", "status": 200}
                h["users"]["vd-mcp"] = {"found": False, "lookup": "error", "status": 429}
        row = self.rows(ledger)[4]
        self.assertIn("8 of 10 asked were found", row["note"])
        self.assertIn("1 not created yet", row["note"])
        self.assertIn("1 lookups failed", row["note"])
        self.assertEqual(row["status"], "PASS")



class InFlightKeyTest(Env):
    """HTTP 409 "Idempotency-Key in progress": the request is still running at Otari after the edge cut our connection. Wait for it on the
    same key (the stored reply is replayed) instead of giving up, or sending a second request that would bill the same work twice."""

    def transport(self, script):
        """script: one entry per request, an HTTP status int (an error) or 'ok'. Returns (urlopen, keys seen)."""
        from tests.test_otari import BODY, HEADERS
        keys, queue = [], list(script)

        def urlopen(request, timeout=None):
            keys.append(request.get_header("Idempotency-key"))
            step = queue.pop(0)
            if step == "ok":
                return FakeResponse(BODY, HEADERS)
            body = b'{"detail":"A request with this Idempotency-Key is still in progress. Retry with the same key to get its result."}' if step == 409 else b"{}"
            raise urllib.error.HTTPError(request.full_url, step, "x", message({"Retry-After": "5"} if step in (409, 429) else {}), io.BytesIO(body))
        return urlopen, keys

    def test_a_request_that_finishes_after_many_polls_is_answered_from_the_same_key(self):
        polls = llm.MAX_RETRIES + 5  # more waits than the retries a failing call gets
        urlopen, keys = self.transport([504] + [409] * polls + ["ok"])
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            text = llm.chat("s", "u", role="narrator")
        self.assertTrue(text)
        self.assertEqual((len(keys), len(set(keys))), (polls + 2, 1), "one logical call, one key, every try")
        receipt = llm.served_receipt()
        self.assertEqual(receipt["client_attempts"], polls + 2)
        self.assertNotIn("refusal", receipt)

    def test_polls_do_not_use_up_the_retries_of_real_failures(self):
        script = [409] * 6 + [429] * llm.MAX_RETRIES + ["ok"]
        urlopen, keys = self.transport(script)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertTrue(llm.chat("s", "u", role="narrator"))
        self.assertEqual(len(keys), len(script))
        urlopen, keys = self.transport([429] * (llm.MAX_RETRIES + 1))
        with mock.patch.object(urllib.request, "urlopen", urlopen), self.assertRaises(llm.OtariError):
            llm.chat("s", "u", role="narrator")
        self.assertEqual(len(keys), llm.MAX_RETRIES + 1, "a real failure still stops after MAX_RETRIES")

    def test_a_key_that_never_finishes_gives_up_inside_the_wait_budget_with_the_409_on_record(self):
        urlopen, keys = self.transport([409] * 200)
        with mock.patch.object(urllib.request, "urlopen", urlopen), self.assertRaises(llm.OtariError) as ctx:
            llm.chat("s", "u", role="narrator")
        self.assertEqual(ctx.exception.status, 409)
        waited = sum(c.args[0] for c in llm._pause.call_args_list)
        self.assertLessEqual(waited, llm.IN_PROGRESS_BUDGET)
        self.assertEqual(len(set(keys)), 1)
        r = llm.served_receipt()
        self.assertEqual((r["refusal"]["status"], r["client_attempts"]), (409, len(keys)))

    def test_the_servers_retry_after_is_the_floor_of_each_wait(self):
        urlopen, _ = self.transport([409, "ok"])

        def slow(request, timeout=None):
            try:
                return urlopen(request, timeout)
            except urllib.error.HTTPError as err:
                raise urllib.error.HTTPError(err.url, 409, "x", message({"Retry-After": "12"}), io.BytesIO(b'{"detail":"still in progress"}'))
        with mock.patch.object(urllib.request, "urlopen", slow):
            llm.chat("s", "u", role="narrator")
        self.assertEqual([c.args[0] for c in llm._pause.call_args_list], [12.0])

    def test_a_409_that_is_not_about_a_key_in_progress_is_not_waited_for(self):
        def other(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 409, "x", message({}), io.BytesIO(b'{"detail":"conflict of another kind"}'))
        with mock.patch.object(urllib.request, "urlopen", other), self.assertRaises(llm.OtariError):
            llm.chat("s", "u", role="narrator")
        llm._pause.assert_not_called()


class NarratorBatchTest(unittest.TestCase):
    def test_batches_are_small_and_the_otari_token_ceiling_leaves_room_without_reserving_a_fortune(self):
        from verdict_desk import narrative
        self.assertLessEqual(narrative.CHUNK, 6, "a slow batch (25 to 30 s with ten headlines) is cut by the edge at about 30 s")
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": "k" * 20}):
            self.assertEqual(narrative.max_tokens(), narrative.OTARI_MAX_TOKENS)
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            self.assertEqual(narrative.max_tokens(), narrative.LLM_MAX_TOKENS)
        self.assertGreaterEqual(narrative.OTARI_MAX_TOKENS, 2000, "the longest answer seen for ten headlines was 1066 tokens")
        self.assertLess(narrative.OTARI_MAX_TOKENS, narrative.LLM_MAX_TOKENS)


@unittest.skipUnless(NODE, "node is not installed")
class VerifyPageTest(unittest.TestCase):
    def test_the_browser_verifier_reads_the_ledger_from_either_layout_and_agrees_with_itself(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "verify_page.test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout[-3000:] + out.stderr[-1000:])


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)
