"""The guardrail in the app: the config switch (off by default), the `guardrails` field on the requests of the roles that carry untrusted text,
the per-call receipt, the audit row 5 (PASS only for the custom profile with status evaluated), the soft closure check `guardrail_ran`, and the
guardrail chip of the cycle page (Python and web/cycle.js say the same). Fake transports: no network, no key, no money."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_otari import BODY, HEADERS, KEY, FakeResponse, plain_config
from tests.test_otari_report import ANSWERED, EVIDENCE, LEDGER, synthetic
from verdict_desk import closure, dossier, llm, otari_report as rep

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
PROFILE = "tally-web-injection"
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "OTARI_MIN_INTERVAL": "", "OTARI_CYCLE_CAP_USD": "", "OTARI_TOOL_CAP_USD": "", "OTARI_GUARDRAILS": ""}
EVALUATED = {"profile": PROFILE, "mode": "monitor", "valid": True, "score": 0.0, "status": "evaluated"}
UNAVAILABLE = {"profile": PROFILE, "mode": "monitor", "valid": None, "score": None, "status": "unavailable", "reason": "evaluation_failed"}
POLICY_ENTRY = {"profile": "prompt-injection", "mode": "monitor", "valid": None, "score": None, "status": "unavailable", "reason": "not_configured"}


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause")):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()
        llm.served_receipt()

    def gateway(self, header=None):
        """A fake gateway; every request body is kept in self.sent."""
        self.sent = []

        def urlopen(request, timeout=None):
            self.sent.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(BODY, {**HEADERS, **({"Otari-Guardrails": json.dumps(header)} if header is not None else {})})
        return mock.patch.object(urllib.request, "urlopen", urlopen)


class ConfigTest(unittest.TestCase):
    def test_the_repo_config_has_the_section_and_the_switch_is_off(self):
        g = llm.otari_config()["guardrails"]
        self.assertEqual((g["enabled"], g["profile"], g["mode"], g["on_unavailable"]), (False, PROFILE, "monitor", "monitor"))
        self.assertEqual(set(g["roles"]), {"bull", "bear", "judge", "checker", "narrator", "searcher", "fetcher"})
        self.assertNotIn("sandbox", g["roles"])
        self.assertNotIn("mcp", g["roles"])

    def test_the_section_is_checked(self):
        cfg = json.loads((ROOT / "config" / "otari.json").read_text(encoding="utf-8"))
        for bad in ({"profile": ""}, {"mode": "kill"}, {"on_unavailable": "maybe"}, {"roles": ["nobody"]}, {"roles": "bull"}):
            broken = {**cfg, "guardrails": {**cfg["guardrails"], **bad}}
            path = Path(tempfile.mkdtemp()) / "otari.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(llm.LLMError, msg=str(bad)):
                llm.otari_config(path)
        broken = {k: v for k, v in cfg.items() if k != "guardrails"}
        path = Path(tempfile.mkdtemp()) / "otari.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        self.assertNotIn("guardrails", llm.otari_config(path), "a config without the section is valid: the feature is optional")


class RequestTest(Env):
    def test_off_by_default_no_request_carries_a_guardrail(self):
        with self.gateway():
            for role in llm.otari_config()["roles"]:
                llm.chat("s", "u", role=role)
        self.assertEqual(len(self.sent), 9)
        self.assertTrue(all("guardrails" not in p for p in self.sent))
        self.assertNotIn("guardrails", llm.otari_summary())

    def test_switched_on_the_roles_that_read_untrusted_text_carry_the_entry_and_the_tool_roles_that_do_not_are_left_out(self):
        entry = [{"profile": PROFILE, "mode": "monitor", "on_unavailable": "monitor"}]
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}), self.gateway():
            roles = list(llm.otari_config()["roles"])
            for role in roles:
                llm.chat("s", "u", role=role)
        carried = {role: p.get("guardrails") for role, p in zip(roles, self.sent)}
        self.assertEqual({r for r, g in carried.items() if g}, {"bull", "bear", "judge", "checker", "narrator", "searcher", "fetcher"})
        self.assertTrue(all(g == entry for g in carried.values() if g))
        self.assertIsNone(carried["sandbox"])
        self.assertIsNone(carried["mcp"])

    def test_the_config_switch_alone_turns_it_on_and_the_environment_overrides_it(self):
        cfg = json.loads((ROOT / "config" / "otari.json").read_text(encoding="utf-8"))
        cfg["guardrails"]["enabled"] = True
        path = Path(tempfile.mkdtemp()) / "otari.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with mock.patch.object(llm, "OTARI_CONFIG", path), self.gateway():
            llm.chat("s", "u", role="bull")
            with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "0"}):
                llm.chat("s", "u", role="bull")
        self.assertEqual([bool(p.get("guardrails")) for p in self.sent], [True, False])

    def test_the_mode_and_on_unavailable_come_from_the_config_so_production_cycles_stay_monitor_monitor(self):
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}):
            [entry] = llm.guardrail_request("judge")
        self.assertEqual((entry["mode"], entry["on_unavailable"]), ("monitor", "monitor"))
        self.assertEqual(set(entry), {"profile", "mode", "on_unavailable"}, "no url and no credential: the endpoint lives in the organization mandate")

    def test_an_explicit_guardrails_field_of_the_caller_wins_and_none_means_none(self):
        mine = [{"profile": PROFILE, "mode": "block"}]
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}), self.gateway():
            llm.chat("s", "u", role="bull", extra={"guardrails": mine})
            llm.chat("s", "u", role="bull", extra={"guardrails": None})
        self.assertEqual(self.sent[0]["guardrails"], mine)
        self.assertNotIn("guardrails", self.sent[1])

    def test_the_failover_canary_does_not_carry_it_even_with_the_switch_on(self):
        from verdict_desk import otari_canary
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}), self.gateway():
            otari_canary.run()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("guardrails", self.sent[0], "the canary's filler is not untrusted text and a long body has no business at the guardrails service")

    def test_the_receipt_keeps_what_was_asked_and_what_the_header_answered(self):
        header = [EVALUATED]
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}), self.gateway(header):
            llm.chat("s", "u", role="bull")
        r = llm.served_receipt()
        self.assertEqual(r["guardrails_requested"], [{"profile": PROFILE, "mode": "monitor", "on_unavailable": "monitor"}])
        self.assertEqual(r["guardrails"], header)
        self.assertEqual(r["guardrail_result"], {PROFILE: "pass"})

    def test_without_the_switch_the_receipt_has_no_guardrails_requested(self):
        with self.gateway():
            llm.chat("s", "u", role="bull")
        self.assertNotIn("guardrails_requested", llm.served_receipt())

    def test_the_messages_call_carries_it_for_a_role_in_the_list_and_for_none_otherwise(self):
        payloads = []

        def urlopen(request, timeout=None):
            payloads.append(json.loads(request.data.decode()))
            return FakeResponse({"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1, "cost_usd": "0.000001"}, "stop_reason": "end_turn"}, HEADERS)
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}), mock.patch.object(urllib.request, "urlopen", urlopen):
            llm.messages_call("bull", "s", "u")
            llm.messages_call("sandbox", "s", "u")
            llm.messages_call("bull", "s", "u", guardrails=None)
            llm.messages_call("sandbox", "s", "u", guardrails=[{"profile": "x", "mode": "block"}])
        self.assertEqual([p.get("guardrails") for p in payloads], [[{"profile": PROFILE, "mode": "monitor", "on_unavailable": "monitor"}], None, None, [{"profile": "x", "mode": "block"}]])

    def test_the_run_summary_names_the_guardrail_only_while_the_switch_is_on(self):
        with mock.patch.dict(os.environ, {"OTARI_GUARDRAILS": "1"}):
            self.assertEqual(llm.otari_summary()["guardrails"]["profile"], PROFILE)
        self.assertNotIn("guardrails", llm.otari_summary())


class ReceiptTest(Env):
    def parse(self, header):
        return llm.parse_receipt({**HEADERS, "Otari-Guardrails": json.dumps(header)}, BODY, "judge", "vd-judge")

    def test_profile_mode_valid_score_status_and_reason_are_recorded_per_call(self):
        r = self.parse([POLICY_ENTRY, EVALUATED])
        self.assertEqual(r["guardrails"], [POLICY_ENTRY, EVALUATED])
        self.assertEqual({k for k in r["guardrails"][0]}, {"profile", "mode", "valid", "score", "status", "reason"})
        self.assertEqual(r["guardrail_result"], {"prompt-injection": "not_evaluated", PROFILE: "pass"})

    def test_only_the_documented_keys_are_kept(self):
        r = self.parse([{**EVALUATED, "explanation": "free text", "endpoint": "https://x"}])
        self.assertEqual(r["guardrails"], [EVALUATED])

    def test_a_status_other_than_evaluated_is_never_a_pass_even_with_a_true_valid(self):
        for status in ("unavailable", "inconclusive"):
            r = self.parse([{**EVALUATED, "status": status}])
            self.assertEqual(r["guardrail_result"], {PROFILE: "not_evaluated"})
        self.assertEqual(self.parse([{**EVALUATED, "valid": False}])["guardrail_result"], {PROFILE: "flagged"})

    def test_an_old_header_without_status_is_read_as_before(self):
        r = self.parse([{"profile": "prompt-injection", "mode": "monitor", "valid": None, "score": None}])
        self.assertEqual(r["guardrail_result"], {"prompt-injection": "not_evaluated"})
        self.assertEqual(self.parse([{"profile": "p", "mode": "monitor", "valid": True, "score": 0.1}])["guardrail_result"], {"p": "pass"})

    def test_a_blocked_request_is_a_403_guardrail_violation_with_the_flagged_profile_and_score_in_the_receipt(self):
        import io
        import urllib.error
        from tests.test_otari import message
        body = json.dumps({"detail": {"message": "Request blocked by guardrail policy.", "code": "guardrail_violation", "guardrails": [{"profile": PROFILE, "explanation": None, "score": 0.985}]}})

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", message({"Otari-Request-ID": "req-blocked"}), io.BytesIO(body.encode()))
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(llm.OtariError) as ctx:
                llm.chat("s", "u", role="bull", extra={"guardrails": [{"profile": PROFILE, "mode": "block"}]})
        err = ctx.exception
        self.assertEqual((err.status, err.code, err.request_id), (403, "guardrail_violation", "req-blocked"))
        self.assertEqual(err.detail["guardrails"][0]["score"], 0.985)
        refusal = llm.served_receipt()["refusal"]
        self.assertEqual((refusal["status"], refusal["code"]), (403, "guardrail_violation"))
        self.assertEqual(refusal["guardrails"], [{"profile": PROFILE, "score": 0.985}])


def with_guardrail(d, entry, asked=True, roles=("bull", "bear", "checker", "judge")):
    d = copy.deepcopy(d)
    for role in roles:
        r = d["llm"][role]["receipt"]
        r["guardrails"] = [copy.deepcopy(entry)]
        r["guardrail_result"] = {entry["profile"]: "x"}
        if asked:
            r["guardrails_requested"] = [{"profile": entry["profile"], "mode": "monitor", "on_unavailable": "monitor"}]
    return d


class AuditRowTest(unittest.TestCase):
    def row5(self, ledger):
        return rep.audit(ledger, [])[4]

    def test_pass_only_when_sealed_receipts_show_status_evaluated_for_the_custom_profile_and_the_note_names_dossier_and_request_ids(self):
        d = with_guardrail(synthetic(), EVALUATED)
        row = self.row5([d])
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["source"], "sealed")
        self.assertEqual(row["dossier"], 900)
        self.assertEqual(row["proof"], ["id-bull", "id-bear", "id-checker"], "up to three request ids, in the order of the cycle")
        self.assertIn("request id-bull", row["note"])
        self.assertIn("dossier 900", row["note"])
        self.assertIn(PROFILE, row["note"])
        self.assertIn("4 passed, 0 flagged", row["note"])

    def test_a_flagged_verdict_is_still_a_check_that_ran(self):
        d = with_guardrail(synthetic(), {**EVALUATED, "valid": False, "score": 0.985})
        row = self.row5([d])
        self.assertEqual(row["status"], "PASS")
        self.assertIn("0 passed, 4 flagged", row["note"])

    def test_status_unavailable_is_never_counted_and_the_note_says_what_the_header_said(self):
        row = self.row5([with_guardrail(synthetic(), UNAVAILABLE)])
        self.assertEqual((row["status"], row["source"]), ("NOT PROVEN", "none"))
        self.assertIn("never counted", row["note"])
        self.assertIn("unavailable", row["note"])
        self.assertIn(PROFILE, row["note"])

    def test_an_inconclusive_verdict_and_an_evaluated_header_with_no_boolean_do_not_count(self):
        for entry in ({**EVALUATED, "status": "inconclusive", "valid": None}, {**EVALUATED, "valid": None}):
            self.assertEqual(self.row5([with_guardrail(synthetic(), entry)])["status"], "NOT PROVEN")

    def test_an_evaluated_verdict_of_another_profile_does_not_prove_the_custom_one(self):
        d = with_guardrail(synthetic(), {**EVALUATED, "profile": "prompt-injection"})
        row = self.row5([d])
        self.assertEqual(row["status"], "NOT PROVEN")
        self.assertIn(f"The custom profile {PROFILE} appears in no sealed receipt", row["note"])

    def test_asked_for_but_no_header_back_is_not_proven_and_says_so(self):
        d = synthetic()
        for role in ("bull", "bear", "checker", "judge"):
            d["llm"][role]["receipt"]["guardrails_requested"] = [{"profile": PROFILE, "mode": "monitor", "on_unavailable": "monitor"}]
        row = self.row5([d])
        self.assertEqual(row["status"], "NOT PROVEN")
        self.assertIn("was asked for on 4 sealed calls but no header said status evaluated (it said no header)", row["note"])

    def test_the_real_ledger_stays_not_proven_with_the_true_reason(self):
        row = self.row5(LEDGER)
        self.assertEqual(row["status"], "NOT PROVEN")
        self.assertIn("from dossier 805 the header says why: status unavailable, reason not_configured", row["note"])
        self.assertIn(f"The custom profile {PROFILE} appears in no sealed receipt yet", row["note"])
        self.assertIn("guardrails.enabled true", row["note"])

    def test_the_other_seven_rows_do_not_change(self):
        before = {r["n"]: (r["status"], r["dossier"], r["proof"]) for r in rep.audit(LEDGER, EVIDENCE) if r["n"] != 5}
        self.assertEqual({n: v[0] for n, v in before.items()}, {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS", 6: "PASS", 7: "PASS", 8: "PASS"})


class ClosureTest(unittest.TestCase):
    def body(self, entry, summary=True):
        d = with_guardrail(ANSWERED[-1], entry)
        if summary:
            d["llm"]["otari"]["guardrails"] = {"profile": PROFILE, "mode": "monitor", "on_unavailable": "monitor", "roles": ["bull", "bear", "judge", "checker"]}
        return {k: v for k, v in d.items() if k not in ("hash", "closure")}

    def check(self, body):
        return {c["name"]: c for c in closure.evaluate(body, None)["checks"]}

    def test_without_the_switch_in_the_run_summary_there_is_no_such_check(self):
        self.assertNotIn("guardrail_ran", self.check(self.body(EVALUATED, summary=False)))
        names = [n for n, _, _ in closure.CHECKS]
        self.assertEqual([c for c in self.check(self.body(UNAVAILABLE, summary=False))], names)

    def test_switched_on_and_evaluated_it_passes_as_a_soft_check(self):
        c = self.check(self.body(EVALUATED))["guardrail_ran"]
        self.assertEqual((c["class"], c["pass"]), ("soft", True))
        self.assertIn("4 of 4 calls carry a verdict the gateway evaluated", c["reason"])

    def test_switched_on_and_never_evaluated_it_is_a_warning_not_a_refusal(self):
        body = self.body(UNAVAILABLE)
        c = self.check(body)["guardrail_ran"]
        self.assertEqual((c["class"], c["pass"]), ("soft", False))
        self.assertIn("4 unavailable", c["reason"])
        result = closure.evaluate(body, None)
        self.assertEqual(result["outcome"], "warning" if all(x["pass"] for x in result["checks"] if x["class"] == "hard") else result["outcome"])
        self.assertTrue(all(x["pass"] for x in result["checks"] if x["class"] == "hard" and x["name"] == "guardrail_ran"))

    def test_the_closure_page_line_names_the_warning(self):
        d = self.body(UNAVAILABLE)
        d["closure"] = closure.evaluate(d, None)
        self.assertIn("guardrail_ran", rep.closure_line(d))

    def test_switched_on_with_no_answered_call_that_asked_is_a_warning_too(self):
        body = self.body(EVALUATED)
        for role in ("bull", "bear", "checker", "judge"):
            body["llm"][role]["receipt"].pop("guardrails_requested")
        c = self.check(body)["guardrail_ran"]
        self.assertFalse(c["pass"])
        self.assertIn("no call that was answered carried it", c["reason"])

    def test_every_sealed_closure_of_the_real_ledger_still_replays(self):
        sealed = [d for d in LEDGER if d.get("closure")]
        self.assertGreater(len(sealed), 3)
        bad = [d["seq"] for d in sealed if closure.recheck(d) != {"closure_ok": True}]
        self.assertEqual(bad, [])


class ChipTest(unittest.TestCase):
    CASES = [
        {}, {"guardrails": []}, {"guardrails": "garbage"},
        {"guardrails": [POLICY_ENTRY]}, {"guardrails": [EVALUATED]}, {"guardrails": [{**EVALUATED, "valid": False, "score": 0.9853}]},
        {"guardrails": [UNAVAILABLE]}, {"guardrails": [{**UNAVAILABLE, "reason": None}]}, {"guardrails": [{**EVALUATED, "status": "inconclusive", "valid": None}]},
        {"guardrails": [{"profile": "prompt-injection", "mode": "monitor", "valid": None, "score": None}]},
        {"guardrails": [{"profile": "p", "mode": "monitor", "valid": True, "score": 1}]},
        {"guardrails": [{**EVALUATED, "score": 12.5}, {"mode": "monitor", "valid": False}]},
        {"guardrails_requested": [{"profile": PROFILE, "mode": "monitor"}]},
        {"guardrails": [EVALUATED], "guardrails_requested": [{"profile": PROFILE}, {"profile": "other"}]},
    ]

    def test_states_and_words(self):
        by = [[(v["state"], v["text"]) for v in rep.guardrail_views(r)] for r in self.CASES]
        self.assertEqual(by[0], [])
        self.assertEqual(by[3], [("unavailable", "prompt-injection: unavailable, not_configured")])
        self.assertEqual(by[9], [("not_evaluated", "prompt-injection: not evaluated")])
        self.assertEqual(by[4], [("passed", f"{PROFILE}: passed (score 0)")])
        self.assertEqual(by[5], [("flagged", f"{PROFILE}: flagged (score 0.9853)")])
        self.assertEqual(by[6], [("unavailable", f"{PROFILE}: unavailable, evaluation_failed")])
        self.assertEqual(by[8], [("inconclusive", f"{PROFILE}: inconclusive")])
        self.assertEqual(by[12], [("no_header", f"{PROFILE}: asked, no verdict in the reply")])

    @unittest.skipUnless(NODE, "node is not installed here")
    def test_javascript_writes_the_same_chips(self):
        real = [c["receipt"] for d in rep.era(LEDGER)[-30:] for c in rep.calls_of(d)]
        cases = self.CASES + real
        script = f"""
const C = require({json.dumps(str(ROOT / 'web' / 'cycle.js'))});
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((r) => C.guardrailsOf(r))));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            out = subprocess.run([NODE, path], input=json.dumps(cases), capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[:400])
        finally:
            os.unlink(path)
        self.assertEqual(json.loads(out.stdout), [rep.guardrail_views(r) for r in cases])
        self.assertTrue(any(rep.guardrail_views(r) for r in real), "the real ledger has receipts with a guardrail entry to compare")


if __name__ == "__main__":
    unittest.main()
