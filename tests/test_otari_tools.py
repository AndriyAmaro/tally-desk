"""F3: Otari's web search, the public MCP server and the code sandbox, used by the app and sealed in the dossier. No network."""

import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from tests.test_otari import BODY, HEADERS, FakeResponse, message, plain_config, served_spelling
from verdict_desk import citations, dossier, llm, otari_mcp, otari_web, sandbox_rederive

KEY = "unit-test-secret-0123456789"
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "MISTRAL_API_KEY": "", "OTARI_USE_POLICIES": "", "OTARI_CYCLE_CAP_USD": "",
       "OTARI_POLICY_JUDGE": "", "OTARI_WEB_SEARCH": "", "OTARI_CODE_EXECUTION": "", "OTARI_MCP": "", "OTARI_MIN_INTERVAL": ""}

SOURCES = [
    {"url": "https://finance.yahoo.com/a", "title": "Bitcoin ETFs Post Strongest Weekly Inflows", "published_date": None},
    {"url": "https://www.youtube.com/watch?v=x", "title": "Bitcoin loses a key source of demand", "published_date": None},
    {"url": "https://finance.yahoo.com/b", "title": "Second Yahoo article", "published_date": None},
    {"url": "http://insecure.example/c", "title": "Plain http is skipped", "published_date": None},
    {"url": "https://sosovalue.com/etf", "title": "", "published_date": None},
    {"url": "https://bitbo.io/etf-flows", "title": "Bitcoin ETF Flows", "published_date": "2026-11-01"},
    {"url": "https://www.coinglass.com/etf", "title": "Live Spot ETF Flows", "published_date": None},
]
SEARCH_CALL = {"id": "otari_ws_1", "type": "web_search", "status": "completed", "query": "latest crypto market news", "sources": SOURCES}


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0),
                      mock.patch.object(llm, "_pause"), plain_config()):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()


class FeatureSwitchTest(Env):
    def test_a_tool_is_used_only_in_otari_mode_and_when_the_config_or_environment_says_so(self):
        cfg = {**llm.otari_config(), "features": {"web_search": True, "code_execution": False}}
        self.assertTrue(llm.feature_on("web_search", cfg))
        self.assertFalse(llm.feature_on("code_execution", cfg))
        self.assertFalse(llm.feature_on("mcp", cfg))
        with mock.patch.dict(os.environ, {"OTARI_CODE_EXECUTION": "1", "OTARI_WEB_SEARCH": "0"}):
            self.assertTrue(llm.feature_on("code_execution", cfg))
            self.assertFalse(llm.feature_on("web_search", cfg))
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}):
            self.assertFalse(llm.feature_on("web_search", cfg))

    def test_the_repo_config_turns_the_three_tools_on_for_roles_that_bill_their_own_end_user(self):
        real = json.loads((Path(__file__).resolve().parent.parent / "config" / "otari.json").read_text())
        self.assertEqual(real["features"], {"web_search": True, "code_execution": True, "mcp": True})
        for role in ("searcher", "sandbox", "mcp"):
            self.assertIn(role, real["roles"])

    def test_the_app_may_add_only_the_request_fields_it_names(self):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode()))
            return FakeResponse(BODY, HEADERS)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            llm.chat("s", "u", role="searcher", extra={"tools": [{"type": "otari_web_search", "max_uses": 1}]})
            with self.assertRaises(llm.LLMError):
                llm.chat("s", "u", role="searcher", extra={"api_key": "x"})
        self.assertEqual(sent[0]["tools"], [{"type": "otari_web_search", "max_uses": 1}])
        self.assertEqual(len(sent), 1)


class ReceiptToolCallsTest(Env):
    def test_the_receipt_keeps_what_otari_reports_about_its_search_without_page_text(self):
        body = {**BODY, "otari_tool_calls": [SEARCH_CALL]}
        r = llm.parse_receipt(HEADERS, body, "searcher", "deepseek/deepseek-v4-flash-0731")
        call = r["tool_calls"][0]
        self.assertEqual((call["type"], call["status"], call["query"], len(call["sources"])), ("web_search", "completed", "latest crypto market news", 7))
        self.assertEqual(set(call["sources"][0]), {"url", "title", "published_date"})
        self.assertNotIn("tool_calls", llm.parse_receipt(HEADERS, BODY, "bull", "x"))


class WebEvidenceTest(Env):
    def test_at_most_three_https_items_from_distinct_domains_with_a_title(self):
        items = otari_web.pick_items([SEARCH_CALL])
        self.assertEqual([i["domain"] for i in items], ["finance.yahoo.com", "youtube.com", "bitbo.io"])
        self.assertTrue(all(i["url"].startswith("https://") for i in items))
        self.assertEqual(items[2]["published_date"], "2026-11-01")

    def test_failed_or_foreign_calls_give_no_items(self):
        self.assertEqual(otari_web.pick_items([{**SEARCH_CALL, "status": "failed"}, {"type": "web_fetch", "status": "completed", "sources": SOURCES}]), [])

    def test_collect_asks_for_one_search_and_freezes_source_url_time_and_request_id(self):
        calls = []

        def ask(system, user, **kw):
            calls.append(kw)
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "searcher", "request_id": "req-web", "request_id_source": "otari",
                                   "captured_at": "2026-11-01T18:30:00Z", "cost_usd": "0.010215", "tool_calls": [SEARCH_CALL]}
            return "done"
        block = otari_web.collect(["BTCUSDT", "ETHUSDT"], ask=ask)
        self.assertEqual((calls[0]["role"], calls[0]["extra"]["tools"]), ("searcher", [{"type": "otari_web_search", "max_uses": 1}]))
        self.assertIn("BTC ETH", block["query"])
        self.assertEqual(len(block["items"]), 3)
        first = block["items"][0]
        self.assertEqual((first["fetched_at"], first["request_id"], first["source_tool"]), ("2026-11-01T18:30:00Z", "req-web", "otari_web_search"))
        self.assertEqual(block["receipt"]["cost_usd"], "0.010215")

    def test_a_refused_search_is_recorded_with_its_receipt_and_the_cycle_goes_on(self):
        def ask(system, user, **kw):
            llm._served.receipt = {"schema": "otari-receipt/1", "request_id": "req-r", "refusal": {"status": 403, "code": "budget_exceeded"}}
            raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded")
        block = otari_web.collect(["BTCUSDT"], ask=ask)
        self.assertEqual((block["items"], block["receipt"]["refusal"]["code"]), ([], "budget_exceeded"))
        self.assertIn("budget_exceeded", block["error"])

    def test_the_frozen_items_become_citable_evidence_and_the_replay_never_searches_again(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        pkt["web"] = {"method": "otari_web_search", "items": otari_web.pick_items([SEARCH_CALL])}
        items = citations.index(pkt, None)
        self.assertIn("Web (finance.yahoo.com): Bitcoin ETFs Post Strongest Weekly Inflows", items.values())
        stub = lambda system, user: json.dumps([{"claim": "Bitcoin ETFs Post Strongest Weekly Inflows", "cites": [next(k for k, v in items.items() if "Yahoo" not in v and "Strongest" in v)]}]) if "bull analyst" in system else "[]"
        path = Path(tempfile.mkdtemp()) / "l.jsonl"
        d = dossier.append(pkt, path, ask=stub, rules_version="v6")
        self.assertEqual(len(d["llm"]["bull"]["accepted"]), 1)
        self.assertEqual(dossier.verify(dossier.read_ledger(path)), [])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(dossier.read_ledger(path)[0], "auto")
        self.assertTrue(r["matches"] and r["llm_citations_valid"])

    def test_a_packet_without_web_evidence_indexes_exactly_as_before(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        before = citations.index(pkt, None)
        pkt["web"] = {"items": []}
        self.assertEqual(citations.index(pkt, None), before)


SSE = [b"event: message_start\n", b'data: {"type":"message_start"}\n', b"\n",
       b'data: {"content_block":{"id":"otari_mcptoolu_1","input":{"code":"async function run(client) { return 1 }","intent":"prices"},"name":"execute","server_name":"coingecko","type":"mcp_tool_use"},"index":0,"type":"content_block_start"}\n',
       b'data: {"content_block":{"content":"{\\n  \\"result\\": {\\n    \\"bitcoin\\": {\\n      \\"usd\\": 77886\\n    },\\n    \\"ethereum\\": {\\"usd\\": 2950.5},\\n    \\"solana\\": {\\"usd\\": 0}\\n  }\\n}","is_error":false,"tool_use_id":"otari_mcptoolu_1","type":"mcp_tool_result"},"index":1,"type":"content_block_start"}\n',
       b'data: {"delta":{"text":"99999","type":"text_delta"},"index":2,"type":"content_block_delta"}\n', b"data: [DONE]\n"]


class FakeStream:
    def __init__(self, lines, headers):
        self.lines, self.headers = lines, message(headers)

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class McpTest(Env):
    def test_the_stream_gives_the_tool_use_the_server_result_and_the_text(self):
        s = otari_mcp.parse_stream(SSE)
        self.assertEqual((s["calls"][0]["server"], s["calls"][0]["tool"]), ("coingecko", "execute"))
        self.assertFalse(s["results"][0]["is_error"])
        self.assertEqual(s["text"], "99999")

    def test_prices_come_from_the_servers_result_block_never_from_the_models_words(self):
        s = otari_mcp.parse_stream(SSE)
        self.assertEqual(otari_mcp.prices_from(s["results"]), {"BTC": 77886.0, "ETH": 2950.5})  # a zero price is not a price
        self.assertEqual(otari_mcp.prices_from([{"is_error": True, "content": '{"bitcoin":{"usd":1}}'}]), {})
        self.assertEqual(otari_mcp.prices_from([{"is_error": False, "content": "not json"}]), {})

    def test_collect_sends_a_streamed_messages_request_with_the_server_and_the_beta_and_keeps_the_proof(self):
        sent = []

        def opener(request, timeout=None):
            sent.append(request)
            return FakeStream(SSE, {**HEADERS, "Otari-Model": served_spelling("deepseek/deepseek-v4-flash-0731")})
        usage = lambda rid: {"request_id": rid, "cost_usd": "0.000079", "prompt_tokens": 1383, "completion_tokens": 104}
        block = otari_mcp.collect(opener=opener, usage=usage)
        request = sent[0]
        payload = json.loads(request.data.decode())
        self.assertTrue(request.full_url.endswith("/messages"))
        self.assertEqual(request.get_header("Anthropic-beta"), "mcp-client-2025-11-20")
        self.assertEqual((payload["stream"], payload["mcp_servers"][0]["name"], payload["mcp_servers"][0]["allowed_tools"]), (True, "coingecko", ["execute"]))
        self.assertEqual(block["prices"], {"BTC": 77886.0, "ETH": 2950.5})
        self.assertEqual((block["mcp_calls"][0]["tool"], block["receipt"]["request_id"], block["receipt"]["cost_usd"]), ("execute", "req-1111", "0.000079"))
        self.assertEqual(llm.cycle_status()["calls"], 1)
        self.assertNotIn(KEY, json.dumps(block))

    def test_a_gateway_refusal_or_a_cap_leaves_a_block_with_a_receipt_and_no_prices(self):
        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", message({"Otari-Request-ID": "req-m", "Otari-Error-Code": "budget_exceeded"}), io.BytesIO(b"{}"))
        block = otari_mcp.collect(opener=refuse)
        self.assertEqual((block["prices"], block["receipt"]["refusal"]["code"]), ({}, "budget_exceeded"))
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.0000001"}):
            llm._cycle["spent"] = llm.decimal.Decimal("1")
            block = otari_mcp.collect(opener=mock.Mock(side_effect=AssertionError("nothing may be sent")))
        self.assertEqual(block["receipt"]["refusal"]["code"], "cycle_cap_exceeded")

    def test_a_reply_with_no_tool_call_says_so(self):
        quiet = lambda request, timeout=None: FakeStream([b'data: {"delta":{"text":"77886","type":"text_delta"}}\n'], HEADERS)
        block = otari_mcp.collect(opener=quiet, usage=lambda rid: None)
        self.assertEqual((block["prices"], block["note"]), ({}, "Otari reported no MCP tool call"))

    def test_the_price_is_citable_evidence_for_its_own_symbol_only(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        pkt["market_mcp"] = {"prices": {"BTC": 77886.0, "ETH": 2950.5}}
        base = pkt["symbol"].upper().removesuffix("USDT")
        texts = list(citations.index(pkt, None).values())
        expected = {"BTC": "CoinGecko MCP via Otari: BTC spot price 77886.0 USD", "ETH": "CoinGecko MCP via Otari: ETH spot price 2950.5 USD"}
        self.assertIn(expected[base], texts)
        self.assertEqual(sum("CoinGecko MCP" in t for t in texts), 1)


def ledger_tail(n=4):
    path = Path(__file__).resolve().parent.parent / "ledger" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()[-n:]]


class SandboxScriptTest(Env):
    def test_the_self_contained_script_reproduces_real_recorded_verdicts_exactly(self):
        for d in ledger_tail(6):
            out = sandbox_rederive.run_local(sandbox_rederive.code_for(sandbox_rederive.inputs_from(d)))
            self.assertTrue(sandbox_rederive.agrees(out, d["verdict"]), d["seq"])

    def test_a_trade_is_rederived_with_stop_take_profit_and_size(self):
        d = next(x for x in reversed(ledger_tail(40)) if x["verdict"]["action"] in ("LONG", "SHORT")) if any(x["verdict"]["action"] in ("LONG", "SHORT") for x in ledger_tail(40)) else None
        if d is None:
            self.skipTest("no trade in the tail of the ledger")
        out = json.loads(sandbox_rederive.run_local(sandbox_rederive.code_for(sandbox_rederive.inputs_from(d))))
        self.assertEqual((out["trade"], out["stop"], out["size"]), (d["verdict"]["action"], d["verdict"]["stop"], d["verdict"]["size"]))

    def test_the_script_imports_nothing_from_this_project(self):
        self.assertNotIn("verdict_desk", sandbox_rederive.SCRIPT)
        self.assertEqual([l for l in sandbox_rederive.SCRIPT.splitlines() if l.startswith(("import", "from"))], ["import json"])

    def test_a_changed_weight_changes_the_output(self):
        d = ledger_tail(1)[0]
        inputs = sandbox_rederive.inputs_from(d)
        inputs["bull"] = inputs["bull"] + [9.0]
        self.assertFalse(sandbox_rederive.agrees(sandbox_rederive.run_local(sandbox_rederive.code_for(inputs)), d["verdict"]))


class SandboxSealTest(Env):
    def body(self):
        return ledger_tail(1)[0]

    def honest_ask(self, system, user, **kw):
        code = re.search(r"```python\n(.*)```", user, re.S).group(1)
        llm._served.receipt = {"schema": "otari-receipt/1", "role": "sandbox", "request_id": "req-sbx", "request_id_source": "otari", "cost_usd": "0.010074"}
        return sandbox_rederive.run_local(code)

    def test_identical_outputs_are_sealed_with_the_receipt_and_the_tool_declared(self):
        seen = []

        def ask(system, user, **kw):
            seen.append(kw)
            return self.honest_ask(system, user, **kw)
        block = sandbox_rederive.seal(self.body(), ask=ask)
        self.assertTrue(block["equal"] and block["local_agrees_with_verdict"])
        self.assertEqual(block["sandbox_stdout"], block["local_stdout"])
        self.assertEqual((seen[0]["role"], seen[0]["extra"]), ("sandbox", {"tools": [{"type": "otari_code_execution"}]}))
        self.assertEqual(block["receipt"]["request_id"], "req-sbx")
        self.assertEqual(len(block["code_sha256"]), 64)

    def test_a_sandbox_that_answers_differently_is_recorded_as_different_not_hidden(self):
        def ask(system, user, **kw):
            llm._served.receipt = {"request_id": "req-x"}
            return '{"edge": 0}'
        block = sandbox_rederive.seal(self.body(), ask=ask)
        self.assertFalse(block["equal"])
        self.assertTrue(block["local_agrees_with_verdict"])
        self.assertEqual(block["sandbox_stdout"], '{"edge": 0}')

    def test_a_fenced_answer_is_read_for_its_content(self):
        def ask(system, user, **kw):
            return "```json\n" + self.honest_ask(system, user, **kw) + "\n```"
        self.assertTrue(sandbox_rederive.seal(self.body(), ask=ask)["equal"])

    def test_a_failed_call_is_an_error_block_with_its_receipt_and_never_an_equal(self):
        def ask(system, user, **kw):
            llm._served.receipt = {"request_id": "req-e", "refusal": {"code": "budget_exceeded"}}
            raise llm.OtariError("HTTP 403", status=403)
        block = sandbox_rederive.seal(self.body(), ask=ask)
        self.assertFalse(block["equal"])
        self.assertIn("HTTP 403", block["error"])
        self.assertEqual(block["receipt"]["refusal"]["code"], "budget_exceeded")

    def test_the_local_recheck_follows_the_frozen_dossier_and_catches_tampering(self):
        d = self.body()
        d["sandbox"] = sandbox_rederive.seal(d, ask=self.honest_ask)
        self.assertEqual(sandbox_rederive.recheck(d), {"sandbox_local_matches": True, "sandbox_equal": True})
        tampered = json.loads(json.dumps(d))
        tampered["bull"][0]["weight"] += 1
        self.assertFalse(sandbox_rederive.recheck(tampered)["sandbox_local_matches"])
        forged = json.loads(json.dumps(d))
        forged["sandbox"]["local_stdout"] = forged["sandbox"]["local_stdout"].replace("edge", "edgy")
        self.assertFalse(sandbox_rederive.recheck(forged)["sandbox_local_matches"])
        self.assertEqual(sandbox_rederive.recheck({}), {})


class SandboxInDossierTest(Env):
    """A decision recorded with the sandbox on seals its output next to the replay, and the replay stays offline."""

    def setUp(self):
        super().setUp()
        cfg = json.loads(llm.OTARI_CONFIG.read_text())
        cfg["features"] = {"code_execution": True}
        patch = mock.patch.object(llm, "OTARI_CONFIG", self.write(cfg))
        patch.start()
        self.addCleanup(patch.stop)
        self.path = Path(tempfile.mkdtemp()) / "l.jsonl"
        self.n = 0

    def write(self, cfg):
        path = Path(tempfile.mkdtemp()) / "otari.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def urlopen(self, request, timeout=None):
        payload = json.loads(request.data.decode())
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        if system.startswith("You run code in the sandbox"):
            content = sandbox_rederive.run_local(re.search(r"```python\n(.*)```", user, re.S).group(1))
        elif "bull analyst" in system:
            content = json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}])
        elif "judge in a market debate" in system:
            content = '{"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "rally"}'
        elif "citation checker" in system:
            content = '[{"label": "B1", "supported": true, "note": "ok"}]'
        else:
            content = "[]"
        self.n += 1
        return FakeResponse({**BODY, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
                            {**HEADERS, "Otari-Request-ID": f"req-{self.n}", "Otari-Model": served_spelling(payload["model"])})

    def test_the_dossier_carries_the_sandbox_block_and_the_replay_rechecks_it_without_the_network(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            d = dossier.append(pkt, self.path, rules_version="v6")
        sb = d["sandbox"]
        self.assertTrue(sb["equal"] and sb["local_agrees_with_verdict"])
        self.assertEqual(sb["receipt"]["role"], "sandbox")
        ledger = dossier.read_ledger(self.path)
        self.assertEqual(dossier.verify(ledger), [])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(ledger[0], "auto")
        self.assertTrue(r["matches"])
        self.assertEqual((r["sandbox_local_matches"], r["sandbox_equal"]), (True, True))

    def test_a_dossier_whose_arguments_were_edited_fails_the_replay_through_the_sandbox_recheck_too(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        with mock.patch.object(urllib.request, "urlopen", self.urlopen):
            d = dossier.append(pkt, self.path, rules_version="v6")
        d["verdict"] = {**d["verdict"], "edge": d["verdict"]["edge"] + 1}
        self.assertFalse(dossier.replay(d, "auto")["matches"])

    def test_without_the_feature_or_with_an_injected_ask_no_sandbox_block_is_made(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        d = dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")
        self.assertNotIn("sandbox", d)
        with mock.patch.dict(os.environ, {"OTARI_CODE_EXECUTION": "0"}), mock.patch.object(urllib.request, "urlopen", self.urlopen):
            d = dossier.build(pkt, dossier.GENESIS, 1, rules_version="v6")
        self.assertNotIn("sandbox", d)


if __name__ == "__main__":
    unittest.main()
