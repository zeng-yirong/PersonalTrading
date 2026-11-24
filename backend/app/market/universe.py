"""Market-wide browser: every A-share, ETF, index and (index-only) Hong Kong.

This is a read-only listing over the snapshot / index tables — it deliberately
does not build anything new. The only nuance is that **Hong Kong stocks are not
in the local panel**; the HK tab shows the Hang Seng index and says so instead of
pretending the list is complete.
"""

from __future__ import annotations

from typing import Any

from app.data import store

ASSETS = ("stock", "etf", "index", "hk")

SORT_COLUMNS = {
    "stock": {
        "symbol": "symbol",
        "name": "name",
        "close": "close",
        "pct_chg": "pct_chg",
        "amount": "amount",
        "total_mv": "total_mv",
        "pe_ttm": "pe_ttm",
        "turnover_rate": "turnover_rate",
        "ret20": "ret20",
        "fd_pead": "fd_pead",
    },
    "etf": {
        "symbol": "symbol",
        "name": "name",
        "close": "close",
        "pct_chg": "pct_chg",
        "amount": "amount",
        "turnover_rate": "turnover_rate",
        "ret20": "ret20",
    },
    "index": {
        "code": "i.ts_code",
        "name": "b.name",
        "close": "i.close",
        "pct_chg": "i.pct_chg",
        "amount": "i.amount",
    },
}

DEFAULT_SORT = {"stock": "pct_chg", "etf": "amount", "index": "pct_chg", "hk": "pct_chg"}


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def list_assets(
    asset: str,
    q: str = "",
    sort: str = "",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    if asset not in ASSETS:
        raise ValueError(f"asset 只能是 {'/'.join(ASSETS)}")
    page = max(1, int(page))
    page_size = max(10, min(int(page_size), 200))
    offset = (page - 1) * page_size
    direction = "DESC" if str(order).lower() != "asc" else "ASC"
    keyword = (q or "").strip()
    with store.session(read_only=True) as conn:
        if asset in {"stock", "etf"}:
            where = ["asset_type = ?", "close IS NOT NULL"]
            params: list[Any] = [asset]
            if keyword:
                where.append("(symbol LIKE ? OR name LIKE ?)")
                params.extend([_like(keyword), _like(keyword)])
            column = SORT_COLUMNS[asset].get(sort, DEFAULT_SORT[asset])
            total = conn.execute(
                f"SELECT COUNT(*) FROM screen_snapshot WHERE {' AND '.join(where)}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT symbol, name, industry, board, close, pct_chg, amount, "
                f"       total_mv, pe_ttm, turnover_rate, ret20, fd_pead "
                f"FROM screen_snapshot WHERE {' AND '.join(where)} "
                f"ORDER BY {column} {direction} NULLS LAST LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
            keys = ("symbol", "name", "industry", "board", "close", "pct_chg", "amount",
                    "total_mv", "pe_ttm", "turnover_rate", "ret20", "fd_pead")
            payload = [dict(zip(keys, row)) for row in rows]
            as_of = conn.execute("SELECT MAX(trade_date) FROM screen_snapshot").fetchone()[0]
            return {
                "asset": asset,
                "as_of": as_of.isoformat() if as_of else None,
                "total": int(total or 0),
                "page": page,
                "page_size": page_size,
                "rows": payload,
            }

        if asset == "index":
            has_basic = store.table_exists(conn, "index_basic")
            join = "LEFT JOIN index_basic b ON b.ts_code = i.ts_code" if has_basic else ""
            name_expr = "COALESCE(b.name, i.ts_code)" if has_basic else "i.ts_code"
            category_expr = "b.category" if has_basic else "NULL"
            sort_map = {
                "code": "i.ts_code",
                "name": name_expr,
                "close": "i.close",
                "pct_chg": "i.pct_chg",
                "amount": "i.amount",
            }
            where = [
                "i.trade_date = (SELECT MAX(trade_date) FROM index_daily)",
                "i.ts_code NOT LIKE '%HKD%'",
                "i.ts_code NOT LIKE '%USD%'",
                "i.ts_code NOT LIKE '%CNY%'",
                f"COALESCE({category_expr}, '') NOT IN ('债券指数', '基金指数', '多资产指数')",
            ]
            params = []
            if keyword:
                where.append(f"(i.ts_code LIKE ? OR {name_expr} LIKE ?)")
                params.extend([_like(keyword), _like(keyword)])
            column = sort_map.get(sort, DEFAULT_SORT["index"])
            base = f"FROM index_daily i {join} WHERE {' AND '.join(where)}"
            total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT i.ts_code, {name_expr}, {category_expr}, i.close, "
                f"       i.pct_chg, i.amount, i.trade_date {base} "
                f"ORDER BY {column} {direction} NULLS LAST LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
            as_of = conn.execute("SELECT MAX(trade_date) FROM index_daily").fetchone()[0]
            return {
                "asset": asset,
                "as_of": as_of.isoformat() if as_of else None,
                "note": "指数列表只含每日更新的主要指数（上证/深成/创业板/沪深300/中证500/中证1000）；"
                        "其余指数是历史回填，未纳入每日更新。",
                "total": int(total or 0),
                "page": page,
                "page_size": page_size,
                "rows": [
                    {
                        "code": row[0],
                        "name": row[1],
                        "category": row[2],
                        "close": float(row[3]) if row[3] is not None else None,
                        "pct_chg": float(row[4]) if row[4] is not None else None,
                        "amount": float(row[5]) if row[5] is not None else None,
                        "date": row[6].isoformat() if row[6] else None,
                    }
                    for row in rows
                ],
            }

        # 港股：本地面板没有港股个股，只有恒生指数
        rows = []
        if store.table_exists(conn, "index_global"):
            rows = conn.execute(
                "SELECT trade_date, close, pct_chg FROM index_global "
                "WHERE ts_code = 'HSI' ORDER BY trade_date DESC LIMIT 1"
            ).fetchall()
        hk_rows = [
            {
                "code": "HSI",
                "name": "恒生指数",
                "close": float(rows[0][1]) if rows[0][1] is not None else None,
                "pct_chg": float(rows[0][2]) if rows[0][2] is not None else None,
            }
        ] if rows else []
        return {
            "asset": asset,
            "as_of": rows[0][0].isoformat() if rows else None,
            "total": len(hk_rows),
            "page": 1,
            "page_size": page_size,
            "rows": hk_rows,
            "note": "本地面板未接入港股个股与港股行业数据，仅显示恒生指数；如需港股个股行情，需要新增数据源。",
        }
