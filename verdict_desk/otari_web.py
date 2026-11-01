"""News evidence found by Otari's own web tools, frozen into the evidence packet.

Per decision cycle: ONE search (about USD 0.01) and ONE page fetch, both through Otari's tools.

- The query is built from what the cycle already knows: the coin the most voices converged on (whichever way they lean, and the query
  asks for both rallies and selloffs, so it does not search for the story the voices already told) and today's date.
- What is kept from the search is not the model's answer but what Otari reports it found, `otari_tool_calls[].sources`. Only results
  with a page path are kept (a bare domain or a tag or category index is not an article), at most three, one per domain, https only,
  dated ones first. A source whose date is known (published date, or a /YYYY/MM/DD/ in its URL) and older than three days is dropped;
  one with no date at all is kept and marked `undated`, never presented as fresh.
- The first item is then read with Otari's `otari_web_fetch` tool. The page text is not in the reply's tool-call entry (only the URL
  and status are), so the excerpt is the model's verbatim relay of the page's opening text; it is sealed with its sha256 and labelled as
  a relay. It is then checked against our own GET of the same page: runs of six consecutive words of the excerpt (those that are not just
  the title) must be found in the page text, in at least 80 percent of the runs. The check seals what it found (`excerpt_check`: runs counted,
  runs found, the fraction, the sha256 and size of the page we got) and `excerpt_verified` is true, false, or null when the page could not be read
  without a browser or the excerpt is too short to judge. It proves the text is on that page, not that Otari's fetch tool is what read it.
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
MAX_AGE_DAYS = 3
MATCH_FRACTION = 0.8
MIN_RUNS = 5
_URL_DATE = re.compile(r"/(20\d\d)/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])(?:/|$)")
SEARCH_SYSTEM = "You are a news scout. Run the web search tool once, then reply with the single word: done."
FETCH_SYSTEM = ("You read one web page with the web fetch tool and reply with JSON only: {\"excerpt\": the first 400 characters of the page's "
                "main text, copied verbatim, no summary}.")
_INDEX_PATH = re.compile(r"/(tag|tags|category|categories|topic|topics|author|authors|page|search|archive|archives)(/|$)", re.I)


def query_for(symbols: list[str], narrative: dict | None = None, today: str | None = None) -> str:
    """Today's news about the coin the most voices converged on (among the symbols asked for when one of them did), else about the
    first symbol. The words ask for both directions, so the narrative does not choose which story the search comes back with."""
    today = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    bases = [s.upper().removesuffix("USDT") for s in symbols]
    convergence = [c for c in (narrative or {}).get("convergence") or [] if isinstance(c, dict) and c.get("token")]
    ranked = sorted(convergence, key=lambda c: (c["token"] not in bases, -len(c.get("voices") or [])))  # stable: ties keep their order
    token = ranked[0]["token"] if ranked else bases[0]
    return f"{COINS.get(token, token)} price news rally selloff {today}"


def is_article(url: str) -> bool:
    """A page with a path of its own: not a bare domain, not a tag, category or archive index."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname) and parts.path.strip("/") != "" and not _INDEX_PATH.search(parts.path)


def source_date(src: dict, url: str) -> tuple[str | None, str]:
    """(date, how we know it): the published date Otari reports, else a /YYYY/MM/DD/ in the URL, else none and `undated`."""
    published = str(src.get("published_date") or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
        return published, "published"
    found = _URL_DATE.search(urllib.parse.urlsplit(url).path)
    if found:
        return "-".join(found.groups()), "from_url"
    return None, "undated"


def pick_items(tool_calls: list[dict], limit: int = MAX_ITEMS, today: str | None = None, max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    """Sources of completed searches that are article pages with a title, not older than `max_age_days` when their date is known,
    one per domain, dated ones first. Each item carries its `date` and `date_status` (published, from_url, undated)."""
    now = datetime.date.fromisoformat(today) if today else datetime.datetime.now(datetime.timezone.utc).date()
    found = []
    for call in tool_calls or []:
        if call.get("type") != "web_search" or call.get("status") != "completed":
            continue
        for src in call.get("sources") or []:
            url, title = str(src.get("url") or ""), str(src.get("title") or "").strip()
            if not title or not is_article(url):
                continue
            date, status = source_date(src, url)
            if date and (now - datetime.date.fromisoformat(date)).days > max_age_days:
                continue  # old news is not news
            found.append({"url": url, "domain": (urllib.parse.urlsplit(url).hostname or "").removeprefix("www."), "title": title[:200],
                          "published_date": src.get("published_date"), "date": date, "date_status": status, "query": call.get("query")})
    found.sort(key=lambda item: item["date"] is None)  # stable: dated first, Otari's order within each group
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


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _plain(text))


def excerpt_check(url: str, excerpt: str, title: str = "", opener=None) -> dict:
    """Does the excerpt come from the page? Runs of six consecutive words of the excerpt are looked up in our own GET of the page
    (markup, punctuation and case ignored). Runs made mostly of words of the title do not count: the title is in the page anyway.
    Verified needs at least five runs and 80 percent of them found; None when the page cannot be read this way or the excerpt is too short."""
    title_words = set(_words(title))
    words = _words(excerpt)
    runs = [" ".join(words[i:i + 6]) for i in range(0, max(0, len(words) - 5)) if sum(1 for w in words[i:i + 6] if w not in title_words) >= 3]
    out = {"method": "runs of six words against our own GET of the page, title words not counted", "runs": len(runs), "threshold": MATCH_FRACTION}
    if len(runs) < MIN_RUNS:
        return {**out, "verified": None, "reason": "the excerpt is too short to judge"}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; verdict-desk/1.0)"})
        with (opener or urllib.request.urlopen)(request, timeout=12) as response:
            raw = response.read(2_000_000)
    except (OSError, ValueError) as err:
        return {**out, "verified": None, "reason": f"the page could not be read here ({type(err).__name__})"}
    have = " ".join(_words(raw.decode("utf-8", "replace")))
    found = sum(1 for run in runs if run in have)
    return {**out, "found": found, "fraction": round(found / len(runs), 4), "page_sha256": hashlib.sha256(raw).hexdigest(), "page_bytes": len(raw),
            "verified": found / len(runs) >= MATCH_FRACTION}


def verify_excerpt(url: str, excerpt: str, opener=None, title: str = "") -> bool | None:
    return excerpt_check(url, excerpt, title, opener)["verified"]


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
                 })
    check = excerpt_check(item["url"], excerpt, item.get("title", ""), opener)
    page.update({"excerpt_check": check, "excerpt_verified": check["verified"]})
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
                      for item in pick_items(receipt.get("tool_calls") or [], today=today)]
    if not block["items"]:
        block["note"] = "the search returned no article page"
        return block
    block["page"] = fetch(block["items"][0]) if fetch else fetch_page(block["items"][0], ask=ask, opener=opener)
    return block


def index_texts(web: dict | None) -> list[str]:
    """The evidence lines the analysts see for a `web` block: one per item, and the page excerpt when there is one."""
    # items sealed before the recency filter have no `date_status` and keep their original line, so their replay does not change
    lines = [(f"Web ({i.get('domain', '')}, {i.get('date') or 'undated'}): {i.get('title', '')}" if "date_status" in i else f"Web ({i.get('domain', '')}): {i.get('title', '')}")
             for i in (web or {}).get("items") or []]
    page = (web or {}).get("page") or {}
    if page.get("excerpt"):
        lines.append(f"Web page excerpt ({page.get('domain', '')}, relayed from Otari's fetch): {page['excerpt']}")
    return lines
