"""Market-wide snapshot for the 大盘 module: indices, breadth, sectors, money."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.data import store

_A_SHARE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

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

        breadth_rows = _optional(
            conn,
            "SELECT COALESCE(s.asset_type, 'other'), "
            "       COUNT(*) FILTER (WHERE d.close > d.pre_close), "
            "       COUNT(*) FILTER (WHERE d.close < d.pre_close), "
            "       COUNT(*) FILTER (WHERE d.close = d.pre_close), "
            "       COALESCE(SUM(d.amount), 0) "
            "FROM daily d LEFT JOIN symbols s ON s.symbol = d.symbol "
            "WHERE d.trade_date = ? GROUP BY 1",
            [latest],
        )
        breadth: dict[str, Any] = {}
        for asset_type, up, down, flat, amount in breadth_rows:
            breadth[str(asset_type)] = {
                "up": int(up or 0),
                "down": int(down or 0),
                "flat": int(flat or 0),
                "amount": float(amount or 0),
            }
        # 港股不在本地面板里（symbols 只有 stock/etf），显式给 null 而不是编一个数。
        breadth.setdefault("hk", None)

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

        hot_date = None
        hot: list[dict[str, Any]] = []
        rows = _optional(conn, "SELECT MAX(trade_date) FROM ths_hot", [])
        hot_date = rows[0][0] if rows else None
        if hot_date is not None:
            rows = _optional(
                conn,
                "SELECT h.ts_code, COALESCE(s.name, h.ts_name), h.rank, h.pct_change, h.hot "
                "FROM ths_hot h LEFT JOIN symbols s "
                "  ON s.symbol = split_part(h.ts_code, '.', 1) "
                "WHERE h.trade_date = ? AND h.data_type = '热股' "
                "QUALIFY row_number() OVER (PARTITION BY h.ts_code ORDER BY h.rank) = 1 "
                "ORDER BY h.rank ASC LIMIT 30",
                [hot_date],
            )
            for row in rows:
                code = str(row[0] or "")
                # 热榜混有港股/基金/期货代码，这里只保留 A 股，避免点进详情是空的。
                if not _A_SHARE.match(code):
                    continue
                hot.append(
                    {
                        "code": code,
                        "name": row[1],
                        "rank": row[2],
                        "pct_chg": float(row[3] or 0),
                        "hot": float(row[4] or 0),
                    }
                )
                if len(hot) >= 10:
                    break

    return {
        "as_of": latest.isoformat() if latest else None,
        "prev_date": prev.isoformat() if prev else None,
        "indices": indices,
        "breadth": breadth,
        "limit": limit,
        "money": {"net_mf_amount": net_mf, "date": money_date},
        "sectors": sectors,
        "hot": hot,
        "hot_date": hot_date.isoformat() if hot_date else None,
    }
