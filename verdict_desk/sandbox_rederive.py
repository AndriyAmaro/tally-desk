"""Re-deriving a verdict inside Otari's hosted code sandbox, and checking it against the local re-derivation.

The decision a dossier records comes from `agents_v2.judge`: edge is the sum of the bull weights minus the sum of the bear weights,
a trade opens when the edge reaches the entry threshold in either direction (unless the evidence was refused as stale), and the stop,
take-profit and size follow from the last close and the ATR. `SCRIPT` is that arithmetic alone, with no import from this project.

The frozen inputs and a fresh random nonce go in the first line of the code (`DATA = {...}`). The script prints the result and
`proof`, the sha256 of the whole DATA line. The nonce is new for every call, so a model that wants to hand us an answer at sealing time
cannot invent one: it cannot compute that hash without running the code. What the nonce does NOT do is prove to a third party that
Otari ran it; that evidence is the native result blocks and Otari's request id (a judge with access to Otari's side can look the id up).
The same code is run locally.

The code goes to Otari's Messages API declared as `code_execution_20250825` with `Otari-Code-Execution: otari`. That path returns the
sandbox's own result blocks (`server_tool_use` with the code it ran and `code_execution_tool_result` with stdout and return code),
which Chat Completions does not. What is sealed in the dossier as `sandbox` is: the hashes, the nonce, the local stdout, the sandbox's
own stdout and the code it ran, whether they are identical, whether they agree with the recorded verdict, and the receipt. A mismatch
is recorded as a mismatch. A block with an `error` is NOT sealed: it says the sandbox did not answer, not that the verdict diverged.
The replay re-runs the local half from the frozen dossier and never calls the sandbox, so it does not depend on the network.

Version 1 blocks (dossiers 794 and before) used the first script, which had no stale case and no proof line; they are rechecked
with that script, so their replay is unchanged.
"""

import hashlib
import json
import subprocess
import sys
import uuid

from . import llm

SCRIPT_V1 = '''import json
bull = DATA["bull"]
bear = DATA["bear"]
bull_score = sum(bull)
bear_score = sum(bear)
edge = round(bull_score - bear_score, 4)
out = {"bull_score": bull_score, "bear_score": bear_score, "edge": edge, "trade": "NONE"}
if edge >= DATA["threshold"] or edge <= -DATA["threshold"]:
    direction = 1 if edge > 0 else -1
    out["trade"] = "LONG" if direction == 1 else "SHORT"
    entry = DATA["last_close"]
    stop_distance = DATA["atr_14"] * DATA["stop_atr"]
    risk_cash = DATA["capital"] * DATA["max_risk_pct"] / 100
    out["entry"] = entry
    out["stop"] = round(entry - direction * stop_distance, 6)
    out["take_profit"] = round(entry + direction * DATA["atr_14"] * DATA["take_atr"], 6)
    out["size"] = round(risk_cash / stop_distance, 8)
print(json.dumps(out, sort_keys=True))
'''
SCRIPT = '''import hashlib, json
proof = hashlib.sha256(json.dumps(DATA, sort_keys=True).encode()).hexdigest()
bull = DATA["bull"]
bear = DATA["bear"]
bull_score = sum(bull)
bear_score = sum(bear)
edge = round(bull_score - bear_score, 4)
blocked = not DATA["fresh_ok"]
out = {"bull_score": bull_score, "bear_score": bear_score, "edge": edge, "trade": "NONE", "blocked": blocked, "proof": proof}
if not blocked and (edge >= DATA["threshold"] or edge <= -DATA["threshold"]):
    direction = 1 if edge > 0 else -1
    out["trade"] = "LONG" if direction == 1 else "SHORT"
    entry = DATA["last_close"]
    stop_distance = DATA["atr_14"] * DATA["stop_atr"]
    risk_cash = DATA["capital"] * DATA["max_risk_pct"] / 100
    out["entry"] = entry
    out["stop"] = round(entry - direction * stop_distance, 6)
    out["take_profit"] = round(entry + direction * DATA["atr_14"] * DATA["take_atr"], 6)
    out["size"] = round(risk_cash / stop_distance, 8)
print(json.dumps(out, sort_keys=True))
'''
SCRIPTS = {"otari-sandbox/1": SCRIPT_V1, "otari-sandbox/2": SCRIPT}
SCHEMA = "otari-sandbox/2"
SYSTEM = "You run code in the sandbox exactly as given and reply with only what it prints, nothing else."
MAX_TOKENS = 1500
TOOLS = [{"type": "code_execution_20250825", "name": "code_execution"}]


def inputs_from(body: dict, version: str = SCHEMA) -> dict:
    """The numbers the verdict rests on, read from a dossier body: argument weights, the policy and, from version 2, whether the
    evidence was fresh (a stale one is refused: no trade whatever the edge)."""
    verdict, feats = body["verdict"], body["features"]
    policy = verdict.get("policy") or {}
    out = {"bull": [a["weight"] for a in body["bull"]], "bear": [a["weight"] for a in body["bear"]],
           "threshold": verdict["threshold"], "stop_atr": policy.get("stop_atr", 1.5), "take_atr": policy.get("take_atr", 3.0),
           "max_risk_pct": verdict.get("risk_pct", policy.get("max_risk_pct", 1.0)), "capital": 10_000.0,
           "last_close": feats["last_close"], "atr_14": feats["atr_14"]}
    if version != "otari-sandbox/1":
        fresh = feats.get("freshness")
        out["fresh_ok"] = fresh is None or bool(fresh.get("ok", True))
    return out


def code_for(inputs: dict, version: str = SCHEMA) -> str:
    """The first line is the data as a Python literal (version 1 wrote it as JSON, which had no booleans), then the script."""
    literal = json.dumps(inputs, sort_keys=True) if version == "otari-sandbox/1" else repr(dict(sorted(inputs.items())))
    return "DATA = " + literal + "\n" + SCRIPTS[version]


def run_local(code: str) -> str:
    """The code run by this machine's Python, isolated; its stdout, stripped."""
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, check=True)
    return done.stdout.strip()


def expected_from(verdict: dict) -> dict:
    """What the recorded verdict says about the same quantities (a trade is LONG or SHORT, anything else, a refusal included, is NONE)."""
    action = verdict.get("action")
    out = {"bull_score": verdict["bull_score"], "bear_score": verdict["bear_score"], "edge": verdict["edge"], "trade": action if action in ("LONG", "SHORT") else "NONE"}
    for k in ("entry", "stop", "take_profit", "size"):
        if out["trade"] != "NONE":
            out[k] = verdict[k]
    return out


def agrees(stdout: str, verdict: dict) -> bool:
    """The script's output has every quantity the verdict has, equal (extra keys such as proof and blocked are not compared here)."""
    try:
        got = json.loads(stdout)
        want = expected_from(verdict)
        if not isinstance(got, dict) or any(got.get(k) != v for k, v in want.items()):
            return False
        if "blocked" in got:
            return got["blocked"] == (verdict.get("action") == "BLOCKED_STALE") and (not got["blocked"] or got["trade"] == "NONE")
        return got["trade"] == want["trade"] and ("entry" in got) == ("entry" in want)
    except (ValueError, KeyError, TypeError):
        return False


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_for(code: str) -> str:
    return "Run exactly this Python code in the sandbox and reply with only its stdout:\n```python\n" + code + "```"


def native_blocks(data) -> dict:
    """The sandbox's own result blocks of a Messages reply: every run, in order, with the code it ran, its stdout, stderr and return code.
    A model may run the code more than once (and each run is billed), so all runs are kept."""
    uses, runs = {}, []
    for block in (data or {}).get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "server_tool_use":
            uses[block.get("id")] = (block.get("input") or {}).get("code")
        elif block.get("type") == "code_execution_tool_result":
            content = block.get("content") if isinstance(block.get("content"), dict) else {}
            runs.append({"code_ran": uses.get(block.get("tool_use_id")), "stdout": content.get("stdout"), "return_code": content.get("return_code"),
                         "stderr": content.get("stderr"), "tool_use_id": block.get("tool_use_id")})
    return {"runs": runs, "text": "".join(str(b.get("text", "")) for b in (data or {}).get("content") or [] if isinstance(b, dict) and b.get("type") == "text")}


def _unfence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
    return text.strip()


def seal(body: dict, ask=None, nonce: str | None = None) -> dict:
    """The `sandbox` block for a dossier body. `ask(system, prompt)` returns the Messages reply (JSON) for tests; by default Otari is called."""
    inputs = inputs_from(body)
    inputs["nonce"] = nonce or uuid.uuid4().hex
    code = code_for(inputs)
    block = {"schema": SCHEMA, "script_sha256": sha(SCRIPT), "code_sha256": sha(code), "inputs": inputs}
    try:
        block["local_stdout"] = run_local(code)
    except (OSError, subprocess.SubprocessError) as err:
        return {**block, "error": f"the local run failed: {type(err).__name__}", "equal": False}
    block["local_agrees_with_verdict"] = agrees(block["local_stdout"], body["verdict"])
    try:
        if ask is not None:
            reply = ask(SYSTEM, prompt_for(code))
        else:
            # max_tokens must leave room for the whole code in the tool call (a first try with 400 cut it short and billed three runs)
            reply = llm.messages_call("sandbox", SYSTEM, prompt_for(code), max_tokens=MAX_TOKENS, timeout=120, tools=TOOLS, max_tool_iterations=2,
                                      extra_headers={"Otari-Code-Execution": "otari"})["data"]
    except (OSError, ValueError) as err:
        return {**block, "error": str(err), "receipt": llm.served_receipt(), "equal": False}
    block["receipt"] = llm.served_receipt()
    native = native_blocks(reply)
    local = json.loads(block["local_stdout"])
    if native["runs"]:
        # every run is recorded; the one that counts is the first whose code is exactly the code we sent (a model that cut or
        # changed the code ran something else, which is not a re-derivation of this verdict)
        block["runs"] = [{"code_ran_sha256": sha(r["code_ran"].strip()) if isinstance(r["code_ran"], str) else None,
                          "is_code_sent": isinstance(r["code_ran"], str) and r["code_ran"].strip() == code.strip(),
                          "return_code": r["return_code"], "stdout": str(r["stdout"] or "").strip()[:2000], "stderr": str(r["stderr"] or "")[:300]} for r in native["runs"]]
        chosen = next((r for r in native["runs"] if isinstance(r["code_ran"], str) and r["code_ran"].strip() == code.strip()), None)
        stdout = str((chosen or {}).get("stdout") or "").strip()
        block.update({"evidence": "native", "sandbox_stdout": stdout, "sandbox_return_code": (chosen or native["runs"][-1]).get("return_code"),
                      "code_ran_sha256": sha(chosen["code_ran"].strip()) if chosen else None, "code_ran_is_code_sent": chosen is not None,
                      "model_text_matches": _unfence(native["text"]) == stdout})
    else:  # no native block: all there is is the model's relay of stdout, which the proof line still binds to the nonce
        stdout = _unfence(native["text"])
        block.update({"evidence": "model_text", "sandbox_stdout": stdout})
    try:
        block["proof_ok"] = json.loads(stdout).get("proof") == local["proof"]
    except (ValueError, AttributeError):
        block["proof_ok"] = False
    block["equal"] = stdout == block["local_stdout"] and block["proof_ok"] and (block["evidence"] == "model_text" or block["code_ran_is_code_sent"])
    return block


def recheck(dossier: dict) -> dict:
    """The replay of a sealed sandbox block: re-run the local half from the frozen dossier. No network, no model. A block that
    carries an error was never sealed (the sandbox did not answer); that is reported as such and never as a divergence."""
    sealed = dossier.get("sandbox")
    if not isinstance(sealed, dict):
        return {}
    if sealed.get("error") or "local_stdout" not in sealed:
        return {"sandbox_sealed": False}
    version = sealed.get("schema") if sealed.get("schema") in SCRIPTS else "otari-sandbox/1"
    try:
        inputs = inputs_from(dossier, version)
        if version != "otari-sandbox/1":
            inputs["nonce"] = (sealed.get("inputs") or {}).get("nonce")
        code = code_for(inputs, version)
        local = run_local(code)
    except (KeyError, OSError, subprocess.SubprocessError):
        return {"sandbox_local_matches": False}
    ok = local == sealed.get("local_stdout") and sha(code) == sealed.get("code_sha256") and inputs == sealed.get("inputs") and agrees(local, dossier["verdict"])
    # the flags sealed with the block are recomputed from what it holds, never believed: the sandbox's stdout must equal the local one, and
    # from version 2 the proof lines must match and (native evidence) the code that ran must be exactly the code of this dossier
    sandbox_stdout = sealed.get("sandbox_stdout")
    again = isinstance(sandbox_stdout, str) and sandbox_stdout == local
    flags = True
    if version != "otari-sandbox/1":
        proof = lambda text: json.loads(text).get("proof") if isinstance(text, str) and text.startswith("{") else None
        try:
            proof_ok = proof(sandbox_stdout) is not None and proof(sandbox_stdout) == proof(local)
        except ValueError:
            proof_ok = False
        code_ok = sealed.get("evidence") != "native" or (sealed.get("code_ran_sha256") == sha(code.strip()) and sealed.get("code_ran_is_code_sent") is True)
        again = again and proof_ok and code_ok
        flags = bool(sealed.get("proof_ok")) == proof_ok
    ok = ok and flags and bool(sealed.get("equal")) == again
    return {"sandbox_sealed": True, "sandbox_local_matches": ok, "sandbox_equal": bool(sealed.get("equal"))}
