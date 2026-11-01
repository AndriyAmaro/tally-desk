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
PAGES = sorted(WEB.glob("*.html"))
# Pages the hourly deploy generates next to the static ones; they are not in web/.
GENERATED = {"scorecard.html", "exhibit/index.html"}
NODE = shutil.which("node")


class PageTest(unittest.TestCase):
    def test_there_are_pages_to_check(self):
        self.assertGreaterEqual(len(PAGES), 7)

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
                self.assertTrue((WEB / src).is_file(), f"{page.name} loads {src}, which is not in web/")

    def test_every_local_link_resolves(self):
        for page in PAGES:
            for href in re.findall(r'<a [^>]*href="([^"#?]+\.html)"', page.read_text(encoding="utf-8")):
                if href.startswith(("http://", "https://")) or href in GENERATED or "$" in href or re.fullmatch(r"exhibit/exhibit-\d+\.html", href):
                    continue  # absolute, generated at deploy time, or built by a script at run time
                self.assertTrue((WEB / href).is_file(), f"{page.name} links to {href}, which is not in web/")

    def test_the_new_pages_are_reachable_from_the_navigation(self):
        index = (WEB / "index.html").read_text(encoding="utf-8")
        for target in ("narrative.html", "witnesses.html", "evidence.html", "learn.html", "ask.html"):
            self.assertIn(f'href="{target}"', index, f"the dashboard does not link to {target}")

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


if __name__ == "__main__":
    unittest.main()


class NoEmDashTest(unittest.TestCase):
    """House style for every text a reader sees: commas, colons, periods or a middle dot, never an em dash."""

    def test_no_page_template_or_readme_carries_an_em_dash(self):
        root = Path(__file__).resolve().parents[1]
        files = sorted((root / "web").glob("*.html")) + [root / "README.md", root / "verdict_desk" / "exhibit.py", root / "verdict_desk" / "scorecard.py"]
        hits = [f"{p.relative_to(root)}:{n}" for p in files for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "—" in line]
        self.assertEqual(hits, [])
