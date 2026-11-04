"""The static pages must not be able to break silently.

A page that loads a script that is not there, links to a page that does not exist, or carries a
syntax error in its inline JavaScript fails for the visitor and not for the test suite. These checks
read every page in web/ and catch those three, plus the metadata a link preview needs.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
PAGES = sorted(WEB.glob("**/*.html"))
NODE = shutil.which("node")


class PageTest(unittest.TestCase):
    def test_there_are_pages_to_check(self):
        self.assertGreaterEqual(len(PAGES), 7)
        self.assertEqual([p for p in PAGES if p.relative_to(WEB).parts[0] == "legacy"], [], "the pages of the earlier project stay in its frozen repository")

    def test_every_page_has_a_title_and_a_description_for_link_previews(self):
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            self.assertRegex(html, r"<title>[^<]{5,}</title>", page.name)
            self.assertRegex(html, r'<meta name="description" content="[^"]{30,}"', page.name)
            self.assertIn('property="og:title"', html, page.name)

    def test_every_local_script_exists(self):
        for page in PAGES:
            for src in re.findall(r'<script[^>]+src="([^"]+)"', page.read_text(encoding="utf-8")):
                if src.startswith(("http://", "https://")):
                    continue
                self.assertTrue((page.parent / src).resolve().is_file(), f"{page.name} loads {src}, which is not in web/")

    def test_every_local_link_resolves(self):
        for page in PAGES:
            for href in re.findall(r'<a [^>]*href="([^"#?]+\.html)"', page.read_text(encoding="utf-8")):
                target = (page.parent / href).resolve()
                site_path = target.relative_to(WEB).as_posix() if target.is_relative_to(WEB) else None
                if href.startswith(("http://", "https://")) or "$" in href:
                    continue  # absolute, or built by a script at run time
                self.assertTrue(target.is_file(), f"{page.name} links to {href}, which is not in web/")

    def test_the_new_pages_are_reachable_from_the_navigation(self):
        index = (WEB / "index.html").read_text(encoding="utf-8")
        for target in ("narrative.html", "witnesses.html", "dashboard.html", "ask.html"):
            self.assertIn(f'href="{target}"', index, f"the landing page does not link to {target}")

    def test_no_page_pitches_lawyers(self):
        for page in PAGES:
            body = re.sub(r"<!-- shell:header:start -->.*?<!-- shell:header:end -->", "", page.read_text(encoding="utf-8"), flags=re.S)
            self.assertNotRegex(body, r"(?i)\blawyers?\b|foundation declaration|\bcourt\b", page.name)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_inline_javascript_of_every_page_parses(self):
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            for n, block in enumerate(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S), 1):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                    fh.write(block)
                out = subprocess.run([NODE, "--check", fh.name], capture_output=True, text=True)
                Path(fh.name).unlink(missing_ok=True)
                self.assertEqual(out.returncode, 0, f"{page.name} inline script {n}: {out.stderr[:300]}")

from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()


class NoEmDashTest(unittest.TestCase):
    """House style for every text a reader sees: commas, colons, periods or a middle dot, never an em dash."""

    def test_no_page_template_or_readme_carries_an_em_dash(self):
        root = Path(__file__).resolve().parents[1]
        files = sorted((root / "web").glob("**/*.html")) + [root / "README.md", root / "docs" / "HISTORY.md", root / "docs" / "architecture.md"]
        hits = [f"{p.relative_to(root)}:{n}" for p in files for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "—" in line]
        self.assertEqual(hits, [])
