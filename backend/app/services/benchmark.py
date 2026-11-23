"""Benchmark index series, forward-filled onto the trading calendar.

Index data is pulled per code rather than per day, so its last bar can lag the
stock panel by a few hours. Carrying the previous close forward keeps an equity
curve comparable on days the index has no row yet, instead of leaving a hole.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.data import store

BENCHMARK = "000300.SH"


def series(
    start: date,
    end: date,
    ts_code: str = BENCHMARK,
    days: Optional[list[date]] = None,
) -> dict[date, float]:
    """Close per trading day in ``[start, end]``; empty when the index is absent."""
    try:
        with store.session(read_only=True) as conn:
            rows = conn.execute(
                "SELECT trade_date, close FROM index_daily "
                "WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date",
                [ts_code, end],
            ).fetchall()
    except Exception:  # noqa: BLE001 - index table may be missing on a fresh panel
        return {}
    if not rows:
        return {}

    known = {row[0]: float(row[1]) for row in rows if row[1] is not None}
    if not known:
        return {}

    calendar = days if days is not None else store.get_trade_days(start, end)
    output: dict[date, float] = {}
    last: Optional[float] = None
    for day, value in sorted(known.items()):
        if day <= start:
            last = value
    for day in calendar:
        last = known.get(day, last)
        if last is not None:
            output[day] = last
    return output
