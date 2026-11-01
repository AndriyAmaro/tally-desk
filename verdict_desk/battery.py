"""Bateria de adulteracao: ataca uma COPIA do ledger de todas as formas que conhecemos e conta o que o
verificador deixa passar.

Um verificador so merece confianca se alguem tentou enganar-lo com vontade e anotou o resultado. A
bateria edita um valor (de varios jeitos, ate trocar uma letra por uma parecida de outro alfabeto),
apaga, troca, duplica, forja uma ponta nova, trunca a cauda e reescreve a historia recalculando todos
os hashes seguintes. Cada ataque passa pelo `verify` do proprio projeto.

Dois ataques a cadeia de hashes NAO consegue ver sozinha, e a bateria diz isso em vez de esconder:
truncar a cauda (o que sobra e uma cadeia valida) e reescrever a historia recalculando os hashes (a
cadeia fica consistente, so a ponta muda). Esses sao pegos pelas pontas carimbadas: cada hora o hash
da ponta e arquivado com uma prova OpenTimestamps (ledger/timestamps/<seq>.json + .ots), e um ledger
mais curto, ou cuja historia mudou, discorda de uma ponta que ja foi carimbada.

A semente vem do hash da ponta: quem rodar de novo com a mesma semente recebe os mesmos ataques.
Nada aqui escreve no ledger; todo ataque e feito numa copia.
"""

import copy
import json
import math
import random
from pathlib import Path

from . import dossier

HOMOGLYPHS = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у"}


def leaves(obj, prefix: tuple = ()) -> list[tuple]:
    """Caminho de cada valor escalar (texto, numero, booleano, nulo) dentro de um objeto JSON."""
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in leaves(v, prefix + (k,))]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in leaves(v, prefix + (i,))]
    return [prefix]


def _get(obj, path):
    for key in path:
        obj = obj[key]
    return obj


def _with(obj, path, value):
    out = copy.deepcopy(obj)
    holder = _get(out, path[:-1]) if path[:-1] else out
    holder[path[-1]] = value
    return out


def edits(obj, path) -> list[tuple[str, object]]:
    """Todas as edicoes que fazemos num valor, cada uma devolvendo o objeto inteiro editado."""
    value = _get(obj, path)
    kinds: list[tuple[str, object]] = []
    if isinstance(value, bool):
        kinds.append(("boolean flip", not value))
    elif isinstance(value, int):
        kinds.append(("integer plus one", value + 1))
    elif isinstance(value, float):
        kinds.append(("float, last binary digit", math.nextafter(value, math.inf)))
        kinds.append(("float, one part in ten thousand", value * 1.0001 if value else 0.0001))
    elif isinstance(value, str):
        kinds.append(("character flip", (value[:-1] + chr(ord(value[-1]) ^ 1)) if value else "x"))
        kinds.append(("trailing space", value + " "))
        if len(value) > 1:
            kinds.append(("drop the last character", value[:-1]))
        for i, ch in enumerate(value):
            if ch in HOMOGLYPHS:
                kinds.append(("homoglyph", value[:i] + HOMOGLYPHS[ch] + value[i + 1:]))
                break
    elif value is None:
        kinds.append(("null to zero", 0))
    return [(kind, _with(obj, path, new)) for kind, new in kinds if new != value]


def timestamped_heads(stamps: Path) -> list[dict]:
    """As pontas ja carimbadas: so conta um snapshot que tem a prova .ots ao lado."""
    heads = []
    for snapshot in sorted(Path(stamps).glob("*.json")):
        if not snapshot.with_name(snapshot.name + ".ots").exists():
            continue
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        heads.append({"seq": int(data["seq"]), "hash": data["hash"]})
    return heads


def _log_index(bundle: dict) -> int:
    """O indice da entrada no log do Sigstore, no formato antigo do cosign ou no novo."""
    legacy = (bundle.get("rekorBundle") or {}).get("Payload") or {}
    if legacy.get("logIndex") is not None:
        return int(legacy["logIndex"])
    entries = (bundle.get("verificationMaterial") or {}).get("tlogEntries") or []
    if entries and entries[0].get("logIndex") is not None:
        return int(entries[0]["logIndex"])
    raise ValueError("the bundle holds no transparency log entry")


def rekor_heads(rekor: Path, stamps: Path) -> list[dict]:
    """As pontas assinadas pelo workflow e registradas no log do Sigstore. So conta a que tem o snapshot
    assinado ao lado; um bundle ilegivel e pulado (a pagina pergunta ao log, entao um indice errado
    pode esconder uma testemunha, nunca inventar uma)."""
    rekor, stamps = Path(rekor), Path(stamps)
    root = stamps.parent.parent  # os caminhos sao relativos ao site, que serve ledger/...
    heads = []
    for bundle_path in sorted(rekor.glob("*.bundle.json")):
        snapshot = stamps / f"{bundle_path.name.removesuffix('.bundle.json')}.json"
        if not snapshot.exists():
            continue
        try:
            data = json.loads(snapshot.read_text(encoding="utf-8"))
            log_index = _log_index(json.loads(bundle_path.read_text(encoding="utf-8")))
            heads.append({"seq": int(data["seq"]), "hash": data["hash"], "log_index": log_index,
                          "snapshot": snapshot.relative_to(root).as_posix(), "bundle": bundle_path.relative_to(root).as_posix()})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(heads, key=lambda h: h["seq"])


def witness_index(stamps: Path, deployment: Path, rekor: Path | None = None) -> dict:
    """O que a pagina de testemunhas le: as pontas carimbadas (com onde estao os arquivos), as assinadas no
    log do Sigstore e o contrato de ancora. A pagina confere tudo sozinha; este indice so diz onde procurar."""
    stamps = Path(stamps)
    root = stamps.parent.parent
    heads = []
    for head in sorted(timestamped_heads(stamps), key=lambda h: h["seq"]):
        snapshot = stamps / f"{head['seq']}.json"
        heads.append({**head, "snapshot": snapshot.relative_to(root).as_posix(), "proof": snapshot.with_name(snapshot.name + ".ots").relative_to(root).as_posix()})
    anchor = None
    try:
        data = json.loads(Path(deployment).read_text(encoding="utf-8"))
        anchor = {"address": data["contract"], "chain_id": data["chainId"], "network": data["network"],
                  "rpc": "https://ethereum-sepolia-rpc.publicnode.com", "explorer": data.get("explorer", {}).get("contract"),
                  "deploy_block": data.get("deployBlock"),
                  # publicnode keeps only recent blocks (eth_getLogs over older ones answers "pruned history
                  # unavailable", seen 26 Oct); the anchor history is read from a node that keeps it all.
                  "history_rpc": "https://sepolia.gateway.tenderly.co"}
    except (OSError, ValueError, KeyError):
        pass
    return {"heads": heads, "anchor": anchor, "rekor": rekor_heads(rekor, stamps) if rekor else []}


def witness_problems(ledger: list[dict], heads: list[dict]) -> list[str]:
    """Onde o ledger discorda de uma ponta que ja foi carimbada (mais curto, ou historia diferente)."""
    problems = []
    for head in heads:
        if head["seq"] > len(ledger):
            problems.append(f"the ledger stops at seq {len(ledger)} but seq {head['seq']} was already timestamped")
        elif ledger[head["seq"] - 1]["hash"] != head["hash"]:
            problems.append(f"seq {head['seq']} differs from the head that was timestamped for it")
    return problems


def _splice_caught(ledger: list[dict], i: int, j: int, new: list[dict], verifier) -> bool:
    """Substitui ledger[i:j] por `new` e confere a janela em volta, usando o hash anterior real."""
    window = ledger[max(0, i - 1):i] + new + ledger[j:j + 1]
    anchor = max(0, i - 1)
    prev = ledger[anchor - 1]["hash"] if anchor > 0 else dossier.GENESIS
    return bool(verifier(window, prev=prev))


def rewrite_paths(record: dict) -> list[tuple]:
    """The values a history rewrite may edit: every leaf except the two the rewrite recomputes itself."""
    return [q for q in leaves(record) if q not in (("hash",), ("prev_hash",))]


def _rehash_from(ledger: list[dict], start: int) -> list[dict]:
    out = copy.deepcopy(ledger)
    prev = out[start - 1]["hash"] if start > 0 else dossier.GENESIS
    for d in out[start:]:
        d["prev_hash"] = prev
        d["hash"] = dossier.digest({k: v for k, v in d.items() if k != "hash"})
        prev = d["hash"]
    return out


def run(ledger: list[dict], stamps: Path, seed: str, trials: int = 2000, verifier=None) -> dict:
    verifier = verifier or dossier.verify
    rng = random.Random(seed)
    n = len(ledger)
    heads = timestamped_heads(stamps)
    attacks = []

    def record(name, caught_by, results, detail=None):
        total = len(results)
        caught = sum(1 for r in results if r["caught"])
        row = {"attack": name, "caught_by": caught_by, "trials": total, "caught": caught, "missed": total - caught,
               "chain_alone_missed": sum(1 for r in results if not r["chain"])}
        if detail:
            row["detail"] = detail
        attacks.append(row)

    # 1. edit one value, by kind
    results, kinds = [], {}
    for _ in range(trials):
        i = rng.randrange(n)
        path = rng.choice(leaves(ledger[i]))
        kind, edited = rng.choice(edits(ledger[i], path))
        caught = _splice_caught(ledger, i, i + 1, [edited], verifier)
        results.append({"caught": caught, "chain": caught})
        slot = kinds.setdefault(kind, {"trials": 0, "caught": 0})
        slot["trials"] += 1
        slot["caught"] += caught
    record("edit one value", "hash chain", results, {"by_kind": dict(sorted(kinds.items()))})

    others = max(10, trials // 5)

    def sample(limit):
        pool = list(range(limit))
        rng.shuffle(pool)
        return pool[: min(others, len(pool))]

    # 2. delete / swap / duplicate / forge
    results = [{"caught": (c := _splice_caught(ledger, i, i + 1, [], verifier)), "chain": c} for i in sample(n - 1)]
    record("delete a dossier", "hash chain", results)
    results = [{"caught": (c := _splice_caught(ledger, i, i + 2, [ledger[i + 1], ledger[i]], verifier)), "chain": c} for i in sample(n - 1)]
    record("swap two neighbours", "hash chain", results)
    results = [{"caught": (c := _splice_caught(ledger, i, i + 1, [ledger[i], ledger[i]], verifier)), "chain": c} for i in sample(n)]
    record("duplicate a dossier", "hash chain", results)
    results = []
    for _ in range(others):
        forged = copy.deepcopy(ledger[-1])
        forged["seq"] += 1
        forged["prev_hash"] = ledger[-1]["hash"]
        forged["hash"] = "%064x" % rng.getrandbits(256)
        c = _splice_caught(ledger, n, n, [forged], verifier)
        results.append({"caught": c, "chain": c})
    record("forge a new head", "hash chain", results)

    # 3. attacks the chain cannot see by itself
    results = []
    for k in sorted({1, 2, 3, 5, 10, 25, *[rng.randrange(1, max(2, n // 2)) for _ in range(others)]}):
        kept = ledger[: n - k]
        chain_ok = not verifier(kept[-3:], prev=kept[-4]["hash"] if len(kept) > 3 else dossier.GENESIS) if len(kept) > 3 else True
        results.append({"caught": bool(witness_problems(kept, heads)), "chain": not chain_ok})
    record("truncate the tail", "timestamped heads", results, {"timestamped_heads": len(heads)})

    results = []
    for p in sorted(rng.sample(range(1, n), min(max(3, others // 4), n - 1))):
        edited = copy.deepcopy(ledger)
        # The rewrite recomputes "hash" and "prev_hash" itself, so an edit there leaves the ledger
        # unchanged: that is no attack, and counting it as one reported three false misses at dossier 728.
        path = rng.choice(rewrite_paths(edited[p]))
        kind_edits = edits(edited[p], path)
        edited[p] = kind_edits[0][1]
        forged = _rehash_from(edited, p)
        chain_flags = verifier(forged[max(0, p - 1):], prev=forged[p - 2]["hash"] if p > 1 else dossier.GENESIS)
        results.append({"caught": bool(witness_problems(forged, heads)), "chain": bool(chain_flags)})
    record("rewrite history and re-hash it", "timestamped heads", results)

    return {
        "seed": seed,
        "dossiers": n,
        "head": {"seq": ledger[-1]["seq"], "hash": ledger[-1]["hash"]},
        "timestamped_heads": len(heads),
        "attacks": attacks,
        "total_trials": sum(a["trials"] for a in attacks),
        "total_missed": sum(a["missed"] for a in attacks),
        "all_caught": all(a["missed"] == 0 for a in attacks),
    }
