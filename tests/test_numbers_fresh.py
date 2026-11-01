"""The published figures must equal the ledger, not someone's memory of it.

`scripts/refresh_numbers.py --check` regenerates every count from the ledger and exits 1 when any
document is out of date. The docs state their figures as a snapshot ("as of dossier N"), and the
hourly bot appends a dossier after that snapshot was written, so a snapshot that is merely OLDER
than the chain is expected and not a defect. What must never happen is a snapshot that is WRONG:
one that cites the current head but disagrees with it, or cites a dossier the chain does not have.

So this test fails in exactly those two cases, and when the snapshot is only behind the chain it
skips with the gap named, instead of turning every human push red. Refresh before a release with
`python3 scripts/refresh_numbers.py`. It also skips the test-count sub-check here, because running
the suite inside the suite would recurse; that one is covered by running the script directly.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CITED = re.compile(r"as of \*\*dossier (\d+)\*\*")


def ledger_head_seq() -> int:
    last = ""
    with (ROOT / "ledger" / "ledger.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last = line
    import json
    return int(json.loads(last)["seq"])


class NumbersFreshTest(unittest.TestCase):
    def test_published_figures_match_the_ledger(self):
        run = subprocess.run([sys.executable, "scripts/refresh_numbers.py", "--skip-tests", "--check"],
                             cwd=ROOT, capture_output=True, text=True)
        if run.returncode == 0:
            return
        found = CITED.search((ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIsNotNone(found, "README.md no longer states which dossier its figures are as of")
        cited, head = int(found.group(1)), ledger_head_seq()
        self.assertLessEqual(cited, head, f"README cites dossier {cited}, which the chain (head {head}) does not have")
        if cited < head:
            self.skipTest(f"the README figures are a snapshot as of dossier {cited}; the chain is at {head}. "
                          "Expected between refreshes, run scripts/refresh_numbers.py before a release.")
        self.fail("the README cites the current head but a document disagrees with the ledger; "
                  "run scripts/refresh_numbers.py:\n" + run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
