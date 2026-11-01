import re
import unittest

from tests.test_dossier import packet
from verdict_desk import citations, llm


class CitationsTest(unittest.TestCase):
    def setUp(self):
        self.items = citations.index(packet(0.004, titles=("Bitcoin rally hits record",)))

    def test_index_has_stable_ids(self):
        self.assertIn("E1", self.items)
        self.assertTrue(any("rally" in v for v in self.items.values()))

    def test_drops_uncited_unknown_and_invented_numbers(self):
        claims = [
            {"claim": "Bitcoin rally hits record", "cites": ["E4"]},
            {"claim": "Moon soon", "cites": []},
            {"claim": "Whales buying", "cites": ["E99"]},
            {"claim": "Funding rate is 0.77", "cites": ["E3"]},
        ]
        result = citations.validate(claims, self.items)
        self.assertEqual([c["claim"] for c in result["accepted"]], ["Bitcoin rally hits record"])
        reasons = [c["rejected_because"] for c in result["rejected"]]
        self.assertEqual(reasons[:2], ["no citation", "unknown evidence id"])
        self.assertTrue(reasons[2].startswith("number not in cited evidence"))

    def test_accepts_number_present_in_cited_item(self):
        claims = [{"claim": "Funding rate is 0.0001", "cites": ["E3"]}]
        self.assertEqual(len(citations.validate(claims, self.items)["accepted"]), 1)

    def test_ids_written_in_the_claim_text_are_references_not_numbers(self):
        # O modelo escreve "(E3)" no texto: o 3 nao e um numero afirmado e nao pode derrubar a afirmacao.
        claims = [
            {"claim": "Funding rate is 0.0001 (E3)", "cites": ["E3"]},
            {"claim": "Bitcoin rally hits record (E4), and the funding rate (E3) is 0.0001", "cites": ["E4", "E3"]},
            {"claim": "Funding rate (E3) is 7.5", "cites": ["E3"]},
        ]
        result = citations.validate(claims, self.items)
        self.assertEqual([c["claim"] for c in result["accepted"]], [c["claim"] for c in claims[:2]])
        self.assertEqual([c["rejected_because"] for c in result["rejected"]], ["number not in cited evidence: 7.5"])  # o resto do teste de numeros segue valendo

    def test_bare_numbers_and_nested_values_are_not_citations(self):
        claims = [
            {"claim": "Bitcoin rally hits record", "cites": [4]},  # numero solto, o que o modelo fez no seq 36
            {"claim": "Bitcoin rally hits record", "cites": "E4"},  # string em vez de lista
            {"claim": "Bitcoin rally hits record", "cites": [["E4"]]},  # nao hashavel
            {"claim": "Bitcoin rally hits record", "cites": [{"id": "E4"}]},  # nao hashavel
            {"claim": "Bitcoin rally hits record", "cites": ["E4"]},
        ]
        result = citations.validate(claims, self.items)
        self.assertEqual([c["cites"] for c in result["accepted"]], [["E4"]])
        self.assertEqual([c["rejected_because"] for c in result["rejected"]], ["unknown evidence id"] * 4)


class FeatureEvidenceTest(unittest.TestCase):
    def test_features_become_citable_items_after_the_market_items(self):
        from verdict_desk import features

        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        base = citations.index(pkt)
        with_feats = citations.index(pkt, features.compute(pkt))
        self.assertEqual({k: v for k, v in with_feats.items() if k in base}, base)
        extra = [v for k, v in with_feats.items() if k not in base]
        self.assertEqual(len(extra), 5)
        self.assertTrue(any(v.startswith("Trend: fast EMA") for v in extra))

    def test_claim_citing_a_feature_item_passes_the_number_check(self):
        from verdict_desk import features

        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        feats = features.compute(pkt)
        items = citations.index(pkt, feats)
        trend_id = next(k for k, v in items.items() if v.startswith("Trend: fast EMA"))
        ok = {"claim": f"Fast EMA {feats['ema_fast']} versus slow EMA {feats['ema_slow']}", "cites": [trend_id]}
        bad = {"claim": "Fast EMA 123456.7 leads", "cites": [trend_id]}
        result = citations.validate([ok, bad], items)
        self.assertEqual(len(result["accepted"]), 1)
        self.assertTrue(result["rejected"][0]["rejected_because"].startswith("number not in cited evidence"))


class LlmTest(unittest.TestCase):
    def test_parse_claims_tolerates_noise(self):
        self.assertEqual(llm.parse_claims('ok [{"claim":"a","cites":["E1"]}] done'), [{"claim": "a", "cites": ["E1"]}])
        self.assertEqual(llm.parse_claims("no json"), [])
        self.assertEqual(llm.parse_claims("[broken"), [])
        self.assertEqual(llm.parse_claims(None), [])

    def test_sides_get_opposite_stances_and_may_return_nothing(self):
        seen = {}

        def capture(system, user):
            seen[len(seen)] = system
            return "[]"

        llm.argue("bull", {"E1": "x"}, ask=capture)
        llm.argue("bear", {"E1": "x"}, ask=capture)
        self.assertIn("LONG position now", seen[0])
        self.assertIn("against a long entry", seen[1])
        self.assertIn("return an empty list only if no item can honestly be read in favor of it", seen[0])
        self.assertIn("Write the ids only in cites, never inside the claim text", seen[0])

    def test_argue_uses_injected_ask(self):
        out = llm.argue("bull", {"E1": "x"}, ask=lambda s, u: '[{"claim":"x","cites":["E1"]}]')
        self.assertEqual(out[0]["cites"], ["E1"])

    def test_prompt_requires_citing_by_the_index_ids(self):
        seen = []
        items = {"E1": "first item", "E2": "second item"}
        for side in ("bull", "bear"):
            llm.argue(side, items, ask=lambda system, user: seen.append((system, user)) or "[]")
        self.assertEqual(len(seen), 2)
        for system, user in seen:
            self.assertIn("starts with its id (E1, E2, ...)", system)
            self.assertIn("You MUST cite items by those ids", system)
            self.assertIn('has "cites": ["E3", "E14"]', system)
            self.assertIn("A bare number such as 14 is not an id", system)
            self.assertIn("character-for-character in the cited evidence items", system)
            self.assertEqual(user.splitlines(), ["E1: first item", "E2: second item"])  # o modelo ve os ids como o prompt os descreve

    def test_prompt_examples_use_the_same_id_format_as_the_index(self):
        pkt = packet(0.004, titles=("Bitcoin rally hits record",))
        self.assertTrue(all(re.fullmatch(r"E\d+", key) for key in citations.index(pkt)))
        seen = []
        llm.argue("bull", {"E1": "x"}, ask=lambda system, user: seen.append(system) or "[]")
        # All evidence id references in the prompt examples must use the E\d+ string format
        cited_ids = re.findall(r'"(E\d+)"', seen[0])
        self.assertTrue(cited_ids, "Prompt examples must show evidence id references")
        self.assertTrue(all(re.fullmatch(r"E\d+", cid) for cid in cited_ids), "All cited ids must match E\\d+ format")
        self.assertIn("E3", cited_ids)  # at least the example id from the prompt
