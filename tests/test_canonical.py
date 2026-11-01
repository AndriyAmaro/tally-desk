import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_dossier import packet
from verdict_desk import dossier
from verdict_desk.canonical import canonical, es_number

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")


class EsNumberTest(unittest.TestCase):
    def test_matches_ecmascript_layout(self):
        cases = {
            100.0: "100", 5.8e-05: "0.000058", 1e21: "1e+21", 1e-7: "1e-7", 1e-6: "0.000001",
            0.1 + 0.2: "0.30000000000000004", 1.2345678901234568e20: "123456789012345680000",
            5e-324: "5e-324", -0.0: "0", 0.0: "0", -1.5e-10: "-1.5e-10", 1e22: "1e+22", 12: "12", -3: "-3",
        }
        for value, expected in cases.items():
            self.assertEqual(es_number(value), expected, repr(value))

    def test_rejects_what_javascript_cannot_hold(self):
        for bad in (float("nan"), float("inf"), 2**53 + 1):
            with self.assertRaises(ValueError):
                es_number(bad)
        with self.assertRaises(TypeError):
            es_number(True)


class CrossLanguageVectorsTest(unittest.TestCase):
    def test_python_matches_the_javascript_reference(self):
        vectors = json.loads((HERE / "canonical_vectors.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(vectors), 9)
        for vector in vectors:
            self.assertEqual(canonical(json.loads(vector["input"])).decode("utf-8"), vector["expected"], vector["input"])

    def test_keys_sort_by_utf16_code_unit_not_code_point(self):
        # U+1F600 is the surrogate pair D83D DE00, which sorts before U+FF5E.
        self.assertEqual(canonical({"～": 1, "\U0001f600": 2}).decode("utf-8"), '{"\U0001f600":2,"～":1}')


@unittest.skipUnless(NODE, "node not installed")
class BrowserVerifierTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        for trend in (0.004, -0.004, 0.0):
            dossier.append(packet(trend, titles=("Bitcoin rally hits record",)), self.path)

    def run_node(self) -> dict:
        out = subprocess.run([NODE, str(HERE / "node_verify.js"), str(self.path)], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_javascript_recomputes_the_same_hashes_as_python(self):
        self.assertEqual(self.run_node(), {"dossiers": 3, "problems": []})
        self.assertEqual(dossier.verify(dossier.read_ledger(self.path)), [])

    def test_javascript_detects_an_edit_that_python_also_detects(self):
        lines = self.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])
        record["verdict"]["rationale"] = "edited after the fact"
        lines[1] = json.dumps(record, ensure_ascii=False)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        problems = self.run_node()["problems"]
        self.assertTrue(any("seq 2: content was altered" in p for p in problems), problems)
        self.assertTrue(any("seq 2" in p for p in dossier.verify(dossier.read_ledger(self.path))))


if __name__ == "__main__":
    unittest.main()
