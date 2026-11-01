"""The LLM gateway (tools/llm-gateway/gate.py): routes each call by the published policy and reports the route.

A fake upstream stands in for the local FreeLLMAPI: each model answers with a chosen status and text. The gateway must
try the models in the order of config/model_routing.json, move on only when a model is unavailable or answers with
no text, stop at the first real answer or a real error, report every attempt in X-Verdict-Route and the policy's
fingerprint in X-Verdict-Policy, and keep everything but authenticated chat completions closed.
"""

import hashlib
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gate", ROOT / "tools" / "llm-gateway" / "gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

TOKEN = "test-token"


def answer(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "model": "x"}


class Upstream(BaseHTTPRequestHandler):
    script: dict = {}
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Upstream.seen.append(body["model"])
        status, text = Upstream.script.get(body["model"], (404, None))
        data = json.dumps(answer(text) if status == 200 else {"error": "x"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = start(ThreadingHTTPServer(("127.0.0.1", 0), Upstream))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.policy_path = Path(cls.tmp.name) / "policy.json"
        cls.policy_path.write_text(json.dumps({"as_of_seq": 383, "order": ["m1", "m2", "m3"]}), encoding="utf-8")
        settings = {"PROXY_TOKEN": TOKEN, "FREELLMAPI_KEY": "k", "UPSTREAM": f"http://127.0.0.1:{cls.upstream.server_port}",
                    "POLICY_URL": str(cls.policy_path)}
        cls.gate = start(gate.make_server(settings, port=0))
        cls.url = f"http://127.0.0.1:{cls.gate.server_port}"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gate, cls.upstream):
            server.shutdown()
            server.server_close()
        cls.tmp.cleanup()

    def call(self, path="/v1/chat/completions", token=TOKEN, model="anything"):
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(self.url + path, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}"), err.headers

    def route(self, headers):
        return [(a["model"], a["status"]) for a in json.loads(headers["X-Verdict-Route"])]

    def setUp(self):
        Upstream.seen = []

    def test_the_first_model_of_the_policy_answers_whatever_the_client_asked_for(self):
        Upstream.script = {"m1": (200, "ok from m1"), "m2": (200, "ok from m2")}
        status, body, headers = self.call(model="something-else")
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "ok from m1")
        self.assertEqual(self.route(headers), [("m1", 200)])

    def test_an_unavailable_or_empty_model_hands_the_call_to_the_next_one_and_the_route_says_why(self):
        Upstream.script = {"m1": (429, None), "m2": (200, ""), "m3": (200, "ok from m3")}
        status, body, headers = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "ok from m3")
        self.assertEqual(self.route(headers), [("m1", 429), ("m2", "empty"), ("m3", 200)])
        self.assertTrue(all(isinstance(a["ms"], int) for a in json.loads(headers["X-Verdict-Route"])))

    def test_a_real_error_is_returned_as_it_is_instead_of_being_hidden_by_another_model(self):
        Upstream.script = {"m1": (400, None), "m2": (200, "ok")}
        status, _, headers = self.call()
        self.assertEqual(status, 400)
        self.assertEqual(self.route(headers), [("m1", 400)])

    def test_when_every_listed_model_is_down_the_gateway_router_is_the_declared_last_resort(self):
        Upstream.script = {"m1": (503, None), "m2": (429, None), "m3": (502, None), "auto": (200, "ok from auto")}
        status, body, headers = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(self.route(headers)[-1], ("auto", 200))

    def test_the_policy_fingerprint_is_the_sha256_of_the_published_file(self):
        Upstream.script = {"m1": (200, "ok")}
        _, _, headers = self.call()
        sha = hashlib.sha256(self.policy_path.read_bytes()).hexdigest()
        self.assertEqual(headers["X-Verdict-Policy"], f"{sha} as_of=383")

    def test_everything_but_authenticated_chat_completions_stays_closed(self):
        self.assertEqual(self.call(token="wrong")[0], 401)
        self.assertEqual(self.call(path="/v1/models")[0], 404)
        self.assertEqual(Upstream.seen, [])


class HardeningTest(unittest.TestCase):
    def test_it_refuses_to_start_without_a_token_or_an_upstream_key(self):
        for settings in ({"PROXY_TOKEN": "", "FREELLMAPI_KEY": "k"}, {"PROXY_TOKEN": "t"}, {"PROXY_TOKEN": "  ", "FREELLMAPI_KEY": "k"}):
            with self.assertRaises(ValueError):
                gate.make_server(settings, port=0)

    def test_a_bad_content_length_is_a_400_not_a_crash(self):
        import http.client
        srv = start(gate.make_server({"PROXY_TOKEN": TOKEN, "FREELLMAPI_KEY": "k", "UPSTREAM": "http://127.0.0.1:9"}, port=0))
        try:
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            conn.putrequest("POST", "/v1/chat/completions")
            conn.putheader("Authorization", f"Bearer {TOKEN}")
            conn.putheader("Content-Length", "abc")
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_the_route_stops_trying_when_the_client_would_have_given_up(self):
        clock = iter([0, 0, 50, 50, 100, 100, 150, 150, 200, 200])
        calls = []

        def slow(body):
            calls.append(json.loads(body)["model"])
            return 503, b"{}", "application/json"

        original = gate.time.monotonic
        gate.time.monotonic = lambda: next(clock)
        try:
            _, _, _, attempts = gate.route({"model": "x"}, ["a", "b", "c", "d"], slow)
        finally:
            gate.time.monotonic = original
        self.assertLess(len(attempts), 4)


class PolicyLoadTest(unittest.TestCase):
    def test_a_broken_policy_keeps_the_last_good_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"as_of_seq": 1, "order": ["a"]}), encoding="utf-8")
            policy = gate.Policy(str(path), refresh_seconds=0)
            self.assertEqual(policy.current()[0], ["a"])
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(policy.current()[0], ["a"])

    def test_the_committed_policy_is_readable_by_the_gateway(self):
        policy = gate.Policy(str(ROOT / "config" / "model_routing.json"), refresh_seconds=0)
        order, sha, as_of = policy.current()
        self.assertTrue(order)
        self.assertEqual(len(sha), 64)


if __name__ == "__main__":
    unittest.main()
