"""The Otari drills, the service-key defaults of the real config, the guardrail header and the lookups. No network."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_otari import BODY, HEADERS, FakeResponse, message, served_spelling
from verdict_desk import __main__ as cli
from verdict_desk import llm, otari_drill

KEY = "unit-test-secret-0123456789"
GUARD = '[{"profile":"prompt-injection","mode":"monitor","valid":null,"score":null}]'
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "OTARI_USE_POLICIES": "", "OTARI_CYCLE_CAP_USD": "",
       "OTARI_POLICY_JUDGE": "", "OTARI_POLICY_BULL": "", "OTARI_USER_JUDGE": "", "OTARI_END_USER_BUDGET_JUDGE": "", "OTARI_MAX_TOKENS_JUDGE": "",
       "OTARI_MIN_INTERVAL": ""}


def ledger_of(n: int, size: int = 50_000) -> list[dict]:
    return [{"seq": i, "evidence": {"symbol": "BTCUSDT", "blob": "x" * size, "i": i}} for i in range(1, n + 1)]


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause")):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()


class RealConfigTest(Env):
    """The repo config as the app runs it: a service key, roles billed to their own end users, the judge on its policy."""

    def fake(self, headers=None):
        self.sent = []

        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode("utf-8"))
            self.sent.append((request, payload))
            return FakeResponse(BODY, {**HEADERS, "Otari-Model": served_spelling(payload["model"]), **(headers or {})})
        return mock.patch.object(urllib.request, "urlopen", urlopen)

    def test_every_role_is_billed_to_its_own_vd_end_user_on_the_vd_analyst_budget(self):
        cfg = llm.otari_config()
        with self.fake():
            for role in cfg["roles"]:
                llm.chat("s", "u", role=role)
        self.assertEqual([p["user"] for _, p in self.sent], [f"vd-{r}" for r in cfg["roles"]])
        self.assertEqual({r.get_header("Otari-end-user-budget") for r, _ in self.sent}, {cfg["end_user_budget"]})
        self.assertRegex(cfg["end_user_budget"], r"^[0-9a-f-]{36}$")

    def test_the_judge_goes_through_the_vd_judge_policy_and_the_others_keep_their_catalog_id(self):
        cfg = llm.otari_config()
        with self.fake():
            llm.chat("s", "u", role="judge")
            llm.chat("s", "u", role="bull")
        self.assertEqual([p["model"] for _, p in self.sent], ["vd-judge", cfg["roles"]["bull"]["model"]])

    def test_the_judge_receipt_keeps_the_policy_the_served_model_the_guardrail_and_the_end_user(self):
        with self.fake(headers={"Otari-Guardrails": GUARD, "Otari-End-User-Budget": llm.otari_config()["end_user_budget"]}):
            llm.chat("s", "u", role="judge")
        r = llm.served_receipt()
        self.assertEqual((r["sent_model"], r["served_model"], r["end_user"]), ("vd-judge", served_spelling("vd-judge"), "vd-judge"))
        self.assertEqual(r["guardrails_raw"], GUARD)
        self.assertEqual(r["guardrails"], [{"profile": "prompt-injection", "mode": "monitor", "valid": None, "score": None}])
        self.assertEqual(r["end_user_budget"], llm.otari_config()["end_user_budget"])

    def test_the_cycle_cap_is_a_couple_of_cents_and_the_drill_ids_are_in_the_config(self):
        cfg = llm.otari_config()
        self.assertEqual((str(llm.cycle_cap(cfg)), str(llm.cycle_cap(cfg, tools=True))), ("0.02", "0.06"))
        self.assertEqual((cfg["drill_user"], cfg["drill_policy"]), ("vd-drill", "vd-judge-drill"))
        self.assertNotEqual(cfg["drill_budget"], cfg["end_user_budget"])

    def test_a_guardrail_header_that_is_not_json_is_kept_raw_only(self):
        r = llm.parse_receipt({**HEADERS, "Otari-Guardrails": "prompt-injection=pass"}, BODY, "judge", "vd-judge")
        self.assertEqual(r["guardrails_raw"], "prompt-injection=pass")
        self.assertNotIn("guardrails", r)

    def test_the_key_never_reaches_a_receipt_or_the_config(self):
        self.assertNotIn(KEY, llm.otari_config()["end_user_budget"])
        text = (Path(__file__).resolve().parent.parent / "config" / "otari.json").read_text()
        self.assertNotRegex(text, r"o" + "tk_|tk-[A-Za-z0-9]{10}")


class LookupTest(Env):
    def test_usage_for_waits_for_the_settlement_and_returns_the_row_summary(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", message({}), io.BytesIO(b"{}"))
            return FakeResponse({"request_id": "req-1", "status": "success", "row_count": 2, "cost_usd": "0.002400"}, {})
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            row = llm.usage_for("req-1")
        self.assertEqual((row["row_count"], len(calls)), (2, 2))
        self.assertTrue(calls[0].endswith("/usage/requests/req-1"))

    def test_usage_for_gives_up_quietly_on_a_real_error_or_without_a_key(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", message({}), io.BytesIO(b"{}"))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertIsNone(llm.usage_for("req-1"))
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            self.assertIsNone(llm.usage_for("req-1"))
        self.assertIsNone(llm.usage_for(None))

    def test_end_user_info_keeps_the_budget_fields_of_the_asked_user_only(self):
        rows = [{"external_id": "other", "budget_id": "x"}, {"external_id": "vd-drill", "budget_id": "b1", "spend": 1e-6, "owner_user_id": "svc_secret", "current_requests": 1}]
        with mock.patch.object(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse(rows, {})):
            info = llm.end_user_info("vd-drill")
        self.assertEqual((info["budget_id"], info["spend"], info["current_requests"]), ("b1", 1e-6, 1))
        self.assertNotIn("owner_user_id", info)
        with mock.patch.object(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse([], {})):
            self.assertIsNone(llm.end_user_info("vd-drill"))


class FallbackDrillTest(Env):
    POLICY = "vd-judge-drill"

    def ask_ok(self, attempts=2, fallback=True):
        calls = []

        def ask(system, user, **kw):
            calls.append(kw)
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "judge", "request_id": "req-fb", "sent_model": kw["model"], "served_model": "deepseek-ai/DeepSeek-V4-Flash-0731",
                                   "served_by": "deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731", "attempt_count": attempts, "fallback": fallback}
            return "bull"
        ask.calls = calls
        return ask

    def test_the_pack_is_long_newest_first_and_hashed(self):
        text, info = otari_drill.long_pack(ledger_of(10), target=120_000)
        self.assertGreaterEqual(info["chars"], 120_000)
        self.assertTrue(text.startswith("Dossier 10 evidence"))
        self.assertEqual(info["source"], "frozen evidence of dossiers 8 to 10 of the ledger")
        self.assertEqual(otari_drill.long_pack(ledger_of(10), target=120_000)[1]["sha256"], info["sha256"])
        self.assertEqual(len(info["sha256"]), 64)

    def test_the_real_pack_is_over_the_32k_token_context_and_under_a_cent(self):
        _, info = otari_drill.long_pack(ledger_of(12, 60_000))
        self.assertGreater(info["chars"] / 4.2, 32_768)
        self.assertLess(info["estimated_cost_usd"], 0.005)

    def test_a_receipt_with_two_attempts_and_a_fallback_passes_and_names_the_policy(self):
        ask = self.ask_ok()
        usage = lambda rid: {"request_id": rid, "row_count": 2, "status": "success"}
        with mock.patch.object(llm, "usage_for", usage):
            record = otari_drill.fallback_drill(ledger_of(8), ask=ask, usage=usage)
        self.assertTrue(record["pass"])
        self.assertEqual((ask.calls[0]["model"], ask.calls[0]["role"], ask.calls[0]["max_tokens"]), (self.POLICY, "judge", 16))
        cfg = llm.otari_config()
        self.assertEqual((ask.calls[0]["end_user"], ask.calls[0]["end_user_budget"]), ("vd-drill-fallback", cfg["end_user_budget"]))
        self.assertNotEqual(ask.calls[0]["end_user"], llm._end_user("judge", cfg), "the drill must not bill the production judge")
        self.assertEqual(record["end_user"], "vd-drill-fallback")
        self.assertEqual((record["receipt"]["attempt_count"], record["usage"]["row_count"], record["policy"]), (2, 2, self.POLICY))
        json.dumps(record)

    def test_a_reply_without_a_fallback_does_not_pass(self):
        record = otari_drill.fallback_drill(ledger_of(8), ask=self.ask_ok(attempts=1, fallback=False), usage=lambda rid: None)
        self.assertFalse(record["pass"])

    def test_a_prompt_that_would_cost_too_much_is_never_sent(self):
        ask = self.ask_ok()
        with mock.patch.object(otari_drill, "MAX_PROMPT_COST_USD", 0.0000001):
            record = otari_drill.fallback_drill(ledger_of(8), ask=ask, usage=lambda rid: None)
        self.assertFalse(record["pass"])
        self.assertEqual(ask.calls, [])
        self.assertIn("nothing was sent", record["error"])

    def test_an_error_is_recorded_with_its_receipt_not_raised(self):
        def ask(system, user, **kw):
            llm._served.receipt = {"schema": "otari-receipt/1", "request_id": "req-x", "error": {"status": 502}}
            raise llm.OtariError("HTTP 502", status=502)
        record = otari_drill.fallback_drill(ledger_of(8), ask=ask, usage=lambda rid: None)
        self.assertFalse(record["pass"])
        self.assertEqual(record["receipt"]["request_id"], "req-x")


class BudgetDrillTest(Env):
    def make_ask(self, second="refuse"):
        calls = []

        def ask(system, user, **kw):
            calls.append(kw)
            if len(calls) == 1:
                llm._served.receipt = {"schema": "otari-receipt/1", "role": "judge", "request_id": "req-small", "end_user": kw["end_user"], "cost_usd": "0.000001"}
                return "ok"
            if second == "pass":
                llm._served.receipt = {"schema": "otari-receipt/1", "request_id": "req-big"}
                return "ok"
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "judge", "request_id": "req-refused", "end_user": kw["end_user"],
                                   "refusal": {"status": 403, "code": "budget_exceeded", "budget_scope": "user", "retry_after": "40000"}}
            raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded", scope="user", retry_after="40000", request_id="req-refused")
        ask.calls = calls
        return ask

    def run_drill(self, ask):
        infos = iter([{"external_id": "vd-drill", "spend": 0.0}, {"external_id": "vd-drill", "spend": 1e-6, "current_requests": 1}])
        return otari_drill.budget_drill(ask=ask, usage=lambda rid: {"request_id": rid, "status": "refused"}, end_user_info=lambda user: next(infos))

    def test_a_small_call_passes_then_the_big_one_is_refused_and_kept_as_a_receipt(self):
        ask = self.make_ask()
        record = self.run_drill(ask)
        cfg = llm.otari_config()
        self.assertTrue(record["pass"])
        self.assertEqual([(c["end_user"], c["end_user_budget"]) for c in ask.calls], [("vd-drill", cfg["drill_budget"])] * 2)
        self.assertEqual([c["max_tokens"] for c in ask.calls], [16, otari_drill.BIG_MAX_TOKENS])
        self.assertTrue(all(c["model"] == llm.role_model("judge") for c in ask.calls), "the drill calls the model, not the policy")
        big = record["big_call"]
        self.assertEqual((big["error"]["status"], big["error"]["code"], big["error"]["budget_scope"]), (403, "budget_exceeded", "user"))
        self.assertEqual(big["receipt"]["refusal"]["code"], "budget_exceeded")
        self.assertEqual((record["end_user_before"]["spend"], record["end_user_after"]["current_requests"]), (0.0, 1))
        json.dumps(record)

    def test_a_big_call_that_goes_through_fails_the_drill(self):
        self.assertFalse(self.run_drill(self.make_ask(second="pass"))["pass"])

    def test_a_failing_small_call_stops_the_drill_without_the_big_one(self):
        calls = []

        def ask(system, user, **kw):
            calls.append(kw)
            raise llm.OtariError("HTTP 403 end_user_budget_not_allowed", status=403, code="end_user_budget_not_allowed")
        record = otari_drill.budget_drill(ask=ask, usage=lambda rid: None, end_user_info=lambda user: None)
        self.assertFalse(record["pass"])
        self.assertEqual(len(calls), 1)


class EndUsersCheckTest(Env):
    """The claim "each role is billed on vd-analyst" is only as good as this read of the end users."""

    def info(self, **overrides):
        want = llm.otari_config()["end_user_budget"]

        def read(name):
            row = {"external_id": name, "budget_id": want, "spend": 0.001, "current_requests": 2, "blocked": False}
            return overrides.get(name, row) if name in overrides else row
        return read

    def test_all_five_production_end_users_on_the_budget_pass_and_each_budget_id_is_recorded(self):
        record = otari_drill.end_users_check(self.info())
        self.assertTrue(record["pass"])
        self.assertEqual(sorted(record["end_users"]), sorted(f"vd-{r}" for r in llm.otari_config()["roles"]))
        self.assertTrue({"vd-bull", "vd-bear", "vd-judge", "vd-checker", "vd-narrator", "vd-searcher", "vd-sandbox", "vd-mcp"} <= set(record["end_users"]))
        self.assertEqual({v["budget_id"] for v in record["end_users"].values()}, {llm.otari_config()["end_user_budget"]})
        self.assertEqual(record["expected_budget_id"], llm.otari_config()["end_user_budget"])

    def test_an_end_user_born_without_a_budget_or_missing_fails_the_check(self):
        record = otari_drill.end_users_check(self.info(**{"vd-judge": {"external_id": "vd-judge", "budget_id": None, "spend": 0.0}, "vd-bull": None}))
        self.assertFalse(record["pass"])
        self.assertFalse(record["end_users"]["vd-judge"]["on_vd_analyst"])
        self.assertIsNone(record["end_users"]["vd-judge"]["budget_id"])
        self.assertFalse(record["end_users"]["vd-bull"]["found"])
        self.assertTrue(record["end_users"]["vd-bear"]["on_vd_analyst"])

    def test_the_check_reads_only_and_run_users_appends_one_record(self):
        path = Path(tempfile.mkdtemp()) / "e.jsonl"
        with mock.patch.object(llm, "end_user_info", self.info()):
            otari_drill.run("users", [], path)
        lines = path.read_text().splitlines()
        self.assertEqual((len(lines), json.loads(lines[0])["drill"]), (1, "end-users"))


class McpDrillTest(Env):
    def block(self, **kw):
        base = {"method": "otari_mcp", "mcp_calls": [{"tool": "execute"}], "prices": {"BTC": 77886.0}, "receipt": {"request_id": "req-m", "request_id_source": "otari", "cost_usd": "0.000087"}}
        return {**base, **kw}

    def test_a_tool_use_a_price_and_a_gateway_id_pass(self):
        record = otari_drill.mcp_drill(collect=lambda: self.block())
        self.assertTrue(record["pass"])
        self.assertEqual(record["block"]["receipt"]["request_id"], "req-m")

    def test_no_tool_call_no_price_or_a_client_id_fail(self):
        for kw in ({"mcp_calls": []}, {"prices": {}}, {"receipt": {"request_id": "client:x", "request_id_source": "client"}}):
            self.assertFalse(otari_drill.mcp_drill(collect=lambda kw=kw: self.block(**kw))["pass"], kw)


class RunAndCommandTest(Env):
    def test_run_appends_one_json_line_per_drill_and_never_touches_a_ledger(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "otari-drills.jsonl"
        fb = FallbackDrillTest().ask_ok()
        with mock.patch.object(llm, "usage_for", lambda rid: {"row_count": 2}), mock.patch.object(llm, "end_user_info", lambda user: None):
            records = otari_drill.run("fallback", ledger_of(8), path, ask=fb)
        lines = path.read_text().splitlines()
        self.assertEqual((len(records), len(lines)), (1, 1))
        self.assertEqual(json.loads(lines[0])["drill"], "fallback")
        self.assertEqual(list(folder.iterdir()), [path])

    def test_the_command_refuses_without_the_key(self):
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            opts = type("O", (), {"which": "all", "evidence": None, "ledger": Path("nope.jsonl")})()
            self.assertEqual(cli.cmd_otari_drill(opts), 2)

    def test_the_evidence_file_never_holds_the_key(self):
        path = Path(tempfile.mkdtemp()) / "e.jsonl"
        otari_drill.append(path, {"drill": "x", "receipt": llm.parse_receipt({**HEADERS, "Otari-Key": KEY}, BODY, "judge", "vd-judge")})
        self.assertNotIn(KEY, path.read_text())

from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
