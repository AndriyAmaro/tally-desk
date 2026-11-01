import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import __main__ as cli
from verdict_desk import dossier, evidence, livestats, llm

HERE = Path(__file__).parent
ROOT = HERE.parent
NODE = shutil.which("node")

_no_llm = mock.patch.object(llm, "available", return_value=False)


def setUpModule():
    _no_llm.start()  # a key in the environment must not turn these tests into calls to the real API


def tearDownModule():
    _no_llm.stop()


def fake(seq, symbol, collected_at, llm=None, action="FLAT"):
    """A dossier-shaped dict with only what the counts read (the hash is not needed here)."""
    d = {"seq": seq, "hash": f"{seq:064x}", "evidence": {"symbol": symbol, "collected_at": collected_at}, "verdict": {"action": action}}
    if llm is not None:
        d["llm"] = llm
    return d


def layer(bull, bear, model="m"):
    def side(a, rejected_reasons=(), error=None):
        if error:
            return {"accepted": [], "rejected": [], "error": error}
        return {"accepted": [{"claim": "x", "cites": ["E1"]}] * a, "rejected": [{"claim": "y", "rejected_because": why} for why in rejected_reasons]}

    return {"model": model, "evidence_index": {}, "bull": side(*bull) if isinstance(bull, tuple) else side(0, error=bull), "bear": side(*bear) if isinstance(bear, tuple) else side(0, error=bear)}


def sample_ledger():
    return [
        fake(1, "BTCUSDT", "2026-10-17T09:26:12+00:00"),  # before the LLM era
        fake(45, "BTCUSDT", "2026-10-17T10:00:00+00:00", layer((0, ["no citation"]), (0, ()))),
        fake(46, "BTCUSDT", "2026-10-17T12:30:00+00:00", layer((3, ["number not in cited evidence: 2384.65"]), (2, ["number not in cited evidence: 1.5", "unknown evidence id"])), "LONG"),
        fake(47, "ETHUSDT", "2026-10-17T12:30:20+00:00", layer((4, ()), (4, ()))),
        fake(48, "BTCUSDT", "2026-10-17T13:20:00+00:00"),  # no LLM layer at all
        fake(49, "BTCUSDT", "2026-10-17T13:45:00+00:00", layer("HTTP 429", (1, ())), "SHORT"),
    ]


class CadenceTest(unittest.TestCase):
    def test_window_clock_hours_and_gaps(self):
        c = livestats.cadence(sample_ledger())
        self.assertEqual(c["total"], 6)
        self.assertEqual(c["per_symbol"], {"BTCUSDT": 5, "ETHUSDT": 1})
        self.assertEqual(c["first"], "2026-10-17T09:26:12.000Z")
        self.assertEqual(c["last"], "2026-10-17T13:45:00.000Z")
        self.assertEqual(c["window_hours"], 4.3)  # 4 h 18 min 48 s
        self.assertEqual(c["window_days"], 0.2)  # 0.179 days, rounded to one decimal
        # BTC gaps in minutes: 33.8, 150, 50, 25 -> only the 150 min one is over 90
        self.assertEqual(c["longest_gap_minutes"], 150)
        self.assertEqual(c["longest_gap_symbol"], "BTCUSDT")
        self.assertEqual(c["gaps_over_90_minutes"], 1)
        # hours 09, 10, 12, 13 have a record; 11 does not
        self.assertEqual((c["clock_hours_with_records"], c["clock_hours_in_window"], c["coverage_pct"]), (4, 5, 80.0))

    def test_empty_ledger_has_no_window(self):
        c = livestats.cadence([])
        self.assertEqual((c["first"], c["window_hours"], c["window_days"], c["coverage_pct"]), (None, 0.0, 0.0, 0.0))

    def test_unparseable_timestamps_are_skipped_not_guessed(self):
        c = livestats.cadence([fake(1, "BTCUSDT", "not a date"), fake(2, "BTCUSDT", "2026-10-17T09:00:00+00:00")])
        self.assertEqual((c["total"], c["first"], c["window_hours"]), (2, "2026-10-17T09:00:00.000Z", 0.0))


class LlmEraTest(unittest.TestCase):
    def test_counts_start_at_the_first_llm_seq(self):
        e = livestats.llm_era(sample_ledger())
        self.assertEqual((e["first_seq"], e["dossiers"], e["dossiers_with_analysts"]), (46, 4, 3))
        self.assertEqual(e["dossiers_without_llm"], [48])
        # seq 46: bull 3+1r, bear 2+2r; seq 47: 4+4; seq 49: bull call failed, bear 1
        self.assertEqual(e["sides"]["bull"], {"accepted": 7, "rejected": 1, "errors": 1})
        self.assertEqual(e["sides"]["bear"], {"accepted": 7, "rejected": 2, "errors": 0})
        self.assertEqual((e["accepted"], e["rejected"], e["accepted_pct"]), (14, 3, 82.4))
        self.assertEqual(e["rejection_reasons"], {"number not in cited evidence": 2, "unknown evidence id": 1})
        self.assertEqual(e["models"], ["m"])

    def test_a_different_first_seq_changes_only_the_window(self):
        e = livestats.llm_era(sample_ledger(), first_seq=45)
        self.assertEqual((e["dossiers"], e["rejected"]), (5, 4))

    def test_a_last_seq_bounds_the_range(self):
        e = livestats.llm_era(sample_ledger(), first_seq=45, last_seq=46)
        self.assertEqual((e["dossiers"], e["accepted"], e["rejected"], e["last_seq"]), (2, 5, 4, 46))

    def test_no_claims_means_no_percentage(self):
        self.assertIsNone(livestats.llm_era([fake(46, "BTCUSDT", "2026-10-17T12:30:00+00:00")])["accepted_pct"])


class ReportTest(unittest.TestCase):
    def test_report_prints_the_numbers_it_counted(self):
        text = livestats.format_report(livestats.summarize(sample_ledger()), {"verdict-desk-bot": [1, 45, 46, 47], "Someone": [48, 49]})
        for expected in ("Dossiers: 6", "= 4.3 h", "4 of 5 (80.0%)", "claims accepted by the citation validator: 14, rejected: 3 (82.4% accepted)", "without any LLM layer (seq): 48", "verdict-desk-bot 4 (seq 1, 45-47), Someone 2 (seq 48-49)"):
            self.assertIn(expected, text)


class SeqRangesTest(unittest.TestCase):
    def test_consecutive_seqs_collapse_to_ranges(self):
        self.assertEqual(livestats.seq_ranges([54, 55, 56, 60, 62, 63]), "54-56, 60, 62-63")
        self.assertEqual(livestats.seq_ranges([]), "")
        self.assertEqual(livestats.seq_ranges([7]), "7")


class CommitAuthorsTest(unittest.TestCase):
    def test_outside_git_there_is_no_author_line(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        dossier.append(packet(0.004), path)
        self.assertIsNone(livestats.commit_authors(path, dossier.read_ledger(path)))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_lines_are_attributed_to_the_commit_author(self):
        repo = Path(tempfile.mkdtemp())
        path = repo / "ledger.jsonl"
        quiet = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

        def git(*args, author):
            # Identity through the environment: it wins over any GIT_AUTHOR_* the machine already exports.
            env = {**quiet, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@example.com", "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author}@example.com"}
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

        git("init", "-q", author="x")
        dossier.append(packet(0.004), path)
        git("add", "ledger.jsonl", author="bot")
        git("commit", "-q", "-m", "first", author="bot")
        dossier.append(packet(-0.004), path)
        dossier.append(packet(0.0), path)
        git("add", "ledger.jsonl", author="Person")
        git("commit", "-q", "-m", "more", author="Person")
        self.assertEqual(livestats.commit_authors(path, dossier.read_ledger(path)), {"Person": [2, 3], "bot": [1]})


class StatsCommandTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        for trend in (0.004, -0.004):
            dossier.append(packet(trend), self.path)

    def run_cmd(self, **kw):
        opts = argparse.Namespace(ledger=self.path, first_llm_seq=46, last_llm_seq=None, json=kw.get("json", False), git=False)
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.cmd_stats(opts)
        return code, out.getvalue(), err.getvalue()

    def test_prints_a_report_for_a_verified_chain(self):
        code, out, _ = self.run_cmd()
        self.assertEqual(code, 0)
        self.assertIn("Dossiers: 2", out)

    def test_json_output_parses(self):
        code, out, _ = self.run_cmd(json=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["cadence"]["total"], 2)

    def test_refuses_to_count_a_tampered_chain(self):
        lines = self.path.read_text().splitlines()
        row = json.loads(lines[0])
        row["verdict"]["action"] = "SHORT" if row["verdict"]["action"] != "SHORT" else "LONG"
        lines[0] = json.dumps(row)
        self.path.write_text("\n".join(lines) + "\n")
        code, out, err = self.run_cmd()
        self.assertEqual(code, 1)
        self.assertIn("Refusing to count a broken chain", err)
        self.assertEqual(out, "")

    def test_empty_ledger_is_an_error_not_a_zero_report(self):
        self.path.unlink()
        code, _, err = self.run_cmd()
        self.assertEqual(code, 1)
        self.assertIn("empty", err)


class PublishedHeadTest(unittest.TestCase):
    def test_publish_head_writes_the_verified_head_next_to_the_ledger(self):
        path = Path(tempfile.mkdtemp()) / "ledger" / "ledger.jsonl"
        dossier.append(packet(0.004), path)
        dossier.append(packet(-0.004), path)
        cli.publish_head(path)
        written = json.loads((path.parent / "head.json").read_text())
        self.assertEqual(written, dossier.head(dossier.read_ledger(path)))
        self.assertEqual(written["seq"], 2)

    def test_the_whole_head_dossier_is_published_next_to_the_head_for_the_cre_replay(self):
        path = Path(tempfile.mkdtemp()) / "ledger" / "ledger.jsonl"
        dossier.append(packet(0.004), path)
        dossier.append(packet(-0.004), path)
        cli.publish_head(path)
        published = json.loads((path.parent / "head-dossier.json").read_text())
        self.assertEqual(published, dossier.read_ledger(path)[-1])
        self.assertEqual(published["hash"], json.loads((path.parent / "head.json").read_text())["hash"])

    def test_the_head_command_also_writes_the_head_dossier_beside_its_output(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        dossier.append(packet(0.004), path)
        out = path.parent / "pub" / "head.json"
        argv = ["verdict_desk", "--ledger", str(path), "head", "--out", str(out)]
        with mock.patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads((out.parent / "head-dossier.json").read_text())["seq"], 1)

    def test_publish_head_leaves_the_old_head_alone_when_the_chain_is_broken(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        dossier.append(packet(0.004), path)
        cli.publish_head(path)
        before = (path.parent / "head.json").read_text()
        row = json.loads(path.read_text())
        row["verdict"]["edge"] = 99
        path.write_text(json.dumps(row) + "\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            cli.publish_head(path)
        self.assertEqual((path.parent / "head.json").read_text(), before)
        self.assertIn("head.json not refreshed", err.getvalue())

    def test_deciding_into_the_default_ledger_refreshes_head_json(self):
        # The 2026-10-20 incident: dossiers 54 to 68 were appended by hand and head.json stayed at seq 53.
        work = Path(tempfile.mkdtemp())
        opts = argparse.Namespace(symbols=["BTCUSDT"], interval="1h", no_news=True, feed=None, default_feeds=False, ledger=cli.DEFAULT_LEDGER)
        previous = os.getcwd()
        os.chdir(work)
        try:
            with mock.patch.dict(os.environ, {"VERDICT_DESK_APPEND_PUBLIC": "1"}), mock.patch.object(evidence, "collect", lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_decide(opts), 0)
                    self.assertEqual(cli.cmd_decide_v2(opts), 0)
            head = json.loads((work / "ledger" / "head.json").read_text())
            ledger = dossier.read_ledger(work / "ledger" / "ledger.jsonl")
        finally:
            os.chdir(previous)
        self.assertEqual(len(ledger), 2)
        self.assertEqual(head, {"seq": 2, "hash": ledger[-1]["hash"], "symbol": "BTCUSDT"})

    def decide_into_default(self, env):
        work = Path(tempfile.mkdtemp())
        opts = argparse.Namespace(symbols=["BTCUSDT"], interval="1h", no_news=True, feed=None, default_feeds=False, ledger=cli.DEFAULT_LEDGER)
        previous = os.getcwd()
        os.chdir(work)
        try:
            clean = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "VERDICT_DESK_APPEND_PUBLIC")}
            with mock.patch.dict(os.environ, {**clean, **env}, clear=True), mock.patch.object(evidence, "collect", lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    code = cli.cmd_decide(opts)
            return code, (work / "ledger" / "ledger.jsonl").exists(), err.getvalue()
        finally:
            os.chdir(previous)

    def test_the_public_ledger_is_not_appended_outside_the_workflow(self):
        # Demo scripts did this on 2026-10-20: dossiers 54 to 68 are demo and test runs inside the public chain.
        code, wrote, err = self.decide_into_default({})
        self.assertEqual((code, wrote), (2, False))
        self.assertIn("Refusing to append to the public ledger", err)

    def test_the_workflow_and_an_explicit_override_may_append(self):
        for env in ({"GITHUB_ACTIONS": "true"}, {"VERDICT_DESK_APPEND_PUBLIC": "1"}):
            with self.subTest(env=env):
                code, wrote, _ = self.decide_into_default(env)
                self.assertEqual((code, wrote), (0, True))

    def test_deciding_into_another_ledger_does_not_write_a_head(self):
        path = Path(tempfile.mkdtemp()) / "other" / "ledger.jsonl"
        opts = argparse.Namespace(symbols=["BTCUSDT"], interval="1h", no_news=True, feed=None, default_feeds=False, ledger=path)
        with mock.patch.object(evidence, "collect", lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_decide(opts)
        self.assertFalse((path.parent / "head.json").exists())


SHIPPED_LEDGER = ROOT / "ledger" / "ledger.jsonl"
SHIPPED_HEAD = ROOT / "ledger" / "head.json"


@unittest.skipUnless(SHIPPED_LEDGER.exists() and SHIPPED_HEAD.exists(), "no shipped ledger")
class ShippedLedgerTest(unittest.TestCase):
    def test_published_head_is_the_head_of_the_published_ledger(self):
        """ledger/head.json is what the CRE workflow anchors: it must never lag the chain."""
        head = json.loads(SHIPPED_HEAD.read_text())
        self.assertEqual(head, dossier.head(dossier.read_ledger(SHIPPED_LEDGER)))


@unittest.skipUnless(NODE, "node not installed")
class StatsCrossLanguageTest(unittest.TestCase):
    """The dashboard counts in the browser with web/stats.js; the README quotes verdict_desk/livestats.py."""

    def node(self, path):
        out = subprocess.run([NODE, str(HERE / "node_livestats.js"), str(path)], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def check(self, ledger_path):
        ledger = dossier.read_ledger(ledger_path)
        js = self.node(ledger_path)
        self.assertEqual(js["cadence"], livestats.cadence(ledger))
        self.assertEqual(js["llmEra"], livestats.llm_era(ledger))
        self.assertEqual(js["llmEarly"], livestats.llm_era(ledger, 5, 45))

    def test_synthetic_ledger(self):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        path.write_text("".join(json.dumps(d) + "\n" for d in sample_ledger()))
        self.check(path)

    @unittest.skipUnless(SHIPPED_LEDGER.exists(), "no shipped ledger")
    def test_shipped_ledger(self):
        self.check(SHIPPED_LEDGER)


if __name__ == "__main__":
    unittest.main()
