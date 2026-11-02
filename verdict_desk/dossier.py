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
    layer = {"model": m["llm"].model_name() if hasattr(m["llm"], "model_name") else m["llm"].MODEL, "evidence_index": items}
    summary = m["llm"].otari_summary() if hasattr(m["llm"], "otari_summary") else None
    if summary:  # Otari run: the base URL, the role config hash and the model of every role
        layer["otari"] = summary
    for side in ("bull", "bear"):
        try:
            claims = m["llm"].argue(side, items, **({"ask": ask} if ask else {}))
        except (OSError, ValueError, KeyError) as err:
            layer[side] = {"accepted": [], "rejected": [], "error": str(err)}
            _served(m, layer[side])
            continue
        layer[side] = m["citations"].validate(claims, items)
        _served(m, layer[side])
    if hasattr(m["llm"], "use_checker") and m["llm"].use_checker(ask):
        layer["checker"] = _checker_layer(m, layer, items)
    if summary:
        # how much of the web and MCP evidence the analysts really used: ids of those items among the cited ones of accepted claims
        excerpt_ids = {k for k, v in items.items() if v.startswith("Web page excerpt")}
        web_ids = {k for k, v in items.items() if v.startswith("Web (")}  # titles of sources; the page excerpt is counted apart
        mcp_ids = {k for k, v in items.items() if v.startswith("CoinGecko MCP")}
        cited = {c for side in ("bull", "bear") for a in (layer.get(side) or {}).get("accepted") or [] for c in a.get("cites") or []}
        layer["otari"]["evidence_use"] = {"web_items": len(web_ids), "web_items_cited": len(web_ids & cited), "web_excerpts": len(excerpt_ids),
                                          "web_excerpts_cited": len(excerpt_ids & cited), "mcp_items": len(mcp_ids), "mcp_items_cited": len(mcp_ids & cited)}
    if getattr(m["agents"], "USES_LLM_JUDGE", False):
        route = None
        if summary and ask is None and hasattr(m["llm"], "judge_route"):
            canary = headroom = None
            if hasattr(m["llm"], "canary_on") and m["llm"].canary_on():
                from . import otari_canary
                canary = layer["otari"]["canary"] = otari_canary.run()  # the backup route, shown before the judge is trusted with it
            if hasattr(m["llm"], "headroom_on") and m["llm"].headroom_on():
                headroom = layer["otari"]["headroom"] = m["llm"].read_headroom()  # Otari's own spend state per end user, free to read
            # the arguments the judge cannot change, to measure how tight the case is: the deterministic scores before its vote
            first = _decide(m, feats if feats is not None else m["features"].compute(packet), None, packet.get("narrative"), packet.get("symbol"), layer)
            bull_ok, bear_ok = (layer.get("bull") or {}).get("accepted") or [], (layer.get("bear") or {}).get("accepted") or []
            route = m["llm"].judge_route(len(bull_ok), len(bear_ok), sum(a["weight"] for a in first["bull"]), sum(a["weight"] for a in first["bear"]),
                                         headroom=headroom, canary=canary)
        layer["judge"] = _judge_layer(m, layer, items, ask, route)
    if summary and hasattr(m["llm"], "cycle_status"):  # what the app's own cycle cap has counted so far in this cycle
        layer["otari"]["cycle"] = m["llm"].cycle_status()
    return layer


def _checker_layer(m: dict, layer: dict, items: dict) -> dict:
    """Otari runs: a fourth model re-reads each validated claim against its citations. Advisory only, never in the verdict."""
    cit = m["citations"]
    labels = cit.claim_labels((layer.get("bull") or {}).get("accepted") or [], (layer.get("bear") or {}).get("accepted") or [])
    if not labels:
        return {"advisory": True, "skipped": "no validated claims to check"}
    try:
        out = {"advisory": True, **m["llm"].check_claims(labels, items)}
    except (OSError, ValueError, KeyError) as err:
        out = {"advisory": True, "error": str(err)}
    _served(m, out)
    return out


def _served(m: dict, block: dict) -> None:
    """Records the model that really answered (a router names it in its reply); a stub or an unnamed reply adds nothing."""
    model = m["llm"].served_by() if hasattr(m["llm"], "served_by") else None
    if model:
        block["served_by"] = model
    receipt = m["llm"].served_receipt() if hasattr(m["llm"], "served_receipt") else None
    if receipt:  # Otari: the sealed receipt of this call (request id, provider, model served, attempts, fallback, cost)
        block["receipt"] = receipt
    route = m["llm"].served_route() if hasattr(m["llm"], "served_route") else None
    if route:  # which models the gateway tried and why it moved on, and the routing policy it followed
        block["route"] = route["attempts"]
        if route.get("policy"):
            block["routing_policy"] = route["policy"]


def _judge_layer(m: dict, layer: dict, items: dict, ask=None, route: dict | None = None) -> dict:
    """Regras v6: o juiz LLM le so as afirmacoes aceitas; o veredito bruto e a checagem ficam congelados."""
    cit = m["citations"]
    bull_ok = (layer.get("bull") or {}).get("accepted") or []
    bear_ok = (layer.get("bear") or {}).get("accepted") or []
    labels = cit.claim_labels(bull_ok, bear_ok)
    if not labels:
        return {"ruling": None, "accepted": False, "weight": 0.0, "rejected_because": "no validated claims to judge"}
    kwargs = {"ask": ask} if ask else {}
    if route and route["hard"]:
        kwargs["model"] = route["model"]  # the larger open model, by catalog id; the standard route is the vd-judge policy
    try:
        ruling = m["llm"].rule(labels, items, **kwargs)
    except (OSError, ValueError, KeyError) as err:
        out = {"ruling": None, "accepted": False, "weight": 0.0, "error": str(err)}
    else:
        out = {"ruling": ruling, **cit.validate_ruling(ruling, bull_ok, bear_ok, items)}
    _served(m, out)
    if route:  # the rule and its reason are in the judge block and in its receipt
        out["route_rule"] = route
        if isinstance(out.get("receipt"), dict):
            out["receipt"]["route_rule"] = {"rule": route["rule"], "hard": route["hard"], "reason": route["reason"], "model": route["model"]}
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
    if use_llm and ask is None and hasattr(m["llm"], "feature_on") and m["llm"].feature_on("code_execution"):
        # Otari runs the verdict arithmetic in its hosted sandbox; the sealed block holds both outputs and whether they are identical
        from . import sandbox_rederive
        body["sandbox"] = sandbox_rederive.seal(body)
        if layer is not None and isinstance(layer.get("otari"), dict):
            layer["otari"]["cycle"] = m["llm"].cycle_status()  # a snapshot taken after the sandbox call, so the sealed spend includes it
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
    judged = ((dossier.get("llm") or {}).get("judge") or {}).get("route_rule")
    if isinstance(judged, dict):
        # the route is recomputed from the frozen claims and arguments with the thresholds that were sealed beside it
        from . import llm as llm_module
        try:
            bull_ok, bear_ok = (dossier["llm"].get("bull") or {}).get("accepted") or [], (dossier["llm"].get("bear") or {}).get("accepted") or []
            sums = [sum(a["weight"] for a in dossier[side] if a.get("evidence_ref") != "llm.judge") for side in ("bull", "bear")]
            sealed = (dossier["llm"].get("otari") or {})
            gated = "gates" in judged  # rule v2: the headroom and the canary are replayed from what was sealed beside the verdict
            again = llm_module.route_decision(len(bull_ok), len(bear_ok), sums[0], sums[1], judged["thresholds"], dossier["llm"]["judge"]["receipt"]["sent_model"] if not judged["hard"] else judged["thresholds"]["hard_model"],
                                              headroom=sealed.get("headroom") if gated and "headroom" in judged["gates"] else None,
                                              canary=sealed.get("canary") if gated and "canary" in judged["gates"] else None)
            keys = ("rule", "hard", "reason", "metrics", "thresholds") + (("wanted_hard", "gates") if gated else ())
            route_ok = {k: again.get(k) for k in keys} == {k: judged.get(k) for k in keys}
        except (KeyError, TypeError, ValueError):
            route_ok = False
        result["route_rule_ok"] = route_ok
        result["matches"] = result["matches"] and route_ok
    canary = ((dossier.get("llm") or {}).get("otari") or {}).get("canary")
    if isinstance(canary, dict):
        from . import otari_canary
        result["canary_ok"] = bool(canary.get("passed")) == otari_canary.passes(canary.get("receipt"))  # the verdict on the canary follows from its own receipt
        result["matches"] = result["matches"] and result["canary_ok"]
    if isinstance((dossier.get("evidence") or {}).get("web"), dict):
        from . import otari_web
        webcheck = otari_web.recheck(dossier)  # the sealed excerpt check, recomputed from its own fields: nothing is fetched again
        result.update(webcheck)
        result["matches"] = result["matches"] and webcheck.get("web_excerpt_ok", True)
    market = None
    if isinstance((dossier.get("evidence") or {}).get("market_mcp"), dict):
        from . import otari_mcp
        market = otari_mcp.recheck(dossier)  # the MCP price check, recomputed from the frozen packet; a mismatch must be a refusal
        result.update(market)
        result["matches"] = result["matches"] and market.get("market_check_ok", True)
    if dossier.get("sandbox") is not None:
        # The sandbox half is re-run locally from the frozen dossier (no network, no model); whether Otari's run was identical stays as sealed
        from . import sandbox_rederive
        extra = sandbox_rederive.recheck(dossier)
        result.update(extra)
        result["matches"] = result["matches"] and extra.get("sandbox_local_matches", True)  # an unsealed (error) block is not a divergence
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
