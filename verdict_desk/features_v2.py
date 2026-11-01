"""Indicadores tecnicos avancados para o Verdict Desk v2.

Adiciona: RSI, MACD, Bollinger Bands, volume confirmation, stochastic.
Todos deterministicos, calculados so a partir do pacote de evidencia.
"""

BULLISH_WORDS = ("surge", "rally", "record", "approval", "inflow", "adoption", "gain", "soar", "bull")
BEARISH_WORDS = ("crash", "plunge", "hack", "ban", "lawsuit", "outflow", "liquidat", "drop", "bear", "fraud")


def ema(values: list[float], period: int) -> float:
    k = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * k + current * (1 - k)
    return current


def sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def rsi(closes: list[float], period: int = 14) -> float:
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    # Signal line: EMA of MACD values (approximated)
    macd_values = []
    for i in range(slow, len(closes) + 1):
        ef = ema(closes[:i], fast)
        es = ema(closes[:i], slow)
        macd_values.append(ef - es)
    signal_line = ema(macd_values, signal) if len(macd_values) >= signal else macd_line
    histogram = macd_line - signal_line
    return {"macd": round(macd_line, 6), "signal": round(signal_line, 6), "histogram": round(histogram, 6)}


def bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> dict:
    recent = closes[-period:]
    middle = sma(closes, period)
    variance = sum((c - middle) ** 2 for c in recent) / period
    std = variance ** 0.5
    return {
        "upper": round(middle + num_std * std, 6),
        "middle": round(middle, 6),
        "lower": round(middle - num_std * std, 6),
        "pct_b": round((closes[-1] - (middle - num_std * std)) / (2 * num_std * std), 4) if std > 0 else 0.5,
    }


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

    macd_vals = macd(closes)
    bb = bollinger(closes)
    rsi_val = rsi(closes)

    out = {
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
        # Novos indicadores v2
        "rsi_14": round(rsi_val, 4),
        "macd": macd_vals["macd"],
        "macd_signal": macd_vals["signal"],
        "macd_histogram": macd_vals["histogram"],
        "bb_upper": bb["upper"],
        "bb_middle": bb["middle"],
        "bb_lower": bb["lower"],
        "bb_pct_b": bb["pct_b"],
    }
    # Frescor viaja congelado do pacote para dentro das features, entao o replay
    # reconstroi exatamente o mesmo veredito. Dossies antigos nao tem a chave e
    # continuam reproduzindo byte a byte (a chave so entra quando existe).
    if "freshness" in packet:
        out["freshness"] = packet["freshness"]
    return out
