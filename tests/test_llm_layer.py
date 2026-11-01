import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import dossier


def stub(system, user):
    if "bull" in system:
        return json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}, {"claim": "Moon soon", "cites": []}])
    return "not json"


class LlmLayerTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        self.pkt = packet(0.004, titles=("Bitcoin rally hits record",))

    def test_no_key_keeps_deterministic_core(self):
        d = dossier.build(self.pkt, dossier.GENESIS, 1)
        self.assertNotIn("llm", d)

    def test_llm_output_is_validated_frozen_and_replayed(self):
        d = dossier.append(self.pkt, self.path, ask=stub)
        self.assertEqual(len(d["llm"]["bull"]["accepted"]), 1)
        self.assertEqual(len(d["llm"]["bull"]["rejected"]), 1)
        self.assertEqual(d["llm"]["bear"]["accepted"], [])
        ledger = dossier.read_ledger(self.path)
        self.assertEqual(dossier.verify(ledger), [])
        r = dossier.replay(ledger[0])
        self.assertTrue(r["matches"] and r["llm_citations_valid"])

    def test_replay_flags_citation_that_no_longer_matches_evidence(self):
        d = dossier.append(self.pkt, self.path, ask=stub)
        d["llm"]["bull"]["accepted"][0]["claim"] = "Bitcoin rally hits 99999"
        self.assertFalse(dossier.replay(d)["matches"])

    def test_claims_cited_by_bare_numbers_are_rejected_and_the_same_claims_cited_by_id_pass(self):
        # O que o modelo fez no seq 36 (cites: [14]) contra o que o prompt manda (cites: ["E14"]).
        bare = lambda system, user: json.dumps([{"claim": "Bitcoin rally hits record", "cites": [4]}])
        by_id = lambda system, user: json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}])
        for ask, accepted in ((bare, 0), (by_id, 1)):
            layer = dossier.build(self.pkt, dossier.GENESIS, 1, ask=ask)["llm"]
            for side in ("bull", "bear"):
                self.assertEqual(len(layer[side]["accepted"]), accepted)
                self.assertEqual([c["rejected_because"] for c in layer[side]["rejected"]], ["unknown evidence id"] * (1 - accepted))

    def test_claims_that_mention_their_ids_in_the_text_are_accepted_and_replay_matches(self):
        # Resposta real do Mistral nos seq 45 e 46: o id aparece no texto e nos cites.
        inline = lambda system, user: json.dumps([{"claim": "Bitcoin rally hits record (E4)", "cites": ["E4"]}])
        d = dossier.append(self.pkt, self.path, ask=inline)
        for side in ("bull", "bear"):
            self.assertEqual(len(d["llm"][side]["accepted"]), 1)
            self.assertEqual(d["llm"][side]["rejected"], [])
        ledger = dossier.read_ledger(self.path)
        self.assertEqual(dossier.verify(ledger), [])
        self.assertTrue(dossier.replay(ledger[0])["matches"])

    def test_malformed_citations_are_rejected_and_do_not_stop_the_dossier(self):
        weird = lambda system, user: json.dumps(
            [{"claim": "x", "cites": [["E4"]]}, {"claim": "y", "cites": {"id": "E4"}}, {"claim": "z", "cites": 4}]
        )
        d = dossier.build(self.pkt, dossier.GENESIS, 1, ask=weird)
        for side in ("bull", "bear"):
            self.assertEqual(d["llm"][side]["accepted"], [])
            self.assertEqual(len(d["llm"][side]["rejected"]), 3)
        self.assertEqual(dossier.verify([d]), [])

    def test_llm_failure_is_recorded_not_fatal(self):
        def boom(system, user):
            raise OSError("down")
        d = dossier.build(self.pkt, dossier.GENESIS, 1, ask=boom)
        self.assertIn("error", d["llm"]["bull"])
        self.assertTrue(dossier.replay(d)["matches"])


class LlmHttpErrorTest(unittest.TestCase):
    def call_with(self, error):
        import io
        import os
        import urllib.error
        import urllib.request

        from verdict_desk import llm

        original = urllib.request.urlopen
        old_key = os.environ.get("MISTRAL_API_KEY")
        os.environ["MISTRAL_API_KEY"] = "  SECRET-VALUE-123 \n"
        urllib.request.urlopen = lambda request, timeout=0: (_ for _ in ()).throw(error)
        try:
            # Sem espacamento aqui: a chamada nao dorme de verdade e nao deixa `_next_start` no futuro para os outros testes.
            with mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), self.assertRaises(OSError) as ctx:
                llm.chat("system", "user")
        finally:
            urllib.request.urlopen = original
            if old_key is None:
                del os.environ["MISTRAL_API_KEY"]
            else:
                os.environ["MISTRAL_API_KEY"] = old_key
        return str(ctx.exception)

    def test_http_error_message_carries_status_and_body_but_never_the_key(self):
        import io
        import urllib.error

        body = io.BytesIO(b'{"status":403,"detail":"Forbidden for SECRET-VALUE-123"}')
        message = self.call_with(urllib.error.HTTPError("https://x", 403, "Forbidden", {}, body))
        self.assertIn("HTTP 403 Forbidden", message)
        self.assertIn("Forbidden for ***", message)
        self.assertNotIn("SECRET-VALUE-123", message)

    def test_key_with_stray_whitespace_is_trimmed(self):
        import os

        from verdict_desk import llm

        os.environ["MISTRAL_API_KEY"] = "  \n"
        try:
            self.assertFalse(llm.available())
        finally:
            del os.environ["MISTRAL_API_KEY"]


if __name__ == "__main__":
    unittest.main()

