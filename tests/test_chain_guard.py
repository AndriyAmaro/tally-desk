"""No test may send a transaction to Sepolia, even with the key in the shell. See tests/chain_guard.py."""

import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests import chain_guard
from verdict_desk import practice

TESTS = Path(__file__).resolve().parent


class ChainGuardTest(unittest.TestCase):
    def test_the_executor_is_never_built_whatever_the_environment_holds(self):
        with mock.patch.dict(os.environ, {"SEPOLIA_PRIVATE_KEY": "0x" + "11" * 32}):
            self.assertIsNone(practice.chain_executor())
        self.assertTrue(chain_guard.GUARDED)
        self.assertNotIn("SEPOLIA_PRIVATE_KEY", os.environ)

    def test_even_the_original_function_would_find_nothing_to_run(self):
        """If something restored the real function, the script and the deployment record are out of reach."""
        self.assertFalse(practice.SCRIPT.exists() or practice.DEPLOYMENT.exists())

    def test_every_test_module_installs_the_guard_so_a_new_one_cannot_forget(self):
        missing = [p.name for p in sorted(TESTS.glob("test_*.py")) if "chain_guard" not in p.read_text(encoding="utf-8")]
        self.assertEqual(missing, [], "add `from tests import chain_guard` to these test modules")

    def test_no_test_patches_the_executor_through_a_name_that_does_not_exist(self):
        """`cli.practice` is not an attribute of the CLI module (the flow imports practice inside the function), so a patch there does nothing."""
        bad = [p.name for p in TESTS.glob("test_*.py") if re.search(r"cli\.practice", p.read_text(encoding="utf-8")) and p.name != Path(__file__).name]
        self.assertEqual(bad, [])


from tests import chain_guard  # noqa: E402,F811,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
