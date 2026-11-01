"""Indicadores deterministicos calculados so a partir do pacote de evidencia."""

BULLISH_WORDS = ("surge", "rally", "record", "approval", "inflow", "adoption", "gain", "soar", "bull")
BEARISH_WORDS = ("crash", "plunge", "hack", "ban", "lawsuit", "outflow", "liquidat", "drop", "bear", "fraud")


def ema(values: list[float], period: int) -> float:
    k = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * k + current * (1 - k)
    return current


def news_tone(articles: list[dict]) -> dict:
    bull = [a for a in articles if any(w in a["title"].lower() for w in BULLISH_WORDS)]
    bear = [a for a in articles if any(w in a["title"].lower() for w in BEARISH_WORDS)]
    return {"bullish": len(bull), "bearish": len(bear), "total": len(articles),
            "bullish_titles": [a["title"] for a in bull[:3]], "bearish_titles": [a["title"] for a in bear[:3]]}


def compute(packet: dict) -> dict:
    candles = packet["market"]["candles"]
    closes = [c["c"] for c in candles]
    volumes = [c["v"] for c in candles]
    last = closes[-1]
    lookback = candles[-25:-1]
    range_high = max(c["h"] for c in lookback)
    range_low = min(c["l"] for c in lookback)
    true_ranges = [c["h"] - c["l"] for c in candles[-14:]]
    return {
        "last_close": last,
        "ema_fast": round(ema(closes, 12), 6),
        "ema_slow": round(ema(closes, 48), 6),
        "return_24": round((last / closes[-25] - 1) * 100, 4),
        "range_high_24": range_high,
        "range_low_24": range_low,
        "volume_ratio": round(volumes[-1] / (sum(volumes[-25:-1]) / 24), 4),
        "atr_14": round(sum(true_ranges) / len(true_ranges), 6),
        "funding_rate": packet["funding"]["rate"],
        "news": news_tone(packet.get("news", {}).get("articles", [])),
    }
