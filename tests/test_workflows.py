"""The one scheduled workflow, otari-cycle, must call a runner that exists, with flags it accepts, and keep the key out of the log.

A typo in the command fails only when the schedule fires, so a lost cycle would be the first sign. This reads the workflow and checks it
against the real runner. (The hourly and Sigstore workflows of the earlier project are not in this tree, and their tests went with them.)
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / ".github" / "workflows" / "otari-cycle.yml"
needs_flow = unittest.skipUnless(FLOW.exists(), ".github/workflows/otari-cycle.yml is not in this tree")


@needs_flow
class CycleWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = FLOW.read_text(encoding="utf-8")

    def test_it_runs_the_runner_with_a_flag_the_runner_accepts(self):
        calls = re.findall(r"python3 (tools/[\w./-]+\.py)((?: --[\w-]+)*)", self.text)
        self.assertEqual([c[0] for c in calls], ["tools/otari_runner.py"])
        self.assertTrue((ROOT / calls[0][0]).is_file())
        helped = subprocess.run([sys.executable, str(ROOT / calls[0][0]), "--help"], capture_output=True, text=True, timeout=60).stdout
        for flag in calls[0][1].split():
            self.assertIn(flag, helped)

    def test_it_works_on_the_otari_branch_and_hands_the_key_over_through_a_file(self):
        self.assertRegex(self.text, r"ref:\s*otari\b")
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("OTARI_KEY_FILE", self.text)
        self.assertNotIn("echo $OTARI_SECRET", self.text)


if __name__ == "__main__":
    unittest.main()
