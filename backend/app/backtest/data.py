"""Panel reads for the backtest, executed inside an already-open connection.

Everything is loaded once per run: candidate bars, limit prices for those bars,
and the benchmark series. The engine then works on plain dictionaries, which
keeps the simulation testable without a database.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional, Sequence

QUOTE_CHUNK = 400  # symbols per IN (...) list; keeps statements small


def load_bars(
    conn, symbols: Iterable[str], start: date, end: date
) -> dict[str, dict[date, dict[str, float]]]:
    output: dict[str, dict[date, dict[str, float]]] = {}
    ordered = sorted({item for item in symbols if item})
    for offset in range(0, len(ordered), QUOTE_CHUNK):
        chunk = ordered[offset:offset + QUOTE_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT symbol, trade_date, open, close, adj_factor FROM daily "
            f"WHERE trade_date BETWEEN ? AND ? AND symbol IN ({placeholders})",
            [start, end, *chunk],
        ).fetchall()
        for symbol, day, open_price, close_price, factor in rows:
            output.setdefault(symbol, {})[day] = {
                "open": float(open_price) if open_price is not None else None,
                "close": float(close_price) if close_price is not None else None,
                "adj": float(factor) if factor is not None else 1.0,
            }
    return output


def load_limits(
    conn, ts_codes: Iterable[str], start: date, end: date
) -> dict[tuple[str, date], tuple[Optional[float], Optional[float]]]:
    output: dict[tuple[str, date], tuple[Optional[float], Optional[float]]] = {}
    ordered = sorted({item for item in ts_codes if item})
    for offset in range(0, len(ordered), QUOTE_CHUNK):
        chunk = ordered[offset:offset + QUOTE_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT ts_code, trade_date, up_limit, down_limit FROM stk_limit "
            f"WHERE trade_date BETWEEN ? AND ? AND ts_code IN ({placeholders})",
            [start, end, *chunk],
        ).fetchall()
        for ts_code, day, up, down in rows:
            output[(ts_code, day)] = (
                float(up) if up is not None else None,
                float(down) if down is not None else None,
            )
    return output


def load_benchmark(conn, ts_code: str, days: Sequence[date]) -> dict[date, float]:
    if not days:
        return {}
    rows = conn.execute(
        "SELECT trade_date, close FROM index_daily "
        "WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date",
        [ts_code, days[-1]],
    ).fetchall()
    if not rows:
        return {}
    known = {row[0]: float(row[1]) for row in rows if row[1] is not None}
    output: dict[date, float] = {}
    last: Optional[float] = None
    for day, value in sorted(known.items()):
        if day <= days[0]:
            last = value
    for day in days:
        last = known.get(day, last)
        if last is not None:
            output[day] = last
    return output


def close_adj(bar: Optional[dict[str, Any]]) -> Optional[float]:
    """Adj-adjusted close of a bar (None when the bar is missing)."""
    if not bar or bar.get("close") is None:
        return None
    return float(bar["close"]) * float(bar.get("adj") or 1.0)
