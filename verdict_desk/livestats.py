"""Numbers of the live operation, computed only from the ledger text.

The definitions are the same as in web/stats.js (the dashboard recomputes them in the browser),
so the README, the dashboard and this command agree on any given head. Nothing here reads the
network and nothing is estimated: every figure is a count over ledger lines.
"""

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

GAP_LIMIT_MINUTES = 90
FIRST_LLM_SEQ = 46  # first dossier recorded after the analyst prompt and citation validator fixes
HOUR_MS = 3_600_000


def _round1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10  # half up, like Math.round in the dashboard


def _millis(stamp) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def cadence(ledger: list[dict]) -> dict:
    """When the dossiers were collected: window, clock hours with a record, gaps per symbol."""
    by_symbol: dict[str, list[int]] = {}
    every: list[int] = []
    for d in ledger:
        t = _millis(d["evidence"]["collected_at"])
        if t is None:
            continue
        by_symbol.setdefault(d["evidence"]["symbol"], []).append(t)
        every.append(t)
    longest, longest_symbol, gaps_over = 0, None, 0
    counts = {}
    for symbol, times in by_symbol.items():
        times.sort()
        counts[symbol] = len(times)
        for before, after in zip(times, times[1:]):
            gap = after - before
            if gap > GAP_LIMIT_MINUTES * 60_000:
                gaps_over += 1
            if gap > longest:
                longest, longest_symbol = gap, symbol
    every.sort()
    out = {
        "total": len(ledger),
        "per_symbol": dict(sorted(counts.items())),
        "first": _iso(every[0]) if every else None,
        "last": _iso(every[-1]) if every else None,
        "window_hours": _round1((every[-1] - every[0]) / HOUR_MS) if every else 0.0,
        "window_days": _round1((every[-1] - every[0]) / (24 * HOUR_MS)) if every else 0.0,
        "longest_gap_minutes": int(math.floor(longest / 60_000 + 0.5)),
        "longest_gap_symbol": longest_symbol,
        "gaps_over_90_minutes": gaps_over,
    }
    hours = {t // HOUR_MS for t in every}
    span = (every[-1] // HOUR_MS - every[0] // HOUR_MS + 1) if every else 0
    out["clock_hours_with_records"] = len(hours)
    out["clock_hours_in_window"] = span
    out["coverage_pct"] = _round1(len(hours) / span * 100) if span else 0.0
    return out


def llm_era(ledger: list[dict], first_seq: int = FIRST_LLM_SEQ, last_seq: int | None = None) -> dict:
    """What the citation validator did with the analysts' claims from `first_seq` (to `last_seq`, when given)."""
    era = [d for d in ledger if d["seq"] >= first_seq and (last_seq is None or d["seq"] <= last_seq)]
    sides = {s: {"accepted": 0, "rejected": 0, "errors": 0} for s in ("bull", "bear")}
    reasons: dict[str, int] = {}
    without_llm, models = [], set()
    for d in era:
        layer = d.get("llm")
        if not layer:
            without_llm.append(d["seq"])
            continue
        models.add(layer.get("model"))
        for side in sides:
            part = layer.get(side) or {}
            if part.get("error"):
                sides[side]["errors"] += 1
                continue
            sides[side]["accepted"] += len(part.get("accepted") or [])
            for claim in part.get("rejected") or []:
                sides[side]["rejected"] += 1
                reason = str(claim.get("rejected_because", "unknown")).split(":")[0]
                reasons[reason] = reasons.get(reason, 0) + 1
    accepted = sum(s["accepted"] for s in sides.values())
    rejected = sum(s["rejected"] for s in sides.values())
    return {
        "first_seq": first_seq,
        "last_seq": last_seq,
        "dossiers": len(era),
        "dossiers_with_analysts": len(era) - len(without_llm),
        "dossiers_without_llm": without_llm,
        "models": sorted(m for m in models if m),
        "accepted": accepted,
        "rejected": rejected,
        "accepted_pct": _round1(accepted / (accepted + rejected) * 100) if accepted + rejected else None,
        "sides": sides,
        "rejection_reasons": dict(sorted(reasons.items())),
    }


def summarize(ledger: list[dict], first_llm_seq: int = FIRST_LLM_SEQ, last_llm_seq: int | None = None) -> dict:
    head = {"seq": ledger[-1]["seq"], "hash": ledger[-1]["hash"], "symbol": ledger[-1]["evidence"]["symbol"]} if ledger else None
    actions: dict[str, int] = {}
    for d in ledger:
        actions[d["verdict"]["action"]] = actions.get(d["verdict"]["action"], 0) + 1
    return {"head": head, "actions": dict(sorted(actions.items())), "cadence": cadence(ledger), "llm_era": llm_era(ledger, first_llm_seq, last_llm_seq)}


def commit_authors(path: Path, ledger: list[dict]) -> dict[str, list[int]] | None:
    """Dossier seqs per git author of the line that recorded them (git history is the independent clock).

    None outside a git checkout, or when blame and the ledger disagree on the number of lines.
    """
    try:
        out = subprocess.run(
            ["git", "blame", "--line-porcelain", "--", path.name], cwd=path.resolve().parent, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names = [line[len("author "):] for line in out.splitlines() if line.startswith("author ")]
    if len(names) != len(ledger):
        return None
    by_author: dict[str, list[int]] = {}
    for d, name in zip(ledger, names):
        by_author.setdefault(name, []).append(d["seq"])
    return dict(sorted(by_author.items()))


def seq_ranges(seqs: list[int]) -> str:
    """[54, 55, 56, 60] -> "54-56, 60" (a hyphen, not a dash)."""
    parts, start, prev = [], None, None
    for n in sorted(seqs):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def format_report(s: dict, authors: dict[str, list[int]] | None = None) -> str:
    c, e = s["cadence"], s["llm_era"]
    lines = []
    if s["head"]:
        lines.append(f"Head: seq {s['head']['seq']}, hash {s['head']['hash']}")
    per = ", ".join(f"{k} {v}" for k, v in c["per_symbol"].items())
    lines.append(f"Dossiers: {c['total']} ({per}); actions {', '.join(f'{k} {v}' for k, v in s['actions'].items())}")
    lines.append(f"Window: {c['first']} to {c['last']} = {c['window_hours']} h ({c['window_days']} days)")
    lines.append(f"Clock hours (UTC) with at least one dossier: {c['clock_hours_with_records']} of {c['clock_hours_in_window']} ({c['coverage_pct']}%)")
    lines.append(f"Longest gap between consecutive dossiers of the same symbol: {c['longest_gap_minutes']} min ({c['longest_gap_symbol']}); gaps over {GAP_LIMIT_MINUTES} min: {c['gaps_over_90_minutes']}")
    if authors:
        lines.append("Recorded by git author: " + ", ".join(f"{k} {len(v)} (seq {seq_ranges(v)})" for k, v in authors.items()))
    pct = f"{e['accepted_pct']}%" if e["accepted_pct"] is not None else "n/a"
    span = f"seq {e['first_seq']} onward" if e["last_seq"] is None else f"seq {e['first_seq']} to {e['last_seq']}"
    lines.append(f"LLM era ({span}): {e['dossiers']} dossiers, {e['dossiers_with_analysts']} with analyst output, model {', '.join(e['models']) or 'none'}")
    if e["dossiers_without_llm"]:
        lines.append(f"  without any LLM layer (seq): {', '.join(str(n) for n in e['dossiers_without_llm'])}")
    lines.append(f"  claims accepted by the citation validator: {e['accepted']}, rejected: {e['rejected']} ({pct} accepted)")
    for side, v in e["sides"].items():
        lines.append(f"  {side}: {v['accepted']} accepted, {v['rejected']} rejected, {v['errors']} failed calls")
    for reason, n in e["rejection_reasons"].items():
        lines.append(f"  rejected because \"{reason}\": {n}")
    return "\n".join(lines)


def to_json(s: dict) -> str:
    return json.dumps(s, indent=1)
