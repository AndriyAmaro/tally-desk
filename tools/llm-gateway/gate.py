"""The LLM gateway of the Verdict Desk bot: chat completions only, routed by the published policy, route reported.

It runs on the operator's machine in front of a local FreeLLMAPI (free models) and is reached by the GitHub bot
through a Cloudflare tunnel. Three jobs:

1. Gatekeeper: only POST /v1/chat/completions with `Authorization: Bearer <PROXY_TOKEN>` gets through; the
   FreeLLMAPI dashboard, key store and every other route stay closed, and the FreeLLMAPI key never leaves the machine.
2. Router: the models are tried in the order of config/model_routing.json (computed from the ledger by
   `python -m verdict_desk models`, never by hand). A call moves to the next model only when a model is unavailable
   (404, 413, 429, 502, 503) or answers with no text; a real error is returned as it is. If every listed model is
   down, the FreeLLMAPI router ("auto") is the declared last resort.
3. Witness: every response carries X-Verdict-Route (each attempt: model, HTTP status or "empty", milliseconds) and
   X-Verdict-Policy (sha256 of the policy file used, and its as_of_seq). The bot records both in the dossier.

No secret is in this file: PROXY_TOKEN, FREELLMAPI_KEY and UPSTREAM come from ~/.config/verdict-desk/llm-proxy.env.
"""

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENV = Path.home() / ".config" / "verdict-desk" / "llm-proxy.env"
POLICY_URL = "https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/config/model_routing.json"
MAX_BODY = 400_000
# The bot's client waits 90 s for a whole call: each model gets at most ATTEMPT_TIMEOUT, and no new model is tried
# once BUDGET is spent, so a fallback can still answer before the client gives up.
ATTEMPT_TIMEOUT = 45
BUDGET = 80
MAX_ATTEMPTS = 4
LAST_RESORT = "auto"
FALLBACK_ON = {404, 413, 429, 502, 503}  # model missing, request too large for it, rate-limited, upstream down


def load_env(path: Path = ENV) -> dict:
    lines = path.read_text(encoding="utf-8").split("\n")
    return {k.strip(): v.strip() for k, v in (line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))}


class Policy:
    """The routing order, re-read every `refresh_seconds`; a missing or broken file keeps the last good one."""

    def __init__(self, source: str, refresh_seconds: int = 600):
        self.source, self.refresh = source, refresh_seconds
        self.order: list[str] = []
        self.sha, self.as_of, self.loaded_at = "", None, 0.0
        self.lock = threading.Lock()

    def _read(self) -> bytes:
        if self.source.startswith(("http://", "https://")):
            with urllib.request.urlopen(f"{self.source}?t={int(time.time())}", timeout=15) as resp:
                return resp.read()
        return Path(self.source).read_bytes()

    def current(self) -> tuple[list[str], str, int | None]:
        with self.lock:
            if time.time() - self.loaded_at >= self.refresh or not self.order:
                try:
                    raw = self._read()
                    data = json.loads(raw)
                    order = [m for m in data.get("order", []) if isinstance(m, str) and m]
                    if order:
                        self.order, self.sha, self.as_of = order, hashlib.sha256(raw).hexdigest(), data.get("as_of_seq")
                except (OSError, ValueError, urllib.error.URLError):
                    pass  # keep the last good policy
                self.loaded_at = time.time()
            return list(self.order), self.sha, self.as_of


def has_text(raw: bytes) -> bool:
    """True when a chat completion carries answer text (a string, or a list of parts with text)."""
    try:
        message = (json.loads(raw).get("choices") or [{}])[0].get("message") or {}
    except (ValueError, AttributeError, IndexError):
        return False
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return isinstance(content, str) and bool(content.strip())


def route(payload: dict, order: list[str], forward) -> tuple[int, bytes, str, list[dict]]:
    """Try the models in order; return the response to send back and every attempt made."""
    candidates = order[:MAX_ATTEMPTS] or [payload.get("model") or LAST_RESORT]
    if LAST_RESORT not in candidates:
        candidates.append(LAST_RESORT)
    attempts, last = [], (502, b'{"error":"no model tried"}', "application/json")
    began = time.monotonic()
    for model in candidates:
        if attempts and time.monotonic() - began >= BUDGET:
            break  # the client would give up before another model answered
        started = time.monotonic()
        code, data, ctype = forward(json.dumps({**payload, "model": model}).encode())
        ms = int((time.monotonic() - started) * 1000)
        if code == 200 and has_text(data):
            attempts.append({"model": model, "status": 200, "ms": ms})
            return code, data, ctype, attempts
        attempts.append({"model": model, "status": "empty" if code == 200 else code, "ms": ms})
        last = (code, data, ctype)
        if code != 200 and code not in FALLBACK_ON:
            break  # a real error (bad request, auth): returned as it is, never hidden behind another model
    return (*last, attempts)


class Gate(BaseHTTPRequestHandler):
    server_version = "gate"
    sys_version = ""

    def _reply(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(404, b'{"error":"not found"}')

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_GET

    def do_POST(self):
        settings = self.server.settings
        if self.path.split("?")[0] != "/v1/chat/completions":
            return self._reply(404, b'{"error":"not found"}')
        auth = self.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {settings['PROXY_TOKEN']}".encode()):
            return self._reply(401, b'{"error":"unauthorized"}')
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._reply(400, b'{"error":"bad Content-Length"}')
        if size <= 0 or size > MAX_BODY:
            return self._reply(413, b'{"error":"body too large or empty"}')
        try:
            payload = json.loads(self.rfile.read(size))
        except ValueError:
            return self._reply(400, b'{"error":"body is not JSON"}')
        if not isinstance(payload, dict):
            return self._reply(400, b'{"error":"body is not a JSON object"}')
        order, sha, as_of = self.server.policy.current()
        code, data, ctype, attempts = route(payload, order, self._forward)
        self._reply(code, data, ctype, {"X-Verdict-Route": json.dumps(attempts, separators=(",", ":")),
                                         "X-Verdict-Policy": f"{sha} as_of={as_of}"})

    def _forward(self, body: bytes) -> tuple[int, bytes, str]:
        settings = self.server.settings
        upstream = settings.get("UPSTREAM", "http://127.0.0.1:3001").rstrip("/")
        req = urllib.request.Request(f"{upstream}/v1/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {settings['FREELLMAPI_KEY']}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=ATTEMPT_TIMEOUT) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.read()[:4000], "application/json"
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            return 502, f'{{"error":"upstream unavailable: {type(err).__name__}"}}'.encode(), "application/json"

    def log_message(self, fmt, *args):  # one line per request, never the headers
        print(f"{self.address_string()} {fmt % args}", flush=True)


def make_server(settings: dict, port: int = 3002) -> ThreadingHTTPServer:
    for name in ("PROXY_TOKEN", "FREELLMAPI_KEY"):
        if not str(settings.get(name) or "").strip():
            raise ValueError(f"{name} is not set in {ENV}: refusing to start (an empty token would let anyone in)")
    server = ThreadingHTTPServer(("127.0.0.1", port), Gate)
    server.settings = settings
    server.policy = Policy(settings.get("POLICY_URL") or POLICY_URL)
    return server


if __name__ == "__main__":
    make_server(load_env(), int(os.environ.get("PORT", "3002"))).serve_forever()
