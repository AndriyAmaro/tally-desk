"""The public site: every page carries the same shell, the landing page's figures match the ledger, and the pages keep their tokens."""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import otari_cards  # noqa: E402
import site_shell  # noqa: E402

WEB = ROOT / "web"
NODE = shutil.which("node")


class SiteTest(unittest.TestCase):
    def test_every_page_carries_the_current_shell(self):
        self.assertEqual(site_shell.main(["--check"]), 0, "run scripts/site_shell.py")

    def test_every_page_has_one_main_landmark_and_a_skip_link_that_reaches_it(self):
        for page in sorted(WEB.glob("**/*.html")):
            html = page.read_text(encoding="utf-8")
            self.assertEqual(html.count('<main id="content">'), 1, page.name)
            self.assertIn('href="#content"', html, page.name)
            self.assertEqual(html.count("shell:footer:start"), 1, page.name)

    def test_the_site_figures_file_matches_the_ledger(self):
        self.assertTrue(otari_cards.refresh_figures(check=True), "run scripts/otari_cards.py (the runner rewrites it every cycle)")

    def test_the_theme_defines_both_schemes_and_the_semantic_tokens_the_new_pages_use(self):
        css = (WEB / "theme.css").read_text(encoding="utf-8")
        self.assertIn("prefers-color-scheme:light", css)
        for token in ("--color-bg", "--color-text", "--color-accent", "--role-bull", "--space-4", "--radius-lg", "--elev-1", "--text-base"):
            self.assertIn(token + ":", css)
        for name in ("index", "cycle", "verify"):
            page = (WEB / f"{name}.html").read_text(encoding="utf-8")
            self.assertNotRegex(re.sub(r"<!--.*?-->", "", page, flags=re.S).split("</style>")[0], r"#[0-9a-fA-F]{3,6}\b(?![^<]*data:)", f"{name}.html styles use tokens, not hex")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_landing_page_runs_against_the_real_ledger(self):
        out = subprocess.run([NODE, "--test", str(ROOT / "tests" / "home.test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout[-800:] + out.stderr[-400:])


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
