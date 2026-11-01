"""F2 in the app: routing policy names, per-role end users and budget headers, budget refusals as receipts, the cycle cap. No network."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from tests.test_otari import BODY, HEADERS, FakeResponse, message, plain_config, served_spelling
from verdict_desk import __main__ as cli
from verdict_desk import dossier, llm, narrative

KEY = "unit-test-secret-0123456789"
CLEAN = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "MISTRAL_API_KEY": "", "OTARI_USE_POLICIES": "",
         "OTARI_CYCLE_CAP_USD": "", "OTARI_POLICY_JUDGE": "", "OTARI_POLICY_BULL": "", "OTARI_USER_JUDGE": "",
         "OTARI_END_USER_BUDGET_JUDGE": "", "OTARI_MAX_TOKENS_JUDGE": "", "OTARI_MIN_INTERVAL": ""}


class Base(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, CLEAN),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
            mock.patch.object(llm, "_pause"),
            plain_config(),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()
        self.sent = []

    def fake(self, cost="0.000009", extra_headers=None):
        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode("utf-8"))
            self.sent.append((request, payload))
            body = {**BODY, "usage": {**BODY["usage"], "cost_usd": cost}}
            return FakeResponse(body, {**HEADERS, "Otari-Model": served_spelling(payload["model"]), **(extra_headers or {})})
        return mock.patch.object(urllib.request, "urlopen", urlopen)


class PolicyTest(Base):
    def test_without_policies_the_catalog_id_is_sent(self):
        self.assertEqual(llm.sent_model("judge"), "deepseek/deepseek-v4-flash-0731")

    def test_use_policies_sends_the_policy_name_of_the_role_that_has_one(self):
        with mock.patch.dict(os.environ, {"OTARI_USE_POLICIES": "1"}), self.fake():
            llm.chat("s", "u", role="judge")
            llm.chat("s", "u", role="bull")
        self.assertEqual([p["model"] for _, p in self.sent], ["vd-judge", "openai/gpt-oss-120b"])

    def test_the_receipt_keeps_the_policy_as_sent_and_the_model_that_answered_as_served(self):
        with mock.patch.dict(os.environ, {"OTARI_USE_POLICIES": "1"}), self.fake(extra_headers={"Otari-Attempt-Count": "2", "Otari-Fallback": "true", "Otari-Provider": "nebius"}):
            llm.chat("s", "u", role="judge")
        r = llm.served_receipt()
        self.assertEqual((r["sent_model"], r["served_model"], r["fallback"], r["attempt_count"]), ("vd-judge", served_spelling("vd-judge"), True, 2))

    def test_one_role_can_be_pointed_at_a_drill_policy_by_the_environment(self):
        with mock.patch.dict(os.environ, {"OTARI_POLICY_JUDGE": "vd-judge-drill"}), self.fake():
            llm.chat("s", "u", role="judge")
            llm.chat("s", "u", role="bear")
        self.assertEqual(self.sent[0][1]["model"], "vd-judge-drill")
        self.assertEqual(self.sent[1][1]["model"], llm.role_model("bear"))

    def test_a_policy_name_with_a_space_is_refused_before_any_request(self):
        with mock.patch.dict(os.environ, {"OTARI_POLICY_JUDGE": "vd judge"}), mock.patch.object(urllib.request, "urlopen") as opened:
            with self.assertRaises(llm.LLMError):
                llm.chat("s", "u", role="judge")
            opened.assert_not_called()

    def test_the_dossier_summary_lists_what_each_role_sends(self):
        with mock.patch.dict(os.environ, {"OTARI_USE_POLICIES": "1"}):
            s = llm.otari_summary()
        self.assertEqual((s["sent"]["judge"], s["sent"]["bull"]), ("vd-judge", "openai/gpt-oss-120b"))
        self.assertEqual(s["roles"]["judge"], "deepseek/deepseek-v4-flash-0731")


class EndUserTest(Base):
    def test_no_user_field_and_no_budget_header_with_an_ordinary_key(self):
        with mock.patch.dict(os.environ, {"OTARI_END_USER_BUDGET_JUDGE": "vd-drill-tiny"}), self.fake():
            llm.chat("s", "u", role="judge")
        request, payload = self.sent[0]
        self.assertNotIn("user", payload)
        self.assertIsNone(request.get_header("Otari-end-user-budget"))

    def test_send_user_names_the_role_and_the_budget_header_rides_with_it(self):
        env = {"OTARI_SEND_USER": "1", "OTARI_END_USER_BUDGET_JUDGE": "vd-analyst-day"}
        with mock.patch.dict(os.environ, env), self.fake(extra_headers={"Otari-End-User-Budget": "vd-analyst-day"}):
            llm.chat("s", "u", role="judge")
            llm.chat("s", "u", role="bull")
        (judge_req, judge), (bull_req, bull) = self.sent
        self.assertEqual((judge["user"], bull["user"]), ("judge", "bull"))
        self.assertEqual(judge_req.get_header("Otari-end-user-budget"), "vd-analyst-day")
        self.assertIsNone(bull_req.get_header("Otari-end-user-budget"))
        r = llm.served_receipt()
        self.assertEqual((r["end_user"], r["end_user_budget"]), ("bull", "vd-analyst-day"))

    def test_a_drill_user_and_budget_and_token_limit_can_be_set_for_one_role(self):
        env = {"OTARI_USER_JUDGE": "judge-drill", "OTARI_END_USER_BUDGET_JUDGE": "vd-drill-tiny", "OTARI_MAX_TOKENS_JUDGE": "8000"}
        with mock.patch.dict(os.environ, env), self.fake():
            llm.chat("s", "u", role="judge", max_tokens=100)
        request, payload = self.sent[0]
        self.assertEqual((payload["user"], payload["max_tokens"], request.get_header("Otari-end-user-budget")), ("judge-drill", 8000, "vd-drill-tiny"))


class CycleCapTest(Base):
    def test_the_cap_stops_calls_once_the_cycle_has_spent_it_and_no_request_is_sent(self):
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.0005"}), self.fake(cost="0.000300"):
            llm.chat("s", "u", role="bull")
            llm.chat("s", "u", role="bear")  # 0.0003 spent, under the cap: still sent
            before = len(self.sent)
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="judge")  # 0.0006 spent: refused here
            self.assertEqual(len(self.sent), before)
        err = ctx.exception
        self.assertEqual((err.code, err.scope), ("cycle_cap_exceeded", "cycle"))
        r = llm.served_receipt()
        self.assertEqual(r["refusal"], {"status": None, "code": "cycle_cap_exceeded", "budget_scope": "cycle", "retry_after": None, "source": "client"})
        self.assertEqual((r["role"], r["request_id"]), ("judge", None))
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.0005"}):
            self.assertEqual(llm.cycle_status(), {"cap_usd": "0.0005", "spent_usd": "0.000600", "calls": 2, "refused": 1, "unpriced_calls": 0})

    def test_a_new_cycle_starts_from_zero(self):
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.0001"}), self.fake(cost="0.000300"):
            llm.chat("s", "u", role="bull")
            with self.assertRaises(llm.OtariError):
                llm.chat("s", "u", role="bear")
            llm.reset_cycle()
            llm.chat("s", "u", role="bear")
        self.assertEqual(len(self.sent), 2)

    def test_the_config_default_cap_is_a_few_cents_and_zero_or_junk_means_no_cap(self):
        self.assertEqual(str(llm.cycle_cap()), "0.05")
        for raw in ("0", "-1", "abc", "nan"):
            with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": raw}):
                self.assertIsNone(llm.cycle_cap(), raw)

    def test_an_unpriced_reply_is_counted_apart_and_never_crashes_the_cap(self):
        def urlopen(request, timeout=None):
            return FakeResponse({"choices": BODY["choices"], "usage": {"prompt_tokens": 1}}, HEADERS)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            llm.chat("s", "u", role="bull")
        self.assertEqual(llm.cycle_status()["unpriced_calls"], 1)

    def test_outside_otari_mode_there_is_no_cycle_status(self):
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            self.assertIsNone(llm.cycle_status())


class RefusalInTheDossierTest(Base):
    """A budget refusal on one role is recorded, shown and does not stop the decision or break the chain."""

    def setUp(self):
        super().setUp()
        self.pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.n = 0

    def urlopen(self, request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        system = payload["messages"][0]["content"]
        if "judge in a market debate" in system:
            h = message({"Otari-Request-ID": "req-refused", "Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "user", "Retry-After": "3600"})
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", h, io.BytesIO(b'{"detail":"User judge has exceeded budget limit"}'))
        content = json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}]) if "bull analyst" in system else "[]"
        if "citation checker" in system:
            content = '[{"label": "B1", "supported": true, "note": "ok"}]'
        self.n += 1
        return FakeResponse({**BODY, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
                            {**HEADERS, "Otari-Request-ID": f"req-{self.n}", "Otari-Model": served_spelling(payload["model"])})

    def record(self):
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            return dossier.append(self.pkt, self.path, rules_version="v6")

    def test_the_judge_refusal_is_a_receipt_in_the_dossier_and_the_chain_verifies(self):
        d = self.record()
        judge = d["llm"]["judge"]
        self.assertFalse(judge["accepted"])
        self.assertIn("budget_exceeded", judge["error"])
        self.assertEqual(judge["receipt"]["refusal"], {"status": 403, "code": "budget_exceeded", "budget_scope": "user", "retry_after": "3600"})
        self.assertEqual(judge["receipt"]["request_id"], "req-refused")
        self.assertEqual(d["llm"]["bull"]["receipt"]["role"], "bull")
        self.assertEqual(dossier.verify(dossier.read_ledger(self.path)), [])
        self.assertTrue(dossier.replay(dossier.read_ledger(self.path)[0], "auto")["matches"])

    def test_the_cycle_counts_are_sealed_with_the_layer(self):
        d = self.record()
        cycle = d["llm"]["otari"]["cycle"]
        self.assertEqual((cycle["cap_usd"], cycle["calls"], cycle["refused"]), ("0.05", 3, 0))

    def test_the_command_line_says_which_role_was_not_answered_and_why(self):
        d = self.record()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.print_record(d)
        text = out.getvalue()
        self.assertIn("[otari judge] NOT answered: budget_exceeded, budget scope user, retry after 3600 s (request req-refused)", text)
        self.assertIn("[otari] cycle spend $", text)

    def test_a_cycle_cap_hit_mid_decision_leaves_client_side_refusals_and_still_records(self):
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.000005"}):
            d = self.record()
        self.assertEqual(d["llm"]["bear"]["receipt"]["refusal"]["code"], "cycle_cap_exceeded")
        self.assertEqual(d["llm"]["judge"]["receipt"]["refusal"]["source"], "client")
        self.assertGreaterEqual(d["llm"]["otari"]["cycle"]["refused"], 1)
        self.assertEqual(dossier.verify(dossier.read_ledger(self.path)), [])


class NarrativeRefusalTest(unittest.TestCase):
    VOICES = [{"name": "CoinDesk", "title": "Bitcoin rally hits record", "link": ""}]

    def test_a_refused_batch_leaves_its_receipt_in_the_block_and_the_error_is_listed(self):
        refusal = {"schema": "otari-receipt/1", "role": "narrator", "request_id": "req-r", "refusal": {"status": 403, "code": "budget_exceeded"}}

        def ask(system, user):
            raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded")
        with mock.patch.object(llm, "served_receipt", return_value=refusal):
            block = narrative.build(self.VOICES, ask, "otari:multi-role")
        self.assertEqual(block["method"], "keywords")
        self.assertIn("budget_exceeded", block["llm_error"])
        self.assertEqual(block["receipts"], [{"batch": 1, **refusal}])


if __name__ == "__main__":
    unittest.main()
