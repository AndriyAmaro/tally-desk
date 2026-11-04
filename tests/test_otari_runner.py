"""The runner of regular cycles: schedule, budgets, the two-failures stop, the key file, the secret scan, what it commits. Git, the app and the
clock are all fakes: no process is started, no network is touched, nothing is pushed."""

import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import otari_runner as R  # noqa: E402

from tests.test_otari_report import synthetic  # noqa: E402

KEY = "fake-runner-key-0123456789"
START = datetime.datetime(2026, 11, 2, 9, 0, tzinfo=datetime.timezone.utc)


class Clock:
    def __init__(self):
        self.t = START

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += datetime.timedelta(seconds=seconds)


class World:
    """Answers the commands the runner issues, and remembers them."""

    def __init__(self, root, branch="otari", dirty="", fail_decide=False, fail_push=False, key_in_readme=False, fail_verify=False, replay_line=None, noisy_error=False, timeout_decide=False, refuse_decide=False):
        self.root, self.branch, self.dirty = Path(root), branch, dirty
        self.fail_decide, self.fail_push, self.key_in_readme, self.fail_verify, self.noisy_error = fail_decide, fail_push, key_in_readme, fail_verify, noisy_error
        self.replay_line, self.timeout_decide, self.refuse_decide = replay_line, timeout_decide, refuse_decide
        self.calls, self.envs = [], []
        (self.root / "ledger" / "timestamps").mkdir(parents=True, exist_ok=True)
        (self.root / "ledger" / "ledger.jsonl").write_text("")
        (self.root / "README.md").write_text("readme\n")

    def cp(self, rc=0, out="", err=""):
        return subprocess.CompletedProcess([], rc, out, err)

    def run(self, args, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        self.calls.append(list(args))
        self.envs.append(env)
        if args[0] == "git":
            sub = args[1] if args[1] != "-c" else args[args.index("commit") if "commit" in args else 1]
            if args[1:3] == ["rev-parse", "--abbrev-ref"]:
                return self.cp(out=self.branch + "\n")
            if args[1:3] == ["status", "--porcelain"]:
                return self.cp(out=self.dirty)
            if args[1:3] == ["diff", "--name-only"]:
                return self.cp(out="README.md\nledger/ledger.jsonl\n")
            if args[1] == "ls-files":
                return self.cp(out="ledger/timestamps/1.json\n")
            if args[1] == "grep":
                return self.cp(rc=1)
            if sub == "push":
                return self.cp(rc=1 if self.fail_push else 0, err="rejected" if self.fail_push else "")
            return self.cp()
        if "decide-v7" in args:
            if self.timeout_decide:
                raise subprocess.TimeoutExpired(args, timeout)
            if self.fail_decide:
                return self.cp(rc=1, err=(f"boom with {KEY} in it" if self.noisy_error else "boom"))
            path = self.root / "ledger" / "ledger.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if self.refuse_decide:  # the closure gate refused: a line in the refusal file, no dossier, the refusal exit code
                line = {"schema": "closure-refusal/1", "symbol": "BTCUSDT", "usd": "0.031000", "users": {"vd-bull": "0.000100", "vd-fetcher": "0.010100"},
                        "failed": [{"name": "receipts", "reason": "2 of 9 calls have no receipt with an Otari request id: bull, bear"}]}
                with (self.root / "ledger" / "closure-refusals.jsonl").open("a") as fh:
                    fh.write(json.dumps(line) + "\n")
                return self.cp(rc=R.closure.EXIT_CODE, err="closure gate REFUSED BTCUSDT")
            d = synthetic(len(rows) + 1)
            d["llm"]["otari"]["cycle"] = {"total_spent_usd": "0.036000"}
            with path.open("a") as fh:
                fh.write(json.dumps(d) + "\n")
            (self.root / "ledger" / "head.json").write_text(json.dumps({"seq": d["seq"]}))
            if self.key_in_readme:
                (self.root / "README.md").write_text("leak " + KEY + "\n")
            return self.cp(out="#1 ok\n")
        if "verify" in args:
            return self.cp(rc=1 if self.fail_verify else 0, out="Chain intact: n dossiers\n")
        if "replay" in args:
            return self.cp(out=self.replay_line or "#1 replay MATCHES: FLAT\n")
        return self.cp()  # ots, the README refresh


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.key = self.root / "key"
        self.key.write_text(KEY + "\n")
        self.clock = Clock()

    def make(self, **kw):
        self.world = World(self.root, **kw)
        r = R.Runner(self.root, self.key, run=self.world.run, now=self.clock.now, sleep=self.clock.advance)
        r.ots = lambda: "ots"  # the stamp client is a fake here
        return r

    def commands(self):
        return [" ".join(c[:4]) if c[0] == "git" else " ".join(c[1:4] if "verdict_desk" in c else c[:2]) for c in self.world.calls]

    def test_the_constants_are_the_approved_ones(self):
        self.assertEqual((R.INTERVAL_S, R.MAX_CYCLES_PER_DAY, R.TOTAL_CAP_USD, R.MAX_FAILURES), (10800, 8, Decimal("2.00"), 2))
        self.assertEqual(R.SYMBOLS, ("BTCUSDT", "ETHUSDT", "SOLUSDT"))

    def test_one_good_tick_runs_the_steps_in_order_and_commits_only_the_dossier_files_as_the_author_with_no_model_trailer(self):
        r = self.make()
        self.assertEqual(r.tick(), "ok")
        steps = self.commands()
        order = ["git rev-parse --abbrev-ref HEAD", "git status --porcelain", "-m verdict_desk decide-v7", "-m verdict_desk verify", "-m verdict_desk replay", "ots stamp"]
        positions = [next(i for i, s in enumerate(steps) if s.startswith(o)) for o in order]
        self.assertEqual(positions, sorted(positions))
        add = next(c for c in self.world.calls if c[:3] == ["git", "add", "--"])
        self.assertEqual(add[3:], R.PATHS)
        self.assertNotIn("logs", " ".join(add))
        commit = next(c for c in self.world.calls if "commit" in c)
        self.assertIn("user.name=AndriyAmaro", commit)
        self.assertIn("user.email=177476258+AndriyAmaro@users.noreply.github.com", commit)
        self.assertNotIn("Co-Authored-By", commit[-1], "a data commit of the app, not of a model")
        self.assertNotIn("Claude", commit[-1])
        self.assertTrue(commit[-1].startswith("otari: era dossier 1 (BTCUSDT), recorded by the runner"))
        push = next(c for c in self.world.calls if "push" in c)
        self.assertEqual(push[-3:], ["-q", "origin", "otari"][-3:])
        self.assertNotIn("--force", push)
        self.assertNotIn("-f", push)

    def test_a_closure_refusal_is_a_normal_outcome_logged_counted_published_and_not_a_failure(self):
        r = self.make(refuse_decide=True)
        self.assertEqual(r.tick(), "refused")
        state = r.load()
        self.assertEqual((state["failures"], state["cycles"], state["stopped"], state["total_usd"]), (0, 1, None, "0.031000"))
        entry = state["history"][-1]
        self.assertEqual((entry["seq"], entry["users"]["vd-fetcher"]), (None, "0.010100"))
        self.assertIn("receipts", entry["refused"])
        log = (self.root / "logs" / "otari-runner.log").read_text()
        self.assertIn("cycle refused by the closure gate (BTCUSDT, nothing appended)", log)
        self.assertIn("counted USD 0.031000", log)
        self.assertNotIn("cycle failed", log)
        steps = self.commands()
        self.assertFalse(any(s.startswith("-m verdict_desk verify") or s.startswith("-m verdict_desk replay") for s in steps), "no dossier, nothing to verify")
        add = next(c for c in self.world.calls if c[:3] == ["git", "add", "--"])
        self.assertEqual(add[3:], ["ledger/closure-refusals.jsonl"], "only the refusal file is published")
        commit = next(c for c in self.world.calls if "commit" in c)
        self.assertIn("closure gate refused the cycle of BTCUSDT", commit[-1])
        self.assertTrue(any("push" in c for c in self.world.calls))
        self.assertFalse(any(c[:3] == ["git", "checkout", "--"] for c in self.world.calls), "nothing to restore")

    def test_refusals_do_not_count_toward_the_two_failures_stop_but_their_money_counts_toward_the_caps(self):
        r = self.make(refuse_decide=True)
        for _ in range(3):
            self.assertEqual(r.tick(), "refused")
            self.clock.advance(R.INTERVAL_S)
        state = r.load()
        self.assertEqual((state["failures"], state["stopped"], state["total_usd"]), (0, None, "0.093000"))
        self.assertEqual(R.Runner.today(state, "2026-11-02")[0], 3, "each counts as a cycle of the day")
        self.world.refuse_decide = False
        self.assertEqual(r.tick(), "ok")

    def test_a_refusal_exit_with_no_refusal_line_is_a_failure_because_the_files_do_not_say_so(self):
        r = self.make(refuse_decide=True)
        original = r.refusals
        r.refusals = lambda: original()[:0] if self.world.calls and any("decide-v7" in c for c in self.world.calls) else original()
        self.assertEqual(r.tick(), "failed")

    def test_a_decide_that_outlives_its_timeout_is_a_failure_with_the_tree_restored_not_an_exception(self):
        r = self.make(timeout_decide=True)
        self.assertEqual(r.tick(), "failed")
        self.assertTrue(any(c[:3] == ["git", "checkout", "--"] for c in self.world.calls), "the tree is restored")
        self.assertTrue(any(c[:2] == ["git", "clean"] for c in self.world.calls))
        self.assertIn("timed out after", (self.root / "logs" / "otari-runner.log").read_text())
        self.assertFalse(any("commit" in c or "push" in c for c in self.world.calls))

    def test_a_failed_cycle_with_no_dossier_is_counted_at_its_two_caps_together(self):
        r = self.make(fail_decide=True)
        r.tick()
        state = r.load()
        self.assertEqual(state["total_usd"], str(R.FAILED_CYCLE_ESTIMATE_USD))
        self.assertEqual(R.FAILED_CYCLE_ESTIMATE_USD, Decimal("0.08"))
        self.assertTrue(state["history"][-1]["estimated"] and state["history"][-1]["failed"])
        self.assertEqual(state["cycles"], 0, "the symbol rotation does not move on a failure")
        self.assertIn("an estimate", (self.root / "logs" / "otari-runner.log").read_text())

    def test_a_failure_after_the_dossier_is_written_counts_what_that_dossier_spent_and_who_spent_it(self):
        r = self.make(fail_verify=True)
        r.tick()
        state = r.load()
        last = state["history"][-1]
        self.assertEqual(last["usd"], "0.036000")
        self.assertNotIn("estimated", last)
        self.assertTrue(last["users"], "the end users of the receipts are charged too")
        self.assertEqual(state["total_usd"], "0.036000")
        count, usd, users = R.Runner.today(state, START.strftime("%Y-%m-%d"))
        self.assertEqual((count, usd), (1, Decimal("0.036000")), "a failed cycle counts toward the day")

    def test_failed_cycles_spend_eats_the_total_cap_too(self):
        r = self.make(fail_decide=True)
        state = r.load()
        state["total_usd"] = "1.95"
        r.save(state)
        r.tick()
        self.assertEqual(r.load()["total_usd"], "2.03")
        self.clock.advance(R.RETRY_S)
        self.assertTrue(r.tick().startswith("stopped"))

    def test_the_key_goes_only_to_the_child_that_runs_the_cycle_and_is_never_logged(self):
        r = self.make()
        r.tick()
        with_key = [e for e in self.world.envs if e and e.get("OTARI_API_KEY") == KEY]
        self.assertEqual(len(with_key), 1)
        self.assertEqual(with_key[0]["VERDICT_DESK_APPEND_PUBLIC"], "1")
        self.assertNotIn(KEY, (self.root / "logs" / "otari-runner.log").read_text())
        self.assertNotIn(KEY, (self.root / "logs" / "otari-runner.state.json").read_text())

    def test_the_state_counts_the_cycle_its_cost_and_what_each_end_user_spent(self):
        r = self.make()
        r.tick()
        state = r.load()
        self.assertEqual((state["cycles"], state["failures"], state["total_usd"]), (1, 0, "0.036000"))
        entry = state["history"][0]
        self.assertEqual((entry["seq"], entry["symbol"], entry["day"]), (1, "BTCUSDT", "2026-11-02"))
        self.assertEqual(entry["users"]["vd-bull"], "0.000100")
        self.assertEqual(state["next_run"], (START + datetime.timedelta(hours=3)).isoformat())

    def test_it_waits_for_the_next_slot_and_takes_the_symbols_in_turn(self):
        r = self.make()
        self.assertEqual(r.tick(), "ok")
        self.assertEqual(r.tick(), "waiting")
        self.assertEqual(len([c for c in self.world.calls if "decide-v7" in c]), 1)
        symbols = ["BTCUSDT"]
        for _ in range(3):
            self.clock.advance(R.INTERVAL_S)
            self.assertEqual(r.tick(), "ok")
            symbols.append(next(c for c in reversed(self.world.calls) if "decide-v7" in c)[4])
        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSDT"])

    def test_a_second_failure_in_a_row_stops_it_and_a_success_in_between_resets_the_count(self):
        r = self.make(fail_decide=True)
        self.assertEqual(r.tick(), "failed")
        state = r.load()
        self.assertEqual(state["failures"], 1)
        self.assertEqual(state["next_run"], (START + datetime.timedelta(seconds=R.RETRY_S)).isoformat())
        self.assertTrue(any(c[:2] == ["git", "checkout"] for c in self.world.calls), "a failed cycle is thrown away")
        self.clock.advance(R.RETRY_S)
        result = r.tick()
        self.assertTrue(result.startswith("stopped"), result)
        self.assertIn("2 cycles failed in a row", r.load()["stopped"])
        before = len(self.world.calls)
        self.clock.advance(10 * 3600)
        self.assertTrue(r.tick().startswith("stopped"))
        self.assertEqual(len(self.world.calls), before, "a stopped runner runs nothing")
        r2 = self.make(fail_decide=True)  # a fresh root has a fresh state: one failure, then a success, then a failure is not two in a row
        self.root = Path(tempfile.mkdtemp())
        self.key = self.root / "key"
        self.key.write_text(KEY)
        r3 = self.make(fail_decide=True)
        r3.tick()
        self.world.fail_decide = False
        self.clock.advance(R.RETRY_S)
        self.assertEqual(r3.tick(), "ok")
        self.assertEqual(r3.load()["failures"], 0)
        self.world.fail_decide = True
        self.clock.advance(R.INTERVAL_S)
        self.assertEqual(r3.tick(), "failed")
        self.assertIsNone(r3.load()["stopped"])

    def test_verify_replay_and_push_failures_count_as_failures_and_nothing_is_left_half_done(self):
        for kw in ({"fail_verify": True}, {"replay_line": "#1 replay DIFFERS: LONG\n"}, {"fail_push": True}):
            self.root = Path(tempfile.mkdtemp())
            self.key = self.root / "key"
            self.key.write_text(KEY)
            r = self.make(**kw)
            self.assertEqual(r.tick(), "failed", kw)
            self.assertEqual(r.load()["cycles"], 0)
            self.assertTrue(any(c[:2] == ["git", "checkout"] for c in self.world.calls), kw)
            if "fail_push" not in kw:
                self.assertFalse(any("commit" in c for c in self.world.calls), kw)

    def test_a_missing_key_file_is_a_clean_stop_with_a_reason_and_no_command_run(self):
        self.key.unlink()
        r = self.make()
        result = r.tick()
        self.assertTrue(result.startswith("stopped") and "key file" in result and "recycled" in result, result)
        self.assertEqual(self.world.calls, [])
        self.assertIn("STOPPED: the key file", (self.root / "logs" / "otari-runner.log").read_text())
        self.assertEqual(R.main.__name__, "main")

    def test_an_unusable_key_file_also_stops_without_running_anything(self):
        self.key.write_text("two words\n")
        r = self.make()
        self.assertIn("does not hold a usable key", r.tick())
        self.assertEqual(self.world.calls, [])

    def test_the_wrong_branch_or_a_dirty_tree_stop_before_any_cycle(self):
        r = self.make(branch="main")
        self.assertIn("not otari", r.tick())
        self.assertFalse(any("decide-v7" in c for c in self.world.calls))
        self.root = Path(tempfile.mkdtemp())
        self.key = self.root / "key"
        self.key.write_text(KEY)
        r = self.make(dirty=" M README.md\n")
        self.assertIn("not clean", r.tick())
        self.assertFalse(any("decide-v7" in c for c in self.world.calls))

    def test_a_key_in_a_changed_file_discards_the_cycle_and_stops_before_any_commit(self):
        r = self.make(key_in_readme=True)
        result = r.tick()
        self.assertTrue(result.startswith("stopped") and "README.md" in result, result)
        self.assertFalse(any("commit" in c or "push" in c for c in self.world.calls))
        self.assertTrue(any(c[:2] == ["git", "checkout"] for c in self.world.calls))
        self.assertNotIn(KEY, (self.root / "logs" / "otari-runner.log").read_text())
        self.assertEqual(r.load()["total_usd"], "0.036000", "the discarded cycle spent that money")

    def test_what_a_failing_child_prints_is_sanitized_before_it_reaches_the_log(self):
        r = self.make(fail_decide=True, noisy_error=True)
        r.tick()
        log = (self.root / "logs" / "otari-runner.log").read_text()
        self.assertIn("boom with *** in it", log)
        self.assertNotIn(KEY, log)

    def test_at_most_eight_cycles_a_day_then_it_waits_for_the_next_utc_day(self):
        r = self.make()
        state = r.load()
        day = START.strftime("%Y-%m-%d")
        state["history"] = [{"day": day, "seq": i, "symbol": "BTCUSDT", "usd": "0.01", "users": {"vd-bull": "0.0001"}} for i in range(8)]
        r.save(state)
        self.assertEqual(r.tick(), "deferred")
        nxt = datetime.datetime.fromisoformat(r.load()["next_run"])
        self.assertEqual((nxt.day, nxt.hour, nxt.minute), (3, 0, 5))
        self.assertFalse(any("decide-v7" in c for c in self.world.calls))
        self.clock.t = nxt
        self.assertEqual(r.tick(), "ok")

    def test_an_end_user_close_to_its_daily_budget_defers_the_cycle(self):
        r = self.make()
        state = r.load()
        day = START.strftime("%Y-%m-%d")
        state["history"] = [{"day": day, "seq": 1, "symbol": "BTCUSDT", "usd": "0.20", "users": {"vd-sandbox": "0.0900", "vd-searcher": "0.0102"}}]
        r.save(state)
        self.assertEqual(r.tick(), "deferred")
        self.assertIn("vd-sandbox", (self.root / "logs" / "otari-runner.log").read_text())

    def test_a_day_already_over_the_organization_margin_defers_it(self):
        r = self.make()
        state = r.load()
        state["history"] = [{"day": START.strftime("%Y-%m-%d"), "seq": i, "symbol": "BTCUSDT", "usd": "0.40", "users": {}} for i in range(3)]
        r.save(state)
        self.assertEqual(r.tick(), "deferred")

    def test_the_total_cap_of_two_dollars_stops_it_for_good(self):
        r = self.make()
        state = r.load()
        state["total_usd"] = "2.00"
        r.save(state)
        result = r.tick()
        self.assertTrue(result.startswith("stopped") and "total cap" in result, result)
        self.assertEqual([c for c in self.world.calls if "decide-v7" in c], [])
        state["total_usd"] = "1.99"
        state["stopped"] = None
        r.save(state)
        self.assertEqual(r.tick(), "ok")
        self.assertEqual(r.load()["total_usd"], "2.026000")
        self.clock.advance(R.INTERVAL_S)
        self.assertTrue(r.tick().startswith("stopped"))

    def test_the_dry_run_describes_and_runs_nothing(self):
        r = self.make()
        text = r.dry_run()
        self.assertIn("next symbol: BTCUSDT", text)
        self.assertIn("budget gate: open", text)
        self.assertIn("key file: present", text)
        self.assertNotIn(KEY, text)
        self.assertEqual(self.world.calls, [])
        self.key.unlink()
        self.assertIn("key file: PROBLEM", r.dry_run())

    def test_the_loop_returns_a_stop_code_and_sleeps_until_the_next_slot(self):
        slept = []
        r = self.make()
        r.sleep = lambda s: (slept.append(s), self.clock.advance(s), setattr(self.world, "fail_decide", len(slept) >= 1))[0]
        code = r.loop()
        self.assertEqual(code, 3)
        self.assertGreaterEqual(slept[0], 10000, "after a good cycle it sleeps about three hours")

    def test_the_gitignore_keeps_the_logs_out_of_the_repository(self):
        self.assertIn("logs/", (Path(__file__).resolve().parents[1] / ".gitignore").read_text().split())



class TreeHasNoKeyShapeTest(unittest.TestCase):
    def test_the_runners_own_tree_scan_does_not_match_the_runner_or_any_file_of_the_repository(self):
        out = subprocess.run(["git", "grep", "--untracked", "-n", R.KEY_PREFIX], cwd=str(R.ROOT if hasattr(R, "ROOT") else Path(__file__).resolve().parents[1]), capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout), (1, ""), "otherwise every real cycle would stop itself on its own source")


class UpgradeProofsTest(RunnerTest):
    def test_the_newest_earlier_proofs_are_asked_again_oldest_first_and_never_the_one_just_stamped(self):
        r = self.make()
        folder = self.root / "ledger" / "timestamps"
        for seq in range(1, 21):
            (folder / f"{seq}.json.ots").write_bytes(b"x")
        (folder / "20.json.ots.bak").write_bytes(b"x")
        r.upgrade_proofs("ots", 20)
        asked = [c[-1] for c in self.world.calls if c[:2] == ["ots", "upgrade"]]
        self.assertEqual(asked, [f"ledger/timestamps/{seq}.json.ots" for seq in range(8, 20)])
        self.assertEqual(len(asked), R.Runner.UPGRADE_LAST)

    def test_a_calendar_that_is_down_is_logged_and_does_not_stop_the_cycle(self):
        r = self.make()
        (self.root / "ledger" / "timestamps" / "1.json.ots").write_bytes(b"x")
        failing = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "calendar down")
        r.run = failing
        r.upgrade_proofs("ots", 2)
        self.assertIn("upgrade of 1.json.ots failed", (self.root / "logs" / "otari-runner.log").read_text())

    def test_a_tick_upgrades_after_it_stamps(self):
        (self.root / "ledger" / "timestamps").mkdir(parents=True, exist_ok=True)
        r = self.make()
        (self.root / "ledger" / "timestamps" / "0.json.ots").write_bytes(b"x")
        self.assertEqual(r.tick(), "ok")
        names = self.commands()
        self.assertLess(names.index("ots stamp"), names.index("ots upgrade"))


if __name__ == "__main__":
    unittest.main()
