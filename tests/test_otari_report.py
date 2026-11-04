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


def answered(d):
    """A cycle in which the four analyst roles answered and the judge was routed: the base of fixtures that need a full cycle. The newest
    dossier may be a degraded real cycle (dossier 810: the gateway refused bull, bear and the narrator), so no test starts from position."""
    layer = d.get("llm") or {}
    return all(isinstance(layer.get(p), dict) and not layer[p].get("error") and not (layer[p].get("receipt") or {}).get("refusal") for p in ("bull", "bear", "judge", "checker")) \
        and isinstance((layer.get("judge") or {}).get("route_rule"), dict)


ANSWERED = [d for d in ERA if answered(d)]
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
        self.assertIn("from dossier 805 the header says why: status unavailable, reason not_configured", rows[5]["note"], "dossier 805 is the first judge call sealed after the header gained status and reason")
        self.assertEqual(len(rep.RUBRIC), 8)

    def test_in_the_real_ledger_fallback_budget_and_end_users_are_proven_from_sealed_receipts_not_from_drills(self):
        rows = self.rows()
        self.assertEqual({n: rows[n]["source"] for n in range(1, 9) if n != 5}, {n: "sealed" for n in (1, 2, 3, 4, 6, 7, 8)})
        self.assertEqual(rows[5]["source"], "none")
        self.assertEqual(rep.summary_line(list(rows.values())), "8 resources: 7 proven from sealed receipts + 0 from drills (not sealed) + 1 NOT PROVEN")
        self.assertTrue(rows[2]["proof"] and rows[3]["proof"], "the sealed proofs name request ids of the ledger")
        self.assertTrue(rows[3]["drill_proof"], "the Otari-side 403 refusal is still named, and marked as a drill")

    def test_a_proof_that_lives_only_in_a_drill_file_is_marked_as_such_and_counted_apart(self):
        d = copy.deepcopy(ANSWERED[-1])  # a real full cycle without the canary and the headroom: nothing is sealed for rows 2 to 4
        d["llm"]["otari"].pop("canary", None)
        d["llm"]["otari"].pop("headroom", None)
        d["llm"]["judge"]["route_rule"].pop("gates", None)
        for c in rep.calls_of(d):
            c["receipt"]["fallback"] = False  # real fallbacks of the cycle's own calls are sealed proof too: take them out as well
        rows = {r["n"]: r for r in rep.audit([d], EVIDENCE)}
        self.assertEqual([rows[n]["source"] for n in (2, 3, 4)], ["drill", "drill", "drill"])
        self.assertTrue(all(rows[n]["note"].startswith("drill only (not sealed") for n in (2, 3, 4)))
        self.assertRegex(rep.summary_line(list(rows.values())), r"^8 resources: \d proven from sealed receipts \+ 3 from drills \(not sealed\) \+ \d NOT PROVEN$")

    def test_a_degraded_dossier_at_the_tail_of_the_ledger_is_tolerated_by_the_audit_and_the_readme_builders(self):
        """Dossier 810 is a real cycle in which the gateway refused bull, bear and the narrator (402 pricing_required): no claims, no judge ruling."""
        bad = next(d for d in reversed(LEDGER) if rep.era([d]) and (d["llm"]["bull"].get("receipt") or {}).get("refusal"))
        degraded = copy.deepcopy(bad)
        self.assertFalse(answered(degraded))
        ledger = copy.deepcopy(ANSWERED[-3:]) + [degraded]
        rows = {r["n"]: r for r in rep.audit(ledger, EVIDENCE)}
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[1]["status"], "PASS", "the proof comes from the full cycles before it")
        rep.render_audit(list(rows.values()))
        rep.board(ledger)
        rep.who_saw(degraded)
        rep.models_markdown(ledger)
        rep.proofs_markdown(list(rows.values()))
        rep.board_markdown(rep.board(ledger))
        text = "<!-- otari:proofs:start -->\n<!-- otari:proofs:end -->\n<!-- otari:board:start -->\n<!-- otari:board:end -->\n<!-- otari:models:start -->\n<!-- otari:models:end -->\n<!-- otari:hosts:start -->\n<!-- otari:hosts:end -->"
        out = rep.refresh_readme(text, ledger, EVIDENCE)
        self.assertIn("8 resources", out)
        self.assertIn("Bull analyst", out)
        self.assertTrue(any(c["receipt"].get("refusal") for c in rep.calls_of(degraded)), "the degraded cycle's refusals are calls like any other")

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
        entry = {"profile": "tally-web-injection", "mode": "monitor", "valid": True, "score": 0.0, "status": "evaluated"}
        d["llm"]["judge"]["receipt"]["guardrails"] = [entry]
        d["llm"]["judge"]["receipt"]["guardrail_result"] = {"tally-web-injection": "pass"}
        self.assertEqual(rep.audit([d], [])[4]["status"], "PASS")
        d["llm"]["judge"]["receipt"]["guardrails"] = [{**entry, "valid": None, "status": "unavailable", "reason": "not_configured"}]
        d["llm"]["judge"]["receipt"]["guardrail_result"] = {"tally-web-injection": "not_evaluated"}
        self.assertEqual(rep.audit([d], [])[4]["status"], "NOT PROVEN")
        d["llm"]["judge"]["receipt"]["guardrails"] = [{**entry, "profile": "prompt-injection"}]
        d["llm"]["judge"]["receipt"]["guardrail_result"] = {"prompt-injection": "pass"}
        self.assertEqual(rep.audit([d], [])[4]["status"], "NOT PROVEN", "an evaluated verdict of the policy's own profile is not the custom profile")

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
        self.assertIn("- **`otari.ai`**,", out)
        self.assertEqual(rep.refresh_readme(out, LEDGER, EVIDENCE), out, "refreshing twice changes nothing")

    def test_the_models_table_counts_answers_from_the_receipts_and_asks_only_open_weights(self):
        table = rep.models_markdown(LEDGER)
        cfg = json.loads(rep.CONFIG.read_text(encoding="utf-8"))
        for role in ("bull", "bear", "judge", "checker", "narrator"):
            self.assertIn(f"`{cfg['roles'][role]['model']}`", table)
        answered = {k: sum(1 for d in rep.era(LEDGER) for c in rep.calls_of(d) if c["receipt"].get("served_by") == k and not rep._failed(c["receipt"])) for k in
                    {c["receipt"]["served_by"] for d in rep.era(LEDGER) for c in rep.calls_of(d) if c["receipt"].get("served_by") and c["label"] == "checker"}}
        for key, n in answered.items():
            self.assertIn(f"`{key}` ({n})", table)

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


SKIPPED = [{"model": "openai/gpt-oss-120b", "reason": "pricing_required", "status": 402, "code": "pricing_required", "request_id": "r-402", "request_id_source": "otari"}]
ROLE_CASES = [
    {}, {"role_fallback": "garbage"}, {"role_fallback": {"skipped": []}}, {"role_fallback": {"skipped": "x"}},
    {"role_fallback": {"chain": ["a", "b", "c"], "skipped": SKIPPED, "answered": "b", "answered_index": 1}},
    {"role_fallback": {"chain": ["a", "b"], "skipped": SKIPPED, "answered": None, "answered_index": None, "exhausted": True}},
    {"role_fallback": {"chain": ["a", "b"], "skipped": SKIPPED, "answered": None, "answered_index": None, "stopped_by": "budget_exceeded"}},
    {"role_fallback": {"skipped": SKIPPED + [{"model": "x", "reason": "gateway_504"}], "answered": "c"}},
]


class RoleFallbackReportTest(unittest.TestCase):
    """The app's own role chain is reported as such: it never turns into a claim about Otari's failover."""

    def with_chain(self, ledger_dossier):
        d = json.loads(json.dumps(ledger_dossier))
        d["llm"]["bull"]["receipt"]["role_fallback"] = {"chain": ["a", "b"], "skipped": SKIPPED, "answered": "b", "answered_index": 1}
        d["llm"]["bear"]["receipt"]["role_fallback"] = {"chain": ["a", "b"], "skipped": SKIPPED, "answered": None, "answered_index": None, "exhausted": True}
        return d

    def test_a_receipt_served_by_its_first_model_has_no_role_fallback(self):
        for case in ROLE_CASES[:4]:
            self.assertIsNone(rep.role_fallback_of(receipt("bull", "x", **case)))

    def test_the_lines_say_which_entry_answered_and_why_the_earlier_ones_were_skipped(self):
        got = [rep.role_fallback_of(receipt("bull", "x", **c)) for c in ROLE_CASES[4:7]]
        self.assertEqual([g["kind"] for g in got], ["answered", "exhausted", "stopped"])
        self.assertEqual(got[0]["line"], "Role fallback: answered by b, entry 2 of 3, after skipping openai/gpt-oss-120b (pricing_required, HTTP 402)")
        self.assertIn("all 2 models of the chain failed", got[1]["line"])
        self.assertIn("budget_exceeded does not move to another model", got[2]["line"])

    def test_the_routing_row_counts_the_app_chain_apart_and_its_status_and_proof_do_not_move(self):
        base = rep.audit(LEDGER)
        d = self.with_chain(ANSWERED[-1])
        d["seq"] = LEDGER[-1]["seq"] + 1
        after = rep.audit(LEDGER + [d])
        before_row, after_row = base[1], after[1]
        self.assertEqual((after_row["status"], after_row["source"]), (before_row["status"], before_row["source"]))
        chain_ids = {"r-402", d["llm"]["bull"]["receipt"]["request_id"], d["llm"]["bear"]["receipt"]["request_id"]}
        self.assertFalse(chain_ids & set(after_row["proof"]), "no request id of the app's chain is offered as proof of Otari's routing")
        self.assertIn("2 calls went down the app's own role fallback chain", after_row["note"])
        self.assertIn("1 of them ended on a later model of the role and 1 did not", after_row["note"])
        self.assertIn("not Otari's provider failover", after_row["note"])
        self.assertNotIn("role fallback", before_row["note"])

    def test_a_ledger_with_only_the_chain_and_no_otari_failover_still_says_no_fallback_seen(self):
        d = self.with_chain(ANSWERED[-1])
        d["llm"]["otari"].pop("canary", None)
        for c in rep.calls_of(d):
            c["receipt"]["fallback"] = False
        only = rep.audit([{**d, "seq": 1}], evidence=[])
        self.assertEqual(only[1]["status"], "NOT PROVEN")
        self.assertTrue(only[1]["note"].startswith("no fallback seen. Apart from that, 2 calls"))


@unittest.skipUnless(NODE, "node is not installed here")
class RoleFallbackParityTest(unittest.TestCase):
    def test_the_two_languages_write_the_same_line(self):
        script = f"""
const C = require({json.dumps(str(ROOT / 'web' / 'cycle.js'))});
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(input.map((r) => C.roleFallbackOf(r))));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            cases = [receipt("bull", "x", **c) for c in ROLE_CASES]
            out = subprocess.run([NODE, path], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        self.assertEqual(json.loads(out.stdout), [rep.role_fallback_of(c) for c in cases])


@unittest.skipUnless(NODE, "node is not installed here")
class GateLinesParityTest(unittest.TestCase):
    """The closure and drift lines of the cycle page are written twice (here and in web/cycle.js); they must say the same words, and calls_of
    must find the drift fetch in the same place in both."""

    CASES = [
        {}, {"closure": "garbage", "drift": 5}, {"closure": {"checks": "nope"}},
        {"closure": {"checks": [{"name": "receipts", "class": "hard", "pass": True}, {"name": "narrative_complete", "class": "soft", "pass": False},
                                {"name": "priced_within_cap", "class": "soft", "pass": False}]}, "drift": {"status": "changed", "source_dossier": 7, "minutes_after_decision": 190}},
        {"closure": {"checks": [{"name": "a", "class": "hard", "pass": True}]}, "drift": {"status": "unchanged", "source_dossier": 8}},
        {"drift": {"status": "none"}}, {"drift": {"status": "unavailable", "source_dossier": 9, "minutes_after_decision": 121, "reason": "boom"}},
    ]

    def test_the_two_languages_write_the_same_lines_and_order_the_calls_alike(self):
        with_call = {**synthetic(), "drift": {"status": "unchanged", "source_dossier": 1, "receipt": receipt("fetcher", "id-drift", started_offset_ms=6000)}}
        script = f"""
const C = require({json.dumps(str(ROOT / 'web' / 'cycle.js'))});
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({{ lines: input.cases.map((d) => [C.closureLine(d), C.driftLine(d)]), order: C.callsOf(input.call).map((c) => c.label + ':' + c.receipt.request_id) }}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            out = subprocess.run([NODE, path], input=json.dumps({"cases": self.CASES, "call": with_call}), capture_output=True, text=True, timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        got = json.loads(out.stdout)
        self.assertEqual(got["lines"], [[rep.closure_line(d), rep.drift_line(d)] for d in self.CASES])
        self.assertEqual(got["order"], [f"{c['label']}:{c['receipt']['request_id']}" for c in rep.calls_of(with_call)])
        self.assertIn("drift:id-drift", got["order"])


@unittest.skipUnless(NODE, "node is not installed here")
class NodeSuiteTest(unittest.TestCase):
    """The node tests of the F4 page run inside the one unittest discovery, so a single command covers them."""

    def test_the_cycle_page_and_policy_node_tests_pass(self):
        files = [str(ROOT / "tests" / n) for n in ("cycle.test.js", "cycle_page.test.js", "policy.test.js")]
        out = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, (out.stdout + out.stderr)[-600:])



if __name__ == "__main__":
    unittest.main()
