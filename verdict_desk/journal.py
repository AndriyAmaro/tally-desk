"""Auditoria de um diario de agente de terceiros contra dados publicos.

Entrada: o `journal.jsonl` de um agente autonomo (um turno por linha, com a
evidencia que ele viu). Saida, em duas partes:

1. Conferencia: cada candle e cada preco que o diario registrou e comparado com
   os candles publicos da Binance. Diz quantos batem, sem opiniao.
2. Repeticao em sombra: para cada simbolo e hora do diario, o Verdict Desk monta
   um pacote so com o que existia naquele instante (candles ja fechados, funding
   que o proprio diario registrou), decide com as regras v1 e compara com o que o
   agente fez. Nada disso e resultado de operacao real.

O raciocinio em texto livre do diario nunca entra na saida.
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import dossier, portfolio
from .evidence import BINANCE_KLINES, _get_json, parse_klines

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
SUMMARY = re.compile(r"^(?P<symbol>[A-Z0-9]+) price (?P<price>-?\d+(?:\.\d+)?), funding (?P<funding>-?\d+(?:\.\d+)?)%")
CANDLE = re.compile(r"^(?P<ts>\S+) o=(?P<o>\S+) h=(?P<h>\S+) l=(?P<l>\S+) c=(?P<c>\S+) v=(?P<v>\S+)$")
BASIS_TOLERANCE = 0.003  # o diario le futuros e o espelho publico e spot: preco difere pela base, volume nao e comparavel
SHADOW_INTERVAL = "1h"  # intervalo nativo das regras v1
SHADOW_CANDLES = 72
LABEL = "SHADOW REPLAY on a third-party journal: what the desk would have recorded, not a trade result"


def read_journal(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["_line_sha256"] = hashlib.sha256(line.encode("utf-8")).hexdigest()
            rows.append(row)
    return rows


def to_ms(stamp: str) -> int:
    """ISO com Z ou 'AAAA-MM-DD HH:MM:SS' (UTC) para milissegundos."""
    text = stamp.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def parse_summary(text: str) -> dict | None:
    m = SUMMARY.match(text or "")
    if not m:
        return None
    return {"symbol": m["symbol"], "price": float(m["price"]), "funding_rate": round(float(m["funding"]) / 100, 10)}


def parse_candle(text: str) -> dict | None:
    m = CANDLE.match(text or "")
    if not m:
        return None
    return {"t": to_ms(m["ts"]), **{k: float(m[k]) for k in ("o", "h", "l", "c", "v")}}


def fetch_as_of(symbol: str, interval: str, end_ms: int, limit: int, get=_get_json) -> list[dict]:
    """Candles publicos ate `end_ms`, incluindo o que ainda estava se formando (sem filtro)."""
    _, rows = get(BINANCE_KLINES, {"symbol": symbol, "interval": interval, "endTime": end_ms, "limit": limit})
    return parse_klines(rows)


def closed_only(candles: list[dict], interval: str, end_ms: int) -> list[dict]:
    """So candles que ja tinham fechado no instante `end_ms`: nada do futuro entra no pacote."""
    width = INTERVAL_MS[interval]
    return [c for c in candles if c["t"] + width <= end_ms]


def crosscheck(item: dict, public: list[dict], end_ms: int, interval: str) -> dict:
    """Compara candles e preco registrados com os publicos.

    So candles ja fechados no instante do turno entram na comparacao: o candle que ainda
    se formava muda ate fechar, e contar isso como divergencia seria injusto com o diario.
    Compara abertura, maxima, minima e fechamento com tolerancia de `BASIS_TOLERANCE`
    (o diario le futuros perpetuos; o espelho publico e spot, entao ha uma base pequena).
    O volume nao entra: mercados diferentes, unidades diferentes.
    """
    width = INTERVAL_MS[interval]
    by_time = {c["t"]: c for c in public}
    recorded = [c for c in (parse_candle(x) for x in item.get("candles", [])) if c]
    closed = [c for c in recorded if c["t"] + width <= end_ms]
    found = [(c, by_time[c["t"]]) for c in closed if c["t"] in by_time]
    within = [r for r, p in found if all(abs(r[k] - p[k]) <= BASIS_TOLERANCE * abs(p[k]) for k in ("o", "h", "l", "c"))]
    summary = parse_summary(item.get("summary", ""))
    price_ok = None
    if summary and public:
        live = [c for c in public if c["t"] <= end_ms][-1:]
        if live:
            c = live[0]
            price_ok = c["l"] * 0.999 <= summary["price"] <= c["h"] * 1.001
    return {
        "candles_recorded": len(recorded),
        "candles_closed": len(closed),
        "closed_found": len(found),
        "closed_within_basis": len(within),
        "price_in_candle_range": price_ok,
    }


def shadow_packet(turn: dict, item: dict, candles: list[dict], summary: dict) -> dict:
    return {
        "symbol": summary["symbol"],
        "collected_at": datetime.fromtimestamp(to_ms(item["at"]) / 1000, timezone.utc).isoformat(timespec="seconds"),
        "origin": {
            "kind": "third-party journal, shadow replay",
            "turn_id": turn["turn_id"],
            "journal_line_sha256": turn["_line_sha256"],
            "automaton_decision": turn["decision"],
        },
        "market": {"source": f"{BINANCE_KLINES} (closed candles at the turn time)", "interval": SHADOW_INTERVAL, "candles": candles},
        "funding": {"source": "recorded by the journal at the turn time", "instrument": "", "rate": summary["funding_rate"]},
    }


def audit(rows: list[dict], get=_get_json, pause: float = 0.0) -> dict:
    """Conferencia e repeticao em sombra de todo o diario. `get` permite injetar dados de teste."""
    totals = {"candles_recorded": 0, "candles_closed": 0, "closed_found": 0, "closed_within_basis": 0, "prices_checked": 0, "prices_plausible": 0}
    seen_hours: set[tuple[str, int, str]] = set()
    shadow, skipped = [], []
    ledger: list[dict] = []
    for turn in rows:
        for item in turn.get("evidence", []):
            summary = parse_summary(item.get("summary", ""))
            if not summary or not item.get("at"):
                skipped.append({"turn_id": turn["turn_id"], "why": "no parsable summary"})
                continue
            end_ms = to_ms(item["at"])
            interval = item.get("interval") if item.get("interval") in INTERVAL_MS else SHADOW_INTERVAL
            try:
                public = fetch_as_of(summary["symbol"], interval, end_ms, 24, get)
            except (OSError, ValueError) as err:
                # Ex.: simbolo que so existe em futuros (1000PEPEUSDT) nao esta no espelho spot publico.
                skipped.append({"turn_id": turn["turn_id"], "symbol": summary["symbol"], "why": f"public data unavailable: {err}"})
                continue
            check = crosscheck(item, public, end_ms, interval)
            for key in ("candles_recorded", "candles_closed", "closed_found", "closed_within_basis"):
                totals[key] += check[key]
            if check["price_in_candle_range"] is not None:
                totals["prices_checked"] += 1
                totals["prices_plausible"] += bool(check["price_in_candle_range"])
            hour = (summary["symbol"], end_ms // INTERVAL_MS["1h"], turn["decision"])
            if hour in seen_hours:
                continue
            seen_hours.add(hour)
            try:
                candles = closed_only(fetch_as_of(summary["symbol"], SHADOW_INTERVAL, end_ms, SHADOW_CANDLES + 1, get), SHADOW_INTERVAL, end_ms)
            except (OSError, ValueError) as err:
                skipped.append({"turn_id": turn["turn_id"], "symbol": summary["symbol"], "why": f"public data unavailable: {err}"})
                continue
            if len(candles) < SHADOW_CANDLES:
                skipped.append({"turn_id": turn["turn_id"], "why": f"only {len(candles)} closed candles"})
                continue
            prev = ledger[-1]["hash"] if ledger else dossier.GENESIS
            record = dossier.build(shadow_packet(turn, item, candles[-SHADOW_CANDLES:], summary), prev, len(ledger) + 1, use_llm=False)
            ledger.append(record)
            shadow.append({"turn": turn, "item": item, "record": record})
            time.sleep(pause)
    return {"totals": totals, "shadow": shadow, "ledger": ledger, "skipped": skipped}


def mark_outcomes(result: dict, get=_get_json) -> list[dict]:
    """Resolve cada decisao em sombra nao neutra contra os candles que vieram depois."""
    out = []
    for entry in result["shadow"]:
        rec, turn = entry["record"], entry["turn"]
        row = {
            "turn_id": turn["turn_id"],
            "at": turn["at"],
            "symbol": rec["evidence"]["symbol"],
            "automaton": turn["decision"],
            "desk": rec["verdict"]["action"],
            "edge": rec["verdict"]["edge"],
        }
        if rec["verdict"]["action"] != "FLAT":
            start = rec["evidence"]["market"]["candles"][-1]["t"] + INTERVAL_MS[SHADOW_INTERVAL]
            _, rows = get(BINANCE_KLINES, {"symbol": rec["evidence"]["symbol"], "interval": SHADOW_INTERVAL, "startTime": start, "limit": 1000})
            outcome = portfolio.resolve(rec, parse_klines(rows))
            row.update({"hypothetical": outcome["status"], "pnl": outcome["pnl"], "bars": outcome["bars"]})
        out.append(row)
    return out


def summarize(result: dict, outcomes: list[dict]) -> dict:
    t = result["totals"]
    by_desk: dict[str, int] = {}
    by_pair: dict[str, int] = {}
    for o in outcomes:
        by_desk[o["desk"]] = by_desk.get(o["desk"], 0) + 1
        key = f"{o['automaton']} / {o['desk']}"
        by_pair[key] = by_pair.get(key, 0) + 1
    closed = [o for o in outcomes if o.get("hypothetical") in ("STOP", "TAKE")]
    return {
        "label": LABEL,
        "journal_check": {
            **t,
            "closed_within_basis_pct": round(t["closed_within_basis"] / t["candles_closed"] * 100, 2) if t["candles_closed"] else None,
        },
        "shadow_decisions": len(outcomes),
        "desk_actions": by_desk,
        "automaton_vs_desk": by_pair,
        "hypothetical_closed": len(closed),
        "hypothetical_net": round(sum(o["pnl"] for o in closed), 4),
        "skipped": len(result["skipped"]),
    }
