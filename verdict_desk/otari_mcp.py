"""Spot prices from a public MCP server (CoinGecko, keyless), called by Otari's managed MCP loop, and a cross-check against the exchange.

The app does not talk to the MCP server: it asks a model through Otari's Messages API, with the server declared in `mcp_servers`,
and Otari runs the model, calls the tool and feeds the result back. On a streamed Messages request with the
`mcp-client-2025-11-20` beta Otari also emits what it did, an `mcp_tool_use` block (server, tool, input) and an
`mcp_tool_result` block (the server's own answer). The price is read from THAT block, not from the model's words, and both blocks
are kept as the proof that the call happened.

A model writes the TypeScript the server runs, so the code it sent is checked too: it must be the one price query we asked for
(`client.simple.price.get({ids, vs_currencies})`) and contain no digit at all, so no price can be typed into it. Otherwise the
prices are marked untrusted and unused.

The trusted price then takes part in the decision as a second price source. It is compared with the latest price in the packet (the
close of its newest candle) with a tolerance that grows with that candle's age: the base limit (1 percent) plus one candle's ATR
for every candle interval of age (the interval is the packet's own, 1h, 15m, 4h...; with an interval we cannot read there is no
allowance), because a price may move that much while the candle waits. Beyond the tolerance the two sources DIVERGE,
and that is a blocking freshness class of its own, `classes.divergence`, next to the untouched `classes.market` (age of the candle).
The packet's freshness `ok` becomes false, which is the fail-closed path the replay, the browser verifier and the CRE workflow already
know (`BLOCKED_STALE`, no position, the refusal recorded with the numbers); the original staleness reason is never overwritten.

A missing or untrusted MCP price is recorded as unavailable and blocks nothing. A third-party server cannot make the agent trade
(a trade needs the exchange data) but, by design, it CAN make it refuse to trade: when it answers with a trusted price that disagrees
with the exchange beyond the tolerance, no position is opened. That is the point of a cross-check, and it fails closed.
"""

import json
import re

from . import llm

SERVER = {"name": "coingecko", "url": "https://mcp.api.coingecko.com/mcp", "allowed_tools": ["execute"],
          "purpose_hint": "Use the execute tool with TypeScript of the form: async function run(client) { return await client.simple.price.get({ids: 'bitcoin,ethereum,solana', vs_currencies: 'usd'}); }"}
IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
BETA = "mcp-client-2025-11-20"
SYSTEM = "You are a market data clerk. Use the coingecko execute tool exactly once, then reply with the single word: done."
PROMPT = "Get the current USD price of bitcoin, ethereum and solana with one call of the coingecko execute tool."
LIMIT_PCT = 1.0  # base tolerance, in percent, of the divergence check

_ARGS = r"\s*(?:(?:ids|vs_currencies)\s*:\s*'[a-z,\-]+'\s*,?\s*){1,2}"
_CODE = re.compile(r"^\s*async function run\(client\)\s*\{\s*(?:const\s+\w+\s*=\s*)?(?:return\s+)?(?:await\s+)?client\.simple\.price\.get\(\s*\{" + _ARGS + r"\}\s*\)\s*;?\s*(?:console\.log\(\s*\w+\s*\)\s*;?\s*)*(?:return\s+\w+\s*;?\s*)?\}\s*$")


def code_trusted(code) -> tuple[bool, str | None]:
    """Whether the TypeScript Otari ran is the one price query we asked for, with no digit that could carry a typed-in price."""
    if not isinstance(code, str):
        return False, "the tool input has no code"
    if re.search(r"\d", code):
        return False, "the code contains a digit, so a price could have been typed into it"
    if not _CODE.match(code):
        return False, "the code is not the single client.simple.price.get({ids, vs_currencies}) query that was asked for"
    return True, None


def parse_stream(lines) -> dict:
    """Reads a Messages SSE stream: the MCP activity blocks (use, with its input also when it arrives in input_json_delta pieces, and
    result), the text and any error event. Unknown events are ignored."""
    calls, results, text, errors, partial = [], [], [], [], {}
    for raw in lines:
        raw = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not raw.startswith("data:"):
            continue
        try:
            event = json.loads(raw[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "error":
            errors.append(str((event.get("error") or {}).get("message") or event.get("error") or "error event")[:200])
            continue
        block = event.get("content_block") if kind == "content_block_start" else None
        if isinstance(block, dict) and block.get("type") == "mcp_tool_use":
            calls.append({"id": block.get("id"), "server": block.get("server_name"), "tool": block.get("name"), "input": block.get("input")})
            partial[event.get("index")] = len(calls) - 1
        elif isinstance(block, dict) and block.get("type") == "mcp_tool_result":
            results.append({"tool_use_id": block.get("tool_use_id"), "is_error": bool(block.get("is_error")), "content": block.get("content")})
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text.append(str(delta.get("text", "")))
        elif isinstance(delta, dict) and delta.get("type") == "input_json_delta" and event.get("index") in partial:
            call = calls[partial[event["index"]]]
            call["_json"] = call.get("_json", "") + str(delta.get("partial_json", ""))
        elif kind == "content_block_stop" and event.get("index") in partial:
            call = calls[partial.pop(event["index"])]
            if "_json" in call:
                try:
                    call["input"] = json.loads(call.pop("_json"))
                except ValueError:
                    call.pop("_json", None)
    return {"calls": calls, "results": results, "text": "".join(text), "errors": errors}


def prices_from(results: list[dict]) -> dict:
    """{"BTC": 77886.0, ...} from the tool result, the server's own JSON; anything else gives an empty dict."""
    for r in results:
        if r.get("is_error"):
            continue
        content = r.get("content")
        if isinstance(content, list):
            content = "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            continue
        data = data.get("result", data) if isinstance(data, dict) else {}
        out = {}
        for sym, cid in IDS.items():
            usd = (data.get(cid) or {}).get("usd") if isinstance(data.get(cid), dict) else None
            if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0:
                out[sym] = float(usd)
        if out:
            return out
    return {}


def collect(opener=None) -> dict:
    """One streamed Messages call through Otari with the MCP server declared; returns the `market_mcp` block of the packet."""
    block = {"method": "otari_mcp", "server": SERVER["name"], "url": SERVER["url"], "tool": "execute", "fetched_at": None, "prices": {}, "trusted": False}
    try:
        got = llm.messages_call("mcp", SYSTEM, PROMPT, max_tokens=300, timeout=120, mcp_servers=[SERVER], max_tool_iterations=4, beta=BETA,
                                stream=True, reader=lambda response: parse_stream(response), opener=opener)
    except llm.OtariError as err:
        return {**block, "error": str(err), "receipt": llm.served_receipt()}
    except (OSError, ValueError) as err:
        return {**block, "error": f"{type(err).__name__} calling the Messages API", "receipt": llm.served_receipt()}
    stream, receipt = got["stream"], got["receipt"]
    block.update({"fetched_at": receipt["captured_at"], "receipt": receipt, "mcp_calls": stream["calls"], "mcp_results": stream["results"]})
    if stream["errors"]:
        block["stream_errors"] = stream["errors"]
    if not stream["calls"]:
        block["note"] = "Otari reported no MCP tool call"
        return block
    verdicts = [code_trusted((c.get("input") or {}).get("code") if isinstance(c.get("input"), dict) else None) for c in stream["calls"]]
    block["trusted"] = all(ok for ok, _ in verdicts)
    prices = prices_from(stream["results"])
    if block["trusted"]:
        block["prices"] = prices
    else:
        block["prices_unverified"] = prices  # kept for the record, never used
        block["untrusted_reason"] = next(why for ok, why in verdicts if not ok)
    return block


def interval_minutes(interval) -> float | None:
    """The length of a candle interval such as 15m, 1h, 4h or 1d in minutes; None when it cannot be read (no guess is made)."""
    found = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(interval or ""), re.I)
    return int(found.group(1)) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[found.group(2).lower()] if found and int(found.group(1)) > 0 else None


def _atr_pct(candles: list[dict]) -> float:
    """The average true range of the last 14 candles as a percent of the last close (the same ATR the features use)."""
    ranges = [c["h"] - c["l"] for c in candles[-14:]]
    return (sum(ranges) / len(ranges)) / candles[-1]["c"] * 100 if ranges and candles[-1]["c"] else 0.0


def check_for(block: dict, packet: dict, limit_pct: float = LIMIT_PCT) -> dict:
    """The comparison of the MCP price of the packet's symbol with the latest price in the packet (its newest candle's close), within
    a tolerance of `limit_pct` plus one candle's ATR per candle interval of that candle's age (the age is in the frozen freshness block, 0
    when it has none; the interval is the packet's market interval, no allowance at all when it cannot be read)."""
    base = packet["symbol"].upper().removesuffix("USDT")
    candles = packet["market"]["candles"]
    close = candles[-1]["c"]
    price = (block.get("prices") or {}).get(base) if block.get("trusted") else None
    out = {"symbol": base, "limit_pct": limit_pct, "exchange_close": close}
    if not price or not close:
        return {**out, "status": "unavailable", "ok": True, "reason": block.get("untrusted_reason") or block.get("error") or block.get("note") or "no price for this symbol"}
    age = (packet.get("freshness") or {}).get("newest_candle_age_minutes")
    age = float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and age > 0 else 0.0
    atr = round(_atr_pct(candles), 4)
    interval = interval_minutes((packet.get("market") or {}).get("interval"))
    tolerance = round(limit_pct + (atr * age / interval if interval else 0.0), 4)
    diff = round(abs(price - close) / close * 100, 4)
    return {**out, "mcp_price": price, "diff_pct": diff, "candle_age_minutes": age, "candle_interval_minutes": interval, "atr_pct": atr, "tolerance_pct": tolerance,
            "status": "ok" if diff <= tolerance else "divergence", "ok": diff <= tolerance}


def legacy_check_for(block: dict, packet: dict, limit_pct: float = LIMIT_PCT) -> dict:
    """The first form of the check (dossier 795 and before): the price against the newest close with a flat limit and no tolerance for
    the candle's age. Kept so those dossiers still replay; new checks carry `tolerance_pct` and use check_for."""
    base = packet["symbol"].upper().removesuffix("USDT")
    close = packet["market"]["candles"][-1]["c"]
    price = (block.get("prices") or {}).get(base) if block.get("trusted") else None
    out = {"symbol": base, "limit_pct": limit_pct, "exchange_close": close}
    if not price or not close:
        return {**out, "status": "unavailable", "ok": True, "reason": block.get("untrusted_reason") or block.get("error") or block.get("note") or "no price for this symbol"}
    diff = round(abs(price - close) / close * 100, 4)
    return {**out, "mcp_price": price, "diff_pct": diff, "status": "ok" if diff <= limit_pct else "mismatch", "ok": diff <= limit_pct}


def apply(packet: dict, block: dict, limit_pct: float = LIMIT_PCT) -> dict:
    """Puts the block and its check into the packet. The check is a freshness class of its own (`classes.divergence`, blocking), next
    to the untouched `classes.market`; when the sources diverge the freshness `ok` turns false, and the verdict refuses to decide (fail
    closed) through the path every verifier already has. A staleness reason that was already there stays the reason."""
    check = check_for(block, packet, limit_pct)
    packet["market_mcp"] = {**block, "check": check}
    if check["status"] == "unavailable" or (check["ok"] and not packet.get("freshness")):
        return check  # nothing to record in a freshness block that does not exist: the check itself is in market_mcp.check
    fresh = dict(packet.get("freshness") or {})
    classes = dict(fresh.get("classes") or {})
    reason = (f"the MCP spot price {check['mcp_price']} and the exchange price {check['exchange_close']} diverge by {check['diff_pct']}%, "
              f"more than the {check['tolerance_pct']}% tolerance")
    classes["divergence"] = {"ok": check["ok"], "blocking": True, "diff_pct": check["diff_pct"], "tolerance_pct": check["tolerance_pct"], "source": "otari_mcp:" + block.get("server", "")}
    if not check["ok"]:
        classes["divergence"]["reason"] = reason
        if not fresh.get("reason"):
            fresh["reason"] = reason  # an existing staleness reason is never overwritten
        fresh["ok"] = False
    fresh["classes"] = classes
    packet["freshness"] = fresh
    return check


def recheck(dossier: dict) -> dict:
    """The replay of the MCP block, recomputed offline from the frozen packet and never trusting a flag that was sealed with it: the
    trust verdict on the code the model sent, the prices read from the server's result block, the cross-check, and the freshness
    class that turns a divergence into a refusal."""
    block = (dossier.get("evidence") or {}).get("market_mcp")
    if not isinstance(block, dict) or not isinstance(block.get("check"), dict):
        return {}
    try:
        calls, results = block.get("mcp_calls") or [], block.get("mcp_results") or []
        trusted = bool(calls) and all(code_trusted((c.get("input") or {}).get("code") if isinstance(c.get("input"), dict) else None)[0] for c in calls)
        if trusted:  # recomputed: the code sent is the one query asked for, and the prices are the ones in the server's own result block
            code_ok = block.get("trusted") is True and block.get("prices") == prices_from(results)
        else:
            code_ok = block.get("trusted") is False and not block.get("prices")
        legacy = "tolerance_pct" not in block["check"] and block["check"].get("status") != "divergence"
        again = (legacy_check_for if legacy else check_for)(block, dossier["evidence"], block["check"].get("limit_pct", LIMIT_PCT))
    except (KeyError, IndexError, TypeError, ValueError):
        return {"market_check_ok": False}
    fresh = (dossier.get("features") or {}).get("freshness") or {}
    cls = (fresh.get("classes") or {}).get("divergence")
    if legacy:  # the first form wrote the refusal into freshness.ok and freshness.reason only
        coherent = again["status"] != "mismatch" or fresh.get("ok") is False
    elif again["status"] == "unavailable":
        coherent = cls is None
    elif again["ok"]:
        coherent = cls is None or cls.get("ok") is True  # no freshness block at all is fine when the sources agree
    else:
        coherent = isinstance(cls, dict) and cls.get("ok") is False and fresh.get("ok") is False
    ok = code_ok and again == block["check"] and coherent
    return {"market_check_ok": ok, "market_check_status": again["status"]}
