"""Otari gateway support in llm.py: the receipt parser, the model of each role and the sealed dossier. No network."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import dossier, llm, narrative

KEY = "unit-test-secret-0123456789"

HEADERS = {
    "Otari-Request-ID": "req-1111",
    "Otari-Provider": "deepinfra",
    "Otari-Model": "openai/gpt-oss-120b",
    "Otari-Attempt-Count": "1",
    "Otari-Fallback": "false",
    "Otari-Response-Duration-Ms": "1880",
}
BODY = {"model": "openai/gpt-oss-120b", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 61, "completion_tokens": 42, "total_tokens": 103, "cost_usd": "0.000009", "pricing_source": "deployment"}}


def served_spelling(model: str) -> str:
    """How a provider spells a catalog id in `Otari-Model` (bedrock answers `nvidia.nemotron-super-3-120b` for the catalog's
    `nvidia/nemotron-3-super-120b-a12b`): never the same string as the one sent."""
    return model.replace("/", ".") + "-served"


def plain_config():
    """A patch that points llm at the repo config with the F2 switches off (no user field, no policies, no end-user budget,
    a 0.05 cycle cap), so the F1-era tests keep testing what they were written for; the real switches are tested apart."""
    cfg = json.loads(llm.OTARI_CONFIG.read_text(encoding="utf-8"))
    cfg.update(send_user=False, use_policies=False, cycle_cap_usd="0.05", end_user_prefix="", end_user_budget="", features={}, canary=False)
    cfg.pop("judge_route", None)  # the F1-era tests judge with the standard route; the routing rule has its own tests
    path = Path(tempfile.mkdtemp()) / "otari.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return mock.patch.object(llm, "OTARI_CONFIG", path)


def message(headers: dict) -> Message:
    m = Message()
    for k, v in headers.items():
        m[k] = v
    return m


class FakeResponse:
    def __init__(self, body: dict, headers: dict):
        self._raw = json.dumps(body).encode("utf-8")
        self.headers = message(headers)

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReceiptParserTest(unittest.TestCase):
    def test_a_plain_reply_becomes_a_receipt(self):
        r = llm.parse_receipt(HEADERS, BODY, "bull", "openai/gpt-oss-120b", None, now="2026-11-01T15:00:00Z")
        self.assertEqual(r, {
            "schema": "otari-receipt/1", "role": "bull", "request_id": "req-1111", "request_id_source": "otari", "sent_model": "openai/gpt-oss-120b", "policy": None,
            "provider": "deepinfra", "served_model": "openai/gpt-oss-120b", "served_by": "deepinfra:openai/gpt-oss-120b",
            "attempt_count": 1, "fallback": False, "duration_ms": 1880, "tokens": {"prompt": 61, "completion": 42},
            "cost_usd": "0.000009", "pricing_source": "deployment", "end_user": None, "captured_at": "2026-11-01T15:00:00Z",
        })

    def test_header_names_are_matched_in_any_case_and_a_message_object_works(self):
        lower = {k.lower(): v for k, v in HEADERS.items()}
        self.assertEqual(llm.parse_receipt(message(lower), BODY, "bull", "x")["request_id"], "req-1111")
        self.assertEqual(llm.parse_receipt(lower, BODY, "bull", "x")["provider"], "deepinfra")

    def test_a_fallback_reply_keeps_its_attempt_count_and_our_own_retries(self):
        h = {**HEADERS, "Otari-Attempt-Count": "2", "Otari-Fallback": "true", "Otari-Provider": "nebius"}
        r = llm.parse_receipt(h, BODY, "judge", "vd-judge", "judge", client_attempts=3)
        self.assertEqual((r["attempt_count"], r["fallback"], r["provider"], r["client_attempts"], r["end_user"]), (2, True, "nebius", 3, "judge"))
        self.assertEqual(r["sent_model"], "vd-judge")

    def test_extra_headers_are_kept_verbatim_only_when_present(self):
        r = llm.parse_receipt({**HEADERS, "Otari-Guardrails": "prompt-injection=pass", "Otari-Idempotent-Replayed": "true"}, BODY, "bull", "x")
        self.assertEqual((r["guardrails_raw"], r["replayed"]), ("prompt-injection=pass", "true"))
        self.assertNotIn("guardrails_raw", llm.parse_receipt(HEADERS, BODY, "bull", "x"))

    def test_missing_headers_and_usage_give_none_not_a_crash(self):
        r = llm.parse_receipt({}, {"choices": []}, "bear", "x")
        self.assertEqual((r["served_by"], r["attempt_count"], r["duration_ms"], r["cost_usd"], r["fallback"]), (None, None, None, None, False))
        self.assertTrue(r["request_id"].startswith("client:"))
        self.assertEqual(r["request_id_source"], "client")
        self.assertEqual(llm.parse_receipt(None, None, "bear", "x")["tokens"], {"prompt": None, "completion": None})

    def test_an_unpriced_reply_has_no_cost_and_a_number_cost_is_kept_as_text(self):
        body = {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        self.assertIsNone(llm.parse_receipt(HEADERS, body, "bull", "x")["cost_usd"])
        self.assertEqual(llm.parse_receipt(HEADERS, {"usage": {"cost_usd": 0.5}}, "bull", "x")["cost_usd"], "0.5")

    def test_a_refusal_receipt_carries_code_scope_and_retry_after(self):
        h = {"Otari-Request-ID": "req-9", "Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "user", "Retry-After": "3600"}
        r = llm.refusal_receipt(h, 403, "judge", "deepseek/deepseek-v4-flash-0731", "judge", now="2026-11-01T15:00:00Z")
        self.assertEqual(r["refusal"], {"status": 403, "code": "budget_exceeded", "budget_scope": "user", "retry_after": "3600"})
        self.assertEqual((r["request_id"], r["role"]), ("req-9", "judge"))

    def test_the_receipt_cannot_hold_the_key(self):
        r = llm.parse_receipt({**HEADERS, "Authorization": f"Bearer {KEY}", "Otari-Key": KEY}, {**BODY, "key": KEY}, "bull", "x")
        self.assertNotIn(KEY, json.dumps(r))


class RoleConfigTest(unittest.TestCase):
    def test_the_repo_config_gives_every_role_its_own_model(self):
        cfg = llm.otari_config()
        # the roles that argue, rule, check and read the news each have a model and a vendor of their own; the roles that only
        # drive an Otari tool (marked tool_role) may share the judge's model
        core = {name: spec for name, spec in cfg["roles"].items() if not spec.get("tool_role")}
        models = {name: spec["model"] for name, spec in cfg["roles"].items()}
        for role in ("bull", "bear", "judge", "checker"):
            self.assertIn(role, models)
        self.assertEqual(len({s["model"] for s in core.values()}), len(core), "two core roles share a model")
        self.assertEqual(len({s["vendor"] for s in core.values()}), len(core), "two core roles share a vendor")
        for role in ("searcher", "sandbox", "mcp"):
            self.assertTrue(cfg["roles"][role].get("tool_role"), role)
        self.assertEqual(models["bull"], "openai/gpt-oss-120b")
        self.assertEqual(models["bear"], "nvidia/nemotron-3-super-120b-a12b")
        self.assertEqual(models["judge"], "deepseek/deepseek-v4-flash-0731")

    def test_role_model_follows_the_role_and_defaults_when_none(self):
        cfg = llm.otari_config()
        self.assertEqual(llm.role_model("judge", cfg), cfg["roles"]["judge"]["model"])
        self.assertEqual(llm.role_model(None, cfg), cfg["roles"][cfg["default_role"]]["model"])
        with self.assertRaises(llm.LLMError):
            llm.role_model("poet", cfg)

    def test_a_broken_config_is_a_clear_error(self):
        folder = Path(tempfile.mkdtemp())
        for name, text in (("nofile.json", None), ("badjson.json", "{"), ("norole.json", '{"roles": {"bull": {}}, "default_role": "bull", "base_url": "x", "auth_header": "y"}'),
                           ("baddefault.json", '{"roles": {"bull": {"model": "m"}}, "default_role": "judge", "base_url": "x", "auth_header": "y"}')):
            path = folder / name
            if text is not None:
                path.write_text(text, encoding="utf-8")
            with self.assertRaises(llm.LLMError, msg=name):
                llm.otari_config(path)

    def test_the_summary_lists_roles_and_the_config_hash_only_in_otari_mode(self):
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            self.assertIsNone(llm.otari_summary())
            self.assertEqual(llm.model_name(), llm.MODEL)
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": KEY}):
            s = llm.otari_summary()
            self.assertEqual(s["roles"]["bear"], "nvidia/nemotron-3-super-120b-a12b")
            self.assertEqual(len(s["config_sha256"]), 64)
            self.assertNotIn(KEY, json.dumps(s))
            self.assertEqual(llm.model_name(), "otari:multi-role")


class CatalogCheckTest(unittest.TestCase):
    """tools/otari_catalog_check.py on a small saved catalog: a role model must exist and be open weights."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("otari_catalog_check", Path(__file__).resolve().parent.parent / "tools" / "otari_catalog_check.py")
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_every_repo_role_is_ok_in_a_catalog_that_lists_them_as_open_weights(self):
        cfg = llm.otari_config()
        catalog = [{"id": spec["model"], "open_weights": True, "vendor": spec["vendor"], "providers": ["deepinfra"]} for spec in cfg["roles"].values()]
        self.assertTrue(all(r["ok"] for r in self.tool.check(cfg, catalog)))

    def test_a_missing_or_closed_model_is_flagged(self):
        cfg = {"roles": {"bull": {"model": "a/open"}, "bear": {"model": "b/closed"}, "judge": {"model": "c/gone"}}}
        catalog = [{"id": "a/open", "open_weights": True}, {"id": "b/closed", "open_weights": False}]
        self.assertEqual({r["role"]: r["ok"] for r in self.tool.check(cfg, catalog)}, {"bull": True, "bear": False, "judge": False})


class OtariCallTest(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "MISTRAL_API_KEY": ""}),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
            mock.patch.object(llm, "_pause"),
            plain_config(),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.sent = []

    def fake(self, content="ok", headers=None):
        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode("utf-8"))
            self.sent.append((request, payload))
            h = {**HEADERS, "Otari-Model": served_spelling(payload["model"])}
            return FakeResponse({**BODY, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}, {**h, **(headers or {})})
        return mock.patch.object(urllib.request, "urlopen", urlopen)

    def test_each_role_sends_its_own_model_to_the_otari_base_with_the_otari_key_header(self):
        cfg = llm.otari_config()
        with self.fake():
            for role in ("bull", "bear", "judge", "checker", "narrator"):
                llm.chat("s", "u", role=role)
        self.assertEqual([p["model"] for _, p in self.sent], [cfg["roles"][r]["model"] for r in ("bull", "bear", "judge", "checker", "narrator")])
        request = self.sent[0][0]
        self.assertEqual(request.full_url, "https://otari.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Otari-key"), KEY)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_header("Otari-tags"), "role=bull")
        self.assertTrue(request.get_header("Idempotency-key"))

    def test_argue_and_rule_pick_the_model_of_their_role(self):
        items = {"E1": "BTC last close 100"}
        labels = {"B1": {"claim": "x", "cites": ["E1"]}}
        with self.fake("[]"):
            llm.argue("bull", items)
            llm.argue("bear", items)
        with self.fake("{}"):
            llm.rule(labels, items)
        with self.fake('[{"label": "B1", "supported": true, "note": "ok"}]'):
            out = llm.check_claims(labels, items)
        cfg = llm.otari_config()["roles"]
        self.assertEqual([p["model"] for _, p in self.sent], [cfg["bull"]["model"], cfg["bear"]["model"], cfg["judge"]["model"], cfg["checker"]["model"]])
        self.assertEqual(out, {"checks": [{"label": "B1", "supported": True, "note": "ok"}], "checked": 1, "supported": 1, "claims": 1})

    def test_the_user_field_is_off_by_default_and_carries_the_role_when_asked(self):
        with self.fake():
            llm.chat("s", "u", role="bear")
        self.assertNotIn("user", self.sent[-1][1])
        with mock.patch.dict(os.environ, {"OTARI_SEND_USER": "1"}), self.fake():
            llm.chat("s", "u", role="bear")
        self.assertEqual(self.sent[-1][1]["user"], "bear")
        self.assertEqual(llm.served_receipt()["end_user"], "bear")

    def test_the_reply_fills_served_by_route_and_a_receipt_read_once(self):
        with self.fake("text"):
            self.assertEqual(llm.chat("s", "u", role="bull"), "text")
        self.assertEqual(llm.served_by(), "deepinfra:" + served_spelling("openai/gpt-oss-120b"))
        self.assertIsNone(llm.served_by())
        self.assertIsNone(llm.served_route(), "Otari sends counts, not a list of attempts: no route is sealed")
        receipt = llm.served_receipt()
        self.assertEqual((receipt["role"], receipt["request_id"], receipt["cost_usd"]), ("bull", "req-1111", "0.000009"))
        self.assertEqual((receipt["sent_model"], receipt["served_model"]), ("openai/gpt-oss-120b", served_spelling("openai/gpt-oss-120b")))
        self.assertIsNone(llm.served_receipt())

    def test_a_fallback_reply_does_not_pretend_the_first_provider_answered(self):
        with self.fake(headers={"Otari-Attempt-Count": "2", "Otari-Fallback": "true", "Otari-Provider": "nebius"}):
            llm.chat("s", "u", role="judge")
        self.assertIsNone(llm.served_route())
        receipt = llm.served_receipt()
        self.assertEqual((receipt["fallback"], receipt["attempt_count"], receipt["provider"]), (True, 2, "nebius"))

    def test_a_budget_refusal_is_an_otari_error_with_its_fields_and_is_not_retried(self):
        h = message({"Otari-Request-ID": "req-7", "Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "user", "Retry-After": "7200"})
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", h, io.BytesIO(f'{{"detail": "over budget for {KEY}"}}'.encode()))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="judge")
        err = ctx.exception
        self.assertEqual((err.status, err.code, err.scope, err.retry_after, err.request_id), (403, "budget_exceeded", "user", "7200", "req-7"))
        self.assertNotIn(KEY, str(err))
        self.assertEqual(len(calls), 1)
        self.assertEqual(llm.served_receipt()["refusal"]["code"], "budget_exceeded")

    def test_429_with_a_short_retry_after_waits_and_repeats_a_long_one_stops(self):
        calls = []

        def limited(retry_after):
            def urlopen(request, timeout=None):
                calls.append(retry_after)
                if len(calls) == 1 or retry_after > 60:
                    raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", message({"Retry-After": str(retry_after), "Otari-Error-Code": "rate_limited"}), io.BytesIO(b"{}"))
                return FakeResponse(BODY, HEADERS)
            return urlopen
        with mock.patch.object(urllib.request, "urlopen", limited(5)):
            self.assertEqual(llm.chat("s", "u", role="bull"), "ok")
        self.assertEqual(len(calls), 2)
        llm._pause.assert_any_call(5.0)
        calls.clear()
        with mock.patch.object(urllib.request, "urlopen", limited(120)):
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="bull")
        self.assertEqual((len(calls), ctx.exception.status, ctx.exception.code, ctx.exception.retry_after), (1, 429, "rate_limited", "120"))
        self.assertEqual(llm.served_receipt()["refusal"]["status"], 429)

    def test_a_504_that_never_clears_runs_out_of_retries_and_raises(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request.get_header("Idempotency-key"))
            raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", message({}), io.BytesIO(b"<html>504</html>"))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="judge")
        self.assertEqual(len(calls), llm.MAX_RETRIES + 1)
        self.assertEqual(len(set(calls)), 1)
        self.assertEqual(ctx.exception.status, 504)
        receipt = llm.served_receipt()
        self.assertEqual(receipt["error"]["status"], 504)
        self.assertNotIn("refusal", receipt, "a gateway failure is not a refusal")

    def test_a_client_timeout_is_recorded_as_a_timeout_not_as_the_504_before_it(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", message({}), io.BytesIO(b"<html>504</html>"))
            raise urllib.error.URLError(TimeoutError("timed out"))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="bear")
        self.assertIn("timeout", str(ctx.exception))
        receipt = llm.served_receipt()
        self.assertEqual(receipt["error"], {"kind": "timeout"})
        self.assertNotIn("refusal", receipt)
        self.assertEqual((receipt["role"], receipt["sent_model"]), ("bear", llm.role_model("bear")))

    def test_a_bare_socket_timeout_and_a_refused_connection_are_otari_errors_too(self):
        for exc, kind in ((TimeoutError("read timed out"), "timeout"), (urllib.error.URLError(ConnectionRefusedError()), "connection")):
            with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=exc)):
                with self.assertRaises(llm.OtariError):
                    llm.chat("s", "u", role="bull")
            self.assertEqual(llm.served_receipt()["error"]["kind"], kind)

    def test_a_reply_with_an_unexpected_shape_names_otari(self):
        def urlopen(request, timeout=None):
            return FakeResponse({"id": "x"}, HEADERS)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(ValueError, "Otari gateway"):
                llm.chat("s", "u", role="bull")

    def test_the_base_url_must_be_https_and_loses_its_trailing_slash(self):
        with mock.patch.dict(os.environ, {"OTARI_BASE_URL": "http://otari.example/api/v1"}), mock.patch.object(urllib.request, "urlopen") as opened:
            with self.assertRaisesRegex(llm.LLMError, "https://"):
                llm.chat("s", "u", role="bull")
            opened.assert_not_called()
            with self.assertRaises(llm.LLMError):
                llm.otari_summary()
        with mock.patch.dict(os.environ, {"OTARI_BASE_URL": "https://eu.example/api/v1//"}), self.fake():
            llm.chat("s", "u", role="bull")
        self.assertEqual(self.sent[-1][0].full_url, "https://eu.example/api/v1/chat/completions")

    def test_otari_calls_do_not_inherit_the_mistral_spacing(self):
        with mock.patch.object(llm, "MIN_INTERVAL", 5.0), mock.patch.object(llm, "_next_start", 0.0), self.fake():
            for _ in range(3):
                llm.chat("s", "u", role="bull")
            llm._pause.assert_not_called()
            with mock.patch.dict(os.environ, {"OTARI_MIN_INTERVAL": "2"}):
                llm.chat("s", "u", role="bull")
                llm.chat("s", "u", role="bull")
            self.assertTrue(llm._pause.called)

    def test_a_gateway_timeout_is_retried_with_the_same_idempotency_key_and_the_receipt_counts_it(self):
        seen = []

        def urlopen(request, timeout=None):
            seen.append(request.get_header("Idempotency-key"))
            if len(seen) == 1:
                raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", message({}), io.BytesIO(b"<html>504</html>"))
            return FakeResponse(BODY, HEADERS)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertEqual(llm.chat("s", "u", role="checker"), "ok")
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(llm.served_receipt()["client_attempts"], 2)

    def test_a_409_still_in_progress_waits_and_asks_again_with_the_same_key(self):
        seen = []

        def urlopen(request, timeout=None):
            seen.append(request.get_header("Idempotency-key"))
            if len(seen) == 1:
                raise urllib.error.HTTPError(request.full_url, 504, "Gateway Timeout", message({}), io.BytesIO(b"<html>504</html>"))
            if len(seen) == 2:
                body = b'{"detail":"A request with this Idempotency-Key is still in progress. Retry with the same key to get its result."}'
                raise urllib.error.HTTPError(request.full_url, 409, "Conflict", message({}), io.BytesIO(body))
            return FakeResponse(BODY, {**HEADERS, "Otari-Idempotent-Replayed": "true"})
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertEqual(llm.chat("s", "u", role="narrator"), "ok")
        self.assertEqual((len(seen), len(set(seen))), (3, 1))
        receipt = llm.served_receipt()
        self.assertEqual((receipt["client_attempts"], receipt["replayed"]), (3, "true"))

    def test_any_other_409_is_an_error_at_once(self):
        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 409, "Conflict", message({}), io.BytesIO(b'{"detail":"conflict"}'))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(llm.OtariError):
                llm.chat("s", "u", role="narrator")

    def test_the_default_role_serves_a_call_without_a_role(self):
        with self.fake():
            llm.chat("s", "u")
        self.assertEqual(self.sent[0][1]["model"], llm.role_model(None))

    def test_without_the_otari_key_the_old_client_runs_unchanged(self):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(request)
            return FakeResponse({"choices": [{"message": {"content": "legacy"}, "finish_reason": "stop"}]}, {})
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": "", "MISTRAL_API_KEY": "mistral-key"}), mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertFalse(llm.otari_active())
            self.assertEqual(llm.chat("s", "u", role="bull"), "legacy")
        self.assertTrue(sent[0].full_url.endswith("/chat/completions"))
        self.assertNotIn("otari.ai", sent[0].full_url)
        self.assertEqual(sent[0].get_header("Authorization"), "Bearer mistral-key")
        self.assertIsNone(llm.served_receipt())


class OtariDossierTest(unittest.TestCase):
    """A decision recorded through Otari carries the roles, one receipt per call and a checker, and still verifies and replays."""

    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "MISTRAL_API_KEY": ""}),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
            mock.patch.object(llm, "_pause"),
            plain_config(),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.counter = 0

    def urlopen(self, request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        system = payload["messages"][0]["content"]
        if "citation checker" in system:
            content = '[{"label": "B1", "supported": true, "note": "matches E4"}]'
        elif "bull analyst" in system:
            content = json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}])
        elif "judge in a market debate" in system:
            content = '{"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "rally"}'
        else:
            content = "[]"
        self.counter += 1
        headers = {**HEADERS, "Otari-Request-ID": f"req-{self.counter}", "Otari-Model": served_spelling(payload["model"])}
        return FakeResponse({**BODY, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}, headers)

    def test_the_sealed_dossier_names_the_models_and_keeps_every_receipt(self):
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            d = dossier.append(self.pkt, self.path, rules_version="v6")
        layer = d["llm"]
        self.assertEqual(layer["model"], "otari:multi-role")
        self.assertEqual(layer["otari"]["roles"]["checker"], "google/gemma-3-27b-it")
        for part, role in (("bull", "bull"), ("bear", "bear"), ("checker", "checker"), ("judge", "judge")):
            receipt = layer[part]["receipt"]
            self.assertEqual(receipt["role"], role, part)
            self.assertEqual(receipt["sent_model"], llm.role_model(role))
            self.assertEqual(receipt["served_model"], served_spelling(llm.role_model(role)))
            self.assertEqual(layer[part]["served_by"], "deepinfra:" + served_spelling(llm.role_model(role)))
            self.assertNotIn("route", layer[part], "no route is sealed in Otari mode")
            self.assertNotIn("routing_policy", layer[part])
        self.assertEqual(len({layer[p]["receipt"]["request_id"] for p in ("bull", "bear", "checker", "judge")}), 4)
        self.assertEqual(layer["checker"]["supported"], 1)
        self.assertTrue(layer["checker"]["advisory"])
        text = json.dumps(d)
        self.assertNotIn(KEY, text)

    def test_the_chain_verifies_and_the_replay_matches_without_calling_the_model(self):
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            dossier.append(self.pkt, self.path, rules_version="v6")
        ledger = dossier.read_ledger(self.path)
        self.assertEqual(dossier.verify(ledger), [])

        def forbidden(*args, **kwargs):
            raise AssertionError("the replay called the network")
        with mock.patch.object(urllib.request, "urlopen", forbidden):
            result = dossier.replay(ledger[0], "auto")
        self.assertTrue(result["matches"] and result["llm_citations_valid"])

    def test_an_injected_ask_never_triggers_the_checker_or_receipts(self):
        stub = lambda system, user: "[]"
        layer = dossier.build(self.pkt, dossier.GENESIS, 1, ask=stub, rules_version="v6")["llm"]
        self.assertNotIn("checker", layer)
        self.assertNotIn("receipt", layer["bull"])

    def test_a_refused_call_leaves_the_refusal_receipt_in_the_dossier_and_the_decision_still_records(self):
        def refuse(request, timeout=None):
            h = message({"Otari-Request-ID": "req-x", "Otari-Error-Code": "budget_exceeded", "Otari-Budget-Scope": "user"})
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", h, io.BytesIO(b'{"detail": "no budget"}'))
        with mock.patch.object(urllib.request, "urlopen", refuse):
            d = dossier.append(self.pkt, self.path, rules_version="v6")
        self.assertEqual(d["llm"]["bull"]["receipt"]["refusal"]["code"], "budget_exceeded")
        self.assertIn("budget_exceeded", d["llm"]["bull"]["error"])
        self.assertEqual(dossier.verify(dossier.read_ledger(self.path)), [])


class NarrativeReceiptTest(unittest.TestCase):
    def test_extraction_batches_keep_their_receipts_in_the_block(self):
        voices = [{"name": "CoinDesk", "title": "Bitcoin rally hits record", "link": ""}]
        receipt = {"schema": "otari-receipt/1", "role": "narrator", "request_id": "req-n1"}

        def ask(system, user):
            return "[]"
        with mock.patch.object(llm, "served_receipt", return_value=receipt):
            block = narrative.build(voices, ask, "otari:multi-role")
        self.assertEqual(block["receipts"], [{"batch": 1, **receipt}])

from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
