"""Re-deriving a verdict inside Otari's hosted code sandbox, and checking it against the local re-derivation.

The decision a dossier records comes from `agents_v2.judge`: edge is the sum of the bull weights minus the sum of the bear weights,
a trade opens when the edge reaches the entry threshold in either direction, and the stop, take-profit and size follow from the
last close and the ATR. `SCRIPT` is that arithmetic alone, with no import from this project, so it can be handed to any Python.

The frozen inputs are put in the first line of the code (`DATA = {...}`), the code is sent to Otari with the `otari_code_execution`
tool, and the model is asked to run it as written and return only stdout. The same code is run locally. What is sealed in the dossier
as `sandbox` is: the code and inputs hashes, the local stdout, the sandbox stdout, whether they are identical, whether they agree with the
recorded verdict, and the receipt of the call. A mismatch is recorded as a mismatch. The replay re-runs the local half from the
frozen dossier and never calls the sandbox, so it does not depend on the network.
"""

import hashlib
import json
import subprocess
import sys

from . import llm

SCRIPT = '''import json
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
SYSTEM = "You run code in the sandbox exactly as given and reply with only what it prints, nothing else."


def inputs_from(body: dict) -> dict:
    """The numbers the verdict rests on, read from a dossier body (or a debate result): argument weights and the policy."""
    verdict, feats = body["verdict"], body["features"]
    policy = verdict.get("policy") or {}
    return {"bull": [a["weight"] for a in body["bull"]], "bear": [a["weight"] for a in body["bear"]],
            "threshold": verdict["threshold"], "stop_atr": policy.get("stop_atr", 1.5), "take_atr": policy.get("take_atr", 3.0),
            "max_risk_pct": verdict.get("risk_pct", policy.get("max_risk_pct", 1.0)), "capital": 10_000.0,
            "last_close": feats["last_close"], "atr_14": feats["atr_14"]}


def code_for(inputs: dict) -> str:
    return "DATA = " + json.dumps(inputs, sort_keys=True) + "\n" + SCRIPT


def run_local(code: str) -> str:
    """The code run by this machine's Python, isolated; its stdout, stripped."""
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, check=True)
    return done.stdout.strip()


def expected_from(verdict: dict) -> dict:
    """What the recorded verdict says about the same quantities (a trade is LONG or SHORT, anything else is NONE)."""
    action = verdict.get("action")
    out = {"bull_score": verdict["bull_score"], "bear_score": verdict["bear_score"], "edge": verdict["edge"], "trade": action if action in ("LONG", "SHORT") else "NONE"}
    for k in ("entry", "stop", "take_profit", "size"):
        if out["trade"] != "NONE":
            out[k] = verdict[k]
    return out


def agrees(stdout: str, verdict: dict) -> bool:
    try:
        return json.loads(stdout) == expected_from(verdict)
    except (ValueError, KeyError):
        return False


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_for(code: str) -> str:
    return "Run exactly this Python code in the sandbox and reply with only its stdout:\n```python\n" + code + "```"


def seal(body: dict, ask=None) -> dict:
    """The `sandbox` block for a dossier body: local run, sandbox run, equality, agreement with the verdict, and the receipt."""
    ask = ask or llm.chat
    inputs = inputs_from(body)
    code = code_for(inputs)
    block = {"schema": "otari-sandbox/1", "script_sha256": sha(SCRIPT), "code_sha256": sha(code), "inputs": inputs}
    try:
        block["local_stdout"] = run_local(code)
    except (OSError, subprocess.SubprocessError) as err:
        return {**block, "error": f"the local run failed: {type(err).__name__}", "equal": False}
    block["local_agrees_with_verdict"] = agrees(block["local_stdout"], body["verdict"])
    try:
        answer = ask(SYSTEM, prompt_for(code), timeout=120, max_tokens=400, role="sandbox", extra={"tools": [{"type": "otari_code_execution"}]})
    except (OSError, ValueError) as err:
        block.update({"error": str(err), "receipt": llm.served_receipt(), "equal": False})
        return block
    block["receipt"] = llm.served_receipt()
    stdout = answer.strip()
    if stdout.startswith("```"):  # a model may fence its answer; the content is what counts
        stdout = stdout.strip("`").removeprefix("json").strip()
    block["sandbox_stdout"] = stdout
    block["equal"] = stdout == block["local_stdout"]
    return block


def recheck(dossier: dict) -> dict:
    """The replay of a sealed sandbox block: re-run the local half from the frozen dossier. No network, no model."""
    sealed = dossier.get("sandbox")
    if not isinstance(sealed, dict):
        return {}
    try:
        inputs = inputs_from(dossier)
        code = code_for(inputs)
        local = run_local(code)
    except (KeyError, OSError, subprocess.SubprocessError):
        return {"sandbox_local_matches": False}
    ok = local == sealed.get("local_stdout") and sha(code) == sealed.get("code_sha256") and inputs == sealed.get("inputs") and agrees(local, dossier["verdict"])
    return {"sandbox_local_matches": ok, "sandbox_equal": bool(sealed.get("equal"))}
