"""Cliente de LLM com modo offline para testes. Dois formatos, so biblioteca padrao:

- padrao: API de chat no formato da OpenAI (Mistral, ou qualquer outra via MISTRAL_BASE_URL e MISTRAL_MODEL);
- LLM_PROVIDER=anthropic: Claude pela API de Mensagens nativa (modelo padrao claude-haiku-5-5).

A chave vem so da variavel de ambiente MISTRAL_API_KEY. Sem chave, `available()`
e falso e os agentes LLM nao rodam (o nucleo deterministico segue sozinho).

Otari: quando OTARI_API_KEY esta no ambiente, toda chamada passa pelo gateway Otari (https://otari.ai) e cada
papel (bull, bear, judge, checker, narrator) usa o modelo open-weight de config/otari.json. Cada resposta deixa
um recibo `otari-receipt/1` (request id, provider, modelo servido, tentativas, fallback, duracao, custo) que o
dossie sela. Sem OTARI_API_KEY nada disto roda e o comportamento e o de sempre.

O plano gratuito da Mistral aceita poucas requisicoes por segundo: o cliente espaca
o inicio das chamadas (tambem entre threads, como nos lotes paralelos da narrativa) e
tenta de novo em 429 e 5xx. Nenhuma excecao deste modulo carrega a chave.
"""

import datetime
import decimal
import email.utils
import functools
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def _number(name: str, default, cast, valid):
    """Le um numero do ambiente; vazio, ilegivel ou fora do intervalo valido volta ao padrao."""
    try:
        value = cast(os.environ.get(name) or default)
    except ValueError:
        return default
    return value if valid(value) else default


# LLM_PROVIDER=anthropic talks to Claude through the native Messages API; anything else is an OpenAI-style
# chat API (Mistral by default). The MISTRAL_* names are historical and still win over the provider defaults.
ANTHROPIC = os.environ.get("LLM_PROVIDER", "").strip().lower() == "anthropic"
BASE_URL = os.environ.get("MISTRAL_BASE_URL") or ("https://api.anthropic.com/v1" if ANTHROPIC else "https://api.mistral.ai/v1")
MODEL = os.environ.get("MISTRAL_MODEL") or ("claude-haiku-5-5" if ANTHROPIC else "mistral-small-latest")
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = _number("MISTRAL_MAX_TOKENS", 3000, int, lambda v: v > 0)  # modelos com raciocinio gastam tokens antes da resposta
MIN_INTERVAL = _number("MISTRAL_MIN_INTERVAL", 1.1, float, lambda v: math.isfinite(v) and v >= 0)  # segundos entre inicios de chamada
MAX_RETRIES = 4
RETRY_STATUS = {429, 500, 502, 503, 504, 529}  # 529: the Anthropic API is overloaded
MAX_WAIT = 60.0

_gate = threading.Lock()
_next_start = 0.0
_served = threading.local()  # the model the provider named in its last reply, per thread (parallel batches too)


class LLMError(OSError):
    """Falha da chamada ao LLM. Herda de OSError para cair nos `except OSError` que ja existem."""


OTARI_CONFIG = Path(__file__).resolve().parent.parent / "config" / "otari.json"
RECEIPT_SCHEMA = "otari-receipt/1"
OTARI_USER_AGENT = "verdict-desk/1.0"


class OtariError(LLMError):
    """A refusal or failure reported by the Otari gateway, with the fields the dossier records."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None, scope: str | None = None,
                 retry_after: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status, self.code, self.scope, self.retry_after, self.request_id = status, code, scope, retry_after, request_id


def _otari_key() -> str:
    return os.environ.get("OTARI_API_KEY", "").strip()


def otari_active() -> bool:
    """True when OTARI_API_KEY is set: every LLM call then goes through the Otari gateway."""
    return bool(_otari_key())


def otari_config(path: Path | None = None) -> dict:
    """config/otari.json, validated: a base URL, an auth header and a model id for every role."""
    path = path or OTARI_CONFIG
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise LLMError(f"cannot read the Otari role config {path.name}: {err}") from None
    roles = cfg.get("roles") if isinstance(cfg, dict) else None
    if not isinstance(roles, dict) or not roles or not all(isinstance(r, dict) and isinstance(r.get("model"), str) and r["model"] for r in roles.values()):
        raise LLMError(f"{path.name}: every role needs a model id")
    if cfg.get("default_role") not in roles:
        raise LLMError(f"{path.name}: default_role must name one of the roles")
    if not isinstance(cfg.get("base_url"), str) or not isinstance(cfg.get("auth_header"), str):
        raise LLMError(f"{path.name}: base_url and auth_header are required")
    return cfg


def role_model(role: str | None, cfg: dict | None = None) -> str:
    """The catalog id that plays `role` (the default role when None)."""
    cfg = cfg or otari_config()
    name = role or cfg["default_role"]
    if name not in cfg["roles"]:
        raise LLMError(f"role {name!r} is not in config/otari.json")
    return cfg["roles"][name]["model"]


def _flag(name: str, default) -> bool:
    value = os.environ.get(name, "").strip()
    return value == "1" if value in ("0", "1") else bool(default)


def sent_model(role: str | None, cfg: dict | None = None) -> str:
    """The `model` field of a call: a routing policy name when one is set for the role, else the role's catalog id.

    A policy (created once in the Otari dashboard, for example `vd-judge` with backups) is what gives a role a fallback
    chain of our choosing; the bare catalog id keeps Otari's automatic fallback across offerings of the same model.
    `OTARI_POLICY_<ROLE>` names one for a single role (a drill); `use_policies` in the config (or OTARI_USE_POLICIES=1)
    turns on every entry of `policies`.
    """
    cfg = cfg or otari_config()
    name = role or cfg["default_role"]
    chosen = os.environ.get(f"OTARI_POLICY_{name.upper()}", "").strip()
    if not chosen and _flag("OTARI_USE_POLICIES", cfg.get("use_policies")):
        chosen = str((cfg.get("policies") or {}).get(name) or "").strip()
    if chosen and (not chosen.isprintable() or " " in chosen):
        raise LLMError(f"the routing policy name for role {name!r} has spaces or control characters")
    return chosen or role_model(name, cfg)


def _end_user(role: str, cfg: dict) -> str | None:
    """The `user` field: `end_user_prefix` plus the role name (`vd-judge`) once send_user is on (a service key),
    or OTARI_USER_<ROLE> for a drill user. The id is stable: each distinct value creates an end user."""
    chosen = os.environ.get(f"OTARI_USER_{role.upper()}", "").strip()
    if chosen:
        return chosen[:256]
    return f"{cfg.get('end_user_prefix') or ''}{role}"[:256] if _send_user(cfg) else None


_cycle_lock = threading.Lock()
_cycle = {"spent": decimal.Decimal(0), "calls": 0, "refused": 0, "unpriced": 0}


def reset_cycle() -> None:
    """Starts a decision cycle: the spend counted against the app's own cycle cap goes back to zero."""
    with _cycle_lock:
        _cycle.update(spent=decimal.Decimal(0), calls=0, refused=0, unpriced=0)


def cycle_cap(cfg: dict | None = None) -> decimal.Decimal | None:
    """The most one cycle may spend through Otari, in USD: OTARI_CYCLE_CAP_USD, else `cycle_cap_usd` in the config; none if 0 or unset."""
    raw = os.environ.get("OTARI_CYCLE_CAP_USD", "").strip() or str((cfg or otari_config()).get("cycle_cap_usd") or "")
    try:
        cap = decimal.Decimal(raw)
    except decimal.InvalidOperation:
        return None
    return cap if cap.is_finite() and cap > 0 else None


def cycle_status() -> dict | None:
    """What this cycle has spent through Otari, for the dossier: the cap, the settled spend, calls, calls the cap refused."""
    if not otari_active():
        return None
    cap = cycle_cap()
    with _cycle_lock:
        return {"cap_usd": None if cap is None else format(cap, "f"), "spent_usd": f"{_cycle['spent']:.6f}", "calls": _cycle["calls"],
                "refused": _cycle["refused"], "unpriced_calls": _cycle["unpriced"]}


def _cycle_check(cap, role: str, sent: str, end_user: str | None) -> None:
    """The app's own second line behind Otari's budgets: once the cycle has spent its cap, no further call is sent."""
    if cap is None:
        return
    with _cycle_lock:
        spent = _cycle["spent"]
        over = spent >= cap
        if over:
            _cycle["refused"] += 1
    if over:
        _served.receipt = {"schema": RECEIPT_SCHEMA, "role": role, "request_id": None, "sent_model": sent, "end_user": end_user,
                           "refusal": {"status": None, "code": "cycle_cap_exceeded", "budget_scope": "cycle", "retry_after": None, "source": "client"},
                           "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        raise OtariError(f"cycle cap reached: ${spent:.6f} spent of ${cap} for this decision cycle; no call was sent for role {role}",
                         code="cycle_cap_exceeded", scope="cycle")


def _cycle_add(receipt: dict) -> None:
    with _cycle_lock:
        _cycle["calls"] += 1
        try:
            _cycle["spent"] += decimal.Decimal(str(receipt.get("cost_usd")))
        except (decimal.InvalidOperation, TypeError):
            _cycle["unpriced"] += 1


def otari_base(cfg: dict | None = None) -> str:
    """The gateway base URL: OTARI_BASE_URL or the config value, https only (the key travels in a header), no trailing slash."""
    cfg = cfg or otari_config()
    base = (os.environ.get("OTARI_BASE_URL") or cfg["base_url"]).strip().rstrip("/")
    if not base.lower().startswith("https://"):
        raise LLMError("the Otari base URL must start with https:// (the key is sent in a header); check OTARI_BASE_URL and config/otari.json")
    return base


def _otari_interval(cfg: dict) -> float:
    """Seconds between call starts in Otari mode: OTARI_MIN_INTERVAL, else the config (0: a burst of 12 parallel calls got no 429)."""
    return _number("OTARI_MIN_INTERVAL", cfg.get("min_interval", 0.0), float, lambda v: math.isfinite(v) and v >= 0)


def otari_summary() -> dict | None:
    """What the dossier records about the run: the base URL, the sha256 of the role config and the model of each role."""
    if not otari_active():
        return None
    cfg = otari_config()
    return {"schema": "otari-run/1", "base_url": otari_base(cfg),
            "config_sha256": hashlib.sha256(OTARI_CONFIG.read_bytes()).hexdigest(),
            "roles": {name: spec["model"] for name, spec in cfg["roles"].items()},
            "sent": {name: sent_model(name, cfg) for name in cfg["roles"]},
            "cycle_cap_usd": None if cycle_cap(cfg) is None else format(cycle_cap(cfg), "f")}


def model_name() -> str:
    """The name written as `llm.model` in the dossier: the configured model, or a label for the multi-role Otari run."""
    return "otari:multi-role" if otari_active() else MODEL


def _send_user(cfg: dict) -> bool:
    flag = os.environ.get("OTARI_SEND_USER", "").strip()
    return flag == "1" if flag in ("0", "1") else bool(cfg.get("send_user"))


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_receipt(headers, body: dict | None, role: str, sent_model: str, end_user: str | None = None,
                  client_attempts: int = 1, now: str | None = None) -> dict:
    """The `otari-receipt/1` of one successful call, read from the response headers and the usage block of the body.

    `headers` is any mapping (names are matched case-insensitively). `usage.cost_usd` is kept as the string Otari sent.
    Nothing here can hold the key: only headers named otari-* and the body's usage block are read.
    """
    h = {str(k).lower(): v for k, v in (headers.items() if headers is not None else [])}
    usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
    provider, served_model = h.get("otari-provider"), h.get("otari-model")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "role": role,
        "request_id": h.get("otari-request-id"),
        "sent_model": sent_model,
        "provider": provider,
        "served_model": served_model,
        "served_by": f"{provider}:{served_model}" if provider and served_model else None,
        "attempt_count": _int(h.get("otari-attempt-count")),
        "fallback": str(h.get("otari-fallback", "")).strip().lower() == "true",
        "duration_ms": _int(h.get("otari-response-duration-ms")),
        "tokens": {"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens")},
        "cost_usd": None if usage.get("cost_usd") is None else str(usage["cost_usd"]),
        "pricing_source": usage.get("pricing_source"),
        "end_user": end_user,
        "captured_at": now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if client_attempts > 1:  # our own retries (for example after a gateway timeout), so the count is not hidden
        receipt["client_attempts"] = client_attempts
    raw_guard = h.get("otari-guardrails")
    if raw_guard is not None:
        try:
            parsed = json.loads(raw_guard)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(g, dict) for g in parsed):
            receipt["guardrails"] = parsed  # [{"profile","mode","valid","score"}]: valid and score are null when nothing evaluated the input
    for name, field in (("otari-guardrails", "guardrails_raw"), ("otari-end-user-budget", "end_user_budget"),
                        ("otari-idempotent-replayed", "replayed")):
        if h.get(name) is not None:
            receipt[field] = h[name]
    return receipt


def refusal_receipt(headers, status: int, role: str, sent_model: str, end_user: str | None = None, now: str | None = None) -> dict:
    """The receipt of a call Otari refused or failed: its error code, the budget scope that refused and Retry-After."""
    h = {str(k).lower(): v for k, v in (headers.items() if headers is not None else [])}
    return {
        "schema": RECEIPT_SCHEMA, "role": role, "request_id": h.get("otari-request-id"), "sent_model": sent_model,
        "end_user": end_user,
        # a 4xx is a refusal (budget, credit, model not allowed); a 5xx is a failure, not a refusal
        ("refusal" if status < 500 else "error"): {"status": status, "code": h.get("otari-error-code"),
                                                   "budget_scope": h.get("otari-budget-scope"), "retry_after": h.get("retry-after")},
        "captured_at": now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def transport_receipt(kind: str, role: str, sent_model: str, end_user: str | None = None, now: str | None = None) -> dict:
    """The receipt of a call that got no HTTP answer at all (client timeout, connection failure): only what we know."""
    return {"schema": RECEIPT_SCHEMA, "role": role, "request_id": None, "sent_model": sent_model, "end_user": end_user,
            "error": {"kind": kind},
            "captured_at": now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def usage_for(request_id: str | None, timeout: int = 20, tries: int = 4) -> dict | None:
    """GET /usage/requests/{id} with the inference key: cost, tokens, status and `row_count` (a fallback leaves an absorbed
    row besides the final one). It answers 404 until the background writer has settled the request, so it asks a few times."""
    if not request_id or not otari_active():
        return None
    cfg = otari_config()
    key = _otari_key()
    auth = cfg["auth_header"]
    headers = {auth: f"Bearer {key}" if auth.lower() == "authorization" else key, "User-Agent": OTARI_USER_AGENT}
    for attempt in range(tries):
        try:
            request = urllib.request.Request(f"{otari_base(cfg)}/usage/requests/{urllib.parse.quote(request_id)}", headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
        except urllib.error.HTTPError as err:
            err.close()
            if err.code != 404:
                return None
        except (OSError, ValueError):
            return None
        if attempt < tries - 1:
            _pause(3.0)
    return None


def end_user_info(external_id: str, timeout: int = 20) -> dict | None:
    """GET /end-users?external_id=...: what a service key reads about one end user (budget id, spend, tokens, requests, reset)."""
    if not otari_active():
        return None
    cfg = otari_config()
    key = _otari_key()
    auth = cfg["auth_header"]
    headers = {auth: f"Bearer {key}" if auth.lower() == "authorization" else key, "User-Agent": OTARI_USER_AGENT}
    try:
        request = urllib.request.Request(f"{otari_base(cfg)}/end-users?external_id={urllib.parse.quote(external_id, safe='')}", headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        err.close()
        return None
    except (OSError, ValueError):
        return None
    rows = data if isinstance(data, list) else [data]
    row = next((r for r in rows if isinstance(r, dict) and r.get("external_id") == external_id), None)
    keep = ("external_id", "budget_id", "blocked", "spend", "reserved", "current_tokens", "current_requests", "budget_started_at", "next_budget_reset_at")
    return {k: row.get(k) for k in keep} if row else None


def served_receipt() -> dict | None:
    """The Otari receipt of the last call made on this thread, read once. None outside Otari mode."""
    receipt = getattr(_served, "receipt", None)
    _served.receipt = None
    return receipt


def _otari_chat(system: str, user: str, timeout: int, max_tokens: int | None, role: str | None, model: str | None = None,
                end_user: str | None = None, end_user_budget: str | None = None) -> str:
    cfg = otari_config()
    role = role or cfg["default_role"]
    model = model or sent_model(role, cfg)  # the policy name when one is set for this role, else the catalog id
    key = _otari_key()
    if not key.isprintable() or " " in key:
        raise LLMError("OTARI_API_KEY has spaces or control characters; paste only the key")
    spec = cfg["roles"][role]
    base = otari_base(cfg)
    interval = _otari_interval(cfg)
    end_user = end_user or _end_user(role, cfg)
    tokens = _number(f"OTARI_MAX_TOKENS_{role.upper()}", 0, int, lambda v: v >= 0) or max_tokens or spec.get("max_tokens") or MAX_TOKENS
    payload = {"model": model, "temperature": 0, "max_tokens": tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if end_user:
        payload["user"] = end_user  # per-user budgets bill this id; only a service key may send it
    if spec.get("reasoning_effort"):
        payload["reasoning_effort"] = spec["reasoning_effort"]
    auth = cfg["auth_header"]
    headers = {auth: f"Bearer {key}" if auth.lower() == "authorization" else key,
               "Content-Type": "application/json", "User-Agent": OTARI_USER_AGENT, "Otari-Tags": f"role={role}",
               "Idempotency-Key": uuid.uuid4().hex}  # one per logical call, so a retry after a lost reply is not billed twice
    budget = (end_user_budget or os.environ.get(f"OTARI_END_USER_BUDGET_{role.upper()}", "").strip()
              or str(spec.get("end_user_budget") or cfg.get("end_user_budget") or ""))
    if end_user and budget:
        headers["Otari-End-User-Budget"] = budget  # applies only when this call creates the end user
    body = json.dumps(payload).encode("utf-8")
    _served.model = None
    _served.route = None
    _served.receipt = None
    _cycle_check(cycle_cap(cfg), role, model, end_user)
    for attempt in range(MAX_RETRIES + 1):
        _wait_turn(interval)
        _served.receipt = None  # each try starts clean: what is kept is the outcome of the LAST try, never an earlier one
        request = urllib.request.Request(f"{base}/chat/completions", data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                reply_headers = getattr(response, "headers", None)
            data = json.loads(raw.decode("utf-8"))
            receipt = parse_receipt(reply_headers, data, role, model, end_user, client_attempts=attempt + 1)
            _served.receipt = receipt
            _cycle_add(receipt)
            _served.model = receipt["served_by"] or (data.get("model") if isinstance(data, dict) else None)
            # No `route` is sealed in Otari mode: Otari sends counts (attempt_count, fallback), not the list of attempts,
            # and a list built from the receipt would look like routing data the gateway never gave. The receipt is the record.
            choice = _first_choice(raw, "the Otari gateway")
            break
        except urllib.error.HTTPError as err:
            detail = _read_detail(err)
            _served.receipt = refusal_receipt(err.headers, err.code, role, model, end_user)
            delay = _retry_delay(err, attempt) if err.code in RETRY_STATUS and attempt < MAX_RETRIES else None
            if delay is None and err.code == 409 and "in progress" in detail and attempt < MAX_RETRIES:
                # Our first try timed out at the edge (504) while Otari kept working: the same Idempotency-Key answers 409
                # "still in progress" until it finishes, and then replays the stored reply. Wait and ask again.
                delay = 5.0 * (attempt + 1)
            if delay is None:
                raise _otari_error(err, key, detail, base, model) from err
            _pause(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:  # no HTTP answer: record what happened, never a stale status
            reason = getattr(err, "reason", err)
            kind = "timeout" if isinstance(reason, TimeoutError) else "connection"
            _served.receipt = transport_receipt(kind, role, model, end_user)
            raise OtariError(f"{kind} calling {base} model {model} ({type(reason).__name__})") from err
    message = choice.get("message")
    content = message_text(message.get("content") if isinstance(message, dict) else None)
    if not content:
        raise ValueError(f"model returned no content (finish_reason={choice.get('finish_reason')}); raise max_tokens for the {role} role")
    return content


def _otari_error(err: urllib.error.HTTPError, key: str, detail: str, base: str, model: str) -> OtariError:
    h = err.headers if err.headers is not None else {}
    detail = detail.replace(key, "***")[:300].replace("\n", " ").strip()
    code, scope = h.get("Otari-Error-Code"), h.get("Otari-Budget-Scope")
    extra = "".join(f" {name}={value}" for name, value in (("code", code), ("budget_scope", scope)) if value)
    return OtariError(f"HTTP {err.code} {err.reason} from {base} model {model}{extra}: {detail or 'no body'}",
                      status=err.code, code=code, scope=scope, retry_after=h.get("Retry-After"), request_id=h.get("Otari-Request-ID"))


def served_by() -> str | None:
    """The model the provider said answered the last call made on this thread, read once.

    A router such as FreeLLMAPI's "auto" picks a different model per call, so the configured name says little;
    the dossier records this one next to each analyst, the judge and the narrative batches. None if unnamed.
    """
    model = getattr(_served, "model", None)
    _served.model = None
    return model


def served_route() -> dict | None:
    """The route the LLM gateway reported for the last call on this thread, read once: every model it tried (HTTP
    status or "empty", milliseconds) and the routing policy it followed (sha256 of config/model_routing.json and its
    as_of_seq). None when the reply did not come through the gateway (tools/llm-gateway/gate.py)."""
    route = getattr(_served, "route", None)
    _served.route = None
    return route


def _remember_route(headers) -> None:
    route = None
    try:
        attempts = json.loads(headers.get("X-Verdict-Route") or "null") if headers is not None else None
        sha, _, as_of = (headers.get("X-Verdict-Policy") or "").partition(" as_of=")
        if isinstance(attempts, list) and attempts and all(isinstance(a, dict) and "model" in a for a in attempts):
            # no policy loaded on the gateway (it then falls back to its own router): the attempts still matter
            policy = {"sha256": sha, "as_of_seq": int(as_of)} if len(sha) == 64 and as_of.isdigit() else None
            route = {"attempts": attempts, "policy": policy}
    except (ValueError, AttributeError, TypeError):
        route = None
    _served.route = route


def _remember_model(raw: bytes) -> None:
    try:
        model = json.loads(raw.decode("utf-8")).get("model")
    except (ValueError, AttributeError, UnicodeDecodeError):
        model = None
    _served.model = model if isinstance(model, str) and model else None


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _key() -> str:
    return os.environ.get("MISTRAL_API_KEY", "").strip()


def _anthropic() -> bool:
    return os.environ.get("LLM_PROVIDER", "").strip().lower() == "anthropic"


def _keyless() -> bool:
    """LLM_KEYLESS=1: the provider needs no key (a free anonymous tier).

    In this mode no Authorization header is sent at all, even when MISTRAL_API_KEY holds a key for
    another provider: a credential is never handed to a host it does not belong to.
    """
    return os.environ.get("LLM_KEYLESS", "").strip() == "1"


def available() -> bool:
    return bool(_key()) or _keyless() or otari_active()


def message_text(content) -> str:
    """`content` vem como texto ou, em modelos com raciocinio, como lista de blocos; so o texto final conta."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _wait_turn(interval: float | None = None) -> None:
    """Reserva o proximo horario de inicio de chamada; a reserva vale entre threads."""
    global _next_start
    with _gate:
        now = time.monotonic()
        start = max(now, _next_start)
        _next_start = start + (MIN_INTERVAL if interval is None else interval)
    if start > now:
        _pause(start - now)


def _retry_delay(err: urllib.error.HTTPError, attempt: int) -> float | None:
    """Segundos ate a proxima tentativa, ou None quando o servidor pede mais que MAX_WAIT.

    `Retry-After` vale em segundos ou como data HTTP. Tentar antes do pedido so gastaria
    tentativas contra o mesmo limite, e esperar mais que MAX_WAIT seguraria a rodada horaria:
    nesse caso o erro e registrado na hora. Sem cabecalho utilizavel, recuo exponencial.
    """
    value = (err.headers or {}).get("Retry-After")
    wait = None
    if value is not None:
        try:
            wait = float(value)
        except (TypeError, ValueError):
            try:
                wait = (email.utils.parsedate_to_datetime(value) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                wait = None
    if wait is None or math.isnan(wait):
        return min(2.0 ** (attempt + 1), MAX_WAIT)
    wait = max(0.0, wait)
    return wait if wait <= MAX_WAIT else None


def _read_detail(err: urllib.error.HTTPError) -> str:
    try:
        detail = err.read(600).decode("utf-8", "replace")
        err.close()
        return detail
    except Exception:  # corpo ausente ou ja consumido: o status basta
        return ""


def _free_limit_delay(err: urllib.error.HTTPError, detail: str) -> float | None:
    """A free anonymous tier answers HTTP 402 FREE_LIMIT with "Retry after Ns": wait that long, once it fits MAX_WAIT."""
    if err.code != 402 or "FREE_LIMIT" not in detail:
        return None
    found = re.search(r"[Rr]etry after (\d+)\s*s", detail)
    wait = float(found.group(1)) + 1.0 if found else 31.0
    return wait if wait <= MAX_WAIT else None


def _http_error(err: urllib.error.HTTPError, key: str, detail: str | None = None) -> LLMError:
    # O erro vai para o dossie: inclui o corpo da resposta (curto, sem a chave) para a causa aparecer no historico.
    if detail is None:
        detail = _read_detail(err)
    if key:  # an empty key would be "redacted" into every gap of the text
        detail = detail.replace(key, "***")
    detail = detail[:300].replace("\n", " ").strip()
    return LLMError(f"HTTP {err.code} {err.reason} from {BASE_URL} model {MODEL}: {detail or 'no body'}")


def _first_choice(raw: bytes, source: str = "the Mistral API") -> dict:
    try:
        choice = json.loads(raw.decode("utf-8"))["choices"][0]
    except (KeyError, IndexError, TypeError):
        choice = None
    if not isinstance(choice, dict):
        raise ValueError(f"unexpected response shape from {source}")
    return choice


def _anthropic_text(raw: bytes) -> str:
    """The answer of a Messages API response: its text blocks, joined. Thinking blocks are not the answer."""
    try:
        data = json.loads(raw.decode("utf-8"))
        blocks = data["content"]
    except (KeyError, TypeError, ValueError):
        raise ValueError("unexpected response shape from the Anthropic API") from None
    text = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    if text:
        return text
    stop = data.get("stop_reason")
    if stop == "refusal":
        category = (data.get("stop_details") or {}).get("category")
        raise ValueError(f"the model refused this request (stop_reason=refusal, category={category})")
    raise ValueError(f"model returned no text (stop_reason={stop}); raise MISTRAL_MAX_TOKENS" if stop == "max_tokens" else f"model returned no text (stop_reason={stop})")


def chat(system: str, user: str, timeout: int = 90, max_tokens: int | None = None, role: str | None = None,
         model: str | None = None, end_user: str | None = None, end_user_budget: str | None = None) -> str:
    """One chat call. `role` picks the model in Otari mode (OTARI_API_KEY set) and is ignored otherwise; `model`,
    `end_user` and `end_user_budget` override the role's choice for one call (the drills use them)."""
    if otari_active():
        return _otari_chat(system, user, timeout, max_tokens, role, model, end_user, end_user_budget)
    keyless = _keyless()
    key = "" if keyless else _key()
    if not keyless:
        if not key:
            raise LLMError("MISTRAL_API_KEY is not set")
        if not key.isprintable() or " " in key:
            # Nao ecoa o valor: um cabecalho invalido faz o urllib imprimir a chave no erro.
            raise LLMError("MISTRAL_API_KEY has spaces or control characters; paste only the key")
    native = _anthropic()
    if native:
        # Claude Haiku 5.5 rejects non-default sampling values with a 400, so no temperature; thinking is off so no
        # token is spent before the answer. The replay never calls the model: the text is frozen in the dossier.
        payload = {"model": MODEL, "max_tokens": max_tokens or MAX_TOKENS, "thinking": {"type": "disabled"},
                   "system": system, "messages": [{"role": "user", "content": user}]}
        url = f"{BASE_URL}/messages"
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "Content-Type": "application/json"}
    else:
        payload = {"model": MODEL, "temperature": 0, "max_tokens": max_tokens or MAX_TOKENS,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        url = f"{BASE_URL}/chat/completions"
        headers = {**({"Authorization": f"Bearer {key}"} if key else {}), "Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    _served.model = None
    _served.route = None
    for attempt in range(MAX_RETRIES + 1):
        _wait_turn()
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                _remember_route(getattr(response, "headers", None))
            _remember_model(raw)
            if native:
                return _anthropic_text(raw)
            choice = _first_choice(raw)
            break
        except urllib.error.HTTPError as err:
            _remember_route(err.headers)  # a failed call still shows which models the gateway tried
            detail = _read_detail(err)
            delay = _retry_delay(err, attempt) if err.code in RETRY_STATUS and attempt < MAX_RETRIES else None
            if delay is None and keyless and attempt < MAX_RETRIES:
                delay = _free_limit_delay(err, detail)
            if delay is None:
                raise _http_error(err, key, detail) from err
            _pause(delay)
    message = choice.get("message")
    content = message_text(message.get("content") if isinstance(message, dict) else None)
    if not content:
        raise ValueError(f"model returned no content (finish_reason={choice.get('finish_reason')}); raise MISTRAL_MAX_TOKENS")
    return content


def parse_claims(text: str) -> list[dict]:
    """Extrai a lista JSON de afirmacoes da resposta; texto invalido vira lista vazia."""
    if not isinstance(text, str):
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    return [c for c in data if isinstance(c, dict)]


def parse_ruling(text: str) -> dict:
    """Extrai o objeto JSON do veredito do juiz; texto invalido vira dict vazio (o validador anula)."""
    if not isinstance(text, str):
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _for_role(ask, role: str | None):
    """The default `chat` is bound to a role (which model plays it in Otari mode); an injected stub is left alone."""
    return functools.partial(chat, role=role) if ask is chat else ask


def rule(labels: dict[str, dict], evidence_items: dict[str, str], ask=chat) -> dict:
    """Pede ao juiz LLM um lado, lendo SO as afirmacoes validadas (rotulos B1.., S1..)."""
    ask = _for_role(ask, "judge")
    system = (
        "You are the judge in a market debate. You see only the analyst claims that passed a citation check: "
        "bull claims are labelled B1, B2, ... and bear claims S1, S2, .... Decide which side the evidence favors "
        "for the next few hours, or say neutral when the cases balance. "
        'Reply ONLY with a JSON object {"lean": "bull" | "bear" | "neutral", "conviction": "low" | "medium" | "high", '
        '"relied_on": [claim labels], "reason": str}. '
        "relied_on lists the claim labels your ruling rests on; a bull or bear lean must rest on at least one claim "
        "of that side. Use only the labels shown. Do not invent numbers: any number in reason must be copied exactly "
        "from the evidence of the claims you rely on. Keep reason to one or two sentences."
    )
    lines = []
    for label, claim in labels.items():
        cites = [c for c in claim.get("cites") or [] if isinstance(c, str)]
        lines.append(f"{label}: {claim.get('claim', '')}")
        lines.extend(f"    {c}: {evidence_items[c]}" for c in cites if c in evidence_items)
    return parse_ruling(ask(system, "\n".join(lines)))


STANCE = {
    "bull": "the strongest honest case for opening a LONG position now",
    "bear": "the strongest honest case against a long entry, or for a SHORT position",
}


def argue(side: str, evidence_items: dict[str, str], ask=chat) -> list[dict]:
    """Pede ao LLM argumentos de um lado, citando ids. `ask` permite injetar um stub."""
    ask = _for_role(ask, side if side in ("bull", "bear") else None)
    system = (
        f"You are the {side} analyst in a market debate. Make {STANCE.get(side, 'your side of the case')}. "
        "Pick the evidence items that best support your side and say what each one implies for the next move. "
        "Be honest, but go through every item before giving up on your side: return an empty list only if no item can honestly be read in favor of it. "
        'Reply ONLY with a JSON list of objects {"claim": str, "cites": [evidence ids]}. '
        "Every evidence item starts with its id (E1, E2, ...) before the colon. You MUST cite items by those ids, "
        'written as strings exactly as listed: a claim that rests on items E3 and E14 has "cites": ["E3", "E14"]. '
        "A bare number such as 14 is not an id: a claim that does not cite by id is discarded. "
        "Write the ids only in cites, never inside the claim text. "
        "Rules: cite at least one evidence id per claim; every number in a claim must appear "
        "character-for-character in the cited evidence items — copy the exact string from the "
        "evidence item, NEVER round or reformat. If the evidence says '3.1596' you must write '3.1596', "
        "NOT '3.16'. If it says '4.53e-05' you must write '4.53e-05', NOT '0.00453%'. If it says "
        "'-19.79%' you must write '-19.79%', NOT '-19.8%'. Scientific notation, sign, decimal places, "
        "trailing zeros — all must be copied verbatim. A claim with a rounded or reformatted number "
        "will be DISCARDED by the validator. "
        "Examples — ACCEPTED claim: {\"claim\": \"Volume spiked to 125.3% of the 24h average, confirming breakout\", \"cites\": [\"E3\"]} (where E3 says 'volume ratio: 125.3%'). "
        "Examples — ACCEPTED claim with two items: {\"claim\": \"Bullish setup\", \"cites\": [\"E3\", \"E14\"]} (E3 and E14 both support it). "
        "Examples — REJECTED claim: {\"claim\": \"Volume spiked to 125% of average\", \"cites\": [\"E3\"]} (125 != 125.3, rounded). "
        "Examples — REJECTED claim: {\"claim\": \"Funding is slightly positive at 0.005%\", \"cites\": [\"E7\"]} (0.005 != 4.53e-05, reformatted). "
        "NEVER invent horizons, thresholds or percentages; "
        "at most 4 claims; the two sides pick different facts because each side favors different ones."
    )
    user = "\n".join(f"{k}: {v}" for k, v in evidence_items.items())
    return parse_claims(ask(system, user))


def use_checker(ask=None) -> bool:
    """The citation checker runs only on the real Otari path: an injected `ask` (tests, replays) never gets a third call."""
    return ask is None and otari_active()


def check_claims(labels: dict[str, dict], evidence_items: dict[str, str], ask=chat) -> dict:
    """Asks the checker model, one call, whether each claim that passed the validator is really supported by the
    evidence it cites. The answer is advisory: it never changes the verdict, so the replay does not depend on it."""
    ask = _for_role(ask, "checker")
    system = (
        "You are a citation checker. For each labelled claim you are shown the evidence items it cites. "
        "Decide whether the cited evidence really supports the claim (supported: true) or whether the claim "
        "overstates it, reverses it or goes beyond it (supported: false). Do not judge whether the claim is a good trade. "
        'Reply ONLY with a JSON list of objects {"label": claim label, "supported": true | false, "note": str}, '
        "one object per claim, using only the labels shown. Keep each note to one short sentence."
    )
    lines = []
    for label, claim in labels.items():
        cites = [c for c in claim.get("cites") or [] if isinstance(c, str)]
        lines.append(f"{label}: {claim.get('claim', '')}")
        lines.extend(f"    {c}: {evidence_items[c]}" for c in cites if c in evidence_items)
    checks, seen = [], set()
    for entry in parse_claims(ask(system, "\n".join(lines))):
        label = entry.get("label")
        if label in labels and label not in seen and isinstance(entry.get("supported"), bool):
            seen.add(label)
            checks.append({"label": label, "supported": entry["supported"], "note": str(entry.get("note", ""))[:200]})
    return {"checks": checks, "checked": len(checks), "supported": sum(1 for c in checks if c["supported"]), "claims": len(labels)}
