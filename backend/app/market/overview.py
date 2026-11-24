"""Market-wide snapshot for the 大盘 module: indices, breadth, sectors, money."""

from __future__ import annotations

from typing import Any, Optional

from app.data import store

INDICES = (
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
)


def _row_dict(row, columns: list[str]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {key: row[index] for index, key in enumerate(columns)}


def _optional(conn, sql: str, params: list[Any]) -> list[Any]:
    """Optional datasets: a missing table/source degrades to an empty block."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 - overview is best-effort
        return []


def overview() -> dict[str, Any]:
    with store.session(read_only=True) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if latest is None:
            return {"as_of": None, "empty": True}
        prev = conn.execute(
            "SELECT MAX(trade_date) FROM daily WHERE trade_date < ?", [latest]
        ).fetchone()[0]

        indices = []
        for code, label in INDICES:
            found = _optional(
                conn,
                "SELECT close, pct_chg, amount FROM index_daily "
                "WHERE ts_code = ? AND trade_date = ?",
                [code, latest],
            )
            if not found:
                continue
            row = found[0]
            indices.append(
                {
                    "code": code,
                    "label": label,
                    "close": float(row[0] or 0),
                    "pct_chg": float(row[1] or 0),
                    "amount": float(row[2] or 0),
                }
            )

        breadth = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE close > pre_close), "
            "       COUNT(*) FILTER (WHERE close < pre_close), "
            "       COUNT(*) FILTER (WHERE close = pre_close), "
            "       COALESCE(SUM(amount), 0) "
            "FROM daily WHERE trade_date = ?",
            [latest],
        ).fetchone()

        rows = _optional(conn, "SELECT MAX(trade_date) FROM limit_list_d", [])
        limit_date = rows[0][0] if rows else None
        limit = {"up": 0, "down": 0, "date": limit_date}
        if limit_date is not None:
            rows = conn.execute(
                'SELECT "limit", COUNT(*) FROM limit_list_d WHERE trade_date = ? GROUP BY 1',
                [limit_date],
            ).fetchall()
            for code, count in rows:
                if code == "U":
                    limit["up"] = int(count)
                elif code == "D":
                    limit["down"] = int(count)

        rows = _optional(conn, "SELECT MAX(trade_date) FROM moneyflow", [])
        money_date = rows[0][0] if rows else None
        net_mf = None
        if money_date is not None:
            value = conn.execute(
                "SELECT SUM(net_mf_amount) FROM moneyflow WHERE trade_date = ?",
                [money_date],
            ).fetchone()[0]
            net_mf = float(value) if value is not None else None

        rows = _optional(conn, "SELECT MAX(trade_date) FROM sw_daily", [])
        sector_date = rows[0][0] if rows else None
        sectors: dict[str, Any] = {"top": [], "bottom": [], "date": sector_date}
        if sector_date is not None:
            rows = conn.execute(
                "SELECT name, pct_change, amount FROM sw_daily "
                "WHERE trade_date = ? AND pct_change IS NOT NULL AND amount > 0 "
                "ORDER BY pct_change DESC",
                [sector_date],
            ).fetchall()
            clean = [
                {"name": row[0], "pct_chg": float(row[1]), "amount": float(row[2] or 0)}
                for row in rows
            ]
            sectors["top"] = clean[:6]
            sectors["bottom"] = list(reversed(clean[-6:])) if len(clean) > 6 else []

        rows = _optional(conn, "SELECT MAX(trade_date) FROM ths_hot", [])
        hot_date = rows[0][0] if rows else None
        hot = []
        if hot_date is not None:
            rows = conn.execute(
                "SELECT ts_code, ts_name, rank, pct_change, hot FROM ths_hot "
                "WHERE trade_date = ? ORDER BY rank ASC LIMIT 10",
                [hot_date],
            ).fetchall()
            hot = [
                {
                    "code": row[0],
                    "name": row[1],
                    "rank": row[2],
                    "pct_chg": float(row[3] or 0),
                    "hot": float(row[4] or 0),
                }
                for row in rows
            ]

    return {
        "as_of": latest.isoformat() if latest else None,
        "prev_date": prev.isoformat() if prev else None,
        "indices": indices,
        "breadth": {
            "up": int(breadth[0] or 0),
            "down": int(breadth[1] or 0),
            "flat": int(breadth[2] or 0),
            "amount": float(breadth[3] or 0),
        },
        "limit": limit,
        "money": {"net_mf_amount": net_mf, "date": money_date},
        "sectors": sectors,
        "hot": hot,
    }
