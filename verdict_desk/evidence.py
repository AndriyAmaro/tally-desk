"""Coleta de evidencia de mercado de fontes publicas, sem chave.

Uma coleta vira um pacote congelado: tudo que os agentes usam para decidir fica
dentro dele, com a fonte e o horario. O replay roda so em cima desse pacote.
"""

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
OKX_FUNDING = "https://www.okx.com/api/v5/public/funding-rate"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "verdict-desk/0.1 (+https://github.com/AndriyAmaro)"

NEWS_TERMS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binance coin"}


def _get_json(url: str, params: dict, timeout: int = 20):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return full, json.loads(response.read().decode("utf-8"))


def base_asset(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT")


def parse_klines(rows: list) -> list[dict]:
    return [
        {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
        for r in rows
    ]


def fetch_candles(symbol: str, interval: str, limit: int) -> dict:
    url, rows = _get_json(BINANCE_KLINES, {"symbol": symbol.upper(), "interval": interval, "limit": limit})
    return {"source": url, "interval": interval, "candles": parse_klines(rows)}


def fetch_funding(symbol: str) -> dict:
    inst = f"{base_asset(symbol)}-USDT-SWAP"
    url, body = _get_json(OKX_FUNDING, {"instId": inst})
    rate = float(body["data"][0]["fundingRate"]) if body.get("data") else None
    return {"source": url, "instrument": inst, "rate": rate}


def fetch_news(symbol: str, max_records: int = 8) -> dict:
    term = NEWS_TERMS.get(base_asset(symbol), base_asset(symbol).lower())
    params = {"query": term, "mode": "artlist", "maxrecords": max_records, "format": "json", "timespan": "1d", "sort": "datedesc"}
    try:
        url, body = _get_json(GDELT_DOC, params)
    except (ValueError, OSError) as err:
        return {"source": GDELT_DOC, "query": term, "articles": [], "error": str(err)}
    articles = [
        {"title": a.get("title", ""), "url": a.get("url", ""), "domain": a.get("domain", ""), "seen": a.get("seendate", "")}
        for a in body.get("articles", [])
    ]
    return {"source": url, "query": term, "articles": articles}


MARKET_WINDOW_MINUTES = 75   # candles de 1h + folga de 15 min para latencia de coleta
SLOW_WINDOW_MINUTES = 1560  # 26h


def _age_minutes(stamp, now: datetime):
    """Idade de um carimbo em minutos, tolerando os formatos que as fontes usam."""
    if not stamp or not isinstance(stamp, str):
        return None
    text = stamp.strip()
    try:
        if len(text) == 16 and "T" in text and text.endswith("Z"):
            when = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return round((now - when).total_seconds() / 60, 2)


def freshness(packet: dict, window_minutes: int | None = None) -> dict:
    """Frescor por classe de fonte, calculado so a partir do proprio pacote.

    Uma janela unica tem dois erros possiveis: aceitar preco velho (perigoso) ou jogar
    fora manchete boa (burro). Preco envelhece em minutos e **bloqueia** a decisao;
    manchete e voz de mercado envelhecem em horas e apenas **avisam** -- um dossie com
    noticia de ontem continua auditavel, um dossie com candle de ontem nao.

    `ok` falso significa evidencia de mercado vencida: o juiz recusa a decisao em vez de
    decidir com preco vencido (fail-closed).
    """
    market_window = window_minutes or MARKET_WINDOW_MINUTES
    candles = packet.get("market", {}).get("candles") or []
    collected = packet.get("collected_at")
    if not candles or not collected:
        return {"window_minutes": market_window, "newest_candle_age_minutes": None, "ok": False,
                "reason": "no candles or collected_at", "classes": {}, "warnings": []}
    try:
        newest = max(int(c["t"]) for c in candles)
        now = datetime.fromisoformat(collected)
        age = (now - datetime.fromtimestamp(newest / 1000, tz=timezone.utc)).total_seconds() / 60
    except (KeyError, TypeError, ValueError) as err:
        return {"window_minutes": market_window, "newest_candle_age_minutes": None, "ok": False,
                "reason": f"unreadable timestamps: {err}", "classes": {}, "warnings": []}

    age = round(age, 2)
    ok = age <= market_window
    classes = {"market": {"age_minutes": age, "window_minutes": market_window, "ok": ok, "blocking": True}}
    warnings = []

    articles = (packet.get("news") or {}).get("articles") or []
    seen = [a for a in (_age_minutes(a.get("seen"), now) for a in articles) if a is not None]
    if seen:
        newest_news = min(seen)
        news_ok = newest_news <= SLOW_WINDOW_MINUTES
        classes["news"] = {"age_minutes": newest_news, "window_minutes": SLOW_WINDOW_MINUTES, "ok": news_ok, "blocking": False}
        if not news_ok:
            warnings.append(f"newest news item is {newest_news:.0f} min old, window is {SLOW_WINDOW_MINUTES} min")

    voices = (packet.get("narrative") or {}).get("voices") or []
    published = [a for a in (_age_minutes(v.get("published"), now) for v in voices) if a is not None]
    if published:
        newest_voice = min(published)
        voice_ok = newest_voice <= SLOW_WINDOW_MINUTES
        classes["narrative"] = {"age_minutes": newest_voice, "window_minutes": SLOW_WINDOW_MINUTES, "ok": voice_ok, "blocking": False}
        if not voice_ok:
            warnings.append(f"newest voice is {newest_voice:.0f} min old, window is {SLOW_WINDOW_MINUTES} min")

    return {
        "window_minutes": market_window,
        "newest_candle_age_minutes": age,
        "ok": ok,
        "reason": "" if ok else f"newest candle is {age:.0f} min old, window is {market_window} min",
        "classes": classes,
        "warnings": warnings,
    }


def collect(symbol: str, interval: str = "1h", limit: int = 72, with_news: bool = True) -> dict:
    """Coleta um pacote de evidencia congelado para um simbolo."""
    packet = {
        "symbol": symbol.upper(),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": fetch_candles(symbol, interval, limit),
        "funding": fetch_funding(symbol),
    }
    if with_news:
        time.sleep(5)  # o GDELT aceita uma chamada a cada 5 segundos
        packet["news"] = fetch_news(symbol)
    # Frescor congelado no pacote: o juiz le daqui, entao o replay nao depende do relogio.
    packet["freshness"] = freshness(packet)
    return packet
