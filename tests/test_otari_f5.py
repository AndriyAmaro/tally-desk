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
        from verdict_desk import otari_report
        ledger = self.ledger()
        for name in ("vd-sandbox", "vd-mcp"):
            ledger[-1]["llm"]["otari"]["headroom"]["users"][name] = {"found": False, "lookup": "absent", "status": 200}
        complete = [d["seq"] for d in otari_report.era(ledger)[:-1]
                    if (u := (d["llm"]["otari"].get("headroom") or {}).get("users")) and all(x.get("found") for x in u.values())]
        rows = self.rows(ledger)
        self.assertEqual(rows[4]["dossier"], max(complete), "the newest dossier before the edited one whose every asked user was found")
        self.assertRegex(rows[4]["note"], r"(\d+) of \1 asked were found")

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



class AuditFallbackRowTest(unittest.TestCase):
    def audit_row(self, edit=None):
        from tests.test_otari_report import LEDGER
        from verdict_desk import otari_report
        ledger = json.loads(json.dumps(LEDGER))
        if edit:
            edit(ledger)
        return {r["n"]: r for r in otari_report.audit(ledger, [])}[2], ledger

    def test_real_fallbacks_of_the_cycles_own_calls_come_first_and_the_canary_stays_as_the_deliberate_test(self):
        row, ledger = self.audit_row()
        from verdict_desk import otari_report
        real = [c["receipt"] for d in otari_report.era(ledger) for c in otari_report.calls_of(d) if c["label"] != "canary" and c["receipt"].get("fallback") and not c["receipt"].get("refusal")]
        self.assertGreaterEqual(len(real), 10, "the narrator batches of dossiers 800 and 801 fell back to the second provider")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["source"], "sealed")
        self.assertIn(f"{len(real)} real calls", row["note"])
        self.assertIn("deliberate test", row["note"])
        newest = max((d for d in otari_report.era(ledger) if any(c["receipt"].get("fallback") and c["label"] != "canary" for c in otari_report.calls_of(d))), key=lambda d: d["seq"])
        self.assertEqual(row["dossier"], newest["seq"], "the newest dossier with a real fallback")
        newest_real = next(c["receipt"] for c in reversed(otari_report.calls_of(newest)) if c["label"] != "canary" and c["receipt"].get("fallback"))
        if newest_real.get("policy") is None:
            self.assertIn("not the vd-judge policy", row["note"], "a fallback with no policy of ours is Otari's own failover: say so")
        else:
            self.assertIn(str(newest_real["policy"]), row["note"])
        canary_id = next(d["llm"]["otari"]["canary"]["receipt"]["request_id"] for d in reversed(otari_report.era(ledger)) if (d["llm"]["otari"].get("canary") or {}).get("passed"))
        self.assertEqual(row["proof"][0] in {r["request_id"] for r in real}, True, "the first proof is a real call")
        self.assertIn(canary_id, row["proof"], "the canary is cited too")

    def test_with_no_real_fallback_the_canary_carries_the_row_and_says_it_is_the_deliberate_test(self):
        def edit(ledger):
            from verdict_desk import otari_report
            for d in otari_report.era(ledger):
                for c in otari_report.calls_of(d):
                    if c["label"] != "canary":
                        c["receipt"]["fallback"] = False
        row, _ = self.audit_row(edit)
        self.assertEqual((row["status"], row["source"]), ("PASS", "sealed"))
        self.assertIn("no real call fell back yet", row["note"])
        self.assertNotIn("real calls were", row["note"])

    def test_a_failed_call_or_a_call_without_an_otari_request_id_is_no_proof_of_a_fallback(self):
        def edit(ledger):
            from verdict_desk import otari_report
            for d in otari_report.era(ledger):
                for c in otari_report.calls_of(d):
                    if c["label"] != "canary" and c["receipt"].get("fallback"):
                        c["receipt"]["request_id_source"] = "client"
        row, _ = self.audit_row(edit)
        self.assertIn("no real call fell back yet", row["note"])



class WebEvidenceUseTest(unittest.TestCase):
    @staticmethod
    def ledger():
        from tests.test_otari_report import LEDGER
        return json.loads(json.dumps(LEDGER))

    def row6(self, ledger):
        from verdict_desk import otari_report
        return {r["n"]: r for r in otari_report.audit(ledger, [])}[6]

    def test_the_web_proof_cites_the_newest_dossier_where_the_analysts_cited_what_was_found(self):
        ledger = self.ledger()
        row = self.row6(ledger)

        def tier(d):
            use = ((d.get("llm") or {}).get("otari") or {}).get("evidence_use") or {}
            web = (d.get("evidence") or {}).get("web") or {}
            page = web.get("page") or {}
            done = page.get("otari_reported_fetch") and page.get("excerpt") and any(t.get("type") == "web_search" and t.get("status") == "completed" for t in (web.get("receipt") or {}).get("tool_calls") or [])
            return (bool(use.get("web_excerpts_cited")), bool(use.get("web_items_cited"))) if done else None
        tiers = {d["seq"]: tier(d) for d in ledger if tier(d) is not None}
        best = max(tiers.values())
        self.assertEqual(row["dossier"], max(seq for seq, t in tiers.items() if t == best), "the newest dossier of the best tier")
        self.assertRegex(row["note"], r"the analysts cited \d+ of \d+ source titles" if any(best) else "cited none of what was found")

    def test_a_cited_excerpt_beats_a_cited_title_and_the_audit_picks_it_up_by_itself(self):
        ledger = self.ledger()
        target = ledger[-1]
        target["llm"]["otari"]["evidence_use"] = {"web_items": 3, "web_items_cited": 0, "web_excerpts": 1, "web_excerpts_cited": 0, "mcp_items": 1, "mcp_items_cited": 0}
        self.assertNotEqual(self.row6(ledger)["dossier"], target["seq"], "newest, but nothing cited")
        older = ledger[-3]
        older["llm"]["otari"]["evidence_use"].update(web_excerpts=1, web_excerpts_cited=1)
        older["evidence"]["web"]["page"].update(otari_reported_fetch=True, excerpt="x" * 40)
        row = self.row6(ledger)
        self.assertEqual(row["dossier"], older["seq"])
        self.assertIn("1 of 1 page excerpts", row["note"])

    def test_when_nothing_was_ever_cited_the_row_says_so_instead_of_implying_use(self):
        ledger = self.ledger()
        for d in ledger:
            use = ((d.get("llm") or {}).get("otari") or {}).get("evidence_use")
            if use:
                use.update(web_items_cited=0, web_excerpts_cited=0)
        row = self.row6(ledger)
        self.assertEqual(row["status"], "PASS")
        self.assertIn("cited none of what was found", row["note"])

    def test_the_fetch_asks_for_the_passage_about_price_or_news_and_the_analyst_note_names_the_excerpt_as_an_item_of_its_own(self):
        from verdict_desk import otari_web
        self.assertIn("price or market news", otari_web.FETCH_SYSTEM)
        self.assertIn("verbatim", otari_web.FETCH_SYSTEM)
        self.assertIn("Web page excerpt", llm.NEWS_NOTE)
        self.assertIn("never cite an item only to have cited it", llm.NEWS_NOTE, "the note still asks for judgment, not for citations")
        web = {"items": [], "page": {"domain": "d.example", "excerpt": "Bitcoin traded at 69,000 on Monday.", "date_status": "undated"}}
        self.assertEqual([t.split(" ")[0:3] for t in otari_web.index_texts(web)], [["Web", "page", "excerpt"]])



class ReplayTest(unittest.TestCase):
    def test_every_dossier_of_the_otari_era_still_replays_to_its_recorded_verdict(self):
        out = subprocess.run(["python3", "-m", "verdict_desk", "replay"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout[-800:] + out.stderr[-500:])
        for seq in range(792, 802):
            self.assertRegex(out.stdout, rf"#{seq} replay MATCHES")


@unittest.skipUnless(NODE, "node is not installed")
class VerifyPageTest(unittest.TestCase):
    def test_the_browser_verifier_reads_the_ledger_from_either_layout_and_agrees_with_itself(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "verify_page.test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout[-3000:] + out.stderr[-1000:])



class TimestampHelperTest(unittest.TestCase):
    """The age check of the archived OpenTimestamps proofs forgives a proof that is old and still pending in the tree only when the public
    calendars really hold its Bitcoin attestation; anything else keeps the strict path."""

    def run_with(self, upgrade_rc, info_text="", error=None):
        from tests import test_timestamp as t

        def fake(args, **kw):
            if error:
                raise error
            return subprocess.CompletedProcess(args, upgrade_rc if args[1] == "upgrade" else 0, info_text if args[1] == "info" else "", "")
        with mock.patch.object(t.subprocess, "run", fake), mock.patch.object(t.shutil, "copyfile"):
            return t.TimestampTest.calendars_can_upgrade(Path("/nonexistent/794.json.ots"))

    def test_a_bitcoin_attestation_from_the_calendars_makes_an_old_pending_proof_sound(self):
        self.assertTrue(self.run_with(0, "verify BitcoinBlockHeaderAttestation(974212)"))

    def test_a_proof_the_calendars_cannot_complete_or_a_host_that_cannot_reach_them_stays_strict(self):
        self.assertFalse(self.run_with(1, "verify PendingAttestation('https://x')"))
        self.assertFalse(self.run_with(0, "verify PendingAttestation('https://x')"))
        self.assertFalse(self.run_with(0, error=subprocess.TimeoutExpired("ots", 90)))
        self.assertFalse(self.run_with(0, error=OSError("no network")))



class NextDossierCannotBreakTheSuiteTest(unittest.TestCase):
    """The ledger grows every three hours. Append one synthetic dossier of each kind the scheduled job can write (a plain one, one with a
    real fallback on an analyst call, one whose headroom read failed, one with a flagged checker mark under v7) to a copy of the real ledger
    and run every reader of the ledger over it: the chain, the replay, the audit, the scoreboard, the README blocks and who-saw."""

    @staticmethod
    def appended():
        import copy
        from verdict_desk import dossier, otari_report
        real = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        base = next(d for d in reversed(real) if d["verdict"].get("rules_version") == "v7" and d.get("llm", {}).get("otari"))
        kinds = {}
        plain = copy.deepcopy(base)
        kinds["plain"] = plain
        fb = copy.deepcopy(base)
        fb["llm"]["bull"]["receipt"].update(fallback=True, attempt_count=2, served_by="bedrock:openai/gpt-oss-120b")
        kinds["analyst fallback"] = fb
        failed = copy.deepcopy(base)
        h = failed["llm"]["otari"]["headroom"]
        h["users"][h["judge_user"]] = {"found": False, "lookup": "error", "status": 429, "attempts": 2}
        kinds["failed lookup"] = failed
        flagged = copy.deepcopy(base)
        flagged["llm"]["checker"] = {"advisory": True, "checks": [{"label": "B1", "supported": False, "note": "x"}], "checked": 1, "supported": 0, "claims": 1}
        kinds["flagged mark"] = flagged
        out = list(real)
        for d in kinds.values():  # re-seal each as the next link of the chain: only receipts and marks differ, so the verdicts still replay
            body = {k: v for k, v in d.items() if k != "hash"}
            body["seq"], body["prev_hash"] = out[-1]["seq"] + 1, out[-1]["hash"]
            out.append({**body, "hash": dossier.digest(body)})
        return real, out, otari_report

    def test_every_reader_of_the_ledger_accepts_each_kind_of_new_dossier(self):
        from verdict_desk import dossier, scoreboard
        real, out, otari_report = self.appended()
        prev = dossier.GENESIS
        for d in out:
            self.assertEqual(d["prev_hash"], prev)
            self.assertEqual(dossier.digest({k: v for k, v in d.items() if k != "hash"}), d["hash"])
            prev = d["hash"]
        for d in out[len(real):]:
            result = dossier.replay(d, rules_version="auto")
            self.assertIn(result["matches"], (True, False))  # never raises; whether a doctored mark matches is the gate test's business
        rows = otari_report.audit(out, otari_report.read_evidence())
        self.assertEqual([r["n"] for r in rows], list(range(1, 9)))
        self.assertTrue(all(r["status"] in ("PASS", "NOT PROVEN") for r in rows))
        self.assertTrue(otari_report.board(out))
        text = otari_report.refresh_readme((ROOT / "README.md").read_text(encoding="utf-8"), out, otari_report.read_evidence())
        self.assertIn("<!-- otari:proofs:end -->", text)
        for d in otari_report.era(out)[-4:]:
            self.assertTrue(otari_report.who_saw(d)["providers"])
        self.assertIsNotNone(scoreboard)

    def test_a_failed_lookup_in_the_newest_dossier_moves_the_budget_proof_to_the_newest_good_read(self):
        real, out, otari_report = self.appended()
        rows = {r["n"]: r for r in otari_report.audit(out, [])}
        failed_seq = real[-1]["seq"] + 3
        self.assertNotEqual(rows[3]["dossier"], failed_seq)
        self.assertNotIn("None", rows[3]["note"])


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)
