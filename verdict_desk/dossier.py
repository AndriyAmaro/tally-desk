"""Dossie de decisao com hash encadeado, replay e verificacao independente.

O ledger e um JSONL: cada dossie guarda o hash do anterior, entao alterar
qualquer dossie antigo quebra a cadeia dali para frente.
"""

import hashlib
import json
from pathlib import Path

from .canonical import canonical

GENESIS = "0" * 64

# Lazy module cache — v1 modules are the default, v2 is opt-in via rules_version.
_MODULES: dict[str, dict] = {}


def _mods(rules_version: str = "v1") -> dict:
    """Return {agents, features, citations, llm} for the requested rules_version."""
    if rules_version not in _MODULES:
        if rules_version == "v1":
            from . import agents, citations, features, llm
        elif rules_version == "v2":
            from . import agents_v2 as agents, citations, features_v2 as features, llm
        elif rules_version == "v3":
            from . import agents_v3 as agents, citations, features_v2 as features, llm
        elif rules_version == "v4":
            from . import agents_v4 as agents, citations, features_v2 as features, llm
        elif rules_version == "v5":
            from . import agents_v5 as agents, citations, features_v2 as features, llm
        elif rules_version == "v6":
            from . import agents_v6 as agents, citations, features_v2 as features, llm
        else:
            raise ValueError(f"unknown rules_version: {rules_version!r}")
        _MODULES[rules_version] = {"agents": agents, "features": features, "citations": citations, "llm": llm}
    return _MODULES[rules_version]


def digest(body: dict) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def analyst_layer(packet: dict, ask=None, feats: dict | None = None, rules_version: str = "v1") -> dict | None:
    """Agentes LLM presos a evidencia. Sem chave (e sem `ask` injetado) devolve None.

    A saida validada fica congelada no dossie; o juiz segue deterministico.
    """
    m = _mods(rules_version)
    if ask is None and not m["llm"].available():
        return None
    items = m["citations"].index(packet, feats if feats is not None else m["features"].compute(packet))
    layer = {"model": m["llm"].MODEL, "evidence_index": items}
    for side in ("bull", "bear"):
        try:
            claims = m["llm"].argue(side, items, **({"ask": ask} if ask else {}))
        except (OSError, ValueError, KeyError) as err:
            layer[side] = {"accepted": [], "rejected": [], "error": str(err)}
            _served(m, layer[side])
            continue
        layer[side] = m["citations"].validate(claims, items)
        _served(m, layer[side])
    if getattr(m["agents"], "USES_LLM_JUDGE", False):
        layer["judge"] = _judge_layer(m, layer, items, ask)
    return layer


def _served(m: dict, block: dict) -> None:
    """Records the model that really answered (a router names it in its reply); a stub or an unnamed reply adds nothing."""
    model = m["llm"].served_by() if hasattr(m["llm"], "served_by") else None
    if model:
        block["served_by"] = model
    route = m["llm"].served_route() if hasattr(m["llm"], "served_route") else None
    if route:  # which models the gateway tried and why it moved on, and the routing policy it followed
        block["route"] = route["attempts"]
        if route.get("policy"):
            block["routing_policy"] = route["policy"]


def _judge_layer(m: dict, layer: dict, items: dict, ask=None) -> dict:
    """Regras v6: o juiz LLM le so as afirmacoes aceitas; o veredito bruto e a checagem ficam congelados."""
    cit = m["citations"]
    bull_ok = (layer.get("bull") or {}).get("accepted") or []
    bear_ok = (layer.get("bear") or {}).get("accepted") or []
    labels = cit.claim_labels(bull_ok, bear_ok)
    if not labels:
        return {"ruling": None, "accepted": False, "weight": 0.0, "rejected_because": "no validated claims to judge"}
    try:
        ruling = m["llm"].rule(labels, items, **({"ask": ask} if ask else {}))
    except (OSError, ValueError, KeyError) as err:
        out = {"ruling": None, "accepted": False, "weight": 0.0, "error": str(err)}
    else:
        out = {"ruling": ruling, **cit.validate_ruling(ruling, bull_ok, bear_ok, items)}
    _served(m, out)
    return out


def _decide(m: dict, feats: dict, rules, narrative, symbol, layer):
    """Chama o decide do modulo; so a v6 recebe a camada LLM (as versoes antigas nem conhecem o parametro)."""
    extra = {"llm": layer} if getattr(m["agents"], "USES_LLM_JUDGE", False) else {}
    if rules is not None:
        return m["agents"].decide(feats, rules, narrative, symbol, **extra)
    return m["agents"].decide(feats, narrative=narrative, symbol=symbol, **extra)


def _rules_for(m: dict, dossier_or_rules=None, rules_version: str = "v1"):
    """Regras efetivas de um dossie: as pedidas pelo usuario, ou o padrao do modulo.

    Um dossie gravado com `--risk-pct 2` so reproduz se o replay reaplicar esse mesmo
    limite, e o limite esta congelado no proprio veredito justamente para isso.
    """
    agent = m["agents"]
    if not hasattr(agent, "with_risk_pct"):
        return None
    base = getattr(agent, "DEFAULT", None)
    if base is None:
        return None
    pct = None
    if isinstance(dossier_or_rules, dict):
        pct = dossier_or_rules.get("verdict", {}).get("risk_pct")
    return agent.with_risk_pct(base, pct)


def build(packet: dict, prev_hash: str, seq: int, ask=None, use_llm: bool = True, rules_version: str = "v1", rules=None,
          execute=None, closed: list | None = None) -> dict:
    m = _mods(rules_version)
    feats = m["features"].compute(packet)
    narrative = packet.get("narrative")
    symbol = packet.get("symbol")
    layer = analyst_layer(packet, ask, feats, rules_version) if use_llm else None
    debate = _decide(m, feats, rules, narrative, symbol, layer)
    body = {
        "seq": seq,
        "prev_hash": prev_hash,
        "evidence": packet,
        "features": feats,
        "bull": debate["bull"],
        "bear": debate["bear"],
        "verdict": debate["verdict"],
        "sensitivity": debate["sensitivity"],
    }
    if layer is not None:
        body["llm"] = layer
    # Practice trades on Sepolia (practice.py): the transaction is sent before sealing, so its hash is inside the dossier.
    from . import practice
    execution = practice.execute_open(seq, packet.get("symbol"), debate["verdict"], packet, execute)
    if execution is not None:
        body["execution"] = execution
    if closed:
        body["executions_closed"] = closed
    return {**body, "hash": digest(body)}


def append(packet: dict, path: Path, ask=None, rules_version: str = "v1", rules=None, execute=None) -> dict:
    ledger = read_ledger(path)
    prev = ledger[-1]["hash"] if ledger else GENESIS
    from . import practice
    closed = practice.execute_closes(ledger, packet.get("symbol"), packet, execute)
    dossier = build(packet, prev, len(ledger) + 1, ask, rules_version=rules_version, rules=rules, execute=execute, closed=closed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dossier, ensure_ascii=False) + "\n")
    return dossier


def replay(dossier: dict, rules_version: str = "v1") -> dict:
    """Refaz a decisao so com a evidencia congelada e compara com o registrado."""
    # Auto-detect rules_version from the dossier's verdict when not provided.
    if rules_version == "auto":
        rules_version = dossier.get("verdict", {}).get("rules_version", "v1")
    m = _mods(rules_version)
    feats = m["features"].compute(dossier["evidence"])
    rules = _rules_for(m, dossier, rules_version)
    narrative = dossier["evidence"].get("narrative")
    symbol = dossier["evidence"].get("symbol")
    debate = _decide(m, feats, rules, narrative, symbol, dossier.get("llm"))
    same = feats == dossier["features"] and debate["verdict"]["action"] == dossier["verdict"]["action"] and debate["verdict"]["edge"] == dossier["verdict"]["edge"]
    result = {"seq": dossier["seq"], "matches": same, "recorded": dossier["verdict"], "replayed": debate["verdict"]}
    layer = dossier.get("llm")
    if layer:
        # A saida do LLM nao se refaz; o replay confere que as citacoes seguem validas na evidencia congelada.
        items = m["citations"].index(dossier["evidence"], dossier["features"])
        recheck = all(
            m["citations"].validate(layer[side]["accepted"], items)["rejected"] == [] for side in ("bull", "bear") if side in layer
        )
        judged = layer.get("judge")
        if isinstance(judged, dict) and "ruling" in judged and judged.get("ruling") is not None:
            # O peso gravado tem de ser o que o validador da hoje para o veredito congelado.
            again = m["citations"].validate_ruling(judged["ruling"], (layer.get("bull") or {}).get("accepted") or [],
                                                   (layer.get("bear") or {}).get("accepted") or [], items)
            recheck = recheck and again["accepted"] == judged.get("accepted") and again["weight"] == judged.get("weight")
        result["llm_citations_valid"] = recheck and items == layer["evidence_index"]
        result["matches"] = same and result["llm_citations_valid"]
    return result


def verify(ledger: list[dict], prev: str = GENESIS) -> list[str]:
    """Lista os problemas da cadeia; lista vazia significa cadeia integra.

    `prev` e o hash que deve vir antes do primeiro dossier da lista: GENESIS para o ledger inteiro,
    ou o hash do dossier anterior quando se confere so uma janela da cadeia.
    """
    problems = []
    for dossier in ledger:
        body = {k: v for k, v in dossier.items() if k != "hash"}
        if dossier["prev_hash"] != prev:
            problems.append(f"seq {dossier['seq']}: prev_hash does not match the previous dossier")
        if digest(body) != dossier["hash"]:
            problems.append(f"seq {dossier['seq']}: content was altered after it was recorded")
        prev = dossier["hash"]
    return problems


def head(ledger: list[dict]) -> dict:
    """Ponta da cadeia para publicar e ancorar (fluxo CRE). Recusa cadeia quebrada ou vazia."""
    if not ledger:
        raise ValueError("the ledger is empty; nothing to anchor")
    problems = verify(ledger)
    if problems:
        raise ValueError("refusing to publish the head of a broken chain: " + "; ".join(problems))
    last = ledger[-1]
    return {"seq": last["seq"], "hash": last["hash"], "symbol": last["evidence"]["symbol"]}
