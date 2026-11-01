import datetime
import email.utils
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from verdict_desk import llm

KEY = "test-key-123"


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(content, finish="stop"):
    return FakeResponse({"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def http_error(code, body=b"", retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://api.mistral.ai/v1/chat/completions", code, "err", headers, io.BytesIO(body))


class ClientTest(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}),
            mock.patch.object(llm, "BASE_URL", "https://api.mistral.ai/v1"),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        pause = mock.patch.object(llm, "_pause")
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def test_available_follows_the_key(self):
        self.assertTrue(llm.available())
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "  \n"}):
            self.assertFalse(llm.available())
        with mock.patch.dict(os.environ):
            del os.environ["MISTRAL_API_KEY"]
            self.assertFalse(llm.available())

    def test_request_shape_and_text(self):
        with mock.patch("urllib.request.urlopen", return_value=reply("hello")) as urlopen:
            self.assertEqual(llm.chat("be brief", "hi"), "hello")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.mistral.ai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        body = json.loads(request.data)
        self.assertEqual(body["model"], llm.MODEL)
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], llm.MAX_TOKENS)
        self.assertEqual(body["messages"], [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}])

    def test_max_tokens_override_reaches_the_request(self):
        with mock.patch("urllib.request.urlopen", return_value=reply("ok")) as urlopen:
            llm.chat("s", "u", max_tokens=6000)
            llm.chat("s", "u")
        sent = [json.loads(call.args[0].data)["max_tokens"] for call in urlopen.call_args_list]
        self.assertEqual(sent, [6000, llm.MAX_TOKENS])

    def test_key_is_trimmed(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": f"{KEY}\n"}):
            with mock.patch("urllib.request.urlopen", return_value=reply("ok")) as urlopen:
                llm.chat("s", "u")
        self.assertEqual(urlopen.call_args.args[0].get_header("Authorization"), f"Bearer {KEY}")

    def test_block_content_keeps_only_the_text_blocks(self):
        blocks = [{"type": "thinking", "thinking": [{"type": "text", "text": "hidden"}]}, {"type": "text", "text": "answer"}]
        with mock.patch("urllib.request.urlopen", return_value=reply(blocks)):
            self.assertEqual(llm.chat("s", "u"), "answer")

    def test_empty_content_names_the_finish_reason(self):
        with mock.patch("urllib.request.urlopen", return_value=reply("", finish="length")):
            with self.assertRaisesRegex(ValueError, "length"):
                llm.chat("s", "u")

    def test_unexpected_shape_is_a_value_error(self):
        for payload in ({"choices": []}, {"error": "x"}, {"choices": "x"}):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
                with self.assertRaises(ValueError):
                    llm.chat("s", "u")

    def test_429_waits_for_retry_after_then_succeeds(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(429, retry_after=3), reply("done")]):
            self.assertEqual(llm.chat("s", "u"), "done")
        self.pause.assert_called_once_with(3.0)

    def test_429_without_retry_after_backs_off(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(429), http_error(503), reply("done")]):
            self.assertEqual(llm.chat("s", "u"), "done")
        self.assertEqual([c.args[0] for c in self.pause.call_args_list], [2.0, 4.0])

    def test_retry_after_as_http_date_is_honored(self):
        when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        header = email.utils.format_datetime(when, usegmt=True)
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(429, retry_after=header), reply("done")]):
            self.assertEqual(llm.chat("s", "u"), "done")
        (waited,), _ = self.pause.call_args
        self.assertAlmostEqual(waited, 30.0, delta=2.0)

    def test_retry_after_beyond_the_cap_fails_fast_instead_of_retrying_early(self):
        for header in ("120", "inf", "Mon, 19 Oct 2099 06:10:00 GMT"):
            with self.subTest(header=header):
                self.pause.reset_mock()
                error = http_error(429, b'{"message":"quota"}', retry_after=header)
                with mock.patch("urllib.request.urlopen", side_effect=[error]) as urlopen:
                    with self.assertRaisesRegex(llm.LLMError, "429"):
                        llm.chat("s", "u")
                self.assertEqual(urlopen.call_count, 1)
                self.pause.assert_not_called()

    def test_unusable_retry_after_falls_back_to_backoff(self):
        for header in ("soon", "nan", ""):
            with self.subTest(header=header):
                self.pause.reset_mock()
                with mock.patch("urllib.request.urlopen", side_effect=[http_error(429, retry_after=header), reply("done")]):
                    self.assertEqual(llm.chat("s", "u"), "done")
                self.pause.assert_called_once_with(2.0)

    def test_gives_up_after_the_retry_budget(self):
        errors = [http_error(429, b'{"message":"rate limit"}') for _ in range(llm.MAX_RETRIES + 1)]
        with mock.patch("urllib.request.urlopen", side_effect=errors) as urlopen:
            with self.assertRaisesRegex(llm.LLMError, "429"):
                llm.chat("s", "u")
        self.assertEqual(urlopen.call_count, llm.MAX_RETRIES + 1)

    def test_401_is_not_retried_and_the_error_never_carries_the_key(self):
        body = f'{{"detail":"Invalid API Key {KEY}"}}'.encode()
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401, body)]) as urlopen:
            with self.assertRaises(llm.LLMError) as caught:
                llm.chat("s", "u")
        message = str(caught.exception)
        self.assertIn(f"HTTP 401 err from https://api.mistral.ai/v1 model {llm.MODEL}:", message)  # formato que vai para o ledger
        self.assertIn("Invalid API Key", message)
        self.assertNotIn(KEY, message)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIsInstance(caught.exception, OSError)  # os `except OSError` do dossie e da narrativa continuam valendo

    def test_malformed_key_is_rejected_without_echoing_it(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "abc\ndef ghi"}):
            with mock.patch("urllib.request.urlopen") as urlopen:
                with self.assertRaises(llm.LLMError) as caught:
                    llm.chat("s", "u")
        self.assertNotIn("abc", str(caught.exception))
        urlopen.assert_not_called()

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ):
            del os.environ["MISTRAL_API_KEY"]
            with self.assertRaisesRegex(llm.LLMError, "not set"):
                llm.chat("s", "u")

    def test_call_starts_are_spaced_even_across_threads(self):
        with mock.patch.object(llm, "MIN_INTERVAL", 0.5):
            threads = [threading.Thread(target=llm._wait_turn) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        waits = sorted(c.args[0] for c in self.pause.call_args_list)
        self.assertEqual(len(waits), 3)  # a primeira chamada nao espera
        for earlier, later in zip(waits, waits[1:]):
            self.assertAlmostEqual(later - earlier, 0.5, delta=0.05)

    def test_model_defaults_to_mistral_small_latest_and_follows_MISTRAL_MODEL(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = f"import sys; sys.path.insert(0, {root!r}); from verdict_desk import llm; print(llm.MODEL)"
        for value, expected in ((None, "mistral-small-latest"), ("", "mistral-small-latest"), ("mistral-medium-latest", "mistral-medium-latest")):
            with self.subTest(value=value):
                env = {k: v for k, v in os.environ.items() if k != "MISTRAL_MODEL"}
                if value is not None:
                    env["MISTRAL_MODEL"] = value
                out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
                self.assertEqual(out, expected)

    def test_bull_bear_and_narrative_all_send_the_one_configured_model(self):
        from tests.test_dossier import packet
        from verdict_desk import dossier, narrative

        sent = []

        def fake_urlopen(request, timeout=0):
            sent.append(json.loads(request.data)["model"])
            return reply("[]")

        voices = [{"name": f"Voice{i % 3}", "title": f"BTC headline {i}", "url": f"http://x/{i}"} for i in range(12)]
        batches = -(-len(voices) // narrative.CHUNK)
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), mock.patch.object(llm, "MODEL", "model-under-test"):
            layer = dossier.analyst_layer(packet(0.004, titles=("Bitcoin rally hits record",)))  # touro e urso
            block = narrative.build(voices, llm.chat, llm.MODEL)  # narrativa, em lotes
        self.assertEqual(len(sent), 2 + batches)
        self.assertEqual(set(sent), {"model-under-test"})
        self.assertEqual(layer["model"], "model-under-test")
        self.assertEqual(block["model"], "model-under-test")

    def test_no_previous_provider_model_name_is_left_in_the_package(self):
        package = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verdict_desk")
        hits = []
        for name in sorted(os.listdir(package)):
            if name.endswith(".py"):
                with open(os.path.join(package, name), encoding="utf-8") as handle:
                    if "nemotron" in handle.read().lower():
                        hits.append(name)
        self.assertEqual(hits, [])

    def test_numeric_env_falls_back_when_invalid_empty_or_out_of_range(self):
        positive = lambda v: v > 0
        for raw, cast, default, expected in (("abc", int, 7, 7), ("", float, 1.5, 1.5), ("9", int, 7, 9), ("-5", int, 7, 7), ("0", int, 7, 7)):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"X_NUM": raw}):
                    self.assertEqual(llm._number("X_NUM", default, cast, positive), expected)

    def test_bad_numeric_env_values_fall_back_to_the_defaults_at_import(self):
        # MISTRAL_MIN_INTERVAL=inf fazia a segunda chamada levantar OverflowError, que o dossie nao captura.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = f"import sys; sys.path.insert(0, {root!r}); from verdict_desk import llm; print(llm.MAX_TOKENS, llm.MIN_INTERVAL)"
        for tokens, interval, expected in (
            ("-5", "inf", "3000 1.1"),
            ("0", "nan", "3000 1.1"),
            ("abc", "-3", "3000 1.1"),
            ("6000", "0", "6000 0.0"),  # espacamento zero e permitido (plano pago)
        ):
            with self.subTest(tokens=tokens, interval=interval):
                env = {**os.environ, "MISTRAL_MAX_TOKENS": tokens, "MISTRAL_MIN_INTERVAL": interval}
                out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
                self.assertEqual(out, expected)


def anthropic_reply(text=None, stop="end_turn", blocks=None, **extra):
    content = blocks if blocks is not None else ([{"type": "text", "text": text}] if text is not None else [])
    return FakeResponse({"id": "msg_1", "type": "message", "role": "assistant", "content": content, "stop_reason": stop, **extra})


class AnthropicTest(unittest.TestCase):
    """Claude Haiku 5.5 through the native Messages API (LLM_PROVIDER=anthropic), standard library only.

    The shape that matters: the key goes in `x-api-key` (not Authorization), the system prompt is a
    top-level field, sampling parameters are left out (Haiku 5.5 rejects non-default values with a 400),
    thinking is turned off so no tokens are spent before the answer, and the answer is the text blocks.
    """

    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "MISTRAL_API_KEY": KEY, "LLM_KEYLESS": ""}),
            mock.patch.object(llm, "BASE_URL", "https://api.anthropic.com/v1"),
            mock.patch.object(llm, "MODEL", "claude-haiku-5-5"),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        pause = mock.patch.object(llm, "_pause")
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def test_request_shape(self):
        with mock.patch("urllib.request.urlopen", return_value=anthropic_reply("hi")) as urlopen:
            self.assertEqual(llm.chat("be brief", "question", max_tokens=500), "hi")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), KEY)
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertIsNone(request.get_header("Authorization"))
        body = json.loads(request.data)
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertEqual(body["max_tokens"], 500)
        self.assertEqual(body["system"], "be brief")
        self.assertEqual(body["messages"], [{"role": "user", "content": "question"}])
        self.assertNotIn("temperature", body, "Haiku 5.5 answers a non-default sampling value with a 400")
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_the_answer_is_the_text_blocks_and_nothing_else(self):
        blocks = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "[1,"}, {"type": "text", "text": "2]"}]
        with mock.patch("urllib.request.urlopen", return_value=anthropic_reply(blocks=blocks)):
            self.assertEqual(llm.chat("s", "u"), "[1,2]")

    def test_a_refusal_says_so_with_its_category(self):
        refused = anthropic_reply(stop="refusal", stop_details={"type": "refusal", "category": "cyber"})
        with mock.patch("urllib.request.urlopen", return_value=refused):
            with self.assertRaisesRegex(ValueError, "refus.*cyber"):
                llm.chat("s", "u")

    def test_running_out_of_tokens_is_named_not_hidden(self):
        with mock.patch("urllib.request.urlopen", return_value=anthropic_reply(stop="max_tokens")):
            with self.assertRaisesRegex(ValueError, "max_tokens"):
                llm.chat("s", "u")

    def test_overloaded_529_and_rate_limit_429_are_retried(self):
        for code in (529, 429):
            with self.subTest(code=code):
                with mock.patch("urllib.request.urlopen", side_effect=[http_error(code), anthropic_reply("ok")]) as urlopen:
                    self.assertEqual(llm.chat("s", "u"), "ok")
                self.assertEqual(urlopen.call_count, 2)

    def test_an_error_never_carries_the_key(self):
        body = f'{{"type":"error","error":{{"type":"authentication_error","message":"invalid x-api-key {KEY}"}}}}'.encode()
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401, body)]):
            with self.assertRaises(llm.LLMError) as caught:
                llm.chat("s", "u")
        self.assertIn("authentication_error", str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("https://api.anthropic.com/v1 model claude-haiku-5-5", str(caught.exception))

    def test_the_defaults_follow_the_provider_and_the_old_overrides_still_win(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = f"import sys; sys.path.insert(0, {root!r}); from verdict_desk import llm; print(llm.MODEL, llm.BASE_URL)"
        cases = (
            ({"LLM_PROVIDER": "anthropic"}, "claude-haiku-5-5 https://api.anthropic.com/v1"),
            ({"LLM_PROVIDER": "anthropic", "MISTRAL_MODEL": "claude-sonnet-5-5"}, "claude-sonnet-5-5 https://api.anthropic.com/v1"),
            ({"LLM_PROVIDER": ""}, "mistral-small-latest https://api.mistral.ai/v1"),
        )
        for extra, expected in cases:
            with self.subTest(extra=extra):
                env = {k: v for k, v in os.environ.items() if k not in ("MISTRAL_MODEL", "MISTRAL_BASE_URL", "LLM_PROVIDER")}
                env.update(extra)
                out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip()
                self.assertEqual(out, expected)

    def test_without_a_key_it_is_a_clear_error_and_nothing_is_sent(self):
        with mock.patch.dict(os.environ):
            del os.environ["MISTRAL_API_KEY"]
            with mock.patch("urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(llm.LLMError, "not set"):
                    llm.chat("s", "u")
        urlopen.assert_not_called()


class KeylessTest(unittest.TestCase):
    """A provider that needs no key (a free anonymous tier) must never receive a key we happen to hold.

    The repository secret still holds a key for a different provider; in keyless mode that key is not
    sent anywhere, and the free tier's "one reply every 30 seconds" is waited out instead of failing.
    """

    FREE_LIMIT = b'{"status":402,"error":{"code":"FREE_LIMIT","message":"Free limit reached: 1 reply every 30 seconds per IP. Retry after 23s"}}'

    def setUp(self):
        for patch in (
            mock.patch.dict(os.environ, {"LLM_KEYLESS": "1"}),
            mock.patch.object(llm, "BASE_URL", "https://free.example/openai"),
            mock.patch.object(llm, "MIN_INTERVAL", 0.0),
            mock.patch.object(llm, "_next_start", 0.0),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        pause = mock.patch.object(llm, "_pause")
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def test_it_is_available_without_a_key_and_sends_no_authorization(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MISTRAL_API_KEY", None)
            self.assertTrue(llm.available())
            with mock.patch("urllib.request.urlopen", return_value=reply("hello")) as urlopen:
                self.assertEqual(llm.chat("s", "u"), "hello")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://free.example/openai/chat/completions")
        self.assertIsNone(request.get_header("Authorization"))

    def test_a_key_that_happens_to_be_set_is_never_forwarded(self):
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}):
            with mock.patch("urllib.request.urlopen", return_value=reply("hello")) as urlopen:
                llm.chat("s", "u")
        request = urlopen.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn(KEY.encode(), request.data)

    def test_the_free_limit_is_waited_out_and_retried(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(402, self.FREE_LIMIT), reply("ok")]) as urlopen:
            self.assertEqual(llm.chat("s", "u"), "ok")
        self.assertEqual(urlopen.call_count, 2)
        self.pause.assert_any_call(24.0)

    def test_a_free_limit_that_asks_for_too_long_a_wait_is_an_error_now(self):
        long_wait = self.FREE_LIMIT.replace(b"23s", b"600s")
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(402, long_wait)]) as urlopen:
            with self.assertRaises(llm.LLMError):
                llm.chat("s", "u")
        self.assertEqual(urlopen.call_count, 1)

    def test_other_errors_are_not_retried_and_the_message_is_not_mangled(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401, b'{"detail":"nope"}')]):
            with self.assertRaises(llm.LLMError) as caught:
                llm.chat("s", "u")
        self.assertIn("nope", str(caught.exception))
        self.assertNotIn("***", str(caught.exception), "an empty key must not be 'redacted' into every gap of the message")

    def test_a_402_with_a_key_set_and_keyless_off_is_still_a_plain_error(self):
        with mock.patch.dict(os.environ, {"LLM_KEYLESS": "", "MISTRAL_API_KEY": KEY}):
            with mock.patch("urllib.request.urlopen", side_effect=[http_error(402, self.FREE_LIMIT)]) as urlopen:
                with self.assertRaises(llm.LLMError):
                    llm.chat("s", "u")
        self.assertEqual(urlopen.call_count, 1)


class LocalServerTest(unittest.TestCase):
    """Caminho real de HTTP (socket, cabecalhos, corpo de erro) contra um servidor local que imita a Mistral."""

    def test_429_then_200_over_a_real_socket(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", 0))
                seen.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(size))))
                if len(seen) == 1:
                    payload, status = b'{"message":"rate limit"}', 429
                else:
                    payload, status = json.dumps({"choices": [{"message": {"content": "from server"}, "finish_reason": "stop"}]}).encode(), 200
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                if status == 429:
                    self.send_header("Retry-After", "2")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}), mock.patch.object(llm, "BASE_URL", f"http://127.0.0.1:{server.server_port}/v1"), mock.patch.object(
            llm, "MIN_INTERVAL", 0.0
        ), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause") as pause, mock.patch("urllib.request.urlopen", side_effect=opener.open):
            self.assertEqual(llm.chat("sys", "usr"), "from server")
        pause.assert_called_once_with(2.0)
        self.assertEqual([s[0] for s in seen], ["/v1/chat/completions"] * 2)
        self.assertEqual({s[1] for s in seen}, {f"Bearer {KEY}"})
        self.assertEqual(seen[1][2]["messages"][1], {"role": "user", "content": "usr"})


if __name__ == "__main__":
    unittest.main()
