"""The scheduled workflows must call commands that exist.

The hourly bot is the heartbeat of the ledger. A typo in a subcommand or a flag fails only when the
schedule fires, so a lost hour is the first sign. This reads every `python -m verdict_desk ...` line
in .github/workflows and checks, against the real command line parser, that the subcommand exists
and that each flag on the line is one it accepts. It also checks that the files the line names exist.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
CALL = re.compile(r"python3? -m verdict_desk((?:\s+(?:--?[\w-]+(?:\s+(?!-)[^\s;|&]+)?|[A-Za-z][\w-]*(?:\s+[A-Z]{3,}USDT)*))+)")


def calls():
    found = []
    for wf in WORKFLOWS:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if "python" not in line or "verdict_desk" not in line or line.lstrip().startswith("#"):
                continue
            m = CALL.search(line)
            if m:
                found.append((wf.name, line.strip(), m.group(1).split()))
    return found


def help_of(*argv):
    return subprocess.run([sys.executable, "-m", "verdict_desk", *argv, "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60)


class WorkflowCallsTest(unittest.TestCase):
    def test_there_are_calls_to_check(self):
        self.assertGreaterEqual(len(calls()), 6)

    def test_every_subcommand_and_flag_used_by_a_workflow_exists(self):
        for wf, line, tokens in calls():
            # global options come before the subcommand: --ledger PATH
            rest, i = [], 0
            while i < len(tokens):
                if tokens[i] == "--ledger":
                    i += 2
                    continue
                rest.append(tokens[i])
                i += 1
            sub = next((t for t in rest if not t.startswith("-")), None)
            self.assertIsNotNone(sub, f"{wf}: no subcommand in {line!r}")
            out = help_of(sub)
            self.assertEqual(out.returncode, 0, f"{wf}: `{sub}` is not a verdict_desk command ({line!r})")
            for flag in (t for t in rest if t.startswith("--")):
                self.assertIn(flag, out.stdout, f"{wf}: `{sub}` has no {flag} option ({line!r})")

    def test_the_files_a_workflow_names_exist(self):
        for wf, line, tokens in calls():
            for i, t in enumerate(tokens):
                if t == "--config":
                    self.assertTrue((ROOT / tokens[i + 1]).is_file(), f"{wf}: {tokens[i + 1]} does not exist")

    def test_the_hourly_decision_uses_the_current_rules_and_the_users_config(self):
        hourly = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")
        self.assertIn("decide-v6", hourly)
        self.assertIn("--config config/voices.json", hourly)
        for old in ("decide-v4", "decide-v5"):
            self.assertNotIn(old, hourly, "the hourly run moved to rules v6")


if __name__ == "__main__":
    unittest.main()


class CadenceGateTest(unittest.TestCase):
    """GitHub drops scheduled runs, so the hourly workflow is triggered several times an hour and a gate
    job stops the extra ones. Both halves have to hold: triggers without the gate would duplicate
    decisions, a gate without the triggers would change nothing."""

    FLOW = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")

    def test_it_is_triggered_at_least_three_times_an_hour(self):
        cron = re.search(r'cron:\s*"([^"]+)"', self.FLOW).group(1)
        minutes = cron.split()[0].split(",")
        self.assertGreaterEqual(len(minutes), 3, cron)

    def test_the_decision_job_waits_for_the_gate_and_the_pages_job_follows_the_decision(self):
        self.assertIn("needs: gate", self.FLOW)
        self.assertIn("needs.gate.outputs.run == 'true'", self.FLOW)
        self.assertIn("needs.gate.result != 'success'", self.FLOW)  # fail open: a broken gate must not stop recording
        self.assertIn("needs: decide", self.FLOW)

    def test_a_manual_run_always_records(self):
        gate = self.FLOW[self.FLOW.index("gate:"):self.FLOW.index("  decide:")]
        self.assertIn("workflow_dispatch", gate)
        self.assertIn('echo "run=true"', gate)

    def test_the_gate_uses_a_real_command_with_the_limit_the_comment_promises(self):
        self.assertIn("verdict_desk due --min-minutes 50", self.FLOW)


class AnchorInstallTest(unittest.TestCase):
    """The CRE CLI installer moved and the old URL answered 404; piping that into bash looked like
    "command not found" and the anchor run died. Keep the URL on the current path and let curl fail loudly."""

    FLOW = (ROOT / ".github" / "workflows" / "anchor.yml").read_text(encoding="utf-8")

    def test_the_installer_url_is_the_current_path(self):
        self.assertIn("cre-cli/main/install/install.sh", self.FLOW)
        self.assertNotIn("cre-cli/main/install.sh", self.FLOW)

    def test_a_download_error_stops_the_step(self):
        self.assertIn("curl -fsSL", self.FLOW)
        self.assertIn("set -o pipefail", self.FLOW)


class AnchorRouteTest(unittest.TestCase):
    """This account cannot create a CRE API key, so the cadence runs on GitHub through cre/anchor/direct.ts: the
    same checks and the same forwarder, named apart from a CRE run on-chain. The CRE route stays for when a key exists."""

    FLOW = (ROOT / ".github" / "workflows" / "anchor.yml").read_text(encoding="utf-8")

    def test_the_sepolia_key_alone_enables_the_direct_route_and_the_cre_route_is_opt_in(self):
        """The stored CRE_API_KEY is rejected as unauthorized; choosing the CRE route because the secret exists failed every run."""
        gate = self.FLOW.split("- uses: actions/checkout")[0]
        self.assertIn('echo "route=direct"', gate)
        self.assertIn('[ "${ANCHOR_ROUTE}" = "cre" ] && [ -n "${CRE_API_KEY}" ]', gate)
        self.assertLess(gate.index('-z "${SEPOLIA_PRIVATE_KEY}"'), gate.index("ANCHOR_ROUTE}\" = \"cre\""))

    def test_it_runs_twice_an_hour_so_one_dropped_schedule_does_not_open_a_gap(self):
        self.assertIn('cron: "7,37 * * * *"', self.FLOW)

    def test_the_run_that_publishes_a_head_also_asks_for_it_to_be_anchored(self):
        """GitHub dropped the :07 and :37 schedules in the first hour; the deploy dispatches the anchor like the Sigstore witness."""
        hourly = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")
        self.assertIn('gh workflow run anchor --repo "$GITHUB_REPOSITORY" --ref main', hourly)
        self.assertIn("workflow_dispatch", self.FLOW.split("jobs:")[0])

    def test_the_direct_route_runs_the_checked_script_and_keeps_evidence_only_for_real_anchors(self):
        step = self.FLOW.split("(direct, same checks, same forwarder)")[1].split("- name:")[0]
        self.assertIn("bun run direct.ts", step)
        self.assertNotIn("--dry-run", step)
        self.assertIn('"status":"anchored"', step)
        self.assertNotIn("CRE_API_KEY", step)


class QueuedRunTest(unittest.TestCase):
    """A queued run must check out the tip of the branch: the event's commit predates what the run before it pushed."""

    HOURLY = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")

    def test_every_checkout_takes_the_tip_of_the_branch(self):
        checkouts = re.findall(r"- uses: actions/checkout@v4\n(\s+with:\n\s+ref: \$\{\{ github.ref_name \}\})?", self.HOURLY)
        self.assertEqual(len(checkouts), 3)
        self.assertTrue(all(checkouts), "a checkout without ref: reads the commit the event was created at")


class ModelRoutingWorkflowTest(unittest.TestCase):
    """The routing policy is re-ranked by its fixed rule in the same commit as the ledger, and the bench is published."""

    HOURLY = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")

    def test_every_commit_of_the_ledger_carries_the_policy_re_ranked_by_the_rule(self):
        self.assertEqual(self.HOURLY.count("python -m verdict_desk models --route config/model_routing.json --every 72"), 2)
        self.assertEqual(self.HOURLY.count("git add ledger config/model_routing.json"), 2)
        self.assertNotIn("git add ledger\n", self.HOURLY)

    def test_the_site_publishes_the_bench(self):
        self.assertIn("python -m verdict_desk models --out site/models.json", self.HOURLY)


class PacemakerTest(unittest.TestCase):
    """GitHub drops scheduled runs (none fired for 6 hours on 25 Oct, four triggers an hour or not), so the
    ledger cannot depend on the schedule alone. A second workflow starts when a run of the hourly one ends,
    waits, and dispatches the next one; the schedule and a manual run only have to restart the chain."""

    PACE = (ROOT / ".github" / "workflows" / "pacemaker.yml").read_text(encoding="utf-8")
    HOURLY = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")

    def test_it_starts_when_a_run_of_the_hourly_workflow_finishes_whatever_the_outcome(self):
        self.assertIn("workflow_run:", self.PACE)
        self.assertIn("workflows: [hourly-decision]", self.PACE)
        self.assertIn("types: [completed]", self.PACE)
        self.assertNotIn("conclusion", self.PACE.split("jobs:")[0], "a failed run must not break the chain")

    def test_it_has_a_group_of_its_own_and_never_cancels_the_waiter_that_is_running(self):
        group = re.search(r"concurrency:\s*\n\s*group:\s*(\S+)\s*\n\s*cancel-in-progress:\s*(\w+)", self.PACE)
        self.assertIsNotNone(group)
        self.assertNotEqual(group.group(1), "ledger")
        # one running and at most one pending: a newer pending run replaces an older pending one, so chains cannot multiply
        self.assertEqual(group.group(2), "false")

    def test_it_queues_its_own_successor_because_runs_made_by_the_token_do_not_start_workflow_run(self):
        """Measured on 25 Oct: the run the pacemaker dispatched recorded decisions, but its end did not wake the pacemaker."""
        self.assertIn("workflow_dispatch:", self.PACE.split("jobs:")[0])
        self.assertIn("gh workflow run pacemaker", self.PACE)
        self.assertLess(self.PACE.index("gh workflow run pacemaker"), self.PACE.index("sleep "), "the successor is queued before the wait, so a failure later cannot end the chain")

    def test_it_waits_less_than_an_hour_inside_a_longer_timeout_and_then_dispatches(self):
        minutes = re.search(r"WAIT_MINUTES:\s*(\d+)", self.PACE)
        self.assertIsNotNone(minutes)
        self.assertLess(int(minutes.group(1)), 60)
        self.assertGreaterEqual(int(minutes.group(1)), 45)
        timeout = int(re.search(r"timeout-minutes:\s*(\d+)", self.PACE).group(1))
        self.assertGreater(timeout, int(minutes.group(1)) + 5)
        self.assertIn("gh workflow run hourly-decision", self.PACE)
        self.assertIn("actions: write", self.PACE)

    def test_the_dispatch_it_sends_goes_through_the_cadence_gate(self):
        self.assertIn("paced", self.PACE)
        self.assertIn("inputs:", self.HOURLY)
        self.assertIn("paced:", self.HOURLY)
        gate = self.HOURLY[self.HOURLY.index("gate:"):self.HOURLY.index("  decide:")]
        self.assertIn("inputs.paced", gate)

    def test_a_manual_run_without_the_flag_still_always_records(self):
        gate = self.HOURLY[self.HOURLY.index("gate:"):self.HOURLY.index("  decide:")]
        self.assertIn('echo "run=true"', gate)


class WitnessChainTest(unittest.TestCase):
    """The Sigstore witness had its own schedule, and GitHub dropped it like the others: 6 heads signed in 18 hours.
    Every hourly run that publishes a head now asks the witness workflow to sign it."""

    HOURLY = (ROOT / ".github" / "workflows" / "hourly.yml").read_text(encoding="utf-8")

    def test_the_publishing_job_dispatches_the_witness(self):
        pages = self.HOURLY[self.HOURLY.index("  pages:"):]
        self.assertIn("gh workflow run rekor-witness", pages)
        self.assertIn("actions: write", pages)

    def test_the_witness_workflow_can_be_dispatched(self):
        rekor = (ROOT / ".github" / "workflows" / "rekor.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", rekor)
