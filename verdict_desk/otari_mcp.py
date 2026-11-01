"""Spot prices from a public MCP server (CoinGecko, keyless), called by Otari's managed MCP loop, and a cross-check against the exchange.

The app does not talk to the MCP server: it asks a model through Otari's Messages API, with the server declared in `mcp_servers`,
and Otari runs the model, calls the tool and feeds the result back. On a streamed Messages request with the
`mcp-client-2025-11-20` beta Otari also emits what it did, an `mcp_tool_use` block (server, tool, input) and an
`mcp_tool_result` block (the server's own answer). The price is read from THAT block, not from the model's words, and both blocks
are kept as the proof that the call happened.

A model writes the TypeScript the server runs, so the code it sent is checked too: it must be the one price query we asked for
(`client.simple.price.get({ids, vs_currencies})`) and contain no digit at all, so no price can be typed into it. Otherwise the
prices are marked untrusted and unused.

The trusted price then takes part in the decision. It is compared with the exchange close of the packet; above the limit (1 percent)
the packet's freshness is marked not ok, which is the fail-closed path the replay, the web verifier and the CRE workflow already know
(`BLOCKED_STALE`, no position, the refusal recorded with the numbers). A missing or untrusted price is recorded as unavailable and
blocks nothing: a free third-party server is not allowed to switch the agent off.
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
LIMIT_PCT = 1.0

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


def check_for(block: dict, packet: dict, limit_pct: float = LIMIT_PCT) -> dict:
    """The comparison of the MCP price of the packet's symbol with the packet's last exchange close."""
    base = packet["symbol"].upper().removesuffix("USDT")
    close = packet["market"]["candles"][-1]["c"]
    price = (block.get("prices") or {}).get(base) if block.get("trusted") else None
    out = {"symbol": base, "limit_pct": limit_pct, "exchange_close": close}
    if not price or not close:
        return {**out, "status": "unavailable", "ok": True, "reason": block.get("untrusted_reason") or block.get("error") or block.get("note") or "no price for this symbol"}
    diff = round(abs(price - close) / close * 100, 4)
    return {**out, "mcp_price": price, "diff_pct": diff, "status": "ok" if diff <= limit_pct else "mismatch", "ok": diff <= limit_pct}


def apply(packet: dict, block: dict, limit_pct: float = LIMIT_PCT) -> dict:
    """Puts the block and its check into the packet. A mismatch above the limit marks the freshness not ok: the verdict then
    refuses to decide (fail closed), through the path every verifier already has."""
    check = check_for(block, packet, limit_pct)
    packet["market_mcp"] = {**block, "check": check}
    if check["status"] == "mismatch":
        packet["freshness"] = {**(packet.get("freshness") or {}), "ok": False,
                               "reason": f"the MCP spot price {check['mcp_price']} differs {check['diff_pct']}% from the exchange close {check['exchange_close']} (limit {limit_pct}%)"}
    return check


def recheck(dossier: dict) -> dict:
    """The replay of the check: recomputed from the frozen packet. A mismatch must also be a refusal in the verdict's freshness."""
    block = (dossier.get("evidence") or {}).get("market_mcp")
    if not isinstance(block, dict) or not isinstance(block.get("check"), dict):
        return {}
    try:
        again = check_for(block, dossier["evidence"], block["check"].get("limit_pct", LIMIT_PCT))
    except (KeyError, IndexError, TypeError):
        return {"market_check_ok": False}
    fresh = (dossier.get("features") or {}).get("freshness") or {}
    refused = not fresh.get("ok", True)
    ok = again == block["check"] and (again["status"] != "mismatch" or refused)
    return {"market_check_ok": ok, "market_check_status": again["status"]}
