"""Evidence packs on the site: ask.html reads the pack of a dossier, and anyone can recompute it without trusting us.

The site only carries packs for the newest dossiers (about 90 KB each); the rest come from `export --seq`.
"""

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from verdict_desk import __main__ as cli
from verdict_desk import dossier

ROOT = Path(__file__).resolve().parents[1]
LEDGER = dossier.read_ledger(ROOT / "ledger" / "ledger.jsonl")


def run(outdir, recent):
    opts = argparse.Namespace(ledger=ROOT / "ledger" / "ledger.jsonl", recent=recent, outdir=outdir)
    with contextlib.redirect_stdout(io.StringIO()):
        return cli.cmd_packs(opts)


class PacksCommandTest(unittest.TestCase):
    def test_it_writes_one_pack_per_recent_dossier_named_by_its_seq(self):
        out = Path(tempfile.mkdtemp())
        self.assertEqual(run(out, 3), 0)
        self.assertEqual(sorted(p.name for p in out.glob("*.json")), sorted(f"{d['seq']}.json" for d in LEDGER[-3:]))

    def test_each_pack_recomputes_to_the_hash_it_records(self):
        out = Path(tempfile.mkdtemp())
        run(out, 2)
        for path in out.glob("*.json"):
            pack = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(pack["recomputed_hash"], pack["recorded_hash"])
            self.assertEqual(pack["format"], "verdict-desk/evidence-pack/v1")

    def test_without_a_ledger_it_fails_instead_of_writing_nothing_silently(self):
        opts = argparse.Namespace(ledger=Path(tempfile.mkdtemp()) / "none.jsonl", recent=3, outdir=Path(tempfile.mkdtemp()))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_packs(opts), 1)


if __name__ == "__main__":
    unittest.main()
