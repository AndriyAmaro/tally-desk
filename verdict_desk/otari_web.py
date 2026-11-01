"""News evidence found by Otari's own web search tool, frozen into the evidence packet.

One search per decision cycle (it costs about USD 0.01 a call on the hosted gateway): the model is asked to run the tool once,
and what is kept is not its answer but what Otari reports it found, `otari_tool_calls[].sources`: URL, title and the date the
source gave, plus the time we asked and the request id. At most three items, from different domains. They go into the packet as
`web`, so the dossier freezes them and the replay reads them from there and never searches again. The titles are untrusted
text, like every headline the agents read.
"""

import urllib.parse

from . import llm

MAX_ITEMS = 3
SYSTEM = "You are a news scout. Run the web search tool once, then reply with the single word: done."


def query_for(symbols: list[str]) -> str:
    names = [s.upper().removesuffix("USDT") for s in symbols]
    return f"latest crypto market news today {' '.join(names)}"


def pick_items(tool_calls: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    """The first sources of a completed search, one per domain, https only."""
    items, seen = [], set()
    for call in tool_calls or []:
        if call.get("type") != "web_search" or call.get("status") != "completed":
            continue
        for src in call.get("sources") or []:
            url = str(src.get("url") or "")
            parts = urllib.parse.urlsplit(url)
            domain = (parts.hostname or "").removeprefix("www.")
            if parts.scheme != "https" or not domain or domain in seen or not str(src.get("title") or "").strip():
                continue
            seen.add(domain)
            items.append({"url": url, "domain": domain, "title": str(src["title"]).strip()[:200], "published_date": src.get("published_date"), "query": call.get("query")})
            if len(items) >= limit:
                return items
    return items


def collect(symbols: list[str], ask=None) -> dict:
    """Runs the one search of the cycle and returns the `web` block of the packet; a failure is recorded in it, never raised."""
    ask = ask or llm.chat
    query = query_for(symbols)
    block = {"method": "otari_web_search", "query": query, "max_uses": 1}
    try:
        ask(SYSTEM, f"Search the web once for: {query}", timeout=120, max_tokens=300, role="searcher",
            extra={"tools": [{"type": "otari_web_search", "max_uses": 1}]})
    except (OSError, ValueError) as err:
        block["error"] = str(err)
        block["receipt"] = llm.served_receipt()
        block["items"] = []
        return block
    receipt = llm.served_receipt() or {}
    block["receipt"] = receipt
    block["fetched_at"] = receipt.get("captured_at")
    block["items"] = [{**item, "fetched_at": receipt.get("captured_at"), "request_id": receipt.get("request_id"), "source_tool": "otari_web_search"}
                      for item in pick_items(receipt.get("tool_calls") or [])]
    if not block["items"]:
        block["note"] = "the search returned no usable source"
    return block
