"""F3: Otari's web search, the public MCP server and the code sandbox, used by the app and sealed in the dossier. No network."""

import io
import json
import types
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
from verdict_desk import __main__ as cli
from verdict_desk import citations, dossier, evidence, llm, narrative, otari_mcp, otari_web, sandbox_rederive

KEY = "unit-test-secret-0123456789"
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "MISTRAL_API_KEY": "", "OTARI_USE_POLICIES": "", "OTARI_CYCLE_CAP_USD": "",
       "OTARI_POLICY_JUDGE": "", "OTARI_WEB_SEARCH": "", "OTARI_CODE_EXECUTION": "", "OTARI_MCP": "", "OTARI_MIN_INTERVAL": "", "OTARI_TOOL_CAP_USD": ""}


SOURCES = [
    {"url": "https://www.youtube.com/watch?v=x", "title": "Bitcoin loses a key source of demand", "published_date": None},
    {"url": "https://news.example.com", "title": "A bare domain is not an article", "published_date": "2026-11-01"},
    {"url": "https://finance.yahoo.com/news/bitcoin-etfs-post-strongest-weekly-inflows-1.html", "title": "Bitcoin ETFs Post Strongest Weekly Inflows", "published_date": None},
    {"url": "https://thecryptohodl.com/tag/current-crypto-market", "title": "current crypto market Archives", "published_date": "2026-11-01"},
    {"url": "https://finance.yahoo.com/news/second-yahoo-article-2.html", "title": "Second Yahoo article", "published_date": None},
    {"url": "http://insecure.example/c/story", "title": "Plain http is skipped", "published_date": None},
    {"url": "https://sosovalue.com/etf/story", "title": "", "published_date": None},
    {"url": "https://bitbo.io/news/etf-flows-today", "title": "Bitcoin ETF Flows Today", "published_date": "2026-11-01"},
    {"url": "https://www.coinglass.com/news/live-spot-etf-flows", "title": "Live Spot ETF Flows", "published_date": None},
]
SEARCH_CALL = {"id": "otari_ws_1", "type": "web_search", "status": "completed", "query": "Bitcoin rally gains news 2026-11-01", "sources": SOURCES}
PAGE_TEXT = "Bitcoin ETFs recorded their strongest weekly inflows in ten months as the price surged past its October high."
FETCH_CALL = {"id": "otari_wf_1", "type": "web_fetch", "status": "completed", "url": "https://bitbo.io/news/etf-flows-today"}


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

    def test_the_repo_config_turns_the_three_tools_on_and_marks_the_four_tool_roles(self):
        real = json.loads((Path(__file__).resolve().parent.parent / "config" / "otari.json").read_text())
        self.assertEqual(real["features"], {"web_search": True, "code_execution": True, "mcp": True})
        for role in ("searcher", "fetcher", "sandbox", "mcp"):
            self.assertTrue(real["roles"][role].get("tool_role"), role)
        self.assertEqual(real["market_check_limit_pct"], 1.0)

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
        r = llm.parse_receipt(HEADERS, {**BODY, "otari_tool_calls": [SEARCH_CALL, FETCH_CALL]}, "searcher", "deepseek/deepseek-v4-flash-0731")
        call = r["tool_calls"][0]
        self.assertEqual((call["type"], call["status"], len(call["sources"])), ("web_search", "completed", len(SOURCES)))
        self.assertEqual(set(call["sources"][0]), {"url", "title", "published_date"})
        self.assertEqual((r["tool_calls"][1]["type"], r["tool_calls"][1]["url"]), ("web_fetch", FETCH_CALL["url"]))
        self.assertNotIn("tool_calls", llm.parse_receipt(HEADERS, BODY, "bull", "x"))


class CycleCapsTest(Env):
    """Tools have a cap of their own: a one-cent tool call can never use up the money bull, bear and the judge need."""

    def fake(self, cost):
        return mock.patch.object(urllib.request, "urlopen", lambda r, timeout=None: FakeResponse({**BODY, "usage": {**BODY["usage"], "cost_usd": cost}}, HEADERS))

    def test_tool_spend_does_not_stop_the_analysts_and_analyst_spend_does_not_stop_the_tools(self):
        with mock.patch.dict(os.environ, {"OTARI_CYCLE_CAP_USD": "0.001", "OTARI_TOOL_CAP_USD": "0.001"}), self.fake("0.010"):
            llm.chat("s", "u", role="searcher")  # a tool call that costs a cent: the tool cap is now spent
            llm.chat("s", "u", role="bull")  # the analysts' cap is untouched
            with self.assertRaises(llm.OtariError) as tools_over:
                llm.chat("s", "u", role="sandbox")
            with self.assertRaises(llm.OtariError) as core_over:
                llm.chat("s", "u", role="bear")  # now the analysts' cap is spent too
        self.assertEqual((tools_over.exception.scope, core_over.exception.scope), ("cycle_tools", "cycle"))
        status = llm.cycle_status()
        self.assertEqual((status["tools"]["calls"], status["tools"]["refused"], status["calls"], status["refused"]), (1, 1, 1, 1))
        self.assertEqual(status["total_spent_usd"], "0.020000")
        self.assertEqual(status["tools"]["spent_usd"], "0.010000")

    def test_the_repo_config_gives_the_tools_room_for_a_search_a_fetch_and_a_few_sandbox_runs(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "otari.json").read_text())
        self.assertEqual((cfg["cycle_cap_usd"], cfg["tool_cap_usd"]), ("0.02", "0.06"))


class WebQueryTest(Env):
    def test_the_query_follows_the_narrative_convergence_and_the_date(self):
        conv = {"convergence": [{"token": "ETH", "bias": "bearish"}, {"token": "BTC", "bias": "bullish"}]}
        self.assertEqual(otari_web.query_for(["BTCUSDT"], conv, "2026-11-01"), "Bitcoin rally gains news 2026-11-01")
        self.assertEqual(otari_web.query_for(["ETHUSDT", "BTCUSDT"], conv, "2026-11-01"), "Ethereum selloff drop news 2026-11-01")
        self.assertEqual(otari_web.query_for(["SOLUSDT"], None, "2026-11-01"), "Solana price news news 2026-11-01")
        self.assertEqual(otari_web.query_for(["BTCUSDT"], {"convergence": []}, "2026-11-01"), "Bitcoin price news news 2026-11-01")

    def test_only_pages_with_a_path_of_their_own_count_as_articles(self):
        for url in ("https://news.example.com", "https://news.example.com/", "https://x.com/tag/bitcoin", "https://x.com/category/news/", "http://x.com/a/b"):
            self.assertFalse(otari_web.is_article(url), url)
        self.assertTrue(otari_web.is_article("https://x.com/news/bitcoin-rally-2026"))

    def test_items_are_articles_one_per_domain_dated_first_at_most_three(self):
        items = otari_web.pick_items([SEARCH_CALL])
        self.assertEqual([i["domain"] for i in items], ["bitbo.io", "youtube.com", "finance.yahoo.com"])  # the one dated article leads
        self.assertEqual(items[0]["published_date"], "2026-11-01")
        self.assertTrue(all(otari_web.is_article(i["url"]) for i in items))

    def test_failed_or_foreign_calls_give_no_items(self):
        self.assertEqual(otari_web.pick_items([{**SEARCH_CALL, "status": "failed"}, {"type": "web_fetch", "status": "completed", "sources": SOURCES}]), [])


class WebCollectTest(Env):
    def asks(self, fetch_text='{"excerpt": "%s"}' % PAGE_TEXT, fetch_calls=(FETCH_CALL,), refuse_fetch=False):
        calls = []

        def ask(system, user, **kw):
            calls.append((kw["role"], user, kw["extra"]["tools"]))
            if kw["role"] == "searcher":
                llm._served.receipt = {"schema": "otari-receipt/1", "role": "searcher", "request_id": "req-search", "request_id_source": "otari",
                                       "captured_at": "2026-11-01T18:30:00Z", "cost_usd": "0.010215", "tool_calls": [SEARCH_CALL]}
                return "done"
            if refuse_fetch:
                llm._served.receipt = {"schema": "otari-receipt/1", "request_id": "req-fr", "refusal": {"status": 403, "code": "budget_exceeded"}}
                raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded")
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "fetcher", "request_id": "req-fetch", "request_id_source": "otari",
                                   "captured_at": "2026-11-01T18:30:05Z", "cost_usd": "0.010", "tool_calls": list(fetch_calls)}
            return fetch_text
        ask.calls = calls
        return ask

    def test_one_search_then_one_fetch_of_the_first_item_with_the_excerpt_sealed_by_its_hash(self):
        ask = self.asks()
        block = otari_web.collect(["BTCUSDT"], {"convergence": [{"token": "BTC", "bias": "bullish"}]}, today="2026-11-01", ask=ask,
                                  opener=lambda request, timeout=None: _Page("<html><body><p>" + PAGE_TEXT + " More text.</p></body></html>"))
        self.assertEqual([c[0] for c in ask.calls], ["searcher", "fetcher"])
        self.assertEqual(ask.calls[0][2], [{"type": "otari_web_search", "max_uses": 1}])
        self.assertEqual(ask.calls[1][2], [{"type": "otari_web_fetch"}])
        self.assertIn("Bitcoin rally gains news 2026-11-01", block["query"])
        self.assertEqual(block["items"][0]["url"], FETCH_CALL["url"])
        first = block["items"][0]
        self.assertEqual((first["fetched_at"], first["request_id"], first["source_tool"]), ("2026-11-01T18:30:00Z", "req-search", "otari_web_search"))
        page = block["page"]
        import hashlib
        self.assertEqual(page["excerpt"], PAGE_TEXT)
        self.assertEqual(page["excerpt_sha256"], hashlib.sha256(PAGE_TEXT.encode()).hexdigest())
        self.assertEqual((page["request_id"], page["otari_reported_fetch"], page["excerpt_verified"]), ("req-fetch", True, True))
        self.assertIn("relay", page["excerpt_source"])

    def test_an_excerpt_the_page_does_not_contain_is_marked_unverified_and_an_unreadable_page_is_unknown(self):
        ask = self.asks(fetch_text='{"excerpt": "An invented opening sentence that is nowhere on the page at all."}')
        block = otari_web.collect(["BTCUSDT"], ask=ask, opener=lambda r, timeout=None: _Page("<p>" + PAGE_TEXT + "</p>"))
        self.assertIs(block["page"]["excerpt_verified"], False)
        ask = self.asks()

        def blocked(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", message({}), io.BytesIO(b""))
        self.assertIsNone(otari_web.collect(["BTCUSDT"], ask=ask, opener=blocked)["page"]["excerpt_verified"])

    def test_a_reply_without_an_excerpt_or_a_refused_fetch_leaves_a_page_error_and_keeps_the_items(self):
        block = otari_web.collect(["BTCUSDT"], ask=self.asks(fetch_text="I could not read it"), opener=lambda r, timeout=None: _Page("x"))
        self.assertEqual((len(block["items"]) > 0, block["page"]["error"]), (True, "the reply carried no excerpt"))
        block = otari_web.collect(["BTCUSDT"], ask=self.asks(refuse_fetch=True))
        self.assertIn("budget_exceeded", block["page"]["error"])
        self.assertEqual(block["page"]["receipt"]["refusal"]["code"], "budget_exceeded")
        self.assertTrue(block["items"])

    def test_a_search_that_finds_no_article_does_not_fetch_and_a_refused_search_is_recorded(self):
        ask = self.asks()

        def only_indexes(system, user, **kw):
            ask(system, user, **kw)
            llm._served.receipt["tool_calls"] = [{**SEARCH_CALL, "sources": [SOURCES[1], SOURCES[3]]}]
            return "done"
        block = otari_web.collect(["BTCUSDT"], ask=only_indexes)
        self.assertEqual((block["items"], block["note"], "page" in block), ([], "the search returned no article page", False))

        def refuse(system, user, **kw):
            llm._served.receipt = {"schema": "otari-receipt/1", "request_id": "req-r", "refusal": {"status": 403, "code": "budget_exceeded"}}
            raise llm.OtariError("HTTP 403 budget_exceeded", status=403, code="budget_exceeded")
        block = otari_web.collect(["BTCUSDT"], ask=refuse)
        self.assertEqual((block["items"], block["receipt"]["refusal"]["code"]), ([], "budget_exceeded"))

    def test_the_frozen_items_and_excerpt_are_citable_the_dossier_counts_the_citations_and_the_replay_never_fetches(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        pkt["web"] = otari_web.collect(["BTCUSDT"], ask=self.asks(), opener=lambda r, timeout=None: _Page("<p>" + PAGE_TEXT + "</p>"))
        index = citations.index(pkt, None)
        excerpt_id = next(k for k, v in index.items() if v.startswith("Web page excerpt"))
        title_id = next(k for k, v in index.items() if v.startswith("Web (bitbo.io)"))
        self.assertIn(PAGE_TEXT, index[excerpt_id])

        def stub(system, user):
            if "bull analyst" in system:
                return json.dumps([{"claim": "Bitcoin ETFs recorded their strongest weekly inflows in ten months", "cites": [excerpt_id]}])
            return "[]"
        path = Path(tempfile.mkdtemp()) / "l.jsonl"
        d = dossier.append(pkt, path, ask=stub, rules_version="v6")
        self.assertEqual(len(d["llm"]["bull"]["accepted"]), 1)
        use = d["llm"]["otari"]["evidence_use"]
        self.assertEqual((use["web_items"], use["web_items_cited"]), (len([k for k, v in index.items() if v.startswith("Web ")]), 1))
        self.assertNotEqual(title_id, excerpt_id)
        self.assertEqual(dossier.verify(dossier.read_ledger(path)), [])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(dossier.read_ledger(path)[0], "auto")
        self.assertTrue(r["matches"] and r["llm_citations_valid"])

    def test_a_packet_without_web_evidence_indexes_exactly_as_before(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        before = citations.index(pkt, None)
        pkt["web"] = {"items": []}
        self.assertEqual(citations.index(pkt, None), before)


class _Page:
    def __init__(self, text):
        self._raw = text.encode()

    def read(self, n=-1):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


GOOD_CODE = "async function run(client) {\n  const price = await client.simple.price.get({ ids: 'bitcoin,ethereum,solana', vs_currencies: 'usd' });\n  return price;\n}"
RESULT = "{\n  \"result\": {\n    \"bitcoin\": {\n      \"usd\": 133.2\n    },\n    \"ethereum\": {\"usd\": 2950.5},\n    \"solana\": {\"usd\": 0}\n  }\n}"


def sse(code=GOOD_CODE, result=RESULT, text="99999"):
    use = {"content_block": {"id": "otari_mcptoolu_1", "input": {"code": code, "intent": "prices"}, "name": "execute", "server_name": "coingecko", "type": "mcp_tool_use"}, "index": 0, "type": "content_block_start"}
    res = {"content_block": {"content": result, "is_error": False, "tool_use_id": "otari_mcptoolu_1", "type": "mcp_tool_result"}, "index": 1, "type": "content_block_start"}
    return [b"event: message_start\n", b'data: {"type":"message_start"}\n', b"\n", ("data: " + json.dumps(use) + "\n").encode(), ("data: " + json.dumps(res) + "\n").encode(),
            ('data: {"delta":{"text":"%s","type":"text_delta"},"index":2,"type":"content_block_delta"}\n' % text).encode(), b"data: [DONE]\n"]


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
    def collect(self, lines, **headers):
        sent = []

        def opener(request, timeout=None):
            sent.append(request)
            return FakeStream(lines, {**HEADERS, "Otari-End-User-Budget": "52a154a1-a369-45b9-8922-6728c4fe53c6", **headers})
        usage = lambda rid: {"request_id": rid, "cost_usd": "0.000079", "prompt_tokens": 1383, "completion_tokens": 104}
        with mock.patch.object(llm, "usage_for", usage):
            block = otari_mcp.collect(opener=opener)
        return block, sent

    def test_the_code_must_be_the_one_price_query_with_no_digit(self):
        self.assertEqual(otari_mcp.code_trusted(GOOD_CODE), (True, None))
        self.assertTrue(otari_mcp.code_trusted("async function run(client) { return await client.simple.price.get({ids: 'bitcoin', vs_currencies: 'usd'}); }")[0])
        for bad in ("async function run(client) { return {bitcoin: {usd: other}}; }",
                    "async function run(client) { const p = await client.simple.price.get({ids:'bitcoin'}); p.bitcoin.usd = 90000; return p; }",
                    "async function run(client) { return await client.simple.price.get({ids: 'bitcoin1', vs_currencies: 'usd'}); }",
                    "async function run(client) { await fetch('https://evil'); return await client.simple.price.get({ids: 'bitcoin', vs_currencies: 'usd'}); }",
                    None, 42):
            self.assertFalse(otari_mcp.code_trusted(bad)[0], bad)

    def test_the_stream_gives_the_tool_use_the_server_result_the_text_and_error_events(self):
        s = otari_mcp.parse_stream(sse())
        self.assertEqual((s["calls"][0]["server"], s["calls"][0]["tool"]), ("coingecko", "execute"))
        self.assertEqual((s["results"][0]["is_error"], s["text"], s["errors"]), (False, "99999", []))
        s = otari_mcp.parse_stream([b'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n'])
        self.assertEqual(s["errors"], ["Overloaded"])

    def test_a_tool_input_that_arrives_in_json_pieces_is_put_together(self):
        pieces = [b'data: {"type":"content_block_start","index":0,"content_block":{"type":"mcp_tool_use","id":"u1","name":"execute","server_name":"coingecko","input":{}}}\n',
                  b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"code\\": \\"async function"}}\n',
                  b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":" run(client) {}\\"}"}}\n',
                  b'data: {"type":"content_block_stop","index":0}\n']
        call = otari_mcp.parse_stream(pieces)["calls"][0]
        self.assertEqual(call["input"], {"code": "async function run(client) {}"})
        self.assertNotIn("_json", call)

    def test_prices_come_from_the_servers_result_never_from_the_models_words(self):
        s = otari_mcp.parse_stream(sse())
        self.assertEqual(otari_mcp.prices_from(s["results"]), {"BTC": 133.2, "ETH": 2950.5})  # a zero price is not a price
        self.assertEqual(otari_mcp.prices_from([{"is_error": True, "content": '{"bitcoin":{"usd":1}}'}]), {})
        self.assertEqual(otari_mcp.prices_from([{"is_error": False, "content": "not json"}]), {})

    def test_collect_sends_a_streamed_messages_request_and_keeps_proof_trust_and_the_budget_echo(self):
        block, sent = self.collect(sse())
        request, payload = sent[0], json.loads(sent[0].data.decode())
        self.assertTrue(request.full_url.endswith("/messages"))
        self.assertEqual(request.get_header("Anthropic-beta"), "mcp-client-2025-11-20")
        self.assertEqual((payload["stream"], payload["mcp_servers"][0]["name"], payload["mcp_servers"][0]["allowed_tools"]), (True, "coingecko", ["execute"]))
        self.assertEqual((block["trusted"], block["prices"]), (True, {"BTC": 133.2, "ETH": 2950.5}))
        r = block["receipt"]
        self.assertEqual((r["request_id"], r["cost_usd"], r["end_user_budget"], r["role"]), ("req-1111", "0.000079", "52a154a1-a369-45b9-8922-6728c4fe53c6", "mcp"))
        self.assertEqual(llm.cycle_status()["tools"]["calls"], 1)
        self.assertNotIn(KEY, json.dumps(block))

    def test_a_model_that_typed_a_price_into_the_code_gets_its_prices_marked_untrusted_and_unused(self):
        block, _ = self.collect(sse(code="async function run(client) { return {bitcoin: {usd: 133}}; }"))
        self.assertFalse(block["trusted"])
        self.assertEqual(block["prices"], {})
        self.assertEqual(block["prices_unverified"], {"BTC": 133.2, "ETH": 2950.5})
        self.assertIn("digit", block["untrusted_reason"])

    def test_a_gateway_refusal_or_a_cap_leaves_a_block_with_a_receipt_and_no_prices(self):
        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", message({"Otari-Request-ID": "req-m", "Otari-Error-Code": "budget_exceeded"}), io.BytesIO(b"{}"))
        block = otari_mcp.collect(opener=refuse)
        self.assertEqual((block["prices"], block["receipt"]["refusal"]["code"]), ({}, "budget_exceeded"))
        with mock.patch.dict(os.environ, {"OTARI_TOOL_CAP_USD": "0.0000001"}):
            llm._cycle["tspent"] = llm.decimal.Decimal("1")
            block = otari_mcp.collect(opener=mock.Mock(side_effect=AssertionError("nothing may be sent")))
        self.assertEqual((block["receipt"]["refusal"]["code"], block["receipt"]["refusal"]["budget_scope"]), ("cycle_cap_exceeded", "cycle_tools"))

    def test_a_reply_with_no_tool_call_says_so(self):
        block, _ = self.collect([b'data: {"delta":{"text":"77886","type":"text_delta"}}\n'])
        self.assertEqual((block["prices"], block["note"]), ({}, "Otari reported no MCP tool call"))


class McpCheckTest(Env):
    """The MCP price takes part in the decision: more than 1 percent from the exchange close and the verdict refuses to decide."""

    def pkt(self):
        return packet(0.004, titles=("Bitcoin rally hits record",))

    def block(self, price, trusted=True):
        return {"method": "otari_mcp", "server": "coingecko", "trusted": trusted, "prices": {"BTC": price} if trusted else {}, "untrusted_reason": None if trusted else "typed price",
                "receipt": {"request_id": "req-m"}}

    def test_a_price_within_the_limit_passes_and_the_difference_is_recorded(self):
        pkt = self.pkt()
        close = pkt["market"]["candles"][-1]["c"]
        check = otari_mcp.apply(pkt, self.block(round(close * 1.005, 4)))
        self.assertEqual((check["status"], check["ok"], check["limit_pct"]), ("ok", True, 1.0))
        self.assertAlmostEqual(check["diff_pct"], 0.5, places=1)
        self.assertEqual(pkt["market_mcp"]["check"], check)
        self.assertNotIn("freshness", pkt)

    def test_a_price_beyond_the_limit_marks_the_freshness_not_ok_with_the_numbers_as_the_reason(self):
        pkt = self.pkt()
        close = pkt["market"]["candles"][-1]["c"]
        check = otari_mcp.apply(pkt, self.block(round(close * 1.05, 4)))
        self.assertEqual((check["status"], check["ok"]), ("mismatch", False))
        self.assertFalse(pkt["freshness"]["ok"])
        self.assertIn("differs 5.0", pkt["freshness"]["reason"])

    def test_an_untrusted_or_missing_price_is_unavailable_and_blocks_nothing(self):
        for block in (self.block(1.0, trusted=False), {"error": "HTTP 403", "trusted": False, "prices": {}}, {**self.block(1.0), "prices": {"ETH": 2000.0}}):
            pkt = self.pkt()
            check = otari_mcp.apply(pkt, block)
            self.assertEqual((check["status"], check["ok"]), ("unavailable", True))
            self.assertNotIn("freshness", pkt)

    def test_a_mismatch_is_a_recorded_refusal_with_no_position_and_the_replay_rechecks_it_offline(self):
        pkt = self.pkt()
        otari_mcp.apply(pkt, self.block(round(pkt["market"]["candles"][-1]["c"] * 1.05, 4)))
        path = Path(tempfile.mkdtemp()) / "l.jsonl"
        d = dossier.append(pkt, path, ask=lambda s, u: "[]", rules_version="v6")
        self.assertEqual(d["verdict"]["action"], "BLOCKED_STALE")
        self.assertNotIn("entry", d["verdict"])
        self.assertIn("MCP spot price", d["verdict"]["rationale"])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("network"))):
            r = dossier.replay(dossier.read_ledger(path)[0], "auto")
        self.assertTrue(r["matches"])
        self.assertEqual((r["market_check_ok"], r["market_check_status"]), (True, "mismatch"))

    def test_the_recheck_catches_an_edited_check_or_a_mismatch_that_was_not_refused(self):
        pkt = self.pkt()
        otari_mcp.apply(pkt, self.block(round(pkt["market"]["candles"][-1]["c"] * 1.05, 4)))
        d = dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")
        forged = json.loads(json.dumps(d))
        forged["evidence"]["market_mcp"]["check"]["diff_pct"] = 0.1
        self.assertFalse(otari_mcp.recheck(forged)["market_check_ok"])
        lenient = json.loads(json.dumps(d))
        lenient["features"]["freshness"] = {"ok": True}
        self.assertFalse(otari_mcp.recheck(lenient)["market_check_ok"])
        self.assertEqual(otari_mcp.recheck({"evidence": {}}), {})

    def test_the_trusted_price_is_citable_evidence_for_its_own_symbol_only(self):
        pkt = self.pkt()
        pkt["market_mcp"] = {"trusted": True, "prices": {"BTC": 133.2, "ETH": 2950.5}}
        texts = list(citations.index(pkt, None).values())
        self.assertIn("CoinGecko MCP via Otari: BTC spot price 133.2 USD", texts)
        self.assertEqual(sum("CoinGecko MCP" in t for t in texts), 1)


def ledger_tail(n=4):
    path = Path(__file__).resolve().parent.parent / "ledger" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()[-n:]]


TRADE_ARGS = (0.006, -0.0005, ("Bitcoin rally hits record", "ETF inflow surge", "adoption soar"))  # an uptrend whose edge opens a LONG under rules v6


def stale_dossier():
    pkt = packet(*TRADE_ARGS)
    pkt["freshness"] = {"ok": False, "reason": "the newest candle is older than the market window"}
    return dossier.build(pkt, dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")


class SandboxScriptTest(Env):
    def run_for(self, d, nonce="n-1"):
        return sandbox_rederive.run_local(sandbox_rederive.code_for({**sandbox_rederive.inputs_from(d), "nonce": nonce}))

    def test_the_script_reproduces_real_recorded_verdicts_exactly(self):
        for d in ledger_tail(6):
            self.assertTrue(sandbox_rederive.agrees(self.run_for(d), d["verdict"]), d["seq"])

    def test_a_stale_dossier_is_refused_by_the_script_too_even_when_the_edge_would_open_a_trade(self):
        d = stale_dossier()
        self.assertEqual(d["verdict"]["action"], "BLOCKED_STALE")
        self.assertGreaterEqual(abs(d["verdict"]["edge"]), d["verdict"]["threshold"])  # the edge alone would have traded
        out = json.loads(self.run_for(d))
        self.assertEqual((out["trade"], out["blocked"]), ("NONE", True))
        self.assertTrue(sandbox_rederive.agrees(json.dumps(out), d["verdict"]))
        self.assertFalse(sandbox_rederive.inputs_from(d)["fresh_ok"])

    def test_a_fresh_trade_is_rederived_with_stop_take_profit_and_size(self):
        d = dossier.build(packet(*TRADE_ARGS), dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")
        self.assertEqual(d["verdict"]["action"], "LONG")
        out = json.loads(self.run_for(d))
        self.assertEqual((out["trade"], out["stop"], out["size"], out["blocked"]), ("LONG", d["verdict"]["stop"], d["verdict"]["size"], False))

    def test_the_script_imports_nothing_from_this_project(self):
        self.assertNotIn("verdict_desk", sandbox_rederive.SCRIPT)
        self.assertEqual([l for l in sandbox_rederive.SCRIPT.splitlines() if l.startswith(("import", "from"))], ["import hashlib, json"])

    def test_the_proof_is_the_hash_of_the_whole_data_line_so_a_new_nonce_changes_it(self):
        d = ledger_tail(1)[0]
        a, b = json.loads(self.run_for(d, "n-1")), json.loads(self.run_for(d, "n-2"))
        self.assertNotEqual(a["proof"], b["proof"])
        self.assertEqual(len(a["proof"]), 64)

    def test_a_changed_weight_changes_the_output(self):
        d = ledger_tail(1)[0]
        inputs = {**sandbox_rederive.inputs_from(d), "nonce": "n"}
        inputs["bull"] = inputs["bull"] + [9.0]
        self.assertFalse(sandbox_rederive.agrees(sandbox_rederive.run_local(sandbox_rederive.code_for(inputs)), d["verdict"]))


def native_reply(code, stdout, code_ran=None, text=None, return_code=0):
    return {"id": "m", "content": [{"type": "server_tool_use", "id": "srv1", "name": "code_execution", "input": {"code": code if code_ran is None else code_ran}},
                                   {"type": "code_execution_tool_result", "tool_use_id": "srv1", "content": {"type": "code_execution_result", "stdout": stdout + "\n", "stderr": "", "return_code": return_code, "content": []}},
                                   {"type": "text", "text": stdout if text is None else text}],
            "usage": {"input_tokens": 700, "output_tokens": 170, "cost_usd": "0.010081", "pricing_source": "deployment"}}


class SandboxSealTest(Env):
    def body(self):
        return ledger_tail(1)[0]

    def honest(self, **overrides):
        def ask(system, prompt):
            code = re.search(r"```python\n(.*)```", prompt, re.S).group(1)
            stdout = sandbox_rederive.run_local(code)
            llm._served.receipt = {"schema": "otari-receipt/1", "role": "sandbox", "request_id": "req-sbx", "request_id_source": "otari", "cost_usd": "0.010074"}
            return native_reply(code, stdout, **overrides)
        return ask

    def test_the_sandboxs_own_result_blocks_are_sealed_with_the_code_it_ran_and_the_proof(self):
        block = sandbox_rederive.seal(self.body(), ask=self.honest())
        self.assertEqual((block["schema"], block["evidence"]), ("otari-sandbox/2", "native"))
        self.assertTrue(block["equal"] and block["proof_ok"] and block["code_ran_is_code_sent"] and block["local_agrees_with_verdict"])
        self.assertEqual(block["sandbox_stdout"], block["local_stdout"])
        self.assertEqual((block["sandbox_return_code"], block["receipt"]["request_id"], block["code_ran_sha256"]), (0, "req-sbx", block["code_sha256"]))
        self.assertTrue(block["model_text_matches"])

    def test_every_call_has_a_new_nonce_and_so_a_new_code_hash(self):
        a = sandbox_rederive.seal(self.body(), ask=self.honest())
        b = sandbox_rederive.seal(self.body(), ask=self.honest())
        self.assertNotEqual((a["inputs"]["nonce"], a["code_sha256"]), (b["inputs"]["nonce"], b["code_sha256"]))

    def test_a_sandbox_that_ran_other_code_or_printed_other_numbers_is_recorded_as_different(self):
        other = sandbox_rederive.seal(self.body(), ask=self.honest(code_ran="print('hi')"))
        self.assertFalse(other["code_ran_is_code_sent"])
        self.assertFalse(other["equal"])

        def altered(system, prompt):
            reply = self.honest()(system, prompt)
            reply["content"][1]["content"]["stdout"] = reply["content"][1]["content"]["stdout"].replace("edge", "edgy")
            return reply
        block = sandbox_rederive.seal(self.body(), ask=altered)
        self.assertFalse(block["equal"])
        self.assertNotEqual(block["sandbox_stdout"], block["local_stdout"])

    def test_without_native_blocks_the_model_text_is_the_evidence_and_the_proof_line_still_binds_it_to_the_nonce(self):
        def text_only(system, prompt):
            code = re.search(r"```python\n(.*)```", prompt, re.S).group(1)
            return {"content": [{"type": "text", "text": "```json\n" + sandbox_rederive.run_local(code) + "\n```"}]}
        block = sandbox_rederive.seal(self.body(), ask=text_only)
        self.assertEqual(block["evidence"], "model_text")
        self.assertTrue(block["equal"] and block["proof_ok"])
        invented = sandbox_rederive.seal(self.body(), ask=lambda s, p: {"content": [{"type": "text", "text": json.dumps({**json.loads(sandbox_rederive.run_local(re.search(r"```python\n(.*)```", p, re.S).group(1))), "proof": "0" * 64})}]})
        self.assertFalse(invented["proof_ok"] or invented["equal"])

    def test_a_failed_call_is_an_error_block_with_its_receipt_never_an_equal(self):
        def ask(system, prompt):
            llm._served.receipt = {"request_id": "req-e", "refusal": {"code": "budget_exceeded"}}
            raise llm.OtariError("HTTP 403", status=403)
        block = sandbox_rederive.seal(self.body(), ask=ask)
        self.assertFalse(block["equal"])
        self.assertIn("HTTP 403", block["error"])
        self.assertEqual(block["receipt"]["refusal"]["code"], "budget_exceeded")

    def test_the_call_uses_the_messages_api_with_the_anthropic_declaration_and_the_otari_executor_header(self):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(request)
            payload = json.loads(request.data.decode())
            code = re.search(r"```python\n(.*)```", payload["messages"][0]["content"], re.S).group(1)
            return FakeResponse(native_reply(code, sandbox_rederive.run_local(code)), HEADERS)
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            block = sandbox_rederive.seal(self.body())
        request = sent[0]
        self.assertTrue(request.full_url.endswith("/messages"))
        self.assertEqual(request.get_header("Otari-code-execution"), "otari")
        self.assertEqual(json.loads(request.data.decode())["tools"], [{"type": "code_execution_20250825", "name": "code_execution"}])
        self.assertTrue(block["equal"])
        self.assertEqual((block["receipt"]["cost_usd"], block["receipt"]["role"]), ("0.010081", "sandbox"))
        self.assertEqual(llm.cycle_status()["tools"]["calls"], 1)

    def test_the_local_recheck_follows_the_frozen_dossier_and_catches_tampering(self):
        d = self.body()
        d["sandbox"] = sandbox_rederive.seal(d, ask=self.honest())
        self.assertEqual(sandbox_rederive.recheck(d), {"sandbox_sealed": True, "sandbox_local_matches": True, "sandbox_equal": True})
        tampered = json.loads(json.dumps(d))
        tampered["bull"][0]["weight"] += 1
        self.assertFalse(sandbox_rederive.recheck(tampered)["sandbox_local_matches"])
        forged = json.loads(json.dumps(d))
        forged["sandbox"]["local_stdout"] = forged["sandbox"]["local_stdout"].replace("edge", "edgy")
        self.assertFalse(sandbox_rederive.recheck(forged)["sandbox_local_matches"])
        self.assertEqual(sandbox_rederive.recheck({}), {})

    def test_a_block_with_an_error_means_not_sealed_not_a_diverging_verdict(self):
        d = stale_dossier()
        d["sandbox"] = sandbox_rederive.seal(d, ask=lambda s, p: (_ for _ in ()).throw(llm.OtariError("HTTP 504", status=504)))
        self.assertEqual(sandbox_rederive.recheck(d), {"sandbox_sealed": False})
        self.assertTrue(dossier.replay(d, "auto")["matches"])

    def test_a_stale_dossier_with_a_sealed_sandbox_replays_forever(self):
        d = stale_dossier()
        d["sandbox"] = sandbox_rederive.seal(d, ask=self.honest())
        self.assertTrue(d["sandbox"]["equal"] and d["sandbox"]["local_agrees_with_verdict"])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("network"))):
            r = dossier.replay(d, "auto")
        self.assertTrue(r["matches"] and r["sandbox_local_matches"])

    def test_dossiers_sealed_with_the_first_script_still_replay_with_it(self):
        v1 = [d for d in ledger_tail(300) if (d.get("sandbox") or {}).get("schema") == "otari-sandbox/1"]
        if not v1:
            self.skipTest("no version 1 sandbox block in the tail of the ledger")
        r = dossier.replay(v1[0], "auto")
        self.assertTrue(r["matches"] and r["sandbox_local_matches"])


# ---- the decide flow end to end, with every tool on and no network -------------------------------------------------------------------

class FlowEnv(Env):
    PRICE_FACTOR = 1.002

    def setUp(self):
        super().setUp()
        cfg = json.loads(llm.OTARI_CONFIG.read_text())
        cfg["features"] = {"web_search": True, "code_execution": True, "mcp": True}
        path = Path(tempfile.mkdtemp()) / "otari.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        for patch in (mock.patch.object(llm, "OTARI_CONFIG", path), mock.patch.object(llm, "usage_for", lambda rid: {"cost_usd": "0.000064", "prompt_tokens": 900, "completion_tokens": 100}),
                      mock.patch.object(otari_web, "verify_excerpt", lambda url, excerpt, opener=None: None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        self.close = self.pkt["market"]["candles"][-1]["c"]
        self.ledger = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.n = 0
        self.web_citation = None

    def reply(self, request, timeout=None):
        payload = json.loads(request.data.decode())
        self.n += 1
        headers = {**HEADERS, "Otari-Request-ID": f"req-{self.n}", "Otari-Model": served_spelling(payload["model"])}
        if request.full_url.endswith("/messages"):
            if payload.get("mcp_servers"):
                price = round(self.close * self.PRICE_FACTOR, 4)
                result = json.dumps({"result": {"bitcoin": {"usd": price}}})
                return FakeStream(sse(result=result), headers)
            code = re.search(r"```python\n(.*)```", payload["messages"][0]["content"], re.S).group(1)
            return FakeResponse(native_reply(code, sandbox_rederive.run_local(code)), {**headers, "Otari-Request-ID": f"req-sbx-{self.n}"})
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        extra = {}
        if system.startswith("You are a news scout"):
            content, extra = "done", {"otari_tool_calls": [SEARCH_CALL]}
        elif system.startswith("You read one web page"):
            content, extra = json.dumps({"excerpt": PAGE_TEXT}), {"otari_tool_calls": [FETCH_CALL]}
        elif "bull analyst" in system:
            ids = re.findall(r"^(E\d+): Web page excerpt", user, re.M)
            content = json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}] + ([{"claim": "Bitcoin ETFs recorded their strongest weekly inflows in ten months", "cites": ids[:1]}] if ids else []))
        elif "judge in a market debate" in system:
            content = '{"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "rally"}'
        elif "citation checker" in system:
            content = '[{"label": "B1", "supported": true, "note": "ok"}]'
        else:
            content = "[]"
        cost = "0.010100" if extra else "0.000100"
        return FakeResponse({**BODY, **extra, "usage": {**BODY["usage"], "cost_usd": cost}, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}, headers)

    def run_flow(self, extra_env=None):
        opts = types.SimpleNamespace(ledger=self.ledger, symbols=["BTCUSDT"], interval="1h", no_news=True, default_feeds=False, feed=["Voice=https://example.com/feed"], config=None, risk_pct=None)
        voices = [{"name": "Voice", "title": "Bitcoin rally hits record", "link": ""}]
        with mock.patch.dict(os.environ, extra_env or {}), mock.patch.object(urllib.request, "urlopen", self.reply), \
                mock.patch.object(evidence, "collect", lambda *a, **k: json.loads(json.dumps(self.pkt))), \
                mock.patch.object(narrative, "collect_with_gaps", lambda feeds, log=None: (voices, {})), \
                mock.patch.object(cli.practice if hasattr(cli, "practice") else llm, "chain_executor", lambda: None, create=True):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = cli._decide_flow(opts, "v6")
        return code, out.getvalue(), dossier.read_ledger(self.ledger)


class DecideFlowTest(FlowEnv):
    def test_one_cycle_with_every_tool_on_records_and_seals_the_evidence_of_each(self):
        code, out, ledger = self.run_flow()
        self.assertEqual((code, len(ledger)), (0, 1))
        d = ledger[0]
        web = d["evidence"]["web"]
        self.assertTrue(web["items"] and web["page"]["excerpt"] == PAGE_TEXT)
        self.assertEqual(web["page"]["receipt"]["role"], "fetcher")
        mcp = d["evidence"]["market_mcp"]
        self.assertEqual((mcp["trusted"], mcp["check"]["status"]), (True, "ok"))
        self.assertEqual(mcp["receipt"]["role"], "mcp")
        sb = d["sandbox"]
        self.assertTrue(sb["equal"] and sb["proof_ok"] and sb["evidence"] == "native")
        self.assertEqual(d["llm"]["otari"]["evidence_use"]["web_items_cited"], 1)
        self.assertIn("cited by the analysts: 1", out)
        self.assertIn("MCP price check ok", out)
        self.assertIn("sandbox (native) output IDENTICAL", out)

    def test_the_sealed_spend_includes_the_sandbox_call_and_matches_the_receipts(self):
        _, _, ledger = self.run_flow()
        d = ledger[0]
        cycle = d["llm"]["otari"]["cycle"]
        receipts = [d["llm"][p]["receipt"] for p in ("bull", "bear", "checker", "judge")] + [d["evidence"]["web"]["receipt"], d["evidence"]["web"]["page"]["receipt"],
                                                                                           d["evidence"]["market_mcp"]["receipt"], d["sandbox"]["receipt"]] + d["evidence"]["narrative"].get("receipts", [])
        total = sum(llm.decimal.Decimal(r["cost_usd"]) for r in receipts)
        self.assertEqual(llm.decimal.Decimal(cycle["total_spent_usd"]), total)
        self.assertEqual(cycle["tools"]["calls"], 4)  # search, fetch, mcp, sandbox

    def test_the_chain_verifies_and_the_replay_matches_without_the_network(self):
        _, _, ledger = self.run_flow()
        self.assertEqual(dossier.verify(ledger), [])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(ledger[0], "auto")
        self.assertTrue(r["matches"] and r["llm_citations_valid"] and r["sandbox_local_matches"] and r["market_check_ok"])

    def test_an_mcp_price_five_percent_off_the_exchange_refuses_the_decision_and_the_cycle_still_records(self):
        self.PRICE_FACTOR = 1.05
        code, out, ledger = self.run_flow()
        d = ledger[0]
        self.assertEqual((code, d["verdict"]["action"]), (0, "BLOCKED_STALE"))
        self.assertNotIn("entry", d["verdict"])
        self.assertEqual(d["evidence"]["market_mcp"]["check"]["status"], "mismatch")
        self.assertTrue(d["sandbox"]["equal"] and d["sandbox"]["local_agrees_with_verdict"])  # the sandbox agrees: no trade, refused
        self.assertIn("MCP price check mismatch", out)
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("network"))):
            self.assertTrue(dossier.replay(d, "auto")["matches"])

    def test_tools_that_hit_their_own_cap_are_refused_while_the_analysts_and_the_judge_still_answer(self):
        _, _, ledger = self.run_flow({"OTARI_TOOL_CAP_USD": "0.0001"})
        d = ledger[0]
        self.assertTrue(d["evidence"]["web"]["items"], "the search that ran first spent the cap")
        self.assertEqual(d["evidence"]["web"]["page"]["receipt"]["refusal"]["code"], "cycle_cap_exceeded")
        self.assertEqual(d["evidence"]["market_mcp"]["receipt"]["refusal"]["budget_scope"], "cycle_tools")
        self.assertTrue(d["sandbox"].get("error"))
        for part in ("bull", "bear", "judge", "checker"):
            self.assertNotIn("error", d["llm"][part], part)
        self.assertTrue(d["llm"]["judge"]["accepted"])
        self.assertEqual(dossier.verify(ledger), [])
        self.assertTrue(dossier.replay(d, "auto")["matches"])  # an unsealed sandbox block is not a divergence

    def test_with_the_switches_off_the_flow_makes_no_tool_call(self):
        _, out, ledger = self.run_flow({"OTARI_WEB_SEARCH": "0", "OTARI_MCP": "0", "OTARI_CODE_EXECUTION": "0"})
        d = ledger[0]
        self.assertTrue("web" not in d["evidence"] and "market_mcp" not in d["evidence"] and "sandbox" not in d)
        self.assertNotIn("[otari web]", out)


if __name__ == "__main__":
    unittest.main()
