#!/usr/bin/env python3
"""Checks every model of config/otari.json against the public Otari catalog (no key needed, no cost).

Each role model, and each entry of a role's `fallbacks`, must exist in the catalog and be flagged open_weights. Prints one row per model and exits 1 on any miss.

    python3 tools/otari_catalog_check.py [--catalog catalog.json]
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_URL = "https://otari.ai/api/v1/catalog/models?limit=1000"


def load_catalog(path: str | None) -> list[dict]:
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "verdict-desk/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    return data["models"] if isinstance(data, dict) else data


def _row(label: str, model: str, by_id: dict) -> dict:
    entry = by_id.get(model)
    return {
        "role": label, "model": model, "found": entry is not None,
        "open_weights": bool(entry and entry.get("open_weights")),
        "vendor": entry.get("vendor") if entry else None,
        "providers": entry.get("providers") if entry else None,
        "ok": bool(entry and entry.get("open_weights") and not entry.get("deprecated")),
    }


def check(config: dict, catalog: list[dict]) -> list[dict]:
    by_id = {m["id"]: m for m in catalog}
    rows = []
    for role, spec in config["roles"].items():
        rows.append(_row(role, spec["model"], by_id))
        rows.extend(_row(f"{role}#{n}", model, by_id) for n, model in enumerate(spec.get("fallbacks", []), start=1))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--catalog", help="a saved catalog JSON instead of the live public endpoint")
    parser.add_argument("--config", default=str(ROOT / "config" / "otari.json"))
    opts = parser.parse_args(argv)
    config = json.loads(Path(opts.config).read_text(encoding="utf-8"))
    rows = check(config, load_catalog(opts.catalog))
    for r in rows:
        print(f"{r['role']:9s} {r['model']:42s} found={r['found']!s:5s} open_weights={r['open_weights']!s:5s} vendor={r['vendor']} providers={','.join(r['providers'] or [])}")
    bad = [r["role"] for r in rows if not r["ok"]]
    print("all roles are open-weight models in the catalog" if not bad else f"NOT OK: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
