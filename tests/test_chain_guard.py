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

    def test_every_test_module_really_imports_the_guard_so_a_new_one_cannot_forget(self):
        """An import statement found in the syntax tree, not a mention in a comment or a string; and, for the modules already loaded in this
        run, the guard module is really an attribute of the module (the import happened)."""
        import ast
        import sys
        missing = []
        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = any((isinstance(n, ast.ImportFrom) and n.module in ("tests", "tests.chain_guard", "chain_guard") and any(a.name in ("chain_guard", "GUARDED") or n.module.endswith("chain_guard") for a in n.names))
                           or (isinstance(n, ast.Import) and any(a.name in ("tests.chain_guard", "chain_guard") for a in n.names)) for n in ast.walk(tree))
            if not imported:
                missing.append(path.name)
        self.assertEqual(missing, [], "add `from tests import chain_guard` to these test modules")
        loaded = [m for name, m in sys.modules.items() if name.split(".")[-1].startswith("test_") and getattr(m, "__file__", "") and Path(m.__file__).parent == TESTS]
        self.assertTrue(loaded)
        self.assertEqual([m.__name__ for m in loaded if not hasattr(m, "chain_guard")], [])

    def test_the_guard_also_clears_the_otari_and_the_other_chain_secrets(self):
        import subprocess
        import sys
        env = {**os.environ, "OTARI_API_KEY": "fake", "OTARI_USE_POLICIES": "1", "CRE_ETH_PRIVATE_KEY": "0x1", "SEPOLIA_PRIVATE_KEY": "0x2"}
        code = "import os; import tests.chain_guard; print(sorted(k for k in os.environ if k.startswith('OTARI_') or k in ('CRE_ETH_PRIVATE_KEY', 'SEPOLIA_PRIVATE_KEY')))"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(TESTS.parent), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr[-300:])

    def test_no_test_patches_the_executor_through_a_name_that_does_not_exist(self):
        """`cli.practice` is not an attribute of the CLI module (the flow imports practice inside the function), so a patch there does nothing."""
        bad = [p.name for p in TESTS.glob("test_*.py") if re.search(r"cli\.practice", p.read_text(encoding="utf-8")) and p.name != Path(__file__).name]
        self.assertEqual(bad, [])


from tests import chain_guard  # noqa: E402,F811,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
