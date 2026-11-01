"""News evidence found by Otari's own web tools, frozen into the evidence packet.

Per decision cycle: ONE search (about USD 0.01) and ONE page fetch, both through Otari's tools.

- The query is built from what the cycle already knows: the narrative convergence the voices produced (which coin, which way) and
  today's date, so the search looks for that day's news about that coin.
- What is kept from the search is not the model's answer but what Otari reports it found, `otari_tool_calls[].sources`. Only results
  with a page path are kept (a bare domain or a tag or category index is not an article), dated ones first, at most three, one per
  domain, https only.
- The first item is then read with Otari's `otari_web_fetch` tool. The page text is not in the reply's tool-call entry (only the URL
  and status are), so the excerpt is the model's verbatim relay of the page's opening text; it is sealed with its sha256, labelled as
  a relay, and checked best-effort against our own GET of the same page (`excerpt_verified`: true, false, or null when the page could not
  be read without a browser).
- They go into the packet as `web`, with ids in the evidence index, so the analysts can cite them and the dossier counts how many they
  did. The replay reads the frozen packet and never searches again. The titles and the excerpt are untrusted text.
"""

import datetime
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import llm

MAX_ITEMS = 3
EXCERPT_CHARS = 400
COINS = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
BIAS_WORDS = {"bullish": "rally gains", "bearish": "selloff drop"}
SEARCH_SYSTEM = "You are a news scout. Run the web search tool once, then reply with the single word: done."
FETCH_SYSTEM = ("You read one web page with the web fetch tool and reply with JSON only: {\"excerpt\": the first 400 characters of the page's "
                "main text, copied verbatim, no summary}.")
_INDEX_PATH = re.compile(r"/(tag|tags|category|categories|topic|topics|author|authors|page|search|archive|archives)(/|$)", re.I)


def query_for(symbols: list[str], narrative: dict | None = None, today: str | None = None) -> str:
    """Today's news about the coin the cycle's narrative convergence points at, else about the first symbol asked for."""
    today = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    bases = [s.upper().removesuffix("USDT") for s in symbols]
    convergence = [c for c in (narrative or {}).get("convergence") or [] if isinstance(c, dict) and c.get("token")]
    pick = next((c for c in convergence if c["token"] in bases), convergence[0] if convergence else None)
    token, words = (pick["token"], BIAS_WORDS.get(pick.get("bias"), "price news")) if pick else (bases[0], "price news")
    return f"{COINS.get(token, token)} {words} news {today}"


def is_article(url: str) -> bool:
    """A page with a path of its own: not a bare domain, not a tag, category or archive index."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname) and parts.path.strip("/") != "" and not _INDEX_PATH.search(parts.path)


def pick_items(tool_calls: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    """Sources of completed searches that are article pages with a title, one per domain, dated ones first."""
    found = []
    for call in tool_calls or []:
        if call.get("type") != "web_search" or call.get("status") != "completed":
            continue
        for src in call.get("sources") or []:
            url, title = str(src.get("url") or ""), str(src.get("title") or "").strip()
            if title and is_article(url):
                found.append({"url": url, "domain": (urllib.parse.urlsplit(url).hostname or "").removeprefix("www."), "title": title[:200],
                              "published_date": src.get("published_date"), "query": call.get("query")})
    found.sort(key=lambda item: item["published_date"] is None)  # stable: dated first, Otari's order within each group
    items, seen = [], set()
    for item in found:
        if item["domain"] not in seen:
            seen.add(item["domain"])
            items.append(item)
        if len(items) >= limit:
            break
    return items


def _plain(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip().lower()


def _windows(text: str, size: int = 6) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _plain(text))
    return [" ".join(words[i:i + size]) for i in range(0, max(0, len(words) - size + 1))]


def verify_excerpt(url: str, excerpt: str, opener=None) -> bool | None:
    """Best effort: do runs of six consecutive words of the excerpt appear in the page as we fetch it ourselves (markup, punctuation
    and case ignored)? True with at least two such runs, False when the page was read and they are not there, None when the page
    cannot be read this way (blocked, or built in the browser)."""
    wanted = _windows(excerpt)[:40]
    if len(wanted) < 2:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; verdict-desk/1.0)"})
        with (opener or urllib.request.urlopen)(request, timeout=12) as response:
            page = response.read(2_000_000).decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    have = " ".join(re.findall(r"[a-z0-9]+", _plain(page)))
    return sum(1 for w in wanted if w in have) >= 2


def _fetched(receipt: dict, url: str) -> bool:
    """Did Otari report a completed fetch of this URL (query string and fragment are not shown by Otari)?"""
    bare = url.split("?")[0].split("#")[0]
    return any(c.get("type") == "web_fetch" and c.get("status") == "completed" and str(c.get("url") or "").split("?")[0] == bare for c in receipt.get("tool_calls") or [])


def fetch_page(item: dict, ask=None, opener=None) -> dict:
    """Reads one item with Otari's fetch tool and returns the `page` block: the excerpt, its sha256, the receipt, the checks."""
    ask = ask or llm.chat
    page = {"url": item["url"], "domain": item["domain"], "source_tool": "otari_web_fetch"}
    try:
        answer = ask(FETCH_SYSTEM, f"Fetch this page and give the excerpt: {item['url']}", timeout=120, max_tokens=500, role="fetcher",
                     extra={"tools": [{"type": "otari_web_fetch"}]})
    except (OSError, ValueError) as err:
        return {**page, "error": str(err), "receipt": llm.served_receipt()}
    receipt = llm.served_receipt() or {}
    page.update({"receipt": receipt, "fetched_at": receipt.get("captured_at"), "request_id": receipt.get("request_id"), "otari_reported_fetch": _fetched(receipt, item["url"])})
    start, end = answer.find("{"), answer.rfind("}")
    try:
        excerpt = str(json.loads(answer[start:end + 1]).get("excerpt") or "").strip()
    except ValueError:
        excerpt = ""
    if not excerpt:
        return {**page, "error": "the reply carried no excerpt"}
    excerpt = re.sub(r"\s+", " ", excerpt)[:EXCERPT_CHARS]
    page.update({"excerpt": excerpt, "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                 "excerpt_source": "verbatim relay by the model of the page text Otari's fetch tool returned",
                 "excerpt_verified": verify_excerpt(item["url"], excerpt, opener)})
    return page


def collect(symbols: list[str], narrative: dict | None = None, today: str | None = None, ask=None, fetch=None, opener=None) -> dict:
    """The cycle's one search and one fetch; returns the `web` block of the packet. A failure is recorded in it, never raised."""
    ask = ask or llm.chat
    query = query_for(symbols, narrative, today)
    block = {"method": "otari_web_search", "query": query, "max_uses": 1}
    try:
        ask(SEARCH_SYSTEM, f"Search the web once for: {query}", timeout=120, max_tokens=300, role="searcher",
            extra={"tools": [{"type": "otari_web_search", "max_uses": 1}]})
    except (OSError, ValueError) as err:
        return {**block, "error": str(err), "receipt": llm.served_receipt(), "items": []}
    receipt = llm.served_receipt() or {}
    block["receipt"] = receipt
    block["fetched_at"] = receipt.get("captured_at")
    block["items"] = [{**item, "fetched_at": receipt.get("captured_at"), "request_id": receipt.get("request_id"), "source_tool": "otari_web_search"}
                      for item in pick_items(receipt.get("tool_calls") or [])]
    if not block["items"]:
        block["note"] = "the search returned no article page"
        return block
    block["page"] = fetch(block["items"][0]) if fetch else fetch_page(block["items"][0], ask=ask, opener=opener)
    return block


def index_texts(web: dict | None) -> list[str]:
    """The evidence lines the analysts see for a `web` block: one per item, and the page excerpt when there is one."""
    lines = [f"Web ({i.get('domain', '')}): {i.get('title', '')}" for i in (web or {}).get("items") or []]
    page = (web or {}).get("page") or {}
    if page.get("excerpt"):
        lines.append(f"Web page excerpt ({page.get('domain', '')}, relayed from Otari's fetch): {page['excerpt']}")
    return lines
