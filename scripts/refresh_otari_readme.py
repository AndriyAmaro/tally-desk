#!/usr/bin/env python3
"""Rewrites the three generated blocks of README.md (proofs, scoreboard, hosts) from the ledger and the evidence file.

    python3 scripts/refresh_otari_readme.py           # rewrite README.md
    python3 scripts/refresh_otari_readme.py --check   # exit 1 if README.md is out of date

Pure Python, no model and no network: it reads ledger/ledger.jsonl, docs/evidence/otari-drills.jsonl and config/external_hosts.json.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verdict_desk import otari_report  # noqa: E402


def main(argv=None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    readme = ROOT / "README.md"
    ledger = [json.loads(line) for line in (ROOT / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    old = readme.read_text(encoding="utf-8")
    new = otari_report.refresh_readme(old, ledger, otari_report.read_evidence())
    if check:
        print("README.md is up to date" if new == old else "README.md is out of date: run scripts/refresh_otari_readme.py")
        return 0 if new == old else 1
    readme.write_text(new, encoding="utf-8")
    print("README.md rewritten" if new != old else "README.md already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
