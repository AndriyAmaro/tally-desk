"""The audit of the eight resources, the reconciliation with Otari's usage, the scoreboard, who saw a prompt, the README blocks and the
timeline offsets. No network: the reconciliation takes the usage lookup as an argument."""

import copy
import json
import re
import subprocess
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from verdict_desk import llm, otari_report as rep

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
LEDGER = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text().splitlines() if line.strip()]
ERA = rep.era(LEDGER)
EVIDENCE = rep.read_evidence()


def receipt(role, rid, **kw):
    base = {"schema": "otari-receipt/1", "role": role, "request_id": rid, "request_id_source": "otari", "sent_model": f"m/{role}", "provider": "deepinfra",
            "served_model": f"served-{role}", "served_by": f"deepinfra:served-{role}", "attempt_count": 1, "fallback": False, "duration_ms": 1000,
            "tokens": {"prompt": 10, "completion": 5}, "cost_usd": "0.000100", "end_user": f"vd-{role}", "end_user_budget": "bud", "captured_at": "2026-11-01T00:00:00Z"}
    return {**base, **kw}


def synthetic(seq=900, offsets=True):
    t = {"bull": 5000, "bear": 5100, "checker": 7000, "judge": 8000}
    rs = {role: receipt(role, f"id-{role}", **({"started_offset_ms": t[role]} if offsets else {})) for role in t}
    return {"seq": seq, "evidence": {"symbol": "BTCUSDT", "narrative": {"receipts": [{**receipt("narrator", "id-n1", **({"started_offset_ms": 100} if offsets else {})), "batch": 1}]}},
            "llm": {"bull": {"accepted": [1, 2], "receipt": rs["bull"]}, "bear": {"accepted": [1], "receipt": rs["bear"]}, "checker": {"receipt": rs["checker"]},
                    "judge": {"receipt": rs["judge"]}, "otari": {"base_url": "https://otari.ai/api/v1", "cycle": {"cap_usd": "0.02"}}}, "verdict": {"action": "FLAT"}}


class CallsTest(unittest.TestCase):
    def test_calls_follow_the_start_offsets_when_every_receipt_has_one_and_the_cycle_order_otherwise(self):
        self.assertEqual([c["label"] for c in rep.calls_of(synthetic())], ["narrator", "bull", "bear", "checker", "judge"])
        shuffled = synthetic()
        shuffled["llm"]["bull"]["receipt"]["started_offset_ms"] = 9000
        self.assertEqual([c["label"] for c in rep.calls_of(shuffled)], ["narrator", "bear", "checker", "judge", "bull"])
        self.assertEqual([c["label"] for c in rep.calls_of(synthetic(offsets=False))], ["narrator", "bull", "bear", "checker", "judge"])

    def test_the_receipt_of_a_call_records_when_it_began_from_the_start_of_the_cycle(self):
        with mock.patch.object(llm, "_clock", {"t0": time.monotonic() - 12.5}):
            start = llm._offset_ms()
        self.assertTrue(12400 <= start <= 12700)
        llm.reset_cycle()
        self.assertLess(llm._offset_ms(), 500)

    def test_served_receipt_carries_the_offset_of_the_call_that_made_it(self):
        llm._served.receipt = {"request_id": "x"}
        llm._served.started_ms = 4321
        self.assertEqual(llm.served_receipt()["started_offset_ms"], 4321)
        llm._served.receipt = {"request_id": "y", "started_offset_ms": 7}
        self.assertEqual(llm.served_receipt()["started_offset_ms"], 7, "a receipt that already says when it began keeps it")
        llm._served.started_ms = None


class AuditTest(unittest.TestCase):
    def rows(self):
        return {r["n"]: r for r in rep.audit(LEDGER, EVIDENCE)}

    def test_the_real_ledger_proves_seven_of_eight_and_says_why_the_guardrail_does_not_count(self):
        rows = self.rows()
        self.assertEqual([rows[n]["status"] for n in range(1, 9)], ["PASS", "PASS", "PASS", "PASS", "NOT PROVEN", "PASS", "PASS", "PASS"])
        self.assertIn("valid null", rows[5]["note"])
        self.assertIn("not counted", rows[5]["note"])
        self.assertEqual(len(rep.RUBRIC), 8)

    def test_in_the_real_ledger_fallback_budget_and_end_users_are_proven_from_sealed_receipts_not_from_drills(self):
        rows = self.rows()
        self.assertEqual({n: rows[n]["source"] for n in range(1, 9) if n != 5}, {n: "sealed" for n in (1, 2, 3, 4, 6, 7, 8)})
        self.assertEqual(rows[5]["source"], "none")
        self.assertEqual(rep.summary_line(list(rows.values())), "8 resources: 7 proven from sealed receipts + 0 from drills (not sealed) + 1 NOT PROVEN")
        self.assertTrue(rows[2]["proof"] and rows[3]["proof"], "the sealed proofs name request ids of the ledger")
        self.assertTrue(rows[3]["drill_proof"], "the Otari-side 403 refusal is still named, and marked as a drill")

    def test_a_proof_that_lives_only_in_a_drill_file_is_marked_as_such_and_counted_apart(self):
        d = copy.deepcopy(ERA[-1])  # a real dossier without the canary and the headroom: nothing is sealed for rows 2 to 4
        d["llm"]["otari"].pop("canary", None)
        d["llm"]["otari"].pop("headroom", None)
        d["llm"]["judge"]["route_rule"].pop("gates", None)
        rows = {r["n"]: r for r in rep.audit([d], EVIDENCE)}
        self.assertEqual([rows[n]["source"] for n in (2, 3, 4)], ["drill", "drill", "drill"])
        self.assertTrue(all(rows[n]["note"].startswith("drill only (not sealed") for n in (2, 3, 4)))
        self.assertRegex(rep.summary_line(list(rows.values())), r"^8 resources: \d proven from sealed receipts \+ 3 from drills \(not sealed\) \+ \d NOT PROVEN$")

    def test_every_proof_id_is_a_request_id_that_the_ledger_or_the_evidence_file_holds(self):
        known = {c["receipt"].get("request_id") for d in ERA for c in rep.calls_of(d)}
        for e in EVIDENCE:
            for r in (e.get("receipt"), (e.get("big_call") or {}).get("receipt")):
                known.add((r or {}).get("request_id"))
        for row in self.rows().values():
            for rid in row["proof"]:
                self.assertIn(rid, known, f"resource {row['n']} cites {rid}")

    def test_rows_without_their_proof_are_not_proven_and_an_evaluated_guardrail_would_pass(self):
        rows = {r["n"]: r["status"] for r in rep.audit([], [])}
        self.assertEqual(set(rows.values()), {"NOT PROVEN"})
        d = synthetic()
        d["llm"]["judge"]["receipt"]["guardrail_result"] = {"prompt-injection": "pass"}
        self.assertEqual(rep.audit([d], [])[4]["status"], "PASS")
        d["llm"]["judge"]["receipt"]["guardrail_result"] = {"prompt-injection": "not_evaluated"}
        self.assertEqual(rep.audit([d], [])[4]["status"], "NOT PROVEN")

    def test_two_roles_on_the_same_model_do_not_prove_distinct_roles(self):
        d = synthetic()
        d["llm"]["bear"]["receipt"]["served_model"] = d["llm"]["bull"]["receipt"]["served_model"]
        self.assertEqual(rep.audit([d], [])[0]["status"], "NOT PROVEN")

    def test_a_native_but_different_sandbox_result_does_not_pass(self):
        d = copy.deepcopy([x for x in ERA if (x.get("sandbox") or {}).get("schema") == "otari-sandbox/2"][-1])
        self.assertEqual(rep.audit([d], [])[6]["status"], "PASS")
        d["sandbox"]["equal"] = False
        self.assertEqual(rep.audit([d], [])[6]["status"], "NOT PROVEN")

    def test_the_text_report_lists_every_row_and_the_count(self):
        text = rep.render_audit(rep.audit(LEDGER, EVIDENCE))
        self.assertEqual(len(re.findall(r"^(PASS|NOT PROVEN) ", text, re.M)), 8)
        self.assertIn("7 proven from sealed receipts + 0 from drills (not sealed) + 1 NOT PROVEN", text)
        self.assertNotIn("NAO", text)


class ReconcileTest(unittest.TestCase):
    ROW = {"status": "success", "cost_usd": "0.000100", "prompt_tokens": 10, "completion_tokens": 5, "row_count": 1}

    def test_matching_records_give_no_divergence_and_each_id_is_compared_once(self):
        calls = []
        r = receipt("bull", "same")
        out = rep.reconcile([r, dict(r), receipt("bear", "b")], lambda rid: calls.append(rid) or dict(self.ROW))
        self.assertEqual((out["checked"], out["divergences"], out["unsettled"]), (2, [], []))
        self.assertEqual(sorted(calls), ["b", "same"])

    def test_every_difference_is_named(self):
        r = receipt("judge", "j", tokens={"prompt": 11, "completion": 5}, attempt_count=2, fallback=True)
        out = rep.reconcile([r], lambda rid: {**self.ROW, "status": "error", "cost_usd": "0.000200", "row_count": 1})
        problems = " | ".join(out["divergences"][0]["problems"])
        for part in ("status", "prompt tokens", "cost", "usage rows"):
            self.assertIn(part, problems)

    def test_a_fallback_leaves_an_absorbed_row_and_that_agrees_with_two_attempts(self):
        r = receipt("judge", "j", attempt_count=2, fallback=True)
        self.assertEqual(rep.reconcile([r], lambda rid: {**self.ROW, "row_count": 2})["divergences"], [])

    def test_refusals_must_be_errors_at_otari_and_unsettled_or_client_ids_are_not_compared(self):
        refused = receipt("judge", "r", refusal={"status": 403, "code": "budget_exceeded"})
        self.assertEqual(rep.reconcile([refused], lambda rid: {"status": "error", "cost_usd": None, "row_count": 1})["divergences"], [])
        self.assertEqual(len(rep.reconcile([refused], lambda rid: dict(self.ROW))["divergences"]), 1)
        client = receipt("bull", "client:abc", request_id_source="client")
        out = rep.reconcile([client, receipt("bear", "gone")], lambda rid: None)
        self.assertEqual((out["checked"], out["unsettled"]), (0, ["gone"]))

    def test_cost_is_compared_as_a_number_not_as_text(self):
        self.assertEqual(rep.reconcile([receipt("bull", "c", cost_usd="0.0001")], lambda rid: dict(self.ROW))["divergences"], [])


class BoardAndWhoSawTest(unittest.TestCase):
    def test_percentiles_are_nearest_rank(self):
        self.assertEqual([rep.percentile([5, 1, 3, 2, 4], p) for p in (0.5, 0.95, 0.2)], [3, 5, 1])
        self.assertIsNone(rep.percentile([], 0.5))

    def test_the_board_counts_calls_successes_refusals_fallbacks_and_cost_per_accepted_claim(self):
        d = synthetic()
        d["llm"]["judge"]["receipt"] = receipt("judge", "jr", refusal={"status": 403, "code": "budget_exceeded"}, served_by=None)
        rows = {r["model"]: r for r in rep.board([d])}
        bull = rows["deepinfra:served-bull"]
        self.assertEqual((bull["calls"], bull["ok"], bull["claims"], bull["cost_per_claim_usd"]), (1, 1, 2, "0.000050"))
        self.assertEqual(rows["(no answer) m/judge"]["refusals"], {"budget_exceeded": 1})
        self.assertIsNone(rows["deepinfra:served-checker"]["cost_per_claim_usd"])

    def test_who_saw_names_the_gateway_region_by_its_url_and_each_provider_with_its_models(self):
        d = synthetic()
        cfg = {"roles": {r: {"model": f"m/{r}", "open_weights": True} for r in ("bull", "bear")}, "models_open_weights": {"m/judge": False}}
        out = rep.who_saw(d, cfg)
        self.assertEqual(out["gateway"]["host"], "otari.ai")
        self.assertIn("default region", out["gateway"]["region"])
        self.assertEqual([p["provider"] for p in out["providers"]], ["deepinfra"])
        self.assertEqual(out["providers"][0]["open_weights"], "no", "one closed model among the calls is reported, not hidden")
        d["llm"]["otari"]["base_url"] = "https://eu.api.otari.ai/api/v1"
        self.assertIn("EU", rep.who_saw(d, cfg)["gateway"]["region"])
        d["llm"]["otari"]["base_url"] = ""
        self.assertEqual(rep.who_saw(d, cfg)["gateway"]["region"], "unknown")

    def test_every_model_a_receipt_can_name_has_an_open_weights_flag_that_is_true(self):
        cfg = json.loads(rep.CONFIG.read_text())
        flags = rep._models_open(cfg)
        for d in ERA:
            for c in rep.calls_of(d):
                sent = c["receipt"].get("sent_model")
                self.assertIn(sent, flags, f"{sent} has no open-weights flag in config/otari.json")
                self.assertTrue(flags[sent], sent)


class HostsTableTest(unittest.TestCase):
    def test_every_host_the_code_or_the_config_names_is_in_the_table_or_explicitly_ignored(self):
        cfg = json.loads(rep.HOSTS.read_text())
        listed = {h["host"] for h in cfg["hosts"]} | set(cfg["ignore"])
        found = set()
        for path in list((ROOT / "verdict_desk").glob("*.py")) + list((ROOT / "tools").glob("*.py")) + [ROOT / "config" / "otari.json", ROOT / "config" / "voices.json"]:
            found |= set(re.findall(r"https?://([A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z]{2,})", path.read_text(encoding="utf-8")))
        found |= {"t.me"} if "t.me" in (ROOT / "config" / "voices.json").read_text() else set()
        missing = sorted(h for h in found if h not in listed)
        self.assertEqual(missing, [], f"add these hosts to config/external_hosts.json: {missing}")

    def test_every_row_says_what_the_host_receives_and_when(self):
        for h in json.loads(rep.HOSTS.read_text())["hosts"]:
            for field in ("host", "what", "receives", "when", "region"):
                self.assertTrue(h.get(field), (h["host"], field))


class ReadmeBlocksTest(unittest.TestCase):
    def test_the_blocks_are_replaced_between_their_markers_and_nothing_else_moves(self):
        text = "before\n" + "".join(f"<!-- {m}:start -->\nold\n<!-- {m}:end -->\nmid\n" for m in rep.MARKERS.values()) + "after\n"
        out = rep.refresh_readme(text, LEDGER, EVIDENCE)
        self.assertTrue(out.startswith("before\n") and out.endswith("after\n"))
        self.assertNotIn("old", out)
        self.assertIn("| 1 | Several open-weight models", out)
        self.assertIn("| Model (provider:model) |", out)
        self.assertIn("| `otari.ai` |", out)
        self.assertEqual(rep.refresh_readme(out, LEDGER, EVIDENCE), out, "refreshing twice changes nothing")

    def test_the_readme_has_the_three_blocks_and_every_proof_id_in_it_is_in_the_ledger_or_evidence(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for m in rep.MARKERS.values():
            self.assertIn(f"<!-- {m}:start -->", text)
        block = text[text.index("<!-- otari:proofs:start -->"):text.index("<!-- otari:proofs:end -->")]
        ids = set(re.findall(r"`([0-9a-f]{8}-[0-9a-f-]{27})`", block))
        self.assertGreaterEqual(len(ids), 8)
        blob = (ROOT / "ledger" / "ledger.jsonl").read_text() + (ROOT / "docs" / "evidence" / "otari-drills.jsonl").read_text()
        self.assertEqual([i for i in ids if i not in blob], [])


@unittest.skipUnless(NODE, "node is not installed here")
class PythonJavascriptParityTest(unittest.TestCase):
    """web/cycle.js and verdict_desk/otari_report.py compute the same scoreboard, the same who-saw view and the same call order."""

    def run_js(self, ledger, cfg):
        script = f"""
const C = require({json.dumps(str(ROOT / 'web' / 'cycle.js'))});
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const era = C.era(input.ledger);
process.stdout.write(JSON.stringify({{ board: C.board(input.ledger), who: era.map((d) => C.whoSaw(d, input.cfg)),
  order: era.map((d) => C.callsOf(d).map((c) => c.label + ':' + c.receipt.request_id)) }}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            out = subprocess.run([NODE, path], input=json.dumps({"ledger": ledger, "cfg": cfg}), capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[:400])
            return json.loads(out.stdout)
        finally:
            Path(path).unlink(missing_ok=True)

    def check(self, ledger):
        cfg = json.loads(rep.CONFIG.read_text())
        got = self.run_js(ledger, cfg)
        self.assertEqual(got["board"], json.loads(json.dumps(rep.board(ledger))))
        self.assertEqual(got["who"], json.loads(json.dumps([rep.who_saw(d, cfg) for d in rep.era(ledger)])))
        self.assertEqual(got["order"], [[f"{c['label']}:{c['receipt']['request_id']}" for c in rep.calls_of(d)] for d in rep.era(ledger)])

    def test_on_the_real_ledger(self):
        self.check(LEDGER)

    def test_on_dossiers_with_offsets_refusals_fallbacks_and_unequal_cost_roundings(self):
        a, b = synthetic(900), synthetic(901, offsets=False)
        b["llm"]["judge"]["receipt"] = receipt("judge", "jr", refusal={"status": 403, "code": "budget_exceeded"}, served_by=None, provider=None)
        a["llm"]["bull"]["receipt"].update(cost_usd="0.000003", fallback=True, attempt_count=2)
        a["llm"]["bull"]["accepted"] = [1, 2]
        b["llm"]["bull"]["receipt"].update(cost_usd="0.000002")
        b["llm"]["bull"]["accepted"] = [1, 2, 3]
        self.check(LEDGER + [a, b])


@unittest.skipUnless(NODE, "node is not installed here")
class NodeSuiteTest(unittest.TestCase):
    """The node tests of the F4 page run inside the one unittest discovery, so a single command covers them."""

    def test_the_cycle_page_and_policy_node_tests_pass(self):
        files = [str(ROOT / "tests" / n) for n in ("cycle.test.js", "cycle_page.test.js", "policy.test.js")]
        out = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, (out.stdout + out.stderr)[-600:])


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
