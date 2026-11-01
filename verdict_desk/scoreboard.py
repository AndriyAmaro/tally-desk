"""O placar da pagina do build: cada numero contado do ledger bruto, nenhum digitado.

O bloco cita um dossier ("as of dossier N"). O ledger so cresce, entao os numeros em N podem ser
recalculados exatamente a qualquer hora depois; `check` faz isso e acusa qualquer diferenca. Um numero
que nao e reproduzido nao e publicado.
"""

import json
import re
from pathlib import Path

from . import battery, dossier, livestats, portfolio

SITE = "https://andriyamaro.github.io/verdict-desk"
REPO = "https://github.com/AndriyAmaro/verdict-desk"
BITCOIN_TAG = bytes.fromhex("0588960d73d71901")
SEPOLIA_LINE = re.compile(r"Anchored seq (\d+) head (?:0x)?([0-9a-f]{64}) at tx (0x[0-9a-f]{64})")


def _varuint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def bitcoin_height(proof: bytes) -> int | None:
    """O menor bloco do Bitcoin que a prova OpenTimestamps cita, ou None se ainda so ha promessas de calendario."""
    heights = []
    start = 0
    while (found := proof.find(BITCOIN_TAG, start)) >= 0:
        start = found + 1
        try:
            length, at = _varuint(proof, found + len(BITCOIN_TAG))
            height, end = _varuint(proof[at:at + length], 0)
        except IndexError:
            continue
        if end == length and height > 0:
            heights.append(height)
    return min(heights) if heights else None


def _bitcoin(root: Path, seq: int | None, limit: int):
    stamps = root / "ledger" / "timestamps"
    candidates = [seq] if seq is not None else sorted(
        (int(p.name.split(".")[0]) for p in stamps.glob("*.json.ots") if int(p.name.split(".")[0]) <= limit), reverse=True)
    for s in candidates:
        proof = stamps / f"{s}.json.ots"
        if proof.exists():
            height = bitcoin_height(proof.read_bytes())
            if height is not None:
                return {"seq": s, "height": height, "proof": f"ledger/timestamps/{s}.json.ots"}
    return None


def _sigstore(root: Path, seq: int | None, limit: int):
    folder = root / "ledger" / "rekor"
    names = [f"{seq}.bundle.json"] if seq is not None else [p.name for p in sorted(
        folder.glob("*.bundle.json"), key=lambda p: -int(p.name.split(".")[0])) if int(p.name.split(".")[0]) <= limit]
    for name in names:
        bundle = folder / name
        if bundle.exists():
            try:
                index = battery._log_index(json.loads(bundle.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                continue
            return {"seq": int(name.split(".")[0]), "log_index": index, "bundle": f"ledger/rekor/{name}"}
    return None


def _sepolia(root: Path, ledger: list[dict], tx: str | None):
    found = []
    for path in sorted((root / "cre" / "evidence").glob("*.txt")):
        for m in SEPOLIA_LINE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            # direct-*.txt is written by cre/anchor/direct.ts on GitHub Actions: same checks, not the CRE runtime.
            found.append((int(m.group(1)), m.group(2), m.group(3), "direct" if path.name.startswith("direct-") else "cre"))
    for seq, head, hash_tx, route in sorted(found, reverse=True):
        if (tx is None or tx == hash_tx) and 0 < seq <= len(ledger) and ledger[seq - 1]["hash"] == head:
            return {"seq": seq, "tx": hash_tx, "url": f"https://sepolia.etherscan.io/tx/{hash_tx}", "route": route}
    return None


def _analyst_calls(prefix: list[dict]) -> dict:
    """Falhas antes do roteamento e chamadas respondidas depois dele. O roteamento comeca no primeiro dossier
    que grava a rota tentada; um timeout depois disso conta como chamada nao respondida da era roteada."""
    sides = [(d["seq"], part) for d in prefix for side in ("bull", "bear") if (part := (d.get("llm") or {}).get(side))]
    routed_from = next((seq for seq, part in sides if "route" in part), None)
    before = [seq for seq, part in sides if part.get("error") and (routed_from is None or seq < routed_from)]
    routed = [part for seq, part in sides if routed_from is not None and seq >= routed_from]
    return {"failed_before": len(before), "failed_first": min(before, default=None), "failed_last": max(before, default=None),
            "routed_from": routed_from, "routed": len(routed), "answered": sum(1 for part in routed if not part.get("error"))}


def _calls_sentence(calls: dict, seq: int) -> str:
    text = ""
    if calls["failed_before"]:
        text = (f"{calls['failed_before']} analyst calls failed {'before routing, ' if calls['routed_from'] else ''}when one provider at a time "
                f"served the analysts (dossiers {calls['failed_first']} to {calls['failed_last']}), each failure recorded in its dossier. ")
    if calls["routed_from"]:
        text += (f"Since routing across several models began (dossiers {calls['routed_from']} to {seq}), "
                 f"{calls['answered']} of {calls['routed']} analyst calls were answered. ")
    return text


def compute(ledger: list[dict], seq: int, root: Path, tests: int | None, anchor_pins: dict | None = None) -> dict:
    """Os numeros do bloco, no dossier `seq`. `anchor_pins` fixa quais ancoras citar (usado pela checagem)."""
    root = Path(root)
    prefix = ledger[:seq]
    pins = anchor_pins or {}
    era = livestats.llm_era(prefix, first_seq=1)
    calls = _analyst_calls(prefix)
    matched = sum(1 for d in prefix if dossier.replay(d, rules_version=d.get("verdict", {}).get("rules_version", "v1"))["matches"])
    anchors = {"bitcoin": _bitcoin(root, pins.get("bitcoin"), seq), "sigstore": _sigstore(root, pins.get("sigstore"), seq),
               "sepolia": _sepolia(root, ledger, pins.get("sepolia"))}
    if anchors["sepolia"] and anchors["sepolia"]["seq"] > seq:
        anchors["sepolia"] = None
    book = portfolio.summarize(prefix)  # settled only on candles frozen in dossiers up to `seq`
    practice = {"closed": book["closed"], "open": book["open"],
                "take": sum(1 for p in book["positions"] if p["status"] == "TAKE"),
                "stop": sum(1 for p in book["positions"] if p["status"] == "STOP"),
                "realized": book["realized_pnl"], "fees": book["fees"], "net": book["realized_net"], "fee_pct": book["fee_pct_per_side"],
                "drawdown": book["max_drawdown_pct"], "start": book["start_capital"]}
    executions = [d["execution"] for d in prefix if d.get("execution")]
    positions = sum(1 for d in prefix if (d.get("verdict") or {}).get("action") in ("LONG", "SHORT"))
    status = [e.get("status") for e in executions]
    sepolia = {"executed": status.count("executed"), "recorded": status.count("recorded"),
               "failed": len(status) - status.count("executed") - status.count("recorded"),
               "closed": sum(1 for d in prefix for c in d.get("executions_closed") or [] if c.get("status") in ("executed", "recorded")),
               "first_seq": next((d["seq"] for d in prefix if d.get("execution")), None)}
    sepolia["simulated_only"] = positions - sepolia["executed"] - sepolia["recorded"]
    return {"seq": prefix[-1]["seq"], "hash": prefix[-1]["hash"], "dossiers": len(prefix), "sepolia": sepolia,
            "claims": {"accepted": era["accepted"], "rejected": era["rejected"], "reasons": era["rejection_reasons"], "calls": calls},
            "replay": {"matched": matched, "total": len(prefix)}, "practice": practice, "tests": tests, "anchors": anchors}


def render(data: dict) -> str:
    c, r, a, p = data["claims"], data["replay"], data["anchors"], data["practice"]
    signed = lambda x: f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"
    result, net = signed(p["realized"]), signed(p["net"])
    reasons = "; ".join(f"{n} {reason}" for reason, n in sorted(c["reasons"].items(), key=lambda kv: (-kv[1], kv[0]))) or "none"
    lines = [
        f"LIVE SCOREBOARD: every number below is counted from the raw ledger, as of dossier {data['seq']} (head {data['hash'][:16]}).",
        "Rebuild and check it yourself: python3 -m verdict_desk scoreboard --check docs/scoreboard.txt. The check fails if any figure differs; no number here is typed by hand.",
        "",
        f"- {data['dossiers']} dossiers, hash-chained. Proves integrity after recording: an edit, deletion or swap anywhere breaks every later hash.",
        f"- {c['accepted']} analyst claims accepted and {c['rejected']} refused by the citation validator ({reasons}); " + _calls_sentence(c["calls"], data["seq"]) +
        "Proves an audit trail: a refused claim is kept with its reason, so the record shows what was rejected and not only what survived.",
        f"- {r['matched']} of {r['total']} dossiers re-derive to the same verdict from their own frozen evidence. Proves repeatability: anyone can run the replay and get the same answer.",
        f"- {p['closed']} practice positions closed by their own rule ({p['take']} at the target, {p['stop']} at the stop) and {p['open']} still open: simulated {result} on ${p['start']:,.0f} before fees and {net} after a {p['fee_pct']}% taker fee per side (slippage excluded), worst drawdown {p['drawdown']}%. "
        "Each decision opens its own position, so positions overlap. Proves the practice trades are recorded and settled by rule: each is marked against candles frozen in later dossiers, the stop counted first when one candle hits both.",
    ]
    s = data["sepolia"]
    if s["first_seq"] is not None:
        lines.append(f"- From dossier {s['first_seq']}, new practice positions go to Sepolia testnet: {s['executed']} executed as a swap on Uniswap v3 (ETHUSDT), "
                     f"{s['recorded']} recorded with a receipt but not executed (BTC and SOL have no testnet market), {s['failed']} failed sends, {s['closed']} closed on-chain by their stop or target; "
                     f"{s['simulated_only']} positions, mostly from before that, stay simulated. Proves the practice trades leave the operator's hands: "
                     "each transaction carries a digest of its decision, and the dossier records the transaction before it is sealed.")
    if data["tests"] is not None:
        lines.append(f"- {data['tests']} tests pin the ledger, the pages and the docs to each other. Proves a controlled process: a figure that stops matching the chain fails the build.")
    lines += ["", "Three witnesses outside the operator (each one proves custody and authentication of the head, and a time we do not control; none is an eIDAS qualified time stamp):"]
    if a["bitcoin"]:
        lines.append(f"- Bitcoin (OpenTimestamps): head #{a['bitcoin']['seq']} stamped in block {a['bitcoin']['height']}. Proof: {REPO}/blob/main/{a['bitcoin']['proof']}")
    else:
        lines.append("- Bitcoin (OpenTimestamps): no confirmed stamp yet.")
    if a["sigstore"]:
        lines.append(f"- Sigstore transparency log: head #{a['sigstore']['seq']} signed by a GitHub workflow, log index {a['sigstore']['log_index']}. Entry: https://search.sigstore.dev/?logIndex={a['sigstore']['log_index']}")
    else:
        lines.append("- Sigstore transparency log: no signed head yet.")
    if a["sepolia"]:
        via = ("GitHub Actions, the CRE workflow's checks outside the CRE runtime" if a["sepolia"].get("route") == "direct"
               else "Chainlink CRE workflow")
        lines.append(f"- Ethereum Sepolia ({via}): dossier #{a['sepolia']['seq']} anchored, hash equal to the ledger's. Transaction: {a['sepolia']['url']}")
    else:
        lines.append("- Ethereum Sepolia (Chainlink CRE): no anchor matching this ledger yet.")
    lines += ["", "JUDGE PATH IN 60 SECONDS (three clicks, nothing to install):",
              f"1. Verify the chain in your browser: {SITE}/",
              f"2. Edit a dossier and watch the chain break (button on the same page): {SITE}/#tamper",
              f"3. Check a witness outside our site: {SITE}/witnesses.html"]
    return "\n".join(lines) + "\n"


def check(text: str, ledger: list[dict], root: Path, tests: int | None) -> list[str]:
    """Recalcula o bloco no dossier que ele cita e devolve o que difere. Vazio quer dizer que confere."""
    m = re.search(r"as of dossier (\d+) \(head ([0-9a-f]{16})\)", text)
    if not m:
        return ["the block does not cite a dossier and a head"]
    seq = int(m.group(1))
    if not 0 < seq <= len(ledger):
        return [f"the block cites dossier {seq}, which the ledger does not have"]
    if ledger[seq - 1]["hash"][:16] != m.group(2):
        return [f"head of dossier {seq} is {ledger[seq - 1]['hash'][:16]} in the ledger, not {m.group(2)}"]
    pins = {}
    if (b := re.search(r"Bitcoin \(OpenTimestamps\): head #(\d+)", text)):
        pins["bitcoin"] = int(b.group(1))
    if (s := re.search(r"Sigstore transparency log: head #(\d+)", text)):
        pins["sigstore"] = int(s.group(1))
    if (e := re.search(r"https://sepolia\.etherscan\.io/tx/(0x[0-9a-f]{64})", text)):
        pins["sepolia"] = e.group(1)
    cited_tests = re.search(r"- (\d+) tests pin the ledger", text)
    problems = []
    if cited_tests and tests is not None and int(cited_tests.group(1)) != tests:
        problems.append(f"the block says {cited_tests.group(1)} tests, the suite has {tests}")
    expected = render(compute(ledger, seq, root, int(cited_tests.group(1)) if cited_tests else None, pins))
    if expected != text:
        a, b = expected.splitlines(), text.splitlines()
        for i in range(max(len(a), len(b))):
            x, y = (a[i] if i < len(a) else ""), (b[i] if i < len(b) else "")
            if x != y:
                problems.append(f"line {i + 1} differs: block says {y[:120]!r}, the ledger gives {x[:120]!r}")
    return problems
