"""One stock, everything the detail view needs, in a single round trip."""

from __future__ import annotations

from typing import Any, Optional

from app.data import intraday, store
from app.db import connection


def _info(conn, symbol: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT symbol, ts_code, name, industry, board, asset_type, list_date "
        "FROM symbols WHERE symbol = ?",
        [symbol],
    ).fetchone()
    if row is None:
        return None
    return {
        "symbol": row[0],
        "ts_code": row[1],
        "name": row[2],
        "industry": row[3],
        "board": row[4],
        "asset_type": row[5],
        "list_date": row[6].isoformat() if row[6] else None,
    }


def _quote(conn, symbol: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT trade_date, close, pre_close, high, low, open, volume, amount, "
        "       close * COALESCE(adj_factor, 1) AS c_adj "
        "FROM daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
        [symbol],
    ).fetchone()
    basic = conn.execute(
        "SELECT pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv, turnover_rate, volume_ratio "
        "FROM daily_basic WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
        [symbol],
    ).fetchone()
    if row is None:
        return {}
    close = float(row[1] or 0)
    pre = float(row[2] or 0)
    return {
        "trade_date": row[0].isoformat(),
        "close": close,
        "pre_close": pre,
        "pct_chg": (close / pre - 1) * 100 if pre else None,
        "high": float(row[3] or 0),
        "low": float(row[4] or 0),
        "open": float(row[5] or 0),
        "volume": float(row[6] or 0),
        "amount": float(row[7] or 0),
        "pe_ttm": basic[0] if basic else None,
        "pb": basic[1] if basic else None,
        "ps_ttm": basic[2] if basic else None,
        "dv_ttm": basic[3] if basic else None,
        "total_mv": basic[4] if basic else None,
        "circ_mv": basic[5] if basic else None,
        "turnover_rate": basic[6] if basic else None,
        "volume_ratio": basic[7] if basic else None,
    }


RANGES = {"3m": 60, "6m": 120, "1y": 250, "3y": 750, "all": 10000}


def _chart(conn, symbol: str, limit: int = 250) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume, "
        "       close * COALESCE(adj_factor, 1) AS c_adj "
        "FROM daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
        [symbol, limit],
    ).fetchall()
    return [
        {
            "date": row[0].isoformat(),
            "open": float(row[1] or 0),
            "high": float(row[2] or 0),
            "low": float(row[3] or 0),
            "close": float(row[4] or 0),
            "volume": float(row[5] or 0),
            "c_adj": float(row[6] or 0),
        }
        for row in reversed(rows)
    ]


def _factors(conn, symbol: str) -> Optional[dict[str, Any]]:
    cursor = conn.execute("SELECT * FROM screen_snapshot WHERE symbol = ?", [symbol])
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [item[0] for item in cursor.description]
    payload = dict(zip(columns, row))
    payload.pop("trade_date", None)
    return payload


def _financial(conn, ts_code: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT ann_date, end_date, roe, netprofit_margin, grossprofit_margin, "
        "       debt_to_assets, or_yoy, netprofit_yoy "
        "FROM fina_indicator_vip WHERE ts_code = ? AND roe IS NOT NULL "
        "ORDER BY end_date DESC LIMIT 1",
        [ts_code],
    ).fetchone()
    if row is None:
        return None
    return {
        "ann_date": row[0].isoformat() if row[0] else None,
        "end_date": row[1].isoformat() if row[1] else None,
        "roe": row[2],
        "net_margin": row[3],
        "gross_margin": row[4],
        "debt_ratio": row[5],
        "revenue_yoy": row[6],
        "profit_yoy": row[7],
    }


def _runs(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, started_at, status, verdict FROM agent_runs "
            "WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    import json

    output = []
    for row in rows:
        verdict = json.loads(row["verdict"]) if row["verdict"] else {}
        output.append(
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "status": row["status"],
                "decision": verdict.get("decision"),
                "position_pct": verdict.get("final_position_pct"),
            }
        )
    return output


def _watching(symbol: str) -> bool:
    with connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
    return row is not None


def detail(symbol: str) -> Optional[dict[str, Any]]:
    with store.session(read_only=True) as conn:
        info = _info(conn, symbol)
        if info is None:
            return None
        return {
            "info": info,
            "quote": _quote(conn, symbol),
            "chart": _chart(conn, symbol),
            "factors": _factors(conn, symbol),
            "financial": _financial(conn, info["ts_code"]),
            "intraday": intraday.latest_or_cached(symbol),
            "runs": _runs(symbol),
            "watching": _watching(symbol),
        }


def history(symbol: str, span: str = "1y") -> Optional[dict[str, Any]]:
    """Daily bars for one symbol over a named span (``3m`` … ``all``)."""
    limit = RANGES.get(span, RANGES["1y"])
    with store.session(read_only=True) as conn:
        info = _info(conn, symbol)
        if info is None:
            return None
        bars = _chart(conn, symbol, limit)
    return {"symbol": symbol, "name": info["name"], "span": span, "bars": bars}
