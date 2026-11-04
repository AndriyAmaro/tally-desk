"""Role fallback chains: when the gateway refuses or fails on the model a role asked for, the app tries the next catalog id of that role.

A model-side refusal (402 pricing_required, model not found, a 5xx that outlived the retries) moves on; a budget refusal (403 budget_exceeded,
the cycle cap) never does. The receipt that stays says which entry answered and why the earlier ones were skipped, and the closure gate sees
only that final outcome. Fake transports: no network, no key, no money."""

import io
import json
import os
import unittest
import urllib.error
import urllib.request
from unittest import mock

from tests.test_otari import BODY, HEADERS, KEY, FakeResponse, message, plain_config, served_spelling
from tests.test_otari_tools import FlowEnv
from verdict_desk import closure, dossier, llm, otari_report

ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "1", "OTARI_BASE_URL": "", "OTARI_MIN_INTERVAL": "", "OTARI_CYCLE_CAP_USD": "", "OTARI_TOOL_CAP_USD": ""}
BULL = ["openai/gpt-oss-120b", "alibaba/qwen3-235b-a22b-instruct-2507", "deepseek/deepseek-v4-flash-0731"]
PRICING = (402, {"Otari-Error-Code": "pricing_required"})
BUDGET = (403, {"Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "end_user"})


def refusal(url, status, headers, request_id):
    return urllib.error.HTTPError(url, status, "x", message({"Otari-Request-ID": request_id, **headers}), io.BytesIO(json.dumps({"detail": "refused"}).encode()))


class Transport:
    """A scripted gateway: `script` maps a model id to what it does (a (status, headers) refusal, a status number for a bare failure, or "ok")."""

    def __init__(self, script):
        self.script, self.requests = script, []

    def __call__(self, request, timeout=None):
        payload = json.loads(request.data.decode())
        self.requests.append({"model": payload["model"], "key": request.get_header("Idempotency-key"), "user": payload.get("user"), "tags": request.get_header("Otari-tags")})
        step = self.script.get(payload["model"], "ok")
        if step == "ok":
            headers = {**HEADERS, "Otari-Request-ID": f"req-{len(self.requests)}", "Otari-Model": served_spelling(payload["model"])}
            return FakeResponse({**BODY, "model": payload["model"]}, headers)
        status, headers = step if isinstance(step, tuple) else (step, {})
        raise refusal(request.full_url, status, headers, f"req-{len(self.requests)}")

    @property
    def models(self):
        return [r["model"] for r in self.requests]


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause"),
                      plain_config(fallbacks=True)):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()
        llm.served_receipt()

    def call(self, script, role="bull", **kw):
        gateway = Transport(script)
        with mock.patch.object(urllib.request, "urlopen", gateway):
            try:
                text, error = llm.chat("s", "u", role=role, **kw), None
            except llm.OtariError as err:
                text, error = None, err
        return gateway, text, error, llm.served_receipt()


class FallbackTest(Env):
    def test_the_first_entry_refused_with_pricing_required_and_the_second_answers(self):
        gateway, text, error, r = self.call({BULL[0]: PRICING})
        self.assertEqual((text, error), ("ok", None))
        self.assertEqual(gateway.models, BULL[:2])
        self.assertEqual((r["sent_model"], r["policy"], r["role"]), (BULL[1], None, "bull"))
        self.assertNotIn("refusal", r)
        note = r["role_fallback"]
        self.assertEqual((note["chain"], note["answered"], note["answered_index"]), (BULL, BULL[1], 1))
        self.assertEqual(note["skipped"], [{"model": BULL[0], "reason": "pricing_required", "status": 402, "code": "pricing_required", "request_id": "req-1", "request_id_source": "otari"}])
        self.assertNotIn("exhausted", note)
        self.assertEqual(llm.served_by(), "deepinfra:" + served_spelling(BULL[1]))

    def test_each_entry_is_a_request_of_its_own_with_the_roles_end_user_and_its_own_idempotency_key(self):
        gateway, *_ = self.call({BULL[0]: PRICING, BULL[1]: PRICING})
        self.assertEqual(gateway.models, BULL)
        self.assertEqual({r["user"] for r in gateway.requests}, {"bull"}, "plain_config has no end-user prefix; the same end user is billed on every entry")
        self.assertEqual({r["tags"] for r in gateway.requests}, {"role=bull"})
        self.assertEqual(len({r["key"] for r in gateway.requests}), 3, "one logical key per request: a different model is a different request")

    def test_the_whole_chain_is_walked_in_order_and_the_last_answer_counts(self):
        gateway, text, error, r = self.call({BULL[0]: PRICING, BULL[1]: (404, {})})
        self.assertEqual((text, gateway.models), ("ok", BULL))
        self.assertEqual([s["model"] for s in r["role_fallback"]["skipped"]], BULL[:2])
        self.assertEqual([s["reason"] for s in r["role_fallback"]["skipped"]], ["pricing_required", "model_refused"])
        self.assertEqual(r["role_fallback"]["answered"], BULL[2])

    def test_a_5xx_that_outlives_the_retries_moves_on(self):
        gateway, text, error, r = self.call({BULL[0]: 504})
        self.assertEqual(gateway.models, [BULL[0]] * (llm.MAX_RETRIES + 1) + [BULL[1]])
        self.assertEqual(text, "ok")
        skipped = r["role_fallback"]["skipped"][0]
        self.assertEqual((skipped["reason"], skipped["status"], skipped["client_attempts"]), ("gateway_504", 504, llm.MAX_RETRIES + 1))

    def test_a_model_the_gateway_does_not_know_moves_on(self):
        for step in ((400, {"Otari-Error-Code": "invalid_model"}), (404, {}), (403, {"Otari-Error-Code": "model_not_allowed"})):
            with self.subTest(step=step):
                gateway, text, error, r = self.call({BULL[0]: step})
                self.assertEqual((gateway.models, text), (BULL[:2], "ok"))

    def test_a_call_served_by_its_first_model_has_no_role_fallback_in_its_receipt(self):
        gateway, text, error, r = self.call({})
        self.assertEqual((gateway.models, text), (BULL[:1], "ok"))
        self.assertNotIn("role_fallback", r)
        self.assertEqual(r["sent_model"], BULL[0])

    def test_the_cycle_counts_one_call_and_the_receipts_the_spend_of_the_entry_that_answered(self):
        self.call({BULL[0]: PRICING})
        status = llm.cycle_status()
        self.assertEqual((status["calls"], status["refused"]), (1, 0))


class MustNotFallBackTest(Env):
    def test_a_budget_refusal_stops_at_once_and_no_other_model_is_asked(self):
        gateway, text, error, r = self.call({BULL[0]: BUDGET})
        self.assertEqual(gateway.models, BULL[:1])
        self.assertEqual((error.status, error.code, error.scope), (403, "budget_exceeded", "end_user"))
        self.assertNotIn("role_fallback", r)
        self.assertEqual((r["refusal"]["code"], r["refusal"]["budget_scope"]), ("budget_exceeded", "end_user"))

    def test_a_budget_refusal_after_a_skipped_entry_stops_there_and_says_so(self):
        gateway, text, error, r = self.call({BULL[0]: PRICING, BULL[1]: BUDGET})
        self.assertEqual(gateway.models, BULL[:2], "the third entry is never asked")
        self.assertEqual(error.code, "budget_exceeded")
        note = r["role_fallback"]
        self.assertEqual((note["answered"], note.get("exhausted"), note["stopped_by"]), (None, None, "budget_exceeded"))
        self.assertEqual([s["model"] for s in note["skipped"]], [BULL[0]])

    def test_the_cycle_cap_is_not_a_reason_to_try_another_model(self):
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.001"}):
            llm._cycle["spent"] = llm.decimal.Decimal("0.01")  # the cycle has already spent more than the cap
            gateway, text, error, r = self.call({})
        self.assertEqual((gateway.models, error.code), ([], "cycle_cap_exceeded"))
        self.assertEqual(r["refusal"]["source"], "client")
        self.assertNotIn("role_fallback", r)

    def test_a_bad_request_a_bad_key_and_a_rate_limit_are_not_model_side(self):
        for status in (400, 401, 403, 409, 429):
            with self.subTest(status=status):
                gateway, text, error, r = self.call({BULL[0]: status})
                self.assertEqual(set(gateway.models), {BULL[0]}, f"HTTP {status} must not move to another model")
                self.assertEqual(error.status, status)

    def test_a_transport_failure_is_not_a_reason_either(self):
        def urlopen(request, timeout=None):
            raise urllib.error.URLError(TimeoutError())
        with mock.patch.object(urllib.request, "urlopen", urlopen), self.assertRaises(llm.OtariError):
            llm.chat("s", "u", role="bull")
        r = llm.served_receipt()
        self.assertEqual(r["error"], {"kind": "timeout"})
        self.assertNotIn("role_fallback", r)


class ExhaustedTest(Env):
    def test_every_entry_refusing_raises_and_the_final_receipt_holds_the_whole_trail(self):
        gateway, text, error, r = self.call({m: PRICING for m in BULL})
        self.assertEqual((text, gateway.models), (None, BULL))
        self.assertEqual((error.status, error.code), (402, "pricing_required"))
        self.assertIn("all 3 models of the bull chain failed", str(error))
        self.assertEqual(r["refusal"]["code"], "pricing_required")
        note = r["role_fallback"]
        self.assertTrue(note["exhausted"])
        self.assertEqual((note["answered"], note["answered_index"]), (None, None))
        self.assertEqual([s["model"] for s in note["skipped"]], BULL[:2])
        self.assertEqual(r["sent_model"], BULL[2], "the receipt is the last attempt's")

    def test_a_role_with_no_chain_behaves_as_before(self):
        gateway, text, error, r = self.call({"deepseek/deepseek-v4-flash-0731": PRICING}, role="sandbox")
        self.assertEqual(len(gateway.models), 1)
        self.assertEqual(error.code, "pricing_required")
        self.assertNotIn("role_fallback", r)

    def test_a_call_that_names_its_model_does_not_use_the_chain_unless_it_asks_for_it(self):
        gateway, text, error, r = self.call({"deepseek/deepseek-v4-pro": PRICING}, role="judge", model="deepseek/deepseek-v4-pro")
        self.assertEqual((gateway.models, error.code), (["deepseek/deepseek-v4-pro"], "pricing_required"))
        gateway, text, error, r = self.call({"deepseek/deepseek-v4-pro": PRICING}, role="judge", model="deepseek/deepseek-v4-pro", role_fallbacks=True)
        self.assertEqual((gateway.models, text), (["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash-0731"], "ok"))
        self.assertEqual(r["role_fallback"]["chain"], ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash-0731"], "an entry equal to the model asked is not tried twice")

    def test_the_hard_judge_route_falls_back_through_rule(self):
        labels = {"B1": {"claim": "c", "cites": ["E1"]}}
        gateway = Transport({"deepseek/deepseek-v4-pro": PRICING})
        gateway_ok = '{"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "r"}'

        def urlopen(request, timeout=None):
            reply = gateway(request, timeout)
            return FakeResponse({**BODY, "choices": [{"message": {"content": gateway_ok}, "finish_reason": "stop"}]}, dict(reply.headers))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            ruling = llm.rule(labels, {"E1": "close 1"}, model="deepseek/deepseek-v4-pro")
        self.assertEqual(ruling["lean"], "bull")
        self.assertEqual(gateway.models, ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash-0731"])
        self.assertEqual(llm.served_receipt()["role_fallback"]["answered"], "deepseek/deepseek-v4-flash-0731")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(llm.OTARI_CONFIG.read_text(encoding="utf-8"))

    def test_every_chain_is_open_weight_distinct_known_to_the_report_and_ends_on_deepseek(self):
        flags = otari_report._models_open(self.cfg)
        for role in ("bull", "bear", "judge", "checker", "narrator"):
            chain = self.cfg["roles"][role].get("fallbacks")
            self.assertTrue(chain, f"{role} has no fallback chain")
            self.assertEqual(len(set(chain)), len(chain))
            self.assertTrue(chain[-1].startswith("deepseek/"), f"{role}: the last resort is a DeepSeek model")
            for model in chain:
                self.assertTrue(flags.get(model), f"{model} has no true open-weights flag")
        bull = self.cfg["roles"]["bull"]
        self.assertNotEqual(bull["fallbacks"][0].split("/")[0], bull["model"].split("/")[0], "the first fallback of the bull is another vendor")

    def test_the_tool_roles_have_no_chain_because_their_tool_loop_is_proven_on_one_model(self):
        for role in ("searcher", "fetcher", "sandbox", "mcp"):
            self.assertNotIn("fallbacks", self.cfg["roles"][role])

    def test_a_malformed_chain_is_refused_with_the_role_named(self):
        import tempfile
        from pathlib import Path
        for bad in ("m/x", ["a/b", "a/b"], ["has space"], [""], [5]):
            cfg = json.loads(json.dumps(self.cfg))
            cfg["roles"]["bull"]["fallbacks"] = bad
            path = Path(tempfile.mkdtemp()) / "otari.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.subTest(bad=bad), self.assertRaisesRegex(llm.LLMError, "bull"):
                llm.otari_config(path)

    def test_every_model_a_receipt_may_now_name_has_an_open_weights_flag(self):
        flags = otari_report._models_open(self.cfg)
        for spec in self.cfg["roles"].values():
            for model in spec.get("fallbacks", []):
                self.assertIn(model, flags)


class CycleTest(FlowEnv):
    """A whole decide-v7 cycle on the fake gateway: the model of the bull is refused with a 402, the next entry answers, the gate seals the dossier."""

    def setUp(self):
        super().setUp()
        for patch in (plain_config(fallbacks=True),):
            patch.start()
            self.addCleanup(patch.stop)
        cfg = json.loads(llm.OTARI_CONFIG.read_text())
        cfg["features"] = {"web_search": True, "code_execution": True, "mcp": True}
        llm.OTARI_CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
        self.refused_models = {}
        self.asked = []

    def reply(self, request, timeout=None, _real=FlowEnv.reply):
        payload = json.loads(request.data.decode())
        system = payload["messages"][0]["content"] if not request.full_url.endswith("/messages") else ""
        role = "bull" if "bull analyst" in system else "bear" if "bear analyst" in system else None
        if role:
            self.asked.append((role, payload["model"]))
            if payload["model"] in self.refused_models.get(role, ()):
                raise refusal(request.full_url, 402, {"Otari-Error-Code": "pricing_required"}, f"req-402-{len(self.asked)}")
        return _real(self, request, timeout)

    def test_a_refused_first_model_is_skipped_the_dossier_is_sealed_and_the_gate_passes(self):
        self.refused_models = {"bull": {"openai/gpt-oss-120b"}}
        code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, len(ledger)), (0, 1), out)
        d = ledger[0]
        receipt = d["llm"]["bull"]["receipt"]
        self.assertEqual(receipt["sent_model"], "alibaba/qwen3-235b-a22b-instruct-2507")
        self.assertEqual(receipt["role_fallback"]["skipped"][0]["code"], "pricing_required")
        self.assertEqual([c["pass"] for c in d["closure"]["checks"] if c["name"] == "analysts_answered"], [True])
        self.assertEqual(dossier.verify(ledger), [])
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])
        self.assertIn(("bull", "openai/gpt-oss-120b"), self.asked)

    def test_when_every_entry_of_a_side_is_refused_the_gate_refuses_to_seal(self):
        self.refused_models = {"bull": set(BULL), "bear": {"nvidia/nemotron-3-super-120b-a12b", "openai/gpt-oss-120b", "deepseek/deepseek-v4-pro"}}
        code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, ledger), (closure.EXIT_CODE, []))
        line = closure.read_refusals(self.ledger)[0]
        self.assertEqual([f["name"] for f in line["failed"]], ["analysts_answered"])
        self.assertEqual({f["role"] for f in line["roles_failed"]}, {"bull", "bear"})


if __name__ == "__main__":
    unittest.main()
