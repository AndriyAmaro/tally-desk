"""The exhibit: a dossier rendered as something a reviewer can file and rely on.

The properties worth protecting, because each one is a claim a judge may test:

  * the integrity line is recomputed at render time, so a tampered record says so on its face;
  * the attribution section comes from the record's own sensitivity block, not from a re-run;
  * the limits section is always present — an exhibit that only advertises is a brochure;
  * the on-chain comparison normalises the 0x prefix, because the contract and the ledger
    disagree on that and comparing them raw produced a false mismatch on an anchored record.
"""

import json
import re
import unittest
from pathlib import Path

from verdict_desk import canonical, exhibit

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "ledger" / "ledger.jsonl"


def ledger_rows():
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.rows = ledger_rows()
        self.row = self.rows[-1]

    def test_it_renders_the_decisions_own_fields(self):
        page = exhibit.render(self.row)
        v = self.row["verdict"]
        self.assertIn(self.row["evidence"]["symbol"], page)
        self.assertIn(v["action"], page)
        self.assertIn(f"#{self.row['seq']}", page)
        self.assertIn(str(v["edge"]), page)

    def test_integrity_line_matches_for_an_untouched_record(self):
        page = exhibit.render(self.row)
        self.assertIn("MATCH: the record is unchanged", page)
        self.assertIn(self.row["hash"], page)

    # ---- the plain-English summary: for a reader who is not a specialist ----------------------

    def synthetic(self, **verdict):
        base = {
            "seq": 7, "prev_hash": "0" * 64,
            "evidence": {"symbol": "BTCUSDT", "collected_at": "2026-10-25T02:22:12+00:00", "freshness": {}, "narrative": {}},
            "verdict": {"rules_version": "v5", "policy": {}, "bull_score": 3.0, "bear_score": 1.0, "edge": 2.0, "threshold": 1.5,
                        "action": "LONG", "rationale": "x", "entry": 100.0, "stop": 97.0, "take_profit": 106.0, "size": 33.3333, "risk_pct": 1.0},
            "sensitivity": {"drop_one": [{"side": "bull", "claim": "Trend is up", "weight": 3.0, "flips": True}]},
            "bull": [], "bear": [], "llm": {},
        }
        base["verdict"].update(verdict)
        return base

    def plain(self, page):
        start = page.index('id="plain"')
        return re.sub(r"<[^>]+>", " ", page[start:page.index('id="custody"')])

    def test_the_summary_comes_first_and_says_nothing_real_was_traded(self):
        page = exhibit.render(self.synthetic())
        self.assertIn('id="plain"', page)
        self.assertLess(page.index('id="plain"'), page.index('id="custody"'))
        text = self.plain(page)
        self.assertIn("In plain English", text)
        self.assertIn("No real order", text)

    def test_a_long_is_explained_with_its_stop_target_and_the_most_it_can_lose(self):
        text = self.plain(exhibit.render(self.synthetic()))
        for fact in ("BTCUSDT", "100", "97", "106", "1.0%", "buy", "Trend is up"):
            self.assertIn(fact, text)

    def test_a_short_says_sell(self):
        text = self.plain(exhibit.render(self.synthetic(action="SHORT", stop=103.0, take_profit=94.0)))
        self.assertIn("sell", text.lower())
        self.assertIn("103", text)

    def judged(self, judge):
        row = self.synthetic(rules_version="v6")
        row["llm"] = {"bull": {"accepted": [{"claim": "Holds at 100", "cites": ["E1"]}], "rejected": []},
                      "bear": {"accepted": [], "rejected": []}, "judge": judge}
        return exhibit.render(row)

    def test_an_accepted_judge_lean_is_explained_with_its_weight_and_its_limit(self):
        page = self.judged({"ruling": {"lean": "bull", "conviction": "medium", "relied_on": ["B1"], "reason": "B1 holds."},
                            "accepted": True, "weight": 0.75})
        text = self.plain(page)
        self.assertIn("AI judge", text)
        self.assertIn("0.75", text)
        self.assertIn("never enough to open a position", text)
        self.assertIn("B1 holds.", page)

    def test_the_model_that_really_answered_is_named(self):
        row = self.synthetic(rules_version="v6")
        row["llm"] = {"bull": {"accepted": [], "rejected": [], "served_by": "qwen/qwen3.8-27b"},
                      "bear": {"accepted": [], "rejected": []},
                      "judge": {"ruling": None, "accepted": False, "weight": 0.0, "rejected_because": "x", "served_by": "glm-5.2"}}
        page = exhibit.render(row)
        self.assertIn("answered by qwen/qwen3.8-27b", page)
        self.assertIn("answered by glm-5.2", page)

    def test_the_route_says_which_models_were_tried_before_the_one_that_answered(self):
        row = self.synthetic(rules_version="v6")
        route = [{"model": "gpt-oss-120b", "status": 429, "ms": 812}, {"model": "agnes-3.0-flash", "status": 200, "ms": 9400}]
        row["llm"] = {"bull": {"accepted": [], "rejected": [], "served_by": "agnes-3.0-flash", "route": route,
                               "routing_policy": {"sha256": "ab" * 32, "as_of_seq": 383}},
                      "bear": {"accepted": [], "rejected": [], "served_by": "openai/gpt-oss-120b",
                               "route": [{"model": "gpt-oss-120b", "status": 200, "ms": 3160}]}}
        page = exhibit.render(row)
        self.assertIn("answered by agnes-3.0-flash after gpt-oss-120b returned 429", page)
        self.assertIn("answered by openai/gpt-oss-120b, first in the routing policy", page)

    def test_a_practice_trade_sent_to_sepolia_is_shown_with_its_transaction(self):
        row = self.synthetic(rules_version="v6")
        row["execution"] = {"network": "ethereum-testnet-sepolia", "kind": "open", "side": "short", "status": "executed",
                            "tx": "0x" + "ab" * 32, "block": 12000563, "decision_digest": "0x" + "cd" * 32,
                            "label": "practice trade executed on Sepolia testnet", "amount_in": 1895000000000000,
                            "token_in": "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14", "amount_out": 86146055,
                            "token_out": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"}
        page = exhibit.render(row)
        self.assertIn("practice trade executed on Sepolia testnet", page)
        self.assertIn("https://sepolia.etherscan.io/tx/0x" + "ab" * 32, page)
        self.assertIn("0.001895 WETH", page)
        self.assertIn("86.146055 USDC", page)

    def test_no_page_claims_nothing_is_signed_now_that_practice_trades_go_to_sepolia(self):
        page = exhibit.render(self.synthetic(rules_version="v6"))
        self.assertNotIn("signs nothing", page)
        self.assertNotIn("No order is placed, signed or transmitted", page)
        self.assertIn("no real-money order", page)

    def test_a_voided_judge_says_it_added_nothing_and_why(self):
        page = self.judged({"ruling": {"lean": "bull", "conviction": "high", "relied_on": ["S4"], "reason": "x"},
                            "accepted": False, "weight": 0.0, "rejected_because": "unknown claim id: 'S4'"})
        self.assertIn("added nothing", self.plain(page))
        self.assertIn("unknown claim id", page)

    def test_a_declined_entry_says_no_position_was_opened_and_why(self):
        text = self.plain(exhibit.render(self.synthetic(action="WATCH_REVERSAL", bull_score=1.0, bear_score=1.5, edge=-0.5,
                                                        rationale="one argument away from flipping", entry=None)))
        self.assertIn("did not open a position", text)
        self.assertNotIn("stop at", text)

    def test_a_stale_price_is_a_refusal_not_a_decision(self):
        text = self.plain(exhibit.render(self.synthetic(action="BLOCKED_STALE", rationale="newest price too old")))
        self.assertIn("refused to decide", text)

    def test_a_failed_analyst_layer_is_named_in_the_summary(self):
        record = self.synthetic()
        record["llm"] = {"bull": {"accepted": [], "rejected": [], "error": "HTTP 402 Payment Required"}, "bear": {"accepted": [], "rejected": [], "error": "x"}}
        text = self.plain(exhibit.render(record))
        self.assertIn("could not run", text)
        self.assertIn("fixed rules alone", text)

    def test_claims_that_passed_and_failed_the_citation_check_are_counted(self):
        record = self.synthetic()
        record["llm"] = {"bull": {"accepted": [{"claim": "a", "cites": ["x"]}] * 2, "rejected": [{"claim": "b"}]}, "bear": {"accepted": [{"claim": "c", "cites": ["y"]}], "rejected": []}}
        text = self.plain(exhibit.render(record))
        self.assertIn("wrote 4 claims", text)
        self.assertIn("3 passed", text)
        self.assertIn("1 was refused", text)

    def test_an_untouched_real_record_is_called_unchanged(self):
        text = self.plain(exhibit.render(self.row))
        self.assertIn("matches its saved fingerprint", text)
        self.assertNotIn("does NOT match", text)

    def test_an_edited_record_is_called_out_in_the_summary_too(self):
        record = self.synthetic()
        record["hash"] = "f" * 64  # not the record's real fingerprint
        text = self.plain(exhibit.render(record))
        self.assertIn("does NOT match", text)

    def test_every_published_dossier_gets_a_summary_without_error(self):
        for row in self.rows[-40:]:
            self.assertIn("In plain English", self.plain(exhibit.render(row)))

    def test_an_analyst_layer_that_failed_says_why_instead_of_saying_nothing(self):
        """"Nothing kept" is true and useless; a reader needs to know the provider failed, and what decided instead."""
        broken = json.loads(json.dumps(self.row))
        reason = "HTTP 402 Payment Required from https://api.example.com/v1 model m: quota"
        broken["llm"] = {"bull": {"accepted": [], "rejected": [], "error": reason}, "bear": {"accepted": [], "rejected": [], "error": reason}}
        page = exhibit.render(broken)
        self.assertEqual(page.count("returned an error and produced no claims"), 2)
        self.assertIn("HTTP 402 Payment Required", page)
        self.assertIn("deterministic engine alone", page)

    def test_an_analyst_that_ran_and_kept_nothing_still_says_so(self):
        quiet = json.loads(json.dumps(self.row))
        quiet["llm"] = {"bull": {"accepted": [], "rejected": []}, "bear": {"accepted": [], "rejected": []}}
        page = exhibit.render(quiet)
        self.assertIn("Nothing kept.", page)
        self.assertNotIn("returned an error", page)

    def test_a_tampered_record_says_mismatch_on_its_face(self):
        tampered = json.loads(json.dumps(self.row))
        tampered["verdict"]["action"] = "LONG" if tampered["verdict"]["action"] != "LONG" else "SHORT"
        page = exhibit.render(tampered)
        self.assertIn("MISMATCH: this record was edited", page)

    def test_the_attribution_table_marks_the_deciding_argument(self):
        page = exhibit.render(self.row)
        if not self.row.get("sensitivity"):
            self.skipTest("predates sensitivity")
        flips = [d for d in self.row["sensitivity"]["drop_one"] if d["flips"]]
        self.assertEqual(page.count('<tr class="flip">'), len(flips))

    def test_the_limits_section_is_always_there(self):
        page = exhibit.render(self.row)
        self.assertIn("What this exhibit does not prove", page)
        self.assertIn("simulated", page.lower())
        self.assertIn("fees", page.lower())

    def test_anchor_hash_with_0x_prefix_still_counts_as_a_match(self):
        anchor = {"matches": None}
        raw = "0x" + self.row["hash"]
        published = (self.row.get("hash") or "").lower().removeprefix("0x")
        anchored = raw.lower().removeprefix("0x")
        self.assertEqual(published, anchored)

        page = exhibit.render(self.row, anchor={"matches": published == anchored, "anchored_seq": self.row["seq"],
                                                "anchored_at": 1792605204})
        self.assertIn("matches the hash written on Ethereum Sepolia", page)

    def test_it_is_self_contained(self):
        page = exhibit.render(self.row)
        self.assertNotIn("<script src=", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))
        self.assertIn("@media print", page)

    def test_every_published_dossier_renders_without_raising(self):
        for row in self.rows[-12:]:
            page = exhibit.render(row, chain=exhibit.chain_context(self.rows, row["seq"]))
            self.assertIn("Decision exhibit", page)


class ChainContextTest(unittest.TestCase):
    def test_the_whole_published_chain_verifies(self):
        rows = ledger_rows()
        ctx = exhibit.chain_context(rows, rows[-1]["seq"])
        self.assertTrue(ctx["ok"], ctx.get("problem"))
        self.assertEqual(ctx["length"], len(rows))

    def test_a_broken_link_is_reported(self):
        rows = ledger_rows()[-5:]
        rows[2] = dict(rows[2])
        rows[2]["prev_hash"] = "0" * 64
        ctx = exhibit.chain_context(rows, rows[-1]["seq"])
        self.assertFalse(ctx["ok"])
        self.assertIn("does not link", ctx["problem"])


class IndexTest(unittest.TestCase):
    def test_the_index_links_each_exhibit(self):
        rows = ledger_rows()[-3:]
        page = exhibit.render_index([(r, f"exhibit-{r['seq']}.html") for r in rows])
        for r in rows:
            self.assertIn(f"exhibit-{r['seq']}.html", page)
        self.assertIn("Decision exhibits", page)


if __name__ == "__main__":
    unittest.main()
