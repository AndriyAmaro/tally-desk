"""Quando uma nova decisao e devida.

O GitHub descarta e atrasa execucoes agendadas, e um unico gatilho por hora disparou a cada 2 a 6 horas
(o ledger cobriu 30% das horas do relogio). O workflow agora dispara varias vezes por hora e grava so
quando a ultima decisao e velha o bastante; sem isso, as tentativas extras duplicariam decisoes.
"""

from datetime import datetime, timezone


def _read(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def due(ledger: list[dict], now: datetime, min_minutes: float) -> bool:
    """True quando passou `min_minutes` desde a decisao mais recente. Falha aberta: ledger vazio ou hora
    ilegivel contam como devido, porque perder uma hora e pior do que repetir uma tentativa."""
    times = [_read((row.get("evidence") or {}).get("collected_at")) for row in ledger]
    times = [t for t in times if t is not None]
    if not times or len(times) < len(ledger):
        return True
    return (now - max(times)).total_seconds() / 60 >= min_minutes
