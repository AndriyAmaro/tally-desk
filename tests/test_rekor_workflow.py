"""The Sigstore witness workflow signs the ledger head every hour with no key and no secret.

Three properties are worth pinning because a mistake in any of them fails quietly:
  * it must not share the hourly bot's concurrency group: GitHub keeps one pending run per group and
    cancels the older pending one, so a witness run waiting there could cancel an hourly decision;
  * the identity the page expects (repository and workflow file) must be the one the workflow signs with,
    otherwise every entry would be shown as signed by a stranger;
  * on a pull request it signs and verifies but never commits, so testing the file cannot touch the ledger.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOW = (ROOT / ".github" / "workflows" / "rekor.yml").read_text(encoding="utf-8")
PAGE = (ROOT / "web" / "witnesses.html").read_text(encoding="utf-8")


class RekorWorkflowTest(unittest.TestCase):
    def test_it_asks_for_an_identity_token_and_nothing_secret(self):
        self.assertIn("id-token: write", FLOW)
        self.assertNotIn("secrets.", FLOW)

    def test_it_does_not_share_the_hourly_concurrency_group(self):
        group = re.search(r"concurrency:\s*\n\s*group:\s*(\S+)", FLOW)
        self.assertIsNotNone(group)
        self.assertNotEqual(group.group(1), "ledger")

    def test_cosign_is_pinned_to_a_release_that_writes_the_bundle_the_page_reads(self):
        self.assertRegex(FLOW, r"cosign-release:\s*['\"]?v2\.\d+\.\d+")

    def test_the_signed_file_is_the_snapshot_the_page_downloads(self):
        self.assertIn("ledger/timestamps/$seq.json", FLOW)
        self.assertIn("ledger/rekor/$seq.bundle.json", FLOW)

    def test_the_signature_is_verified_against_this_repository_and_workflow_before_it_is_kept(self):
        self.assertIn("verify-blob", FLOW)
        self.assertIn("certificate-oidc-issuer https://token.actions.githubusercontent.com", FLOW)
        self.assertIn("workflows/rekor.yml@refs/", FLOW)

    def test_a_pull_request_never_commits(self):
        keep = FLOW[FLOW.index("- name: Keep the bundle"):]
        self.assertIn("if: github.event_name != 'pull_request'", keep.split("run:")[0])
        self.assertEqual(FLOW.count("git commit"), 1)
        self.assertGreater(FLOW.index("git commit"), FLOW.index("- name: Keep the bundle"))

    def test_the_page_expects_the_identity_the_workflow_has(self):
        self.assertIn('repo: "AndriyAmaro/verdict-desk"', PAGE)
        self.assertIn('workflow: "rekor.yml"', PAGE)
        self.assertTrue((ROOT / ".github" / "workflows" / "rekor.yml").exists())


if __name__ == "__main__":
    unittest.main()
