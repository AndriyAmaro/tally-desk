"""Bring your own agent: seal the Otari calls of any workload into a hash-chained `receipts/1` record.

    with Session("my-agent", {"question": q}, ledger="my-ledger.jsonl", config="my-roles.json") as s:
        text = s.ask("reader", SYSTEM, user_text)      # llm.chat: role config, receipt, end user, fallbacks, cycle caps
        s.output("answer", text)
        result = s.seal()                              # result.sealed, result.record, result.reasons

`ask` goes through the same client as the trading agent and keeps the receipt of every call, failed ones too. `seal` builds one record of
kind `receipts/1` (workload name, the frozen input and its sha256, the outputs, the receipts, the Otari run summary, the cycle spend), runs
the generic closure checks below, and only if every hard check passes appends it to the ledger the caller named, in the chain format
`verify` reads. If a hard check fails nothing is appended and `result.reasons` says why.

The generic closure checks know nothing about trading. Hard (the record is not sealed if one fails):
  calls_accounted   every call has a receipt with an Otari request id, or a recorded failure
  receipts_schema   every receipt has the fields of `otari-receipt/1`
  input_frozen      the sha256 of the recorded input is the one taken when the session opened
  priced_within_cap no call is unpriced while a cap is in force, and no pool spent more than its cap
Soft (sealed, with the warning in the closure): the record holds at least one call, and the calls were live (not a dry run).

There is no rule set for a foreign workload, so `replay` re-checks the record, its receipts schema and the closure, and says so; it
cannot re-derive the workload's answer. The ledger is yours: this module refuses the public trading ledger and any ledger that holds
anything but `receipts/1` records. One session at a time per process (the client keeps its call state per thread, its config per module).
"""

import datetime
import json
import os
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from email.message import Message
from pathlib import Path

from . import dossier, llm
from .closure import HARD, SOFT, _accounted

KIND = dossier.RECEIPTS_KIND
VERSION = 1
PUBLIC_LEDGER = Path(__file__).resolve().parent.parent / "ledger" / "ledger.jsonl"
DRY_BASE = "https://dry-run.invalid/api/v1"
RECEIPT_FIELDS = ("schema", "role", "request_id", "request_id_source", "sent_model", "captured_at")
RECORD_FIELDS = ("seq", "prev_hash", "kind", "workload", "input", "input_sha256", "outputs", "calls", "otari", "cycle", "sealed_at", "closure", "hash")
_busy = {"session": None}


# ---------------------------------------------------------------- the checks: each returns (passed, one-line reason)

def receipt_problem(receipt) -> str | None:
    """Why `receipt` is not an `otari-receipt/1`, or None. A successful call also carries its cost and tokens (None when unpriced)."""
    if not isinstance(receipt, dict):
        return "no receipt"
    missing = [f for f in RECEIPT_FIELDS if not isinstance(receipt.get(f), str) or not receipt[f]]
    if missing:
        return "missing " + ", ".join(missing)
    if receipt["schema"] != llm.RECEIPT_SCHEMA:
        return f"schema {receipt['schema']!r}"
    if receipt["request_id_source"] not in ("otari", "client"):
        return "unknown request_id_source"
    if not (receipt.get("refusal") or receipt.get("error")) and ("cost_usd" not in receipt or not isinstance(receipt.get("tokens"), dict)):
        return "a successful call without cost_usd and tokens"
    return None


def _calls(body: dict) -> list[dict]:
    calls = body.get("calls")
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def check_accounted(body: dict, frozen) -> tuple[bool, str]:
    calls = _calls(body)
    bad = [str(c.get("label", "?")) for c in calls if not _accounted(c.get("receipt"))]
    if bad:
        return False, f"{len(bad)} of {len(calls)} calls have no receipt with an Otari request id or a recorded failure: {', '.join(bad[:4])}"
    return True, f"{len(calls)} calls, each with an Otari request id or a recorded failure"


def check_schema(body: dict, frozen) -> tuple[bool, str]:
    bad = [f"{c.get('label', '?')} ({receipt_problem(c.get('receipt'))})" for c in _calls(body) if receipt_problem(c.get("receipt"))]
    if bad:
        return False, "receipts that are not otari-receipt/1: " + ", ".join(bad[:4])
    return True, f"{len(_calls(body))} receipts have the otari-receipt/1 fields"


def check_input(body: dict, frozen) -> tuple[bool, str]:
    if dossier.digest(body.get("input")) != body.get("input_sha256"):
        return False, "the recorded input does not hash to input_sha256"
    if frozen is not None and body["input_sha256"] != frozen:
        return False, "the input is not the input the session froze when it opened (its digest changed)"
    return True, "the input hashes to input_sha256, the digest taken when the session opened"


def _usd(value) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return out if out.is_finite() else None


def check_priced(body: dict, frozen) -> tuple[bool, str]:
    cycle = body.get("cycle") if isinstance(body.get("cycle"), dict) else {}
    caps = {"core": _usd(cycle.get("cap_usd")), "tools": _usd((cycle.get("tools") or {}).get("cap_usd"))}
    problems, parts = [], []
    for pool, cap in caps.items():
        done = [c["receipt"] for c in _calls(body) if (c.get("pool") or "core") == pool and isinstance(c.get("receipt"), dict)
                and not (c["receipt"].get("refusal") or c["receipt"].get("error"))]
        costs = [_usd(r.get("cost_usd")) if r.get("cost_usd") is not None else None for r in done]
        unpriced, spent = costs.count(None), sum((c for c in costs if c is not None), Decimal(0))
        if cap is not None and unpriced:
            problems.append(f"{unpriced} {pool} calls are unpriced, so the cap of USD {cap} cannot count them")
        if cap is not None and spent > cap:
            problems.append(f"{pool} spend USD {spent} passed its cap of USD {cap}")
        parts.append(f"{pool} USD {spent} of {cap if cap is not None else 'no cap'}")
    return (False, "; ".join(problems)) if problems else (True, "no unpriced call under a cap; " + ", ".join(parts))


def check_has_calls(body: dict, frozen) -> tuple[bool, str]:
    n = len(_calls(body))
    return (True, f"{n} calls") if n else (False, "the record holds no Otari call, so it proves nothing about a model")


def check_live(body: dict, frozen) -> tuple[bool, str]:
    mode = (body.get("workload") or {}).get("mode")
    return (True, "live calls through the gateway") if mode == "live" else (False, "dry run: a fake transport answered, no Otari call was made")


CHECKS = (
    ("calls_accounted", HARD, check_accounted),
    ("receipts_schema", HARD, check_schema),
    ("input_frozen", HARD, check_input),
    ("priced_within_cap", HARD, check_priced),
    ("has_calls", SOFT, check_has_calls),
    ("live_calls", SOFT, check_live),
)


def evaluate(body: dict, frozen: str | None) -> dict:
    """Every generic check over a candidate body (a record without `hash` and `closure`). A check that cannot run fails."""
    checks = []
    for name, klass, fn in CHECKS:
        try:
            ok, reason = fn(body, frozen)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as err:
            ok, reason = False, f"the check could not run ({type(err).__name__})"
        checks.append({"name": name, "class": klass, "pass": bool(ok), "reason": reason})
    warned = any(c["class"] == SOFT and not c["pass"] for c in checks)
    refused = any(c["class"] == HARD and not c["pass"] for c in checks)
    return {"version": VERSION, "input_sha256": frozen, "checks": checks, "outcome": "refused" if refused else "warning" if warned else "pass"}


def recheck(record: dict) -> dict:
    """The sealed closure recomputed from the record alone: the same pass and fail check by check, and every hard check passing."""
    sealed = record.get("closure")
    if not isinstance(sealed, dict) or sealed.get("version") != VERSION or not isinstance(sealed.get("checks"), list):
        return {"closure_ok": False}
    again = evaluate({k: v for k, v in record.items() if k not in ("hash", "closure")}, sealed.get("input_sha256"))
    mine = {c.get("name"): c.get("pass") for c in sealed["checks"] if isinstance(c, dict)}
    theirs = {c["name"]: c["pass"] for c in again["checks"]}
    return {"closure_ok": mine == theirs and all(c["pass"] for c in again["checks"] if c["class"] == HARD)}


def replay(record: dict) -> dict:
    """What `replay` can honestly do for a receipts record: the hash, the shape of the record, every receipt's schema and the closure,
    each recomputed. There are no trading rules and no workload rules, so no verdict is re-derived (`replayed` is None)."""
    body = {k: v for k, v in record.items() if k != "hash"}
    shape = all(f in record for f in RECORD_FIELDS) and record.get("kind") == KIND and isinstance(record.get("workload"), dict) and isinstance(record.get("calls"), list)
    parts = {"hash_ok": dossier.digest(body) == record.get("hash"), "record_ok": shape,
             "receipts_schema_ok": shape and all(receipt_problem(c.get("receipt")) is None for c in _calls(record)),
             **(recheck(record) if shape else {"closure_ok": False})}
    return {"seq": record.get("seq"), "kind": KIND, "matches": all(parts.values()), "recorded": None, "replayed": None,
            "note": "no rules to re-derive (receipts record)", **parts}


# ---------------------------------------------------------------- the dry-run transport

class _Reply:
    def __init__(self, body: dict, headers: dict):
        self._raw, self.headers = json.dumps(body).encode("utf-8"), Message()
        for k, v in headers.items():
            self.headers[k] = v

    def read(self, *_):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeGateway:
    """Stands in for urllib.request.urlopen in a dry run, so the real client (receipts, caps, end users) runs with nothing leaving the machine.
    `transport(payload)` returns the answer text, or (text, tool_calls) to put Otari-style tool-call entries on the receipt."""

    def __init__(self, transport):
        self.transport, self.n = transport, 0

    def __call__(self, request, timeout=None):
        self.n += 1
        payload = json.loads(request.data)
        out = self.transport(payload)
        text, tool_calls = out if isinstance(out, tuple) else (out, [])
        body = {"model": payload["model"], "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": "0.000000", "pricing_source": "dry-run"}, "otari_tool_calls": tool_calls}
        return _Reply(body, {"Otari-Request-ID": f"dry-run-{self.n:04d}", "Otari-Provider": "dry-run", "Otari-Model": payload["model"],
                             "Otari-Attempt-Count": "1", "Otari-Fallback": "false", "Otari-Response-Duration-Ms": "1"})


# ---------------------------------------------------------------- the session

@dataclass
class SealResult:
    sealed: bool
    reasons: list[str] = field(default_factory=list)  # why nothing was appended (empty when sealed)
    record: dict | None = None
    closure: dict | None = None
    ledger: Path | None = None


class Session:
    """One run of a workload. `ledger` is where `seal` appends (there is no default: a receipts ledger is the caller's). `config` is a role
    config like config/otari.json (default: that file). `transport` makes it a dry run: a function of the request payload that returns the answer."""

    def __init__(self, workload: str, input, ledger, *, config=None, transport=None):
        if not isinstance(workload, str) or not workload.strip() or not workload.isprintable() or len(workload) > 80:
            raise ValueError("the workload needs a printable name of at most 80 characters")
        self.workload, self.input, self.ledger = workload.strip(), input, Path(ledger)
        self.config, self.transport = Path(config) if config else None, transport
        self.frozen = dossier.digest(input)  # also refuses an input that cannot be hashed (NaN, a huge integer, an object)
        self.calls: list[dict] = []
        self.outputs: dict = {}
        self._saved: dict = {}
        self._done = False

    def __enter__(self):
        if _busy["session"] is not None:
            raise RuntimeError("another seal.Session is open in this process")
        _busy["session"] = self
        self._saved = {"config": llm.OTARI_CONFIG, "urlopen": urllib.request.urlopen,
                       "env": {k: os.environ.get(k) for k in ("OTARI_API_KEY", "OTARI_BASE_URL")}}
        try:
            if self.config:
                llm.OTARI_CONFIG = self.config
            if self.transport:
                os.environ["OTARI_API_KEY"], os.environ["OTARI_BASE_URL"] = "dry-run-not-a-key", DRY_BASE
                urllib.request.urlopen = _FakeGateway(self.transport)
            elif not llm.otari_active():
                raise llm.LLMError(llm.NO_KEY)
            llm.otari_config()  # a bad role config fails here, before any call
            llm.reset_cycle()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        llm.OTARI_CONFIG = self._saved["config"]
        urllib.request.urlopen = self._saved["urlopen"]
        for k, v in self._saved["env"].items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _busy["session"] = None
        return False

    def ask(self, role: str, system: str, user: str, *, label: str | None = None, **options) -> str:
        """One call through `llm.chat` (options: max_tokens, timeout, extra, model, end_user, role_fallbacks). Its receipt is kept whether the
        call answered or failed; a failure is raised again for the agent to handle."""
        if _busy["session"] is not self:
            raise RuntimeError("ask() works inside the `with Session(...)` block")
        if role not in llm.otari_config()["roles"]:
            raise llm.LLMError(f"role {role!r} is not in the role config; add it with a catalog model")
        try:
            text = llm.chat(system, user, role=role, **options)
        except (OSError, ValueError):
            self._keep(label, role)
            raise
        self._keep(label, role)
        return text

    def _keep(self, label: str | None, role: str) -> None:
        receipt = llm.served_receipt()
        if receipt is None:
            return  # the call failed before anything was sent (a bad key): there is no call to account for
        self.calls.append({"label": label or f"{role} {len(self.calls) + 1}", "role": role, "pool": "tools" if llm.is_tool_role(role) else "core", "receipt": receipt})

    def output(self, name: str, value) -> None:
        """Keeps a named result of the workload in the record (it must be JSON that can be hashed)."""
        dossier.digest(value)
        self.outputs[str(name)] = value

    def _refusal(self, reasons: list[str], closure: dict | None = None) -> SealResult:
        return SealResult(False, reasons, None, closure, self.ledger)

    def seal(self) -> SealResult:
        """Builds the record, runs the closure, and appends it to the ledger when no hard check failed. Nothing is appended otherwise."""
        if _busy["session"] is not self:
            raise RuntimeError("seal() works inside the `with Session(...)` block")
        if self._done:
            raise RuntimeError("this session is already sealed")
        if self.ledger.resolve() == PUBLIC_LEDGER.resolve():
            return self._refusal([f"{self.ledger} is the public trading ledger; a receipts ledger is a file of your own"])
        existing = dossier.read_ledger(self.ledger)
        if any(d.get("kind") != KIND for d in existing):
            return self._refusal([f"{self.ledger} holds records that are not {KIND}; use a ledger of its own"])
        broken = dossier.verify(existing)
        if broken:
            return self._refusal([f"{self.ledger} does not verify, so nothing is added to it: " + "; ".join(broken[:2])])
        body = {"seq": len(existing) + 1, "prev_hash": existing[-1]["hash"] if existing else dossier.GENESIS, "kind": KIND,
                "workload": {"name": self.workload, "mode": "dry-run" if self.transport else "live"},
                "input": self.input, "input_sha256": dossier.digest(self.input), "outputs": self.outputs, "calls": self.calls,
                "otari": llm.otari_summary(), "cycle": llm.cycle_status(),
                "sealed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        closure = evaluate(body, self.frozen)
        failed = [c for c in closure["checks"] if c["class"] == HARD and not c["pass"]]
        if failed:
            return self._refusal([f"{c['name']}: {c['reason']}" for c in failed], closure)
        body["closure"] = closure
        record = {**body, "hash": dossier.digest(body)}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._done = True
        return SealResult(True, [], record, closure, self.ledger)
