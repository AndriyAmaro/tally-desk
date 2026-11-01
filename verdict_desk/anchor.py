"""Monitor: compara o head ancorado na chain com o head publicado.

O fluxo CRE ancora o head do ledger num contrato na Sepolia. Ancorar so vale se
alguem puder conferir depois que o head publicado continua sendo o mesmo que foi
ancorado. Este modulo faz exatamente isso, sem confiar em nenhum dos dois lados:
le o valor gravado no contrato e compara com o dossie local (ou publicado).

Se o operador reescrever o historico, o head muda e a comparacao acusa MISMATCH.
Nada aqui escreve na chain: e leitura pura.
"""

import json
import urllib.request

# Seletores das funcoes publicas do LedgerAnchor (keccak das assinaturas).
SEL_LATEST_SEQ = "0x1075b671"   # latestSeq()
SEL_ANCHORS = "0xfa210653"      # anchors(uint64)

DEFAULT_RPC = "https://ethereum-sepolia-rpc.publicnode.com"
DEFAULT_ADDRESS = "0x50443D51Db71A61E131B147B1d36aF21d48CDdF7"
USER_AGENT = "verdict-desk/0.1 (+https://github.com/AndriyAmaro)"


def _rpc(url: str, method: str, params: list, timeout: int = 30) -> str:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"rpc error: {payload['error']}")
    return payload["result"]


def _uint64_word(value: int) -> str:
    return f"{value:064x}"


def read_latest_seq(rpc_url: str, address: str) -> int:
    result = _rpc(rpc_url, "eth_call", [{"to": address, "data": SEL_LATEST_SEQ}, "latest"])
    return int(result, 16)


def read_anchor(rpc_url: str, address: str, seq: int) -> dict:
    """Le o Anchor gravado para um seq: {head_hash, price, price_decimals, price_updated_at, anchored_at}."""
    data = SEL_ANCHORS + _uint64_word(seq)
    result = _rpc(rpc_url, "eth_call", [{"to": address, "data": data}, "latest"]).removeprefix("0x")
    if len(result) < 64 * 5:
        raise RuntimeError(f"unexpected anchors() payload length: {len(result)}")
    words = [result[i * 64:(i + 1) * 64] for i in range(5)]
    return {
        "head_hash": "0x" + words[0],
        "price": int(words[1], 16),
        "price_decimals": int(words[2], 16),
        "price_updated_at": int(words[3], 16),
        "anchored_at": int(words[4], 16),
    }


def compare(head: dict, onchain: dict) -> dict:
    """Compara o head publicado com o ancorado. `head` precisa de {seq, hash}."""
    problems = []
    if head.get("seq") != onchain.get("seq"):
        problems.append(f"seq differs: published {head.get('seq')} vs anchored {onchain.get('seq')}")
    published = (head.get("hash") or "").lower().removeprefix("0x")
    anchored = (onchain.get("head_hash") or "").lower().removeprefix("0x")
    if published != anchored:
        problems.append(
            f"head hash differs: published 0x{published[:16]}… vs anchored 0x{anchored[:16]}…"
        )
    return {
        "matches": not problems,
        "problems": problems,
        "published_seq": head.get("seq"),
        "anchored_seq": onchain.get("seq"),
        "published_hash": head.get("hash"),
        "anchored_hash": onchain.get("head_hash"),
        "anchored_at": onchain.get("anchored_at"),
        "price": onchain.get("price"),
        "price_decimals": onchain.get("price_decimals"),
    }


def monitor(ledger: list[dict], rpc_url: str = DEFAULT_RPC, address: str = DEFAULT_ADDRESS, seq: int | None = None) -> dict:
    """Confere se o dossie ancorado ainda tem, no ledger publicado, o hash que foi ancorado.

    O ledger publicado costuma estar a frente da ancora (a ancora roda de tempos em
    tempos), entao a comparacao certa e contra o dossie de mesmo seq, nao contra o head.
    """
    target = seq if seq is not None else read_latest_seq(rpc_url, address)
    onchain = read_anchor(rpc_url, address, target)
    onchain["seq"] = target
    dossier = next((d for d in ledger if d.get("seq") == target), None)
    if dossier is None:
        return {
            "matches": False,
            "problems": [f"the published ledger has no dossier with seq {target}, but seq {target} is anchored on chain"],
            "anchored_seq": target,
            "anchored_hash": onchain.get("head_hash"),
            "published_seq": None,
            "published_hash": None,
            "anchored_at": onchain.get("anchored_at"),
            "price": onchain.get("price"),
            "price_decimals": onchain.get("price_decimals"),
        }
    head = {"seq": dossier["seq"], "hash": dossier["hash"]}
    result = compare(head, onchain)
    result["ledger_length"] = len(ledger)
    result["ledger_head_seq"] = ledger[-1]["seq"] if ledger else None
    return result
