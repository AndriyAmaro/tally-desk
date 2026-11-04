"""The product name is one constant: renaming the product is changing `site_shell.NAME` and replacing the display string in the hand-written pages.

Nothing a script generates may spell the name itself. The check changes the constant to a sentinel, rebuilds what the scripts generate (the
site header and footer, the README header card) and finds the sentinel everywhere the old name was, and the old name nowhere."""

import importlib
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import otari_cards  # noqa: E402
import site_shell  # noqa: E402

OLD = site_shell.NAME


class ProductNameTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(otari_cards)

    def test_the_shell_and_the_cards_take_the_name_from_the_one_constant(self):
        with mock.patch.object(site_shell, "NAME", "Zebra Desk"), mock.patch.object(site_shell, "NAME_SUB", "on Mars"):
            importlib.reload(otari_cards)
            shell = site_shell.header("index.html") + site_shell.footer()
            card = otari_cards.header_svg()
        self.assertIn("Zebra Desk", shell)
        self.assertIn("on Mars", shell)
        self.assertNotIn(OLD, shell)
        self.assertIn("Zebra Desk on Mars", card)
        self.assertNotIn(OLD, card)

    def test_no_script_spells_the_name_outside_the_constant(self):
        spelled = []
        for path in sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tools").glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD in line and not re.match(r"NAME, NAME_SUB = ", line):
                    spelled.append(f"{path.relative_to(ROOT)}:{n}")
        self.assertEqual(spelled, [], "a generator must read site_shell.NAME, not retype the name")


if __name__ == "__main__":
    unittest.main()
