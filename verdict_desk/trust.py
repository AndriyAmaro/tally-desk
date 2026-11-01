"""Confianca por voz: quanto cada voz acertou, medido so com o que ja existia.

O destaque da bounty pede detectar quando vozes CONFIAVEIS convergem. "Confiavel" aqui nao e uma
lista escolhida a dedo: e o historico de acertos de cada voz, medido pelo proprio ledger.

Cada chamada e uma extracao aceita pelo portao de citacao literal (voz, token, alta ou baixa). O
resultado e o retorno do par (BTCUSDT, ETHUSDT, SOLUSDT) entre a abertura da primeira vela depois
da chamada e a abertura da vela `HORIZON_HOURS` depois, lido das velas congeladas nos dossiers. Uma
chamada so conta quando essa segunda vela existe: nada pendente entra na nota.

Sem lookahead POR CONSTRUCAO: `compute` recebe uma lista de dossiers e so le essa lista. A nota de
um dossier e calculada com os dossiers anteriores a ele e congelada dentro dele; `check` recalcula
cada nota a partir do prefixo e compara, entao uma nota que usou o futuro, ou foi editada, aparece.

Acertar nao e habilidade: o acerto esperado so por sorte (dado o vies da propria voz e para onde o
mercado foi) e descontado, e o que sobra e encolhido para zero em amostra pequena. Com menos de
`MIN_CALLS` chamadas a voz nao tem historico, vale peso neutro 1.0 e sai marcada como tal. O peso so
se afasta de 1.0 quando ha evidencia, e nunca passa de [0.5, 1.5].
"""

import bisect
import re
from datetime import datetime, timezone

HORIZON_HOURS = 4
MIN_CALLS = 8
PRIOR_CALLS = 4
WEIGHT_MIN, WEIGHT_MAX = 0.5, 1.5
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp() * 1000)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def calls(ledger: list[dict]) -> list[dict]:
    """Chamadas direcionais aceitas, uma por (voz, citacao): a PRIMEIRA vez que o ledger a viu.

    O mesmo titulo reaparece em varias horas; contar de novo daria a ele varias chances de acertar.
    O instante e o da coleta do dossier, nao o da publicacao: e o que o sistema sabia e quando.
    """
    seen: set[tuple[str, str]] = set()
    out = []
    for d in ledger:
        ev = d.get("evidence") or {}
        nar = ev.get("narrative") or {}
        if not ev.get("collected_at"):
            continue
        at = _ms(ev["collected_at"])
        for x in nar.get("extractions") or []:
            name, quote = x.get("name"), x.get("quote")
            if not name or not quote or x.get("stance") not in ("bullish", "bearish"):
                continue
            key = (name, _squash(quote))
            if key in seen:
                continue
            seen.add(key)
            for token in x.get("tokens") or []:
                if token in PAIRS:
                    out.append({"voice": name, "token": token, "stance": x["stance"], "at": at})
    return out


def price_book(ledger: list[dict]) -> dict[str, tuple[list[int], list[float]]]:
    """Abertura de cada vela por par, das velas congeladas em todos os dossiers do prefixo."""
    raw: dict[str, dict[int, float]] = {sym: {} for sym in PAIRS.values()}
    for d in ledger:
        ev = d.get("evidence") or {}
        sym = ev.get("symbol")
        if sym in raw:
            for candle in (ev.get("market") or {}).get("candles") or []:
                raw[sym][int(candle["t"])] = float(candle["o"])
    book = {}
    for sym, by_time in raw.items():
        times = sorted(by_time)
        book[sym] = (times, [by_time[t] for t in times])
    return book


def _open_at_or_after(book, symbol: str, ms: int):
    times, opens = book[symbol]
    i = bisect.bisect_left(times, ms)
    return opens[i] if i < len(times) else None


def settled(ledger: list[dict]) -> tuple[list[dict], int]:
    """As chamadas ja resolvidas, cada uma com seu retorno; e quantas ainda estao pendentes."""
    book = price_book(ledger)
    horizon = HORIZON_HOURS * 3_600_000
    done, pending = [], 0
    for call in calls(ledger):
        sym = PAIRS[call["token"]]
        entry = _open_at_or_after(book, sym, call["at"])
        out = _open_at_or_after(book, sym, call["at"] + horizon)
        if entry is None or out is None or entry <= 0:
            pending += 1
            continue
        ret = out / entry - 1.0
        sign = 1 if call["stance"] == "bullish" else -1
        done.append({**call, "ret": ret, "hit": ret * sign > 0, "signed": ret * sign})
    return done, pending


def weight_for(n: int, excess: float) -> float:
    """Peso neutro (1.0) sem historico; depois, 1 + 2 x habilidade, encolhida e limitada.

    `excess` e acerto menos o acerto esperado so por sorte. A habilidade e o excesso dividido por
    n + PRIOR_CALLS, o que puxa a nota para zero enquanto a amostra e pequena.
    """
    if n < MIN_CALLS:
        return 1.0
    skill = excess / (n + PRIOR_CALLS)
    return round(min(WEIGHT_MAX, max(WEIGHT_MIN, 1.0 + 2.0 * skill)), 3)


def compute(ledger: list[dict]) -> dict:
    """A nota de todas as vozes que ja fizeram chamadas, usando SO os dossiers recebidos.

    Acertar nao basta: numa janela em que o mercado so subiu, uma voz que so diz "alta" acerta tudo
    sem saber nada. O acerto esperado so por sorte e calculado com o vies da PROPRIA voz (a fracao de
    chamadas de alta) contra se cada janela subiu ou desceu; a habilidade e o que sobra acima disso.
    Uma voz sempre otimista tem excesso exatamente zero.
    """
    done, pending = settled(ledger)
    by_voice: dict[str, list[dict]] = {}
    for c in done:
        by_voice.setdefault(c["voice"], []).append(c)
    voices = {}
    for name in sorted(by_voice):
        rows = by_voice[name]
        n, hits = len(rows), sum(1 for r in rows if r["hit"])
        bull_share = sum(1 for r in rows if r["stance"] == "bullish") / n
        expected = sum(bull_share if r["ret"] > 0 else 1.0 - bull_share for r in rows)
        excess = hits - expected
        voices[name] = {
            "n": n,
            "hits": hits,
            "hit_rate": round(hits / n, 4),
            "chance_rate": round(expected / n, 4),
            "skill": round(excess / (n + PRIOR_CALLS), 4),
            "bull_share": round(bull_share, 4),
            "mean_signed_bps": round(sum(r["signed"] for r in rows) / n * 10_000, 2),
            "weight": weight_for(n, excess),
            "proven": n >= MIN_CALLS,
        }
    return {
        "horizon_hours": HORIZON_HOURS,
        "min_calls": MIN_CALLS,
        "as_of_seq": ledger[-1]["seq"] if ledger else 0,
        "pending_calls": pending,
        "voices": voices,
    }


def record_for(ledger: list[dict], names) -> dict:
    """O que um dossier congela: so a nota das vozes que ele proprio leu, calculada do passado."""
    full = compute(ledger)
    neutral = {"n": 0, "hits": 0, "weight": 1.0}
    return {
        "horizon_hours": full["horizon_hours"],
        "min_calls": full["min_calls"],
        "as_of_seq": full["as_of_seq"],
        "voices": {
            name: ({k: full["voices"][name][k] for k in ("n", "hits", "weight")} if name in full["voices"] else dict(neutral))
            for name in sorted(set(names))
        },
    }


def check(ledger: list[dict]) -> list[str]:
    """Recalcula a nota congelada de cada dossier so com os dossiers ANTES dele; devolve os problemas."""
    problems = []
    for d in ledger:
        frozen = ((d.get("evidence") or {}).get("narrative") or {}).get("trust")
        if frozen is None:
            continue
        seq, cut = d.get("seq"), frozen.get("as_of_seq")
        # Todos os dossiers de um mesmo ciclo congelam a MESMA nota, calculada antes do ciclo: o corte
        # gravado manda, e tem de ser anterior ao proprio dossier (uma nota "de hoje" nao e do passado).
        if not isinstance(cut, int) or not (0 <= cut < seq):
            problems.append(f"dossier {seq}: the trust table is as of seq {cut!r}, which is not before it")
            continue
        expect = record_for(ledger[:cut], frozen.get("voices", {}))
        if frozen != expect:
            problems.append(f"dossier {seq}: the frozen trust table does not match what the ledger up to seq {cut} supports")
    return problems
