"""Cadence guard: the workflow is triggered several times an hour because GitHub drops scheduled runs,
and records only when the last dossier is old enough. Measured on the public run history, a single
hourly trigger fired every 2 to 6 hours, so the ledger covered 30% of the clock hours."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from verdict_desk import __main__ as cli
from verdict_desk import cadence

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def row(minutes_ago, seq=1):
    return {"seq": seq, "evidence": {"collected_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}}


class DueTest(unittest.TestCase):
    def test_a_fresh_dossier_means_not_due(self):
        self.assertFalse(cadence.due([row(10)], NOW, 50))

    def test_an_old_enough_dossier_means_due(self):
        self.assertTrue(cadence.due([row(51)], NOW, 50))
        self.assertTrue(cadence.due([row(50)], NOW, 50), "exactly at the limit is due")

    def test_the_newest_dossier_decides_not_the_last_row_written_out_of_order(self):
        self.assertFalse(cadence.due([row(5, 2), row(200, 1)], NOW, 50))

    def test_an_empty_ledger_is_due(self):
        self.assertTrue(cadence.due([], NOW, 50))

    def test_a_row_without_a_readable_time_fails_open(self):
        self.assertTrue(cadence.due([{"seq": 1, "evidence": {}}], NOW, 50))
        self.assertTrue(cadence.due([{"seq": 1, "evidence": {"collected_at": "not a date"}}], NOW, 50))

    def test_a_time_without_a_zone_is_read_as_utc(self):
        naive = {"seq": 1, "evidence": {"collected_at": (NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat()}}
        self.assertFalse(cadence.due([naive], NOW, 50))


class DueCommandTest(unittest.TestCase):
    def run_cmd(self, rows, minutes=50):
        path = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        import argparse
        opts = argparse.Namespace(ledger=path, min_minutes=minutes)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_due(opts)
        return code, out.getvalue()

    def test_it_prints_a_line_a_workflow_can_append_to_its_outputs_and_always_succeeds(self):
        code, out = self.run_cmd([])
        self.assertEqual(code, 0)
        self.assertIn("run=true", out.splitlines())
        fresh = {"seq": 1, "evidence": {"collected_at": datetime.now(timezone.utc).isoformat()}}
        code, out = self.run_cmd([fresh])
        self.assertEqual(code, 0)
        self.assertIn("run=false", out.splitlines())


if __name__ == "__main__":
    unittest.main()
