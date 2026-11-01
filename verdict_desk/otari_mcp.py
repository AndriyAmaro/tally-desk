"""Spot prices from a public MCP server (CoinGecko, keyless), called by Otari's managed MCP loop.

The app does not talk to the MCP server: it asks a model through Otari's Messages API, with the server declared in `mcp_servers`,
and Otari runs the model, calls the tool and feeds the result back. On a streamed Messages request with the
`mcp-client-2025-11-20` beta Otari also emits what it did, an `mcp_tool_use` block (server, tool, input) and an
`mcp_tool_result` block (the server's own answer). The price is read from THAT block, not from the model's words, and both
blocks are kept as the proof that the call happened. The result goes into the packet as `market_mcp`; the replay reads it from there.
"""

import datetime
import json
import urllib.error
import urllib.request
import uuid

from . import llm

SERVER = {"name": "coingecko", "url": "https://mcp.api.coingecko.com/mcp", "allowed_tools": ["execute"],
          "purpose_hint": "Use the execute tool with TypeScript of the form: async function run(client) { return await client.simple.price.get({ids: 'bitcoin,ethereum,solana', vs_currencies: 'usd'}); }"}
IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
BETA = "mcp-client-2025-11-20"
SYSTEM = "You are a market data clerk. Use the coingecko execute tool exactly once, then reply with the single word: done."
PROMPT = "Get the current USD price of bitcoin, ethereum and solana with one call of the coingecko execute tool."


def parse_stream(lines) -> dict:
    """Reads a Messages SSE stream: the MCP activity blocks (use and result) and the text. Unknown events are ignored."""
    calls, results, text = [], [], []
    for raw in lines:
        raw = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if not raw.startswith("data:"):
            continue
        try:
            event = json.loads(raw[5:].strip())
        except ValueError:
            continue
        block = event.get("content_block") if event.get("type") == "content_block_start" else None
        if isinstance(block, dict) and block.get("type") == "mcp_tool_use":
            calls.append({"id": block.get("id"), "server": block.get("server_name"), "tool": block.get("name"), "input": block.get("input")})
        elif isinstance(block, dict) and block.get("type") == "mcp_tool_result":
            results.append({"tool_use_id": block.get("tool_use_id"), "is_error": bool(block.get("is_error")), "content": block.get("content")})
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text.append(str(delta.get("text", "")))
    return {"calls": calls, "results": results, "text": "".join(text)}


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


def collect(opener=None, usage=None) -> dict:
    """One streamed Messages call through Otari with the MCP server declared; returns the `market_mcp` block of the packet."""
    opener, usage = opener or urllib.request.urlopen, usage or llm.usage_for
    cfg = llm.otari_config()
    role = "mcp"
    model = llm.sent_model(role, cfg)
    end_user = llm._end_user(role, cfg)
    base = llm.otari_base(cfg)
    key = llm._otari_key()
    auth = cfg["auth_header"]
    local_id = uuid.uuid4().hex
    headers = {auth: f"Bearer {key}" if auth.lower() == "authorization" else key, "Content-Type": "application/json",
               "anthropic-version": "2023-06-01", "anthropic-beta": BETA, "User-Agent": llm.OTARI_USER_AGENT,
               "Idempotency-Key": local_id, "Otari-Tags": f"role={role}"}
    budget = str(cfg.get("end_user_budget") or "")
    if end_user and budget:
        headers["Otari-End-User-Budget"] = budget
    payload = {"model": model, "max_tokens": 300, "stream": True, "system": SYSTEM, "max_tool_iterations": 4, "mcp_servers": [SERVER],
               "messages": [{"role": "user", "content": PROMPT}]}
    if end_user:
        payload["user"] = end_user
    block = {"method": "otari_mcp", "server": SERVER["name"], "url": SERVER["url"], "tool": "execute", "fetched_at": None}
    try:
        llm._cycle_check(llm.cycle_cap(cfg), role, model, end_user, local_id)
    except llm.OtariError as err:  # the app's own cap: nothing is sent, the refusal receipt is kept
        return {**block, "error": str(err), "receipt": llm.served_receipt(), "prices": {}}
    request = urllib.request.Request(f"{base}/messages", data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with opener(request, timeout=120) as response:
            reply_headers = getattr(response, "headers", None)
            stream = parse_stream(response)
    except urllib.error.HTTPError as err:
        detail = llm._read_detail(err)
        receipt = llm.refusal_receipt(err.headers, err.code, role, model, end_user, local_id=local_id)
        return {**block, "error": f"HTTP {err.code} {detail[:200].replace(key, '***')}", "receipt": receipt, "prices": {}}
    except (OSError, ValueError) as err:
        return {**block, "error": f"{type(err).__name__} calling the Messages API", "receipt": llm.transport_receipt("timeout" if isinstance(err, TimeoutError) else "connection", role, model, end_user, local_id=local_id), "prices": {}}
    receipt = llm.parse_receipt(reply_headers, None, role, model, end_user, local_id=local_id)
    row = usage(receipt.get("request_id")) if receipt.get("request_id_source") == "otari" else None
    if row and row.get("cost_usd") is not None:  # a stream carries no cost in its headers: the settled row gives it
        receipt["cost_usd"] = str(row["cost_usd"])
        receipt["tokens"] = {"prompt": row.get("prompt_tokens"), "completion": row.get("completion_tokens")}
    llm._cycle_add(receipt)
    block.update({"fetched_at": receipt["captured_at"], "receipt": receipt, "mcp_calls": stream["calls"], "mcp_results": stream["results"],
                  "prices": prices_from(stream["results"])})
    if not stream["calls"]:
        block["note"] = "Otari reported no MCP tool call"
    return block
