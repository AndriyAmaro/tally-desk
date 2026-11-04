"""`python3 -m verdict_desk otari-drill guardrail`: a benign and a malicious headline through one role with the custom guardrail in block mode.
The benign one is served with an evaluated verdict, the malicious one is refused with 403 guardrail_violation and no provider call. Fake transport:
no network, no key, no money."""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_otari import BODY, HEADERS, KEY, FakeResponse, message
from verdict_desk import __main__ as cli
from verdict_desk import llm, otari_drill, otari_report as rep

PROFILE = "tally-web-injection"
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "OTARI_MIN_INTERVAL": "", "OTARI_CYCLE_CAP_USD": "", "OTARI_GUARDRAILS": ""}
BLOCKED = {"detail": {"message": "Request blocked by guardrail policy.", "code": "guardrail_violation", "guardrails": [{"profile": PROFILE, "explanation": None, "score": 0.985}]}}


class Gateway:
    """Behaves like Otari with a block guardrail: a text the rules would flag is refused with 403 before any model, the rest is served."""

    def __init__(self, flags=lambda text: "ignore all previous" in text.lower(), verdict=None, unreachable=False):
        self.requests, self.flags, self.verdict, self.unreachable = [], flags, verdict, unreachable

    def __call__(self, request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        self.requests.append(payload)
        rid = f"req-{len(self.requests)}"
        text = payload["messages"][-1]["content"]
        if self.unreachable:
            raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", message({"Otari-Request-ID": rid}),
                                         io.BytesIO(json.dumps({"detail": f"guardrail profile '{PROFILE}' could not be evaluated"}).encode()))
        if self.flags(text):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", message({"Otari-Request-ID": rid}), io.BytesIO(json.dumps(BLOCKED).encode()))
        header = self.verdict if self.verdict is not None else [{"profile": PROFILE, "mode": "block", "valid": True, "score": 0.0, "status": "evaluated"}]
        return FakeResponse(BODY, {**HEADERS, "Otari-Request-ID": rid, "Otari-Guardrails": json.dumps(header)})


class DrillTest(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause")):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()
        llm.served_receipt()

    def drill(self, gateway, usage=lambda rid: {"request_id": rid, "cost_usd": "0", "row_count": 1}):
        with mock.patch.object(urllib.request, "urlopen", gateway):
            return otari_drill.guardrail_drill(usage=usage)

    def test_the_benign_headline_is_served_evaluated_and_the_malicious_one_is_refused_before_any_provider(self):
        gateway = Gateway()
        r = self.drill(gateway)
        self.assertTrue(r["pass"], r.get("error"))
        self.assertEqual((r["drill"], r["profile"], r["mode"], r["role"]), ("guardrail", PROFILE, "block", "narrator"))
        self.assertEqual(r["benign"]["verdict"], {"profile": PROFILE, "mode": "block", "valid": True, "score": 0.0, "status": "evaluated"})
        self.assertEqual(r["benign"]["receipt"]["guardrail_result"], {PROFILE: "pass"})
        inj = r["injection"]
        self.assertEqual((inj["error"]["status"], inj["error"]["code"], inj["error"]["request_id"]), (403, "guardrail_violation", "req-2"))
        self.assertEqual(inj["flagged"], [{"profile": PROFILE, "explanation": None, "score": 0.985}])
        self.assertEqual(inj["cost_usd"], "0")
        self.assertEqual(inj["receipt"]["refusal"]["code"], "guardrail_violation")
        self.assertNotIn("provider", inj["receipt"], "the refusal receipt names no provider: none was called")

    def test_both_requests_carry_the_block_entry_and_the_drill_end_user(self):
        gateway = Gateway()
        self.drill(gateway)
        self.assertEqual(len(gateway.requests), 2)
        for payload in gateway.requests:
            self.assertEqual(payload["guardrails"], [{"profile": PROFILE, "mode": "block", "on_unavailable": "block"}])
            self.assertEqual(payload.get("user"), "vd-drill-guardrail")
        self.assertIn(otari_drill.BENIGN_HEADLINE, gateway.requests[0]["messages"][-1]["content"])
        self.assertIn(otari_drill.MALICIOUS_HEADLINE, gateway.requests[1]["messages"][-1]["content"])

    def test_a_benign_headline_that_is_refused_fails_the_drill_and_says_so(self):
        r = self.drill(Gateway(flags=lambda text: True))
        self.assertFalse(r["pass"])
        self.assertIn("the benign headline was not served", r["error"])
        self.assertIn("guardrail_violation", r["error"])
        self.assertNotIn("receipt", r["injection"], "the malicious one was never sent")

    def test_a_malicious_headline_that_is_served_fails_the_drill(self):
        r = self.drill(Gateway(flags=lambda text: False))
        self.assertFalse(r["pass"])
        self.assertIn("did not block it", r["error"])

    def test_an_unreachable_service_fails_closed_and_the_drill_points_at_the_deployment(self):
        r = self.drill(Gateway(unreachable=True))
        self.assertFalse(r["pass"])
        self.assertIn("HTTP 502", r["error"])
        self.assertIn("is the service deployed and the mandate created", r["error"])

    def test_a_benign_reply_whose_header_is_unavailable_or_null_is_not_a_pass(self):
        for verdict in ([{"profile": PROFILE, "mode": "block", "valid": None, "score": None, "status": "unavailable", "reason": "not_configured"}],
                        [{"profile": "prompt-injection", "mode": "block", "valid": True, "score": 0, "status": "evaluated"}], []):
            with self.subTest(verdict=verdict):
                r = self.drill(Gateway(verdict=verdict))
                self.assertFalse(r["pass"])
                self.assertIn("did not say evaluated and valid true", r["error"])

    def test_a_refused_request_that_shows_a_cost_means_a_provider_was_called_and_fails(self):
        r = self.drill(Gateway(), usage=lambda rid: {"request_id": rid, "cost_usd": "0.000123", "row_count": 1})
        self.assertFalse(r["pass"])
        self.assertEqual(r["injection"]["cost_usd"], "0.000123")

    def test_a_missing_usage_row_is_told_not_confirmed_and_the_403_still_passes(self):
        r = self.drill(Gateway(), usage=lambda rid: None)
        self.assertTrue(r["pass"])
        self.assertEqual(r["injection"]["cost_usd"], "not confirmed (no usage row)")

    def test_the_summary_for_a_screen_names_each_input_and_what_otari_did(self):
        lines = otari_drill.guardrail_summary_lines(self.drill(Gateway()))
        text = "\n".join(lines)
        self.assertTrue(lines[0].startswith("drill guardrail: PASS"))
        self.assertIn("BENIGN", text)
        self.assertIn("SERVED    valid true, score 0.0, status evaluated", text)
        self.assertIn("INJECTION", text)
        self.assertIn("REFUSED   HTTP 403 guardrail_violation, score 0.985, request req-2", text)
        self.assertIn("no provider call: cost on the usage row of the refused request: 0", text)
        self.assertIn("not sealed in the ledger", text)
        self.assertNotIn("—", text)
        failed = "\n".join(otari_drill.guardrail_summary_lines(self.drill(Gateway(unreachable=True))))
        self.assertIn("drill guardrail: FAIL", failed)
        self.assertIn("reason:", failed)

    def test_guardrail_is_a_choice_of_the_command_and_not_part_of_all(self):
        chosen = []
        with mock.patch.object(cli, "cmd_otari_drill", lambda opts: chosen.append(opts.which) or 0), mock.patch("sys.argv", ["verdict_desk", "otari-drill", "guardrail"]):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(chosen, ["guardrail"])
        calls = []
        with mock.patch.object(otari_drill, "guardrail_drill", lambda: calls.append(1) or {"pass": True}), \
             mock.patch.object(otari_drill, "fallback_drill", lambda *a, **k: {"pass": True}), mock.patch.object(otari_drill, "budget_drill", lambda *a, **k: {"pass": True}), \
             mock.patch.object(otari_drill, "role_fallback_drill", lambda *a, **k: {"pass": True}), mock.patch.object(otari_drill, "mcp_drill", lambda *a, **k: {"pass": True}), \
             mock.patch.object(otari_drill, "end_users_check", lambda *a, **k: {"pass": True}):
            otari_drill.run("all", [], Path(tempfile.mkdtemp()) / "e.jsonl")
            self.assertEqual(calls, [])
            otari_drill.run("guardrail", [], Path(tempfile.mkdtemp()) / "e.jsonl")
        self.assertEqual(calls, [1])

    def test_the_command_prints_the_summary_and_appends_one_evidence_line(self):
        evidence = Path(tempfile.mkdtemp()) / "otari-drills.jsonl"
        opts = argparse.Namespace(which="guardrail", ledger=Path(tempfile.mkdtemp()) / "none.jsonl", evidence=evidence)
        out = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", Gateway()), mock.patch.object(llm, "usage_for", lambda rid, **k: {"request_id": rid, "cost_usd": "0", "row_count": 1}), contextlib.redirect_stdout(out):
            code = cli.cmd_otari_drill(opts)
        self.assertEqual(code, 0)
        self.assertIn("drill guardrail: PASS", out.getvalue())
        self.assertIn("REFUSED   HTTP 403 guardrail_violation", out.getvalue())
        lines = evidence.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual((record["schema"], record["drill"], record["pass"]), ("otari-drill/1", "guardrail", True))
        self.assertNotIn(KEY, lines[0])

    def test_the_command_exits_1_when_the_drill_fails(self):
        evidence = Path(tempfile.mkdtemp()) / "otari-drills.jsonl"
        opts = argparse.Namespace(which="guardrail", ledger=Path(tempfile.mkdtemp()) / "none.jsonl", evidence=evidence)
        with mock.patch.object(urllib.request, "urlopen", Gateway(unreachable=True)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_otari_drill(opts), 1)

    def test_the_command_refuses_without_a_key(self):
        opts = argparse.Namespace(which="guardrail", ledger=Path("none.jsonl"), evidence=None)
        with mock.patch.dict(os.environ, {"OTARI_API_KEY": ""}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_otari_drill(opts), 2)


class AuditDrillNoteTest(unittest.TestCase):
    def test_a_passing_guardrail_drill_is_named_in_row_5_but_does_not_make_it_pass(self):
        drill = {"schema": "otari-drill/1", "drill": "guardrail", "pass": True, "profile": PROFILE,
                 "benign": {"receipt": {"request_id": "b-1"}}, "injection": {"error": {"request_id": "i-1", "status": 403, "code": "guardrail_violation"}, "flagged": [{"score": 0.985}]}}
        row = rep.audit([], [drill])[4]
        self.assertEqual(row["status"], "NOT PROVEN")
        self.assertEqual(row["drill_proof"], ["b-1", "i-1"])
        self.assertIn("drill", row["note"])
        self.assertIn("not sealed, not counted", row["note"])
        failed = rep.audit([], [{**drill, "pass": False}])[4]
        self.assertEqual(failed["drill_proof"], [])


if __name__ == "__main__":
    unittest.main()
