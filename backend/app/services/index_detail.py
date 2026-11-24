"""Index detail: the six daily-updated indices shown in 全市场.

Indices live in ``index_daily`` rather than ``daily`` and have no fundamentals,
so the payload mirrors the stock detail shape but fills those blocks with nulls;
the frontend reuses the same view and hides the sections that need a company.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.data import store

RANGES = {"3m": 60, "6m": 120, "1y": 250, "3y": 750, "all": 10000}
PERIODS = ("day", "week", "month")
PERIOD_COUNTS = {
    "day": RANGES,
    "week": {"3m": 13, "6m": 26, "1y": 52, "3y": 156, "all": 10000},
    "month": {"3m": 3, "6m": 6, "1y": 12, "3y": 36, "all": 10000},
}


def _info(conn, code: str) -> Optional[dict[str, Any]]:
    if store.table_exists(conn, "index_basic"):
        row = conn.execute(
            "SELECT i.ts_code, COALESCE(b.name, i.ts_code), b.category, b.publisher "
            "FROM index_daily i LEFT JOIN index_basic b ON b.ts_code = i.ts_code "
            "WHERE i.ts_code = ? LIMIT 1",
            [code],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT ts_code, ts_code, '指数', '' FROM index_daily WHERE ts_code = ? LIMIT 1",
            [code],
        ).fetchone()
    if row is None:
        return None
    return {
        "symbol": row[0],
        "name": row[1],
        "category": row[2] or "指数",
        "publisher": row[3] or "",
    }


def _bars(conn, code: str, limit: int = 250) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, vol, pre_close, pct_chg, amount "
        "FROM index_daily WHERE ts_code = ? ORDER BY trade_date DESC LIMIT ?",
        [code, limit],
    ).fetchall()
    return [
        {
            "date": row[0].isoformat(),
            "open": float(row[1] or 0),
            "high": float(row[2] or 0),
            "low": float(row[3] or 0),
            "close": float(row[4] or 0),
            "volume": float(row[5] or 0),
            "c_adj": float(row[4] or 0),
            "pre_close": float(row[6]) if row[6] is not None else None,
            "pct_chg": float(row[7]) if row[7] is not None else None,
            "amount": float(row[8]) if row[8] is not None else None,
        }
        for row in reversed(rows)
    ]


def _aggregate(conn, code: str, period: str, limit: int) -> list[dict[str, Any]]:
    bucket = (
        "CAST(date_trunc('week', trade_date) AS DATE)"
        if period == "week"
        else "CAST(date_trunc('month', trade_date) AS DATE)"
    )
    rows = conn.execute(
        f"SELECT {bucket} AS bucket, arg_min(open, trade_date), max(high), min(low), "
        "       arg_max(close, trade_date), sum(vol) "
        "FROM index_daily WHERE ts_code = ? GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
        [code, limit],
    ).fetchall()
    return [
        {
            "date": row[0].isoformat(),
            "open": float(row[1] or 0),
            "high": float(row[2] or 0),
            "low": float(row[3] or 0),
            "close": float(row[4] or 0),
            "volume": float(row[5] or 0),
            "c_adj": float(row[4] or 0),
        }
        for row in reversed(rows)
    ]


def detail(code: str) -> Optional[dict[str, Any]]:
    with store.session(read_only=True) as conn:
        info = _info(conn, code)
        if info is None:
            return None
        chart = _bars(conn, code, PERIOD_COUNTS["day"]["1y"])
    latest = chart[-1] if chart else {}
    return {
        "info": {
            "symbol": info["symbol"],
            "ts_code": info["symbol"],
            "name": info["name"],
            "industry": info["category"],
            "board": "指数",
            "asset_type": "index",
            "list_date": None,
        },
        "quote": {
            "trade_date": latest.get("date"),
            "close": latest.get("close"),
            "pre_close": latest.get("pre_close"),
            "pct_chg": latest.get("pct_chg"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "open": latest.get("open"),
            "volume": latest.get("volume"),
            "amount": latest.get("amount"),
            "pe_ttm": None,
            "pb": None,
            "ps_ttm": None,
            "dv_ttm": None,
            "total_mv": None,
            "circ_mv": None,
            "turnover_rate": None,
            "volume_ratio": None,
        },
        "chart": chart,
        "factors": None,
        "financial": None,
        "intraday": None,
        "runs": [],
        "watching": False,
        "kind": "index",
    }


def history(code: str, span: str = "1y", period: str = "day") -> Optional[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period 只能是 {'/'.join(PERIODS)}")
    limit = PERIOD_COUNTS[period].get(span, PERIOD_COUNTS[period]["1y"])
    with store.session(read_only=True) as conn:
        info = _info(conn, code)
        if info is None:
            return None
        bars = _bars(conn, code, limit) if period == "day" else _aggregate(conn, code, period, limit)
    return {
        "symbol": code,
        "name": info["name"],
        "span": span,
        "period": period,
        "bars": bars,
    }
