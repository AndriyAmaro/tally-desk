"""The client of the one LLM path, the Otari gateway: request shape, retries, the missing key, the call spacing. No network."""

import datetime
import email.utils
import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_otari import KEY, FakeResponse, message, plain_config
from verdict_desk import llm

ROOT = Path(__file__).resolve().parents[1]
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "OTARI_MIN_INTERVAL": ""}


def reply(content, finish="stop"):
    return FakeResponse({"choices": [{"message": {"content": content}, "finish_reason": finish}]}, {"Otari-Request-ID": "req-client"})


def http_error(code, body=b"", retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://otari.example/api/v1/chat/completions", code, "err", message(headers), io.BytesIO(body))


class ClientTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), plain_config()):
            patch.start()
            self.addCleanup(patch.stop)
        pause = mock.patch.object(llm, "_pause")
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def test_available_follows_the_key(self):
        self.assertTrue(llm.available())
        for blank in ("  \n", ""):
            with mock.patch.dict(os.environ, {"OTARI_API_KEY": blank}):
                self.assertFalse(llm.available())

    def test_request_shape_and_text(self):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(request)
            return reply("hello")
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            self.assertEqual(llm.chat("system text", "user text", role="bull", max_tokens=77), "hello")
        request = sent[0]
        body = json.loads(request.data)
        self.assertTrue(request.full_url.endswith("/chat/completions"))
        self.assertIn("otari.ai", request.full_url)
        self.assertEqual((body["model"], body["max_tokens"], body["temperature"]), (llm.role_model("bull"), 77, 0))
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(request.get_header("Otari-key"), KEY)

    def test_the_key_is_trimmed(self):
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": f"{KEY}\n"}), mock.patch("urllib.request.urlopen", return_value=reply("ok")) as urlopen:
            self.assertEqual(llm.chat("s", "u"), "ok")
        self.assertEqual(urlopen.call_args.args[0].get_header("Otari-key"), KEY)

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
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload, {})):
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
        error = http_error(401, f'{{"message":"bad key {KEY}"}}'.encode())
        with mock.patch("urllib.request.urlopen", side_effect=[error]) as urlopen:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.chat("s", "u")
        self.assertEqual(urlopen.call_count, 1)
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_a_malformed_key_is_rejected_without_echoing_it(self):
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": "abc\ndef ghi"}), mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(llm.LLMError) as ctx:
                llm.chat("s", "u")
        self.assertNotIn("abc", str(ctx.exception))
        urlopen.assert_not_called()

    def test_the_base_url_must_be_https_because_the_key_travels_in_a_header(self):
        with mock.patch.dict(os.environ, {"OTARI_BASE_URL": "http://127.0.0.1:9/api/v1"}), mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(llm.LLMError, "https"):
                llm.chat("s", "u")
        urlopen.assert_not_called()

    def test_call_starts_are_spaced_even_across_threads(self):
        with mock.patch.object(llm, "MIN_INTERVAL", 0.5):
            threads = [threading.Thread(target=llm._wait_turn) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        waits = sorted(c.args[0] for c in self.pause.call_args_list)
        self.assertEqual(len(waits), 3)  # the first call does not wait
        for earlier, later in zip(waits, waits[1:]):
            self.assertAlmostEqual(later - earlier, 0.5, delta=0.05)

    def test_bull_bear_and_narrator_each_send_the_model_of_their_role(self):
        from tests.test_dossier import packet
        from verdict_desk import dossier, narrative

        sent = []

        def urlopen(request, timeout=None):
            payload = json.loads(request.data)
            sent.append((request.get_header("Otari-tags"), payload["model"]))
            return reply("[]")

        voices = [{"name": f"Voice{i % 3}", "title": f"BTC headline {i}", "url": f"http://x/{i}"} for i in range(12)]
        batches = -(-len(voices) // narrative.CHUNK)
        ask = lambda system, user: llm.chat(system, user, role="narrator")
        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            layer = dossier.analyst_layer(packet(0.004, titles=("Bitcoin rally hits record",)))
            block = narrative.build(voices, ask, llm.model_name())
        self.assertEqual(sorted(sent[:2]), sorted([("role=bull", llm.role_model("bull")), ("role=bear", llm.role_model("bear"))]))
        self.assertEqual(len([s for s in sent if s[0] == "role=narrator"]), batches)
        self.assertEqual({m for r, m in sent if r == "role=narrator"}, {llm.role_model("narrator")})
        self.assertEqual((layer["model"], block["model"]), ("otari:multi-role", "otari:multi-role"))

    def test_numeric_env_falls_back_when_invalid_empty_or_out_of_range(self):
        positive = lambda v: v > 0
        for raw, cast, default, expected in (("abc", int, 7, 7), ("", float, 1.5, 1.5), ("9", int, 7, 9), ("-5", int, 7, 7), ("0", int, 7, 7)):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {"X_NUM": raw}):
                    self.assertEqual(llm._number("X_NUM", default, cast, positive), expected)


class NoKeyTest(unittest.TestCase):
    """The only LLM path is Otari: without OTARI_API_KEY every call fails with the same message and sends nothing."""

    def setUp(self):
        patch = mock.patch.dict(os.environ, {"OTARI_API_KEY": ""})
        patch.start()
        self.addCleanup(patch.stop)
        llm.served_receipt()  # a receipt an earlier test left on this thread

    def test_chat_says_what_is_missing_and_sends_nothing(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            for role in (None, "bull", "judge", "narrator"):
                with self.assertRaises(llm.LLMError) as ctx:
                    llm.chat("s", "u", role=role)
                self.assertEqual(str(ctx.exception), llm.NO_KEY)
        urlopen.assert_not_called()
        self.assertIn("OTARI_API_KEY", llm.NO_KEY)
        self.assertIsNone(llm.served_receipt())

    def test_the_agent_functions_fail_with_the_same_message(self):
        items = {"E1": "close 100"}
        labels = {"B1": {"claim": "c", "cites": ["E1"]}}
        for call in (lambda: llm.argue("bull", items), lambda: llm.rule(labels, items), lambda: llm.check_claims(labels, items)):
            with self.assertRaises(llm.LLMError) as ctx:
                call()
            self.assertEqual(str(ctx.exception), llm.NO_KEY)


if __name__ == "__main__":
    unittest.main()
