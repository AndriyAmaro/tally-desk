"""Which model really answered: a router such as FreeLLMAPI's "auto" picks a different model per call, so the
configured name says little. The client keeps the model the provider names in its reply, per thread, and the
dossier records it next to each analyst, the judge and the narrative extraction. Recording it changes no verdict.
"""

import json
import os
import unittest
from unittest import mock

from tests.test_dossier import packet
from tests.test_llm_client import FakeResponse
from verdict_desk import dossier, llm, narrative

KEY = "test-key-123"


def answer(content, model):
    payload = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    if model:
        payload["model"] = model
    return FakeResponse(payload)


class ClientTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}), mock.patch.object(llm, "MIN_INTERVAL", 0)):
            patch.start()
            self.addCleanup(patch.stop)
        llm.served_by()

    def test_the_model_named_in_the_reply_is_kept_once(self):
        with mock.patch("urllib.request.urlopen", return_value=answer("hi", "qwen/qwen3.8-27b")):
            self.assertEqual(llm.chat("s", "u"), "hi")
        self.assertEqual(llm.served_by(), "qwen/qwen3.8-27b")
        self.assertIsNone(llm.served_by())  # read once: a later call cannot inherit it

    def test_a_reply_without_a_model_leaves_nothing(self):
        with mock.patch("urllib.request.urlopen", return_value=answer("hi", None)):
            llm.chat("s", "u")
        self.assertIsNone(llm.served_by())

    def test_the_anthropic_reply_names_its_model_too(self):
        reply = FakeResponse({"model": "claude-haiku-5-5", "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"})
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}), mock.patch("urllib.request.urlopen", return_value=reply):
            self.assertEqual(llm.chat("s", "u"), "hi")
        self.assertEqual(llm.served_by(), "claude-haiku-5-5")


class DossierTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}), mock.patch.object(llm, "MIN_INTERVAL", 0)):
            patch.start()
            self.addCleanup(patch.stop)
        self.pkt = packet(0.0, titles=("Bitcoin rally hits record",))

    def test_each_analyst_and_the_judge_record_who_answered_and_the_replay_still_matches(self):
        replies = [
            answer(json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}]), "model-bull"),
            answer(json.dumps([{"claim": "Rally is old news", "cites": ["E4"]}]), "model-bear"),
            answer(json.dumps({"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "B1."}), "model-judge"),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=replies):
            d = dossier.build(self.pkt, dossier.GENESIS, 1, rules_version="v6")
        self.assertEqual(d["llm"]["bull"]["served_by"], "model-bull")
        self.assertEqual(d["llm"]["bear"]["served_by"], "model-bear")
        self.assertEqual(d["llm"]["judge"]["served_by"], "model-judge")
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_injected_stubs_record_nothing(self):
        stub = lambda system, user: "[]"
        layer = dossier.build(self.pkt, dossier.GENESIS, 1, ask=stub, rules_version="v6")["llm"]
        self.assertNotIn("served_by", layer["bull"])


class NarrativeTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"MISTRAL_API_KEY": KEY}), mock.patch.object(llm, "MIN_INTERVAL", 0)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_extraction_lists_the_models_that_answered_its_batches(self):
        voices = [{"name": "A", "title": "Bitcoin rally continues"}, {"name": "B", "title": "BTC breakout again"}]
        with mock.patch("urllib.request.urlopen", return_value=answer("[]", "model-x")):
            block = narrative.build(voices, ask=lambda s, u: llm.chat(s, u), model="auto")
        self.assertEqual(block["method"], "llm")
        self.assertEqual(block["served_by"], ["model-x"])


if __name__ == "__main__":
    unittest.main()
