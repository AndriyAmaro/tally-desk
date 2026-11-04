"""The source drift seal: has the page an earlier dossier cited changed since that decision?

Each cycle already reads one web page through Otari's fetch tool and freezes an excerpt of it (otari_web.py). This module reads ONE of those
pages again, through the same fetch path, and seals what it found as `drift` in the new dossier:

- Which page: the page the newest Otari-era dossier fetched that is at least two hours old (its first source), taken from the ledger, so the
  choice is deterministic. No such dossier means no call at all (`status: none`).
- What is compared: the passage that dossier froze. The fetcher is asked for the passage of the page that begins with the same words and
  runs as long as the old one; both are normalized (markup, entities, case and punctuation gone) and hashed, the new one cut to the length of
  the old. Equal hashes: `unchanged`; otherwise `changed`, with our own GET of the page saying whether the new text is really on it.
- `unavailable` when Otari did not report a completed fetch or the call failed. Never an exception.

It is an evidence-freshness receipt and nothing else: nothing in the rules reads `drift`, so it cannot move a verdict. The call is one
fetch by the `fetcher` role (billed to its own end user, `vd-drift`, on the same daily budget), inside the tool cap of the cycle, and its
receipt is a call of the cycle like any other.
"""

import datetime
import hashlib
import json
import re

from . import llm, otari_web

SCHEMA = "otari-drift/1"
MIN_AGE_MINUTES = 120
LOOKBACK = 12  # how many of the newest Otari-era dossiers are looked at for an eligible page
ANCHOR_WORDS = 10
SYSTEM = ("You read one web page with the web fetch tool and reply with JSON only: {\"excerpt\": the passage of the page's main text that begins "
          "with the words you are given, copied verbatim, as long as the length you are given; an empty string if no passage of the page begins "
          "with those words}. No summary, no comment.")


NORM = 2  # 1: words only. 2: also drops live price tickers ("BTC▲$78,251.00"), which change on every read of a news page and say nothing about the article.
TICKER = re.compile(r"\b[A-Z]{2,6}\s?[▲▼△▽↑↓]\s?\$?\s?\d[\d,]*(?:\.\d+)?")


def normalize(text: str, version: int = NORM) -> str:
    """Words of the text in lower case, markup and entities removed, joined by single spaces: what the two readings are compared on.
    `version` is the normalization recorded in the sealed block, so a block sealed under an earlier one still replays."""
    if version >= 2:
        text = TICKER.sub(" ", str(text))
    return " ".join(otari_web._words(text))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _moment(text) -> datetime.datetime | None:
    try:
        moment = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def pick(ledger: list[dict], now: datetime.datetime, min_age_minutes: int = MIN_AGE_MINUTES) -> dict | None:
    """The first source of the newest Otari-era dossier old enough: its url, the passage it froze, and when it was decided. None if there is none."""
    from . import otari_report
    for d in list(reversed(otari_report.era(ledger)))[:LOOKBACK]:
        page = (((d.get("evidence") or {}).get("web")) or {}).get("page")
        decided = _moment((d.get("evidence") or {}).get("collected_at"))
        if not isinstance(page, dict) or not isinstance(page.get("excerpt"), str) or not str(page.get("url") or "").startswith("https://") or decided is None:
            continue
        if not normalize(page["excerpt"]) or (now - decided).total_seconds() < min_age_minutes * 60:
            continue
        return {"url": page["url"], "excerpt": page["excerpt"], "seq": d["seq"], "decided": decided, "title": page.get("title") or ""}
    return None


def collect(ledger: list[dict], now: datetime.datetime | None = None, ask=None, opener=None) -> dict:
    """The `drift` block for the new dossier. At most one fetch; a failure is recorded in the block, never raised."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    source = pick(ledger, now)
    if source is None:
        return {"schema": SCHEMA, "status": "none", "reason": f"no earlier Otari-era dossier at least {MIN_AGE_MINUTES // 60} hours old holds a fetched page"}
    old = normalize(source["excerpt"])
    block = {"schema": SCHEMA, "url": source["url"], "source_dossier": source["seq"], "decided_at": source["decided"].strftime("%Y-%m-%dT%H:%M:%SZ"),
             "norm": NORM, "old_sha256": sha(old), "compared_chars": len(old)}
    anchor = " ".join(source["excerpt"].split()[:ANCHOR_WORDS])
    asked = f"Fetch this page: {source['url']}\nThe passage begins with: {anchor}\nCopy at most {len(source['excerpt'])} characters, starting there."
    ask = ask or llm.chat
    cfg = llm.otari_config()
    try:
        # its own end user, on the same daily budget: a second fetch a cycle must not eat the day of the page fetch's `vd-fetcher`
        answer = ask(SYSTEM, asked, timeout=120, max_tokens=500, role="fetcher", end_user=f"{cfg.get('end_user_prefix') or ''}drift",
                     end_user_budget=cfg.get("end_user_budget"), extra={"tools": [{"type": "otari_web_fetch"}]})
    except (OSError, ValueError) as err:
        return {**block, "status": "unavailable", "reason": str(err)[:200], "receipt": llm.served_receipt()}
    receipt = llm.served_receipt() or {}
    when = _moment(receipt.get("captured_at")) or now
    block.update({"receipt": receipt, "request_id": receipt.get("request_id"), "fetched_at": receipt.get("captured_at"),
                  "minutes_after_decision": max(0, int((when - source["decided"]).total_seconds() // 60))})
    block["otari_reported_fetch"] = otari_web._fetched(receipt, source["url"])
    if not block["otari_reported_fetch"]:
        return {**block, "status": "unavailable", "reason": "Otari did not report a completed fetch of the page"}
    start, end = answer.find("{"), answer.rfind("}")
    try:
        excerpt = str(json.loads(answer[start:end + 1]).get("excerpt") or "").strip()
    except ValueError:
        return {**block, "status": "unavailable", "reason": "the reply was not the JSON that was asked for"}
    excerpt = " ".join(excerpt.split())[:len(source["excerpt"]) + 40]
    new = normalize(excerpt)[:len(old)]
    block.update({"excerpt": excerpt, "new_sha256": sha(new), "status": "unchanged" if new == old else "changed"})
    if block["status"] == "changed":
        if not excerpt:
            block["reason"] = "no passage of the page begins with the words of the earlier one"
        else:
            check = otari_web.excerpt_check(source["url"], excerpt, source["title"], opener)  # our own GET: is the new text really on the page?
            block["new_excerpt_verified"] = check["verified"]
    return block


def recheck(dossier: dict) -> dict:
    """The replay of a sealed drift block, offline: the new hash is the one of the sealed passage, and the status is what the two hashes say."""
    d = dossier.get("drift")
    if not isinstance(d, dict) or d.get("status") not in ("unchanged", "changed"):
        return {}
    try:
        again = sha(normalize(d["excerpt"], int(d.get("norm", 1)))[:d["compared_chars"]])
        ok = again == d["new_sha256"] and d["status"] == ("unchanged" if d["new_sha256"] == d["old_sha256"] else "changed")
    except (KeyError, TypeError, ValueError):
        ok = False
    return {"drift_ok": ok}

