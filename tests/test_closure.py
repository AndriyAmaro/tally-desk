"""The closure gate: a clean cycle passes and seals `closure`; a planted fault (a missing receipt, a wrong evidence hash, a verdict that does
not replay) is refused and nothing is appended; soft checks seal a warning; dossiers recorded before the gate verify and replay as before.
Fake transports only: no network, no key, no money."""

import copy
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_otari_tools import FlowEnv
from verdict_desk import closure, dossier, llm, otari_report as rep

ROOT = Path(__file__).resolve().parents[1]
LEDGER = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")


class Gate(FlowEnv):
    """One real fake cycle through decide-v7, kept as the clean fixture."""

    def clean(self):
        code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, len(ledger)), (0, 1), out)
        return ledger[0]

    @staticmethod
    def body(d):
        return {k: v for k, v in d.items() if k not in ("hash", "closure")}

    def refused(self, body, frozen=None):
        with self.assertRaises(closure.ClosureRefused) as ctx:
            closure.seal(body, frozen or dossier.digest(body["evidence"]))
        return [c["name"] for c in ctx.exception.failed]


class CleanCycleTest(Gate):
    def test_a_clean_cycle_passes_every_check_and_the_closure_is_sealed_in_the_dossier(self):
        d = self.clean()
        c = d["closure"]
        self.assertEqual((c["version"], c["outcome"]), (1, "pass"))
        self.assertEqual([x["name"] for x in c["checks"]], [n for n, _, _ in closure.CHECKS])
        self.assertTrue(all(x["pass"] and x["reason"] and "\n" not in x["reason"] for x in c["checks"]))
        self.assertEqual({x["class"] for x in c["checks"]}, {"hard", "soft"})
        self.assertEqual(c["evidence_sha256"], dossier.digest(d["evidence"]))
        self.assertEqual(rep.closure_line(d), "Closure gate: 9 of 9 checks passed")

    def test_the_chain_verifies_and_the_replay_recomputes_the_gate_from_the_dossier_alone(self):
        d = self.clean()
        self.assertEqual(dossier.verify([d]), [])
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(d, "auto")
        self.assertTrue(r["matches"])
        self.assertIs(r["closure_ok"], True)

    def test_an_edited_closure_or_an_edited_dossier_under_it_fails_the_replay(self):
        d = self.clean()
        flipped = copy.deepcopy(d)
        flipped["closure"]["checks"][5]["pass"] = False  # claims a warning the recomputation does not find
        self.assertFalse(dossier.replay(flipped, "auto")["closure_ok"])
        dropped = copy.deepcopy(d)
        dropped["closure"]["checks"].pop()
        self.assertFalse(dossier.replay(dropped, "auto")["closure_ok"])
        other = copy.deepcopy(d)
        other["closure"]["version"] = 2
        self.assertFalse(dossier.replay(other, "auto")["closure_ok"])
        tampered = copy.deepcopy(d)
        tampered["evidence"]["funding"]["rate"] = 0.5  # the evidence no longer hashes to what the gate sealed
        r = dossier.replay(tampered, "auto")
        self.assertFalse(r["closure_ok"])
        self.assertFalse(r["matches"])

    def test_the_gate_runs_after_the_calls_and_before_the_closes_of_earlier_positions(self):
        with mock.patch("verdict_desk.practice.execute_closes", return_value=[]) as closes:
            self.clean()
        closes.assert_called_once()


class PlantedFaultTest(Gate):
    def test_a_missing_receipt_is_refused(self):
        body = self.body(self.clean())
        del body["llm"]["bull"]["receipt"]
        self.assertEqual(self.refused(body), ["receipts"])

    def test_a_receipt_without_an_otari_request_id_is_refused_but_a_recorded_failure_is_not(self):
        body = self.body(self.clean())
        body["llm"]["bear"]["receipt"]["request_id_source"] = "client"
        self.assertEqual(self.refused(body), ["receipts"])
        body["llm"]["bear"]["receipt"]["refusal"] = {"status": None, "code": "cycle_cap_exceeded", "budget_scope": "cycle", "source": "client"}
        checks = {c["name"]: c for c in closure.evaluate(body, dossier.digest(body["evidence"]))["checks"]}
        self.assertTrue(checks["receipts"]["pass"], "a recorded failure is an accounted call")
        self.assertFalse(checks["analysts_answered"]["pass"], "but the bear did not answer, which is its own hard check")

    def test_the_drift_fetch_is_a_call_too_and_needs_its_receipt(self):
        body = self.body(self.clean())
        body["drift"] = {"schema": "otari-drift/1", "status": "unchanged", "receipt": None}
        self.assertIn("receipts", self.refused(body))

    def test_a_wrong_evidence_hash_is_refused(self):
        d = self.clean()
        body = self.body(d)
        self.assertEqual(self.refused(body, "0" * 64), ["evidence_frozen"])
        body["evidence"]["web"]["page"]["excerpt_sha256"] = "f" * 64  # a hash sealed inside the evidence that does not recompute
        self.assertEqual(self.refused(body), ["evidence_frozen", "verdict_replays"])

    def test_a_verdict_that_does_not_replay_is_refused(self):
        body = self.body(self.clean())
        body["verdict"]["action"] = "LONG" if body["verdict"]["action"] != "LONG" else "SHORT"
        self.assertEqual(self.refused(body), ["verdict_replays"])

    def test_a_checker_gate_that_differs_from_its_recomputation_is_refused(self):
        body = self.body(self.clean())
        body["verdict"]["checker_gate"] = {"flagged": ["S1"], "judge_relied_on_flagged": ["S1"], "judge_voided": True}
        self.assertIn("checker_gate", self.refused(body))

    def test_a_silently_partial_narrative_is_refused_and_a_declared_one_only_warns(self):
        body = self.body(self.clean())
        nar = body["evidence"]["narrative"]
        nar["chunk_errors"] = ["batch 2 of 3: boom"]  # failed batches, no partial flag, and batch 2 and 3 left no receipt
        self.assertIn("narrative_flagged", self.refused(body))
        nar["partial"] = True
        nar["receipts"] = [{"batch": n, "schema": "otari-receipt/1", "request_id": f"r{n}", "request_id_source": "otari"} for n in (1, 2, 3)]
        sealed = closure.seal(body, dossier.digest(body["evidence"]))
        by = {c["name"]: c for c in sealed["checks"]}
        self.assertTrue(by["narrative_flagged"]["pass"])
        self.assertFalse(by["narrative_complete"]["pass"])
        self.assertEqual(sealed["outcome"], "warning")

    def test_the_refusal_names_every_failed_hard_check(self):
        body = self.body(self.clean())
        del body["llm"]["judge"]["receipt"]
        body["verdict"]["action"] = "LONG" if body["verdict"]["action"] != "LONG" else "SHORT"
        with self.assertRaises(closure.ClosureRefused) as ctx:
            closure.seal(body, dossier.digest(body["evidence"]))
        self.assertIn("receipts", str(ctx.exception))
        self.assertIn("verdict_replays", str(ctx.exception))
        self.assertEqual(ctx.exception.closure["outcome"], "refused")


class AnalystsAnsweredTest(Gate):
    def degraded(self):
        """A copy of dossier 810, a real cycle in which the gateway refused bull, bear and the narrator with 402 pricing_required."""
        d = next(x for x in reversed(LEDGER) if (((x.get("llm") or {}).get("bull") or {}).get("receipt") or {}).get("refusal", {}).get("code") == "pricing_required")
        return copy.deepcopy(d)

    def test_a_copy_of_dossier_810_is_refused_for_analysts_answered_with_the_roles_and_the_otari_code(self):
        d = self.degraded()
        body = {k: v for k, v in d.items() if k not in ("hash", "closure")}
        with self.assertRaises(closure.ClosureRefused) as ctx:
            closure.seal(body, dossier.digest(body["evidence"]))
        self.assertIn("analysts_answered", [c["name"] for c in ctx.exception.failed])
        self.assertEqual([c["name"] for c in ctx.exception.failed if c["name"] != "analysts_answered"], [], "810 is otherwise a coherent record")
        reason = next(c["reason"] for c in ctx.exception.failed)
        self.assertIn("bull did not answer (pricing_required, HTTP 402)", reason)
        self.assertIn("bear did not answer (pricing_required, HTTP 402)", reason)
        record = closure.refusal_record(ctx.exception, "2026-11-04T17:00:00Z")
        self.assertEqual([(f["role"], f["code"], f["status"]) for f in record["roles_failed"]], [("bull", "pricing_required", 402), ("bear", "pricing_required", 402)])
        self.assertGreater(float(record["usd"]), 0, "what the refused cycle spent is on the line")

    def test_one_failed_analyst_is_enough_and_an_error_without_a_receipt_counts_too(self):
        body = {k: v for k, v in self.clean().items() if k not in ("hash", "closure")}
        body["llm"]["bear"]["error"] = "HTTP 504 from the gateway"
        self.assertEqual([f["role"] for f in closure.analyst_failures(body)], ["bear"])
        self.assertIn("analysts_answered", self.refused(body))

    def test_a_clean_dossier_passes_and_a_dossier_with_no_model_layer_has_nothing_to_judge(self):
        d = self.clean()
        self.assertTrue(next(c for c in d["closure"]["checks"] if c["name"] == "analysts_answered")["pass"])
        self.assertEqual(closure.analyst_failures({"evidence": {}}), [])

    def test_a_cycle_whose_analysts_are_refused_by_the_gateway_is_refused_whole_with_nothing_written_and_the_head_untouched(self):
        import io
        import urllib.error
        real = self.reply
        head = self.ledger.parent / "head.json"
        head.write_text('{"seq": 0}\n')

        def reply(request, timeout=None):
            payload = json.loads(request.data.decode())
            if not request.full_url.endswith("/messages") and any(w in payload["messages"][0]["content"] for w in ("bull analyst", "bear analyst")):
                from tests.test_otari import message
                raise urllib.error.HTTPError(request.full_url, 402, "Payment Required", message({"Otari-Request-ID": "req-402", "Otari-Error-Code": "pricing_required"}), io.BytesIO(b'{"code": "pricing_required"}'))
            return real(request, timeout)
        with mock.patch.object(self, "reply", reply), mock.patch("verdict_desk.practice.execute_open") as opens, mock.patch("verdict_desk.practice.execute_closes") as closes:
            code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, ledger), (closure.EXIT_CODE, []))
        self.assertFalse(self.ledger.exists(), "no dossier, not even a half-written one")
        self.assertEqual(head.read_text(), '{"seq": 0}\n', "the head file is as it was")
        opens.assert_not_called()
        closes.assert_not_called()
        line = closure.read_refusals(self.ledger)[0]
        self.assertEqual([f["name"] for f in line["failed"]], ["analysts_answered"])
        self.assertEqual({f["code"] for f in line["roles_failed"]}, {"pricing_required"})
        self.assertEqual({f["role"] for f in line["roles_failed"]}, {"bull", "bear"})


class SoftCheckTest(Gate):
    def test_unpriced_calls_and_a_spend_over_the_cap_seal_a_warning_not_a_refusal(self):
        body = self.body(self.clean())
        cycle = body["llm"]["otari"]["cycle"]
        cycle["unpriced_calls"] = 2
        cycle["tools"]["spent_usd"] = "0.070000"
        sealed = closure.seal(body, dossier.digest(body["evidence"]))
        by = {c["name"]: c for c in sealed["checks"]}
        self.assertFalse(by["priced_within_cap"]["pass"])
        self.assertIn("2 core calls are unpriced", by["priced_within_cap"]["reason"])
        self.assertIn("passed its cap", by["priced_within_cap"]["reason"])
        self.assertEqual(sealed["outcome"], "warning")

    def test_a_headroom_lookup_with_no_recorded_status_warns(self):
        body = self.body(self.clean())
        body["llm"]["otari"]["headroom"] = {"users": {"vd-bull": {"found": True, "used_usd": "0.001"}, "vd-judge": {"found": False}, "vd-mcp": {"found": False, "lookup": "absent"}},
                                           "read": {"asked": 3, "found": 1, "absent": 1, "failed": 1}}
        sealed = closure.seal(body, dossier.digest(body["evidence"]))
        by = {c["name"]: c for c in sealed["checks"]}
        self.assertFalse(by["headroom_lookups"]["pass"])
        self.assertIn("vd-judge", by["headroom_lookups"]["reason"])
        body["llm"]["otari"]["headroom"]["users"]["vd-judge"] = {"found": False, "lookup": "error", "status": 504}
        self.assertTrue({c["name"]: c for c in closure.seal(body, dossier.digest(body["evidence"]))["checks"]}["headroom_lookups"]["pass"])


class RefusedCycleTest(Gate):
    def test_a_refused_cycle_appends_nothing_logs_the_reason_and_exits_with_the_refusal_code(self):
        with mock.patch.object(dossier, "_served", lambda m, block: None), mock.patch("verdict_desk.practice.execute_closes") as closes, \
                mock.patch("verdict_desk.practice.execute_open") as opens:
            code, out, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, ledger), (closure.EXIT_CODE, []))
        self.assertFalse(self.ledger.exists(), "nothing was appended")
        closes.assert_not_called()
        opens.assert_not_called()
        lines = closure.read_refusals(self.ledger)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual((line["schema"], line["symbol"], line["seq_not_used"]), ("closure-refusal/1", "BTCUSDT", 1))
        self.assertEqual([f["name"] for f in line["failed"]], ["receipts"])
        self.assertIn("no receipt", line["failed"][0]["reason"])
        self.assertEqual(line["closure"]["outcome"], "refused")
        self.assertRegex(line["usd"], r"^\d+\.\d+$")
        self.assertIsInstance(line["users"], dict)
        self.assertEqual(len(line["candidate_sha256"]), 64)
        self.assertEqual(line["prev_hash"], dossier.GENESIS)

    def test_the_refusal_goes_to_the_error_stream_for_the_runner_log(self):
        import io
        err = io.StringIO()
        with mock.patch.object(dossier, "_served", lambda m, block: None), mock.patch("sys.stderr", err):
            self.run_flow(rules_version="v7")
        self.assertIn("closure gate REFUSED BTCUSDT", err.getvalue())
        self.assertIn("nothing appended", err.getvalue())

    def test_the_next_clean_cycle_is_appended_after_a_refusal_with_the_same_sequence_number(self):
        with mock.patch.object(dossier, "_served", lambda m, block: None):
            self.run_flow(rules_version="v7")
        self.n = 0
        code, _, ledger = self.run_flow(rules_version="v7")
        self.assertEqual((code, [d["seq"] for d in ledger]), (0, [1]))
        self.assertEqual(len(closure.read_refusals(self.ledger)), 1)

    def test_a_run_without_an_otari_key_has_no_gate_and_no_closure(self):
        with mock.patch.dict("os.environ", {"OTARI_API_KEY": ""}):
            d = dossier.append(self.pkt, Path(tempfile.mkdtemp()) / "l.jsonl", ask=lambda *a, **k: "[]", rules_version="v7")
        self.assertNotIn("closure", d)


class OldDossiersTest(unittest.TestCase):
    def test_dossiers_without_the_field_are_untouched_by_the_replay_the_verify_and_the_report_line(self):
        self.assertEqual(dossier.verify(LEDGER), [])
        old = [d for d in LEDGER if "closure" not in d]
        self.assertGreater(len(old), 100)
        for d in old[:3] + old[-3:]:
            r = dossier.replay(d, rules_version="auto")
            self.assertNotIn("closure_ok", r, d["seq"])
            self.assertEqual(closure.recheck(d), {})
            self.assertEqual(rep.closure_line(d), "Closure gate: not applied to this dossier (recorded before the gate)")

    def test_every_dossier_the_ledger_holds_still_replays_with_the_gate_in_the_code(self):
        bad = [d["seq"] for d in LEDGER if not dossier.replay(d, rules_version="auto")["matches"]]
        self.assertEqual(bad, [])

    def test_the_recent_real_dossiers_would_pass_every_hard_check(self):
        """Calibration against the live record: the gate must not refuse what the app really produces."""
        for d in rep.era(LEDGER)[-10:]:
            if int(d["seq"]) < 794:  # the receipts of the first Otari dossiers predate request_id_source
                continue
            result = closure.evaluate({k: v for k, v in d.items() if k != "hash"}, None)
            hard = [c["name"] for c in result["checks"] if c["class"] == "hard" and not c["pass"]]
            self.assertEqual(hard, [] if not closure.analyst_failures(d) else ["analysts_answered"], d["seq"])  # only a cycle whose analysts were refused is stopped


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
