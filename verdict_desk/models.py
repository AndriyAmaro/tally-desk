"""The model bench and the routing policy: which free model answers is a measured, recorded decision.

The analysts and the judge run on free models through a self-hosted gateway (tools/llm-gateway/gate.py). Free models
hit rate limits, so a call can be routed to another model; this module makes that routing a decision with a record:

- `bench` counts, from the ledger alone, how each model that answered did: calls, empty answers, claims and how many
  passed the citation validator, refusal reasons, the judge's rulings kept or voided. It never scores market skill:
  with this few rulings that would be noise.
- `policy` orders the models by that record under a rule fixed in advance (RULE). config/model_routing.json is its
  output on the ledger up to `as_of_seq`, and tests/test_models.py fails if the file differs: nobody hand-tunes it.
  The gateway reads that file and tries the models in its order.
"""

import json
import math
from collections import Counter
from pathlib import Path

MIN_CLAIMS = 20
MIN_WILSON_LOW = 0.85
Z = 1.96
# The policy is not re-ranked during the judging days: the order in force on Oct 30 stays until Nov 1 (UTC).
FROZEN = ("2026-10-30T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
RULE = (f"Rank the models that answered by the Wilson lower bound (95%) of the share of their claims that passed the "
        f"citation validator, counting only models with at least {MIN_CLAIMS} claims; models with fewer claims follow, "
        f"under observation, by calls answered; a model whose lower bound is under {MIN_WILSON_LOW} is dropped. "
        f"Computed from the ledger up to as_of_seq only; not re-ranked from {FROZEN[0][:10]} to {FROZEN[1][:10]}.")

# The name the gateway asks for, and the names providers answer with: data, kept in config (no model name in code).
ALIASES_FILE = Path(__file__).resolve().parents[1] / "config" / "model_aliases.json"
ALIASES: dict[str, list[str]] = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))["aliases"] if ALIASES_FILE.exists() else {}
_ROUTING_NAME = {served: name for name, served_names in ALIASES.items() for served in served_names}


def routing_name(served_by: str) -> str:
    return _ROUTING_NAME.get(served_by, served_by)


def wilson_low(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    p = passed / total
    centre = p + Z * Z / (2 * total)
    margin = Z * math.sqrt(p * (1 - p) / total + Z * Z / (4 * total * total))
    return round((centre - margin) / (1 + Z * Z / total), 4)


def _reason(text: str) -> str:
    """'number not in cited evidence: 24' -> 'number not in cited evidence' (the kind, not the number)."""
    return str(text or "no reason").split(":")[0].strip()


def bench(ledger: list[dict]) -> dict:
    rows: dict[str, dict] = {}
    unanswered = 0

    def row(served: str, seq: int) -> dict:
        name = routing_name(served)
        r = rows.setdefault(name, {"id": name, "served_as": [], "calls": 0, "empty": 0, "claims": 0, "passed": 0,
                                   "reasons": Counter(), "judge": {"rulings": 0, "kept": 0, "voided": 0},
                                   "voice_batches": 0, "first_seq": seq, "last_seq": seq})
        if served not in r["served_as"]:
            r["served_as"].append(served)
        r["last_seq"] = seq
        return r

    for d in ledger:
        llm = d.get("llm") or {}
        for side in ("bull", "bear"):
            block = llm.get(side)
            if not isinstance(block, dict):
                continue
            if not block.get("served_by"):
                unanswered += 1 if block.get("error") else 0
                continue
            r = row(block["served_by"], d["seq"])
            kept, refused = block.get("accepted") or [], block.get("rejected") or []
            r["calls"] += 1
            r["empty"] += 0 if kept or refused else 1
            r["claims"] += len(kept) + len(refused)
            r["passed"] += len(kept)
            r["reasons"].update(_reason(x.get("rejected_because")) for x in refused)
        judge = llm.get("judge")
        if isinstance(judge, dict) and judge.get("served_by") and judge.get("ruling"):
            r = row(judge["served_by"], d["seq"])
            r["judge"]["rulings"] += 1
            r["judge"]["kept" if judge.get("accepted") else "voided"] += 1
        for served in ((d.get("evidence") or {}).get("narrative") or {}).get("served_by") or []:
            row(served, d["seq"])["voice_batches"] += 1

    out = []
    for r in rows.values():
        r["reasons"] = dict(r["reasons"].most_common())
        r["pass_rate"] = round(r["passed"] / r["claims"], 4) if r["claims"] else None
        r["wilson_low"] = wilson_low(r["passed"], r["claims"])
        out.append(r)
    out.sort(key=lambda r: (-r["wilson_low"], -r["calls"], r["id"]))
    return {"as_of_seq": ledger[-1]["seq"] if ledger else 0, "models": out, "unanswered_calls": unanswered}


def policy(ledger: list[dict]) -> dict:
    rows = bench(ledger)["models"]
    ranked = [r for r in rows if r["claims"] >= MIN_CLAIMS and r["wilson_low"] >= MIN_WILSON_LOW]
    dropped = [r for r in rows if r["claims"] >= MIN_CLAIMS and r["wilson_low"] < MIN_WILSON_LOW]
    observing = sorted((r for r in rows if r["claims"] < MIN_CLAIMS and r["calls"] > 0), key=lambda r: (-r["calls"], r["id"]))
    order = [r["id"] for r in ranked] + [r["id"] for r in observing]
    names = order + [r["id"] for r in dropped]
    return {"version": 1, "rule": RULE, "min_claims": MIN_CLAIMS, "min_wilson_low": MIN_WILSON_LOW,
            "frozen": list(FROZEN), "as_of_seq": ledger[-1]["seq"] if ledger else 0,
            "ranked": [r["id"] for r in ranked], "observing": [r["id"] for r in observing], "dropped": [r["id"] for r in dropped],
            "order": order, "aliases": {n: ALIASES.get(n, [n]) for n in names}}


def frozen(now_iso: str) -> bool:
    return FROZEN[0] <= now_iso < FROZEN[1]
