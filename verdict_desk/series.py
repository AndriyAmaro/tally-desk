"""The judged series: which model wrote each stretch of the ledger, and why it changed.

Read from the ledger alone, nothing rewritten. Consecutive dossiers form one era while their configured model
(`llm.model`) and the outcome of the LLM layer stay the same; a failed era is named from the error the dossiers
recorded; `served_by` (recorded from dossier 348) counts the models a router actually used.
"""

from collections import Counter


def _status(d: dict) -> tuple[str, str]:
    llm = d.get("llm")
    if not llm:
        return "no LLM layer", "the fixed rules decided alone"
    sides = [llm.get(s) or {} for s in ("bull", "bear")]
    errors = [s.get("error") for s in sides if s.get("error")]
    if len(errors) == len(sides):
        e = " ".join(errors)
        if "402" in e:
            return "failed", "provider billing (HTTP 402)"
        if "credit balance is too low" in e:
            return "failed", "provider credit exhausted"
        if "429" in e:
            return "failed", "rate limited (HTTP 429)"
        return "failed", "provider error"
    return "answered", ""


def eras(ledger: list[dict]) -> list[dict]:
    out: list[dict] = []
    for d in ledger:
        llm = d.get("llm") or {}
        status, reason = _status(d)
        key = (llm.get("model"), status, reason)
        served = [b.get("served_by") for b in (llm.get("bull") or {}, llm.get("bear") or {}, llm.get("judge") or {}) if isinstance(b, dict) and b.get("served_by")]
        when = (d.get("evidence") or {}).get("collected_at")
        if out and out[-1]["_key"] == key:
            era = out[-1]
            era["to_seq"], era["to_time"] = d["seq"], when
            era["records"] += 1
        else:
            era = {"_key": key, "from_seq": d["seq"], "to_seq": d["seq"], "from_time": when, "to_time": when, "records": 1,
                   "model": llm.get("model"), "status": status, "reason": reason, "served_by": Counter()}
            out.append(era)
        era["served_by"].update(served)
    for era in out:
        del era["_key"]
        era["served_by"] = dict(era["served_by"])
    return out
