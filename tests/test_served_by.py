"""Which model really answered: Otari names the provider and the model in its response headers, so the configured catalog id says little
(a provider spells the same model its own way). The client keeps the `provider:model` of the last reply, per thread, and the dossier records
it next to each analyst, the checker and the judge, and the narrative extraction lists the ones that answered its batches.
Recording it changes no verdict."""

import json
import os
import unittest
from unittest import mock

from tests.test_dossier import packet
from tests.test_otari import KEY, FakeResponse, plain_config
from verdict_desk import dossier, llm, narrative


def answer(content, served=None):
    headers = {"Otari-Request-ID": f"req-{served or 'none'}"}
    if served:
        headers.update({"Otari-Provider": "deepinfra", "Otari-Model": served})
    return FakeResponse({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}, headers)


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": ""}), mock.patch.object(llm, "MIN_INTERVAL", 0),
                      mock.patch.object(llm, "_next_start", 0.0), plain_config()):
            patch.start()
            self.addCleanup(patch.stop)
        llm.served_by()


class ClientTest(Env):
    def test_the_model_named_in_the_headers_is_kept_once(self):
        with mock.patch("urllib.request.urlopen", return_value=answer("hi", "qwen-served")):
            self.assertEqual(llm.chat("s", "u"), "hi")
        self.assertEqual(llm.served_by(), "deepinfra:qwen-served")
        self.assertIsNone(llm.served_by())  # read once: a later call cannot inherit it

    def test_a_reply_without_a_provider_leaves_nothing(self):
        with mock.patch("urllib.request.urlopen", return_value=answer("hi", None)):
            llm.chat("s", "u")
        self.assertIsNone(llm.served_by())


class DossierTest(Env):
    def setUp(self):
        super().setUp()
        self.pkt = packet(0.0, titles=("Bitcoin rally hits record",))

    def test_each_analyst_the_checker_and_the_judge_record_who_answered_and_the_replay_still_matches(self):
        replies = [
            answer(json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}]), "model-bull"),
            answer(json.dumps([{"claim": "Rally is old news", "cites": ["E4"]}]), "model-bear"),
            answer(json.dumps([{"label": "B1", "supported": True, "note": "ok"}, {"label": "S1", "supported": True, "note": "ok"}]), "model-checker"),
            answer(json.dumps({"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "B1."}), "model-judge"),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=replies):
            d = dossier.build(self.pkt, dossier.GENESIS, 1, rules_version="v6")
        for part, model in (("bull", "model-bull"), ("bear", "model-bear"), ("checker", "model-checker"), ("judge", "model-judge")):
            self.assertEqual(d["llm"][part]["served_by"], f"deepinfra:{model}")
            self.assertEqual(d["llm"][part]["receipt"]["served_model"], model)
        self.assertTrue(dossier.replay(d, rules_version="auto")["matches"])

    def test_injected_stubs_record_nothing(self):
        stub = lambda system, user: "[]"
        layer = dossier.build(self.pkt, dossier.GENESIS, 1, ask=stub, rules_version="v6")["llm"]
        self.assertNotIn("served_by", layer["bull"])


class NarrativeTest(Env):
    def test_the_extraction_lists_the_models_that_answered_its_batches(self):
        voices = [{"name": "A", "title": "Bitcoin rally continues"}, {"name": "B", "title": "BTC breakout again"}]
        with mock.patch("urllib.request.urlopen", return_value=answer("[]", "model-x")):
            block = narrative.build(voices, ask=lambda s, u: llm.chat(s, u, role="narrator"), model="otari:multi-role")
        self.assertEqual(block["method"], "llm")
        self.assertEqual(block["served_by"], ["deepinfra:model-x"])


if __name__ == "__main__":
    unittest.main()
