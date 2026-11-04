"""The bring-your-own-agent API (verdict_desk/seal.py): sessions, the generic closure, verify and replay of a `receipts/1` record.
Every call goes through a fake gateway or a fake transport: no network, no key, no spend."""

import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

from verdict_desk import __main__ as cli
from verdict_desk import dossier, llm, seal

ROOT = Path(__file__).resolve().parents[1]
ROLES = ROOT / "examples" / "otari_roles.json"
FIXTURE = ROOT / "tests" / "fixtures" / "receipts_ledger.jsonl"
NODE = shutil.which("node")


def answer(payload):
    return "an answer", [{"id": "t1", "type": "web_fetch", "status": "completed", "url": "https://example.test/", "sources": []}]


def refusal(payload):
    headers = Message()
    headers["Otari-Error-Code"], headers["Otari-Budget-Scope"] = "budget_exceeded", "user"
    raise urllib.error.HTTPError("https://dry-run.invalid/api/v1/chat/completions", 403, "Forbidden", headers, io.BytesIO(b"{}"))


class SealTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ledger = self.dir / "mine.jsonl"
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop("OTARI_API_KEY", None)
        self.addCleanup(env.stop)

    def run_session(self, input=None, transport=answer, ledger=None, mutate=None):
        with seal.Session("test-agent", input or {"q": 1}, ledger or self.ledger, config=ROLES, transport=transport) as s:
            try:
                s.output("text", s.ask("reader", "sys", "user", label="read", max_tokens=50))
            except llm.LLMError as err:
                s.output("error", str(err))
            if mutate:
                mutate(s)
            return s.seal()

    def test_a_session_seals_a_record_that_verify_and_replay_accept(self):
        first, second = self.run_session(), self.run_session({"q": 2})
        self.assertTrue(first.sealed and second.sealed, first.reasons)
        ledger = dossier.read_ledger(self.ledger)
        self.assertEqual([d["seq"] for d in ledger], [1, 2])
        self.assertEqual(ledger[1]["prev_hash"], ledger[0]["hash"])
        self.assertEqual(dossier.verify(ledger), [])
        record = ledger[0]
        self.assertEqual((record["kind"], record["workload"], record["input_sha256"]), ("receipts/1", {"name": "test-agent", "mode": "dry-run"}, dossier.digest({"q": 1})))
        self.assertEqual(record["outputs"], {"text": "an answer"})
        self.assertEqual([(c["label"], c["role"], c["pool"]) for c in record["calls"]], [("read", "reader", "tools")])
        self.assertEqual(record["calls"][0]["receipt"]["request_id_source"], "otari")
        self.assertEqual(record["otari"]["base_url"], seal.DRY_BASE)
        self.assertEqual(dossier.head(ledger)["symbol"], "test-agent")
        result = dossier.replay(record)
        self.assertTrue(result["matches"])
        self.assertEqual((result["replayed"], result["note"]), (None, "no rules to re-derive (receipts record)"))

    def test_a_dry_run_says_so_in_the_record_and_the_closure(self):
        closure = self.run_session().closure
        self.assertEqual(closure["outcome"], "warning")
        self.assertEqual([c["name"] for c in closure["checks"] if not c["pass"]], ["live_calls"])

    def test_a_refused_call_is_a_recorded_failure_and_the_record_is_sealed(self):
        result = self.run_session(transport=refusal)
        self.assertTrue(result.sealed, result.reasons)
        receipt = result.record["calls"][0]["receipt"]
        self.assertEqual((receipt["refusal"]["code"], receipt["refusal"]["status"]), ("budget_exceeded", 403))
        self.assertIn("error", result.record["outputs"])

    def test_a_hard_check_failure_appends_nothing_and_returns_the_reason(self):
        def change_input(s):
            s.input = {"q": "changed after the session froze it"}
        result = self.run_session(mutate=change_input)
        self.assertFalse(result.sealed)
        self.assertIsNone(result.record)
        self.assertRegex(result.reasons[0], r"^input_frozen: ")
        self.assertFalse(self.ledger.exists())

    def test_an_unaccounted_call_is_refused(self):
        def lose_receipt(s):
            s.calls.append({"label": "ghost", "role": "reader", "pool": "tools", "receipt": None})
        result = self.run_session(mutate=lose_receipt)
        self.assertFalse(result.sealed)
        self.assertTrue(any(r.startswith("calls_accounted") and "ghost" in r for r in result.reasons))

    def test_the_public_ledger_and_foreign_ledgers_are_refused(self):
        refused = self.run_session(ledger=seal.PUBLIC_LEDGER)
        self.assertFalse(refused.sealed)
        self.assertIn("public trading ledger", refused.reasons[0])
        trading = self.dir / "trading.jsonl"
        shutil.copy(ROOT / "ledger" / "ledger.jsonl", trading)
        before = trading.read_bytes()
        refused = self.run_session(ledger=trading)
        self.assertFalse(refused.sealed)
        self.assertEqual(trading.read_bytes(), before)
        broken = self.dir / "broken.jsonl"
        lines = FIXTURE.read_text(encoding="utf-8").splitlines()
        broken.write_text("\n".join([lines[0].replace("fixture-agent", "other-agent")] + lines[1:]) + "\n", encoding="utf-8")
        self.assertIn("does not verify", self.run_session(ledger=broken).reasons[0])

    def test_no_key_and_no_transport_sends_nothing_and_says_why(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("a request was sent")):
            with self.assertRaisesRegex(llm.LLMError, "OTARI_API_KEY is not set"):
                with seal.Session("test-agent", {}, self.ledger, config=ROLES):
                    pass
        self.assertIsNone(seal._busy["session"])

    def test_the_dry_run_leaves_the_process_as_it_found_it(self):
        saved = (llm.OTARI_CONFIG, urllib.request.urlopen)
        self.run_session()
        self.assertEqual((llm.OTARI_CONFIG, urllib.request.urlopen), saved)
        self.assertNotIn("OTARI_API_KEY", os.environ)
        self.assertNotIn("OTARI_BASE_URL", os.environ)
        self.assertIsNone(seal._busy["session"])

    def test_misuse_is_refused_early(self):
        with self.assertRaises(ValueError):
            seal.Session("", {}, self.ledger)
        with self.assertRaises(ValueError):
            seal.Session("x", {"n": float("nan")}, self.ledger)
        with seal.Session("x", {}, self.ledger, config=ROLES, transport=answer) as s:
            with self.assertRaisesRegex(llm.LLMError, "not in the role config"):
                s.ask("judge", "s", "u")
            with self.assertRaises(RuntimeError):
                seal.Session("y", {}, self.ledger, config=ROLES, transport=answer).__enter__()
            s.seal()
            with self.assertRaisesRegex(RuntimeError, "already sealed"):
                s.seal()

    def test_the_cycle_cap_stops_a_call_and_the_refusal_is_sealed(self):
        cfg = json.loads(ROLES.read_text(encoding="utf-8"))
        cfg["tool_cap_usd"] = "0.01"
        capped = self.dir / "capped.json"
        capped.write_text(json.dumps(cfg), encoding="utf-8")
        with seal.Session("capped", {}, self.ledger, config=capped, transport=lambda payload: "x") as s:
            s.ask("reader", "s", "u")
            llm._cycle["tspent"] += llm.decimal.Decimal("0.5")  # the first call settled over the cap
            with self.assertRaises(llm.OtariError) as raised:
                s.ask("reader", "s", "u")
            self.assertEqual(raised.exception.code, "cycle_cap_exceeded")
            result = s.seal()
        self.assertTrue(result.sealed, result.reasons)
        self.assertEqual([("refusal" in c["receipt"]) for c in result.record["calls"]], [False, True])


class TamperTest(unittest.TestCase):
    def setUp(self):
        self.records = dossier.read_ledger(FIXTURE)

    def sealed_again(self, record):
        body = {k: v for k, v in record.items() if k != "hash"}
        return {**body, "hash": dossier.digest(body)}

    def test_the_fixture_ledger_verifies_and_replays(self):
        self.assertEqual(dossier.verify(self.records), [])
        self.assertTrue(all(dossier.replay(d)["matches"] for d in self.records))

    def test_an_edited_record_fails_verify_and_replay(self):
        edited = copy.deepcopy(self.records)
        edited[1]["outputs"]["error"] = "nothing happened"
        self.assertIn("seq 2: content was altered after it was recorded", dossier.verify(edited))
        replay = dossier.replay(edited[1])
        self.assertFalse(replay["matches"])
        self.assertFalse(replay["hash_ok"])

    def test_a_lie_with_the_hash_recomputed_still_fails_replay(self):
        for name, change in (("request id source", lambda d: d["calls"][0]["receipt"].__setitem__("request_id_source", "client")),
                             ("receipt removed", lambda d: d["calls"][0].__setitem__("receipt", None)),
                             ("schema", lambda d: d["calls"][0]["receipt"].__setitem__("schema", "other/1")),
                             ("input", lambda d: d.__setitem__("input", {"url": "https://example.test/else"})),
                             ("cost over the cap", lambda d: d["calls"][0]["receipt"].__setitem__("cost_usd", "9.0")),
                             ("closure edited", lambda d: d["closure"]["checks"][-1].__setitem__("pass", True)),
                             ("closure removed", lambda d: d.pop("closure"))):
            lie = copy.deepcopy(self.records[0])
            change(lie)
            result = dossier.replay(self.sealed_again(lie))
            self.assertTrue(result["hash_ok"], name)
            self.assertFalse(result["matches"], name)

    def test_cmd_replay_and_verify_on_a_receipts_ledger(self):
        out = io.StringIO()
        opts = mock.Mock(ledger=FIXTURE, seq=None)
        with mock.patch("sys.stdout", out):
            self.assertEqual(cli.cmd_verify(opts), 0)
            self.assertEqual(cli.cmd_replay(opts), 0)
        text = out.getvalue()
        self.assertIn("Chain intact: 3 dossiers", text)
        self.assertEqual(text.count("replay MATCHES: no rules to re-derive (receipts record)"), 3)
        with self.assertRaisesRegex(ValueError, "receipts record"):
            cli.export_pack(self.records, 1)


@unittest.skipUnless(NODE, "node is not installed")
class JsParityTest(unittest.TestCase):
    """web/receipts.js must say the same as seal.py about the same record, check by check."""

    SCRIPT = """
const V = require(process.argv[2]), R = require(process.argv[3]);
const rows = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const digestOf = (v) => V.webSha256(V.canonical(v));
(async () => { const out = []; for (const d of rows) { const c = await R.check(d, digestOf); out.push(c.checks.map((x) => [x.name, x.pass])); } process.stdout.write(JSON.stringify(out)); })();
"""

    def test_python_and_javascript_agree_on_good_and_bad_records(self):
        base = dossier.read_ledger(FIXTURE)[0]
        changes = (lambda d: d["calls"][0]["receipt"].__setitem__("request_id_source", "client"), lambda d: d["calls"][0].__setitem__("receipt", None),
                   lambda d: d["calls"][0]["receipt"].__setitem__("cost_usd", None), lambda d: d["calls"][0]["receipt"].__setitem__("cost_usd", "0.06"),
                   lambda d: d["calls"][0]["receipt"].__setitem__("schema", "x"), lambda d: d.__setitem__("input", {"other": 1}),
                   lambda d: d["workload"].__setitem__("mode", "live"), lambda d: d.__setitem__("calls", []),
                   lambda d: d["calls"][0].__setitem__("pool", "core"), lambda d: d["calls"][0]["receipt"].pop("tokens"))
        mutants = [base]
        for change in changes:
            m = copy.deepcopy(base)
            change(m)
            mutants.append(m)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(self.SCRIPT)
        try:
            run = subprocess.run([NODE, fh.name, str(ROOT / "web" / "canonical.js"), str(ROOT / "web" / "receipts.js")], input=json.dumps(mutants), capture_output=True, text=True, timeout=60)
        finally:
            Path(fh.name).unlink(missing_ok=True)
        self.assertEqual(run.returncode, 0, run.stderr[:300])
        js = json.loads(run.stdout)
        for n, m in enumerate(mutants):
            py = [[c["name"], c["pass"]] for c in seal.evaluate({k: v for k, v in m.items() if k not in ("hash", "closure")}, m["closure"]["input_sha256"])["checks"]]
            self.assertEqual(js[n], py, f"mutant {n}")
        self.assertTrue(all(p for _, p in js[0][:4]), "the base record passes the hard checks")
        self.assertTrue(any(not p for n in range(1, len(mutants)) for _, p in js[n][:4]))


if __name__ == "__main__":
    unittest.main()
