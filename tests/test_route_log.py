"""Each LLM call records the route the gateway took: the models it tried, why it moved on, and the policy it followed.

The gateway (tools/llm-gateway/gate.py) answers with X-Verdict-Route and X-Verdict-Policy. The client keeps them per
thread, like the model that answered, and the dossier stores them next to each analyst, the judge and the voice
batches, so "which model wrote this, and why that one" is answered from the record itself.
"""

import io
import json
import os
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from verdict_desk import dossier, llm

ROUTE = [{"model": "gpt-oss-120b", "status": 429, "ms": 812}, {"model": "agnes-3.0-flash", "status": 200, "ms": 9400}]
POLICY = "90eeb7083371980696a476741bb6d6df64a2b8203dfa99262610c3144c1c8783 as_of=383"


def headers(route=ROUTE, policy=POLICY):
    h = Message()
    if route is not None:
        h["X-Verdict-Route"] = json.dumps(route)
    if policy is not None:
        h["X-Verdict-Policy"] = policy
    return h


class Response:
    def __init__(self, payload, hdrs):
        self._raw, self.headers = json.dumps(payload).encode(), hdrs

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(model="agnes-3.0-flash", hdrs=None):
    return Response({"model": model, "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]}, hdrs if hdrs is not None else headers())


class ClientRouteTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"MISTRAL_API_KEY": "k"}), mock.patch.object(llm, "MIN_INTERVAL", 0.0),
                      mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_route_and_the_policy_of_the_last_call_are_kept_and_read_once(self):
        with mock.patch("urllib.request.urlopen", return_value=ok()):
            llm.chat("s", "u")
        self.assertEqual(llm.served_route(), {"attempts": ROUTE, "policy": {"sha256": POLICY.split()[0], "as_of_seq": 383}})
        self.assertIsNone(llm.served_route())

    def test_a_reply_without_the_gateway_headers_records_no_route(self):
        with mock.patch("urllib.request.urlopen", return_value=ok(hdrs=Message())):
            llm.chat("s", "u")
        self.assertIsNone(llm.served_route())

    def test_a_failed_call_keeps_the_route_that_shows_every_model_was_down(self):
        down = [{"model": "gpt-oss-120b", "status": 503, "ms": 40}, {"model": "auto", "status": 503, "ms": 35}]
        err = urllib.error.HTTPError("u", 400, "bad", headers(down), io.BytesIO(b'{"error":"x"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(OSError):
                llm.chat("s", "u")
        self.assertEqual(llm.served_route()["attempts"], down)

    def test_a_route_without_a_loaded_policy_keeps_its_attempts(self):
        with mock.patch("urllib.request.urlopen", return_value=ok(hdrs=headers(policy=" as_of=None"))):
            llm.chat("s", "u")
        self.assertEqual(llm.served_route(), {"attempts": ROUTE, "policy": None})

    def test_malformed_headers_are_ignored_instead_of_breaking_the_call(self):
        bad = Message()
        bad["X-Verdict-Route"] = "not json"
        bad["X-Verdict-Policy"] = "nonsense"
        with mock.patch("urllib.request.urlopen", return_value=ok(hdrs=bad)):
            llm.chat("s", "u")
        self.assertIsNone(llm.served_route())


class DossierRouteTest(unittest.TestCase):
    def test_the_dossier_block_carries_the_route_and_the_policy(self):
        stub = mock.Mock()
        stub.served_by.return_value = "agnes-3.0-flash"
        stub.served_route.return_value = {"attempts": ROUTE, "policy": {"sha256": "ab" * 32, "as_of_seq": 383}}
        block = {"accepted": [], "rejected": []}
        dossier._served({"llm": stub}, block)
        self.assertEqual(block["served_by"], "agnes-3.0-flash")
        self.assertEqual(block["route"], ROUTE)
        self.assertEqual(block["routing_policy"], {"sha256": "ab" * 32, "as_of_seq": 383})

    def test_an_older_client_without_routes_still_records_the_model(self):
        stub = mock.Mock(spec=["served_by"])
        stub.served_by.return_value = "m"
        block = {}
        dossier._served({"llm": stub}, block)
        self.assertEqual(block, {"served_by": "m"})


if __name__ == "__main__":
    unittest.main()
