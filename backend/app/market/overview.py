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
        limit: dict[str, Any] = {"up": 0, "down": 0, "date": limit_date}
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
            ladder = _optional(conn,
                """
                SELECT COUNT(*) FILTER (WHERE "limit" = 'U' AND COALESCE(limit_times, 1) = 1),
                       COUNT(*) FILTER (WHERE "limit" = 'U' AND limit_times = 2),
                       COUNT(*) FILTER (WHERE "limit" = 'U' AND limit_times >= 3),
                       MAX(limit_times) FILTER (WHERE "limit" = 'U'),
                       COUNT(*) FILTER (WHERE "limit" = 'Z')
                FROM limit_list_d WHERE trade_date = ?
                """,
                [limit_date],
            )
            ladder = ladder[0] if ladder else (0, 0, 0, 0, 0)
            first, second, third, height, broken = (int(v or 0) for v in ladder)
            limit.update(
                {
                    "first": first,
                    "second": second,
                    "third": third,
                    "height": height,
                    "broken": broken,
                    "seal_rate": round(limit["up"] / (limit["up"] + broken) * 100, 1)
                    if limit["up"] + broken
                    else None,
                }
            )

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
            # 申万一级（801xxx0，31 个）与二级（801xxx 非 0，约 150 个）分开返回；
            # 三级（85xxxx）与同花顺「行业板块」口径差太远，不展示。
            rows = _optional(conn,
                "SELECT ts_code, name, pct_change, amount FROM sw_daily "
                "WHERE trade_date = ? AND pct_change IS NOT NULL AND amount > 0 "
                "  AND ts_code LIKE '801%.SI' AND name NOT LIKE '申万%' "
                "ORDER BY pct_change DESC",
                [sector_date],
            )

            def _bucket(items):
                clean = [
                    {"name": row[1], "pct_chg": float(row[2]), "amount": float(row[3] or 0)}
                    for row in items
                ]
                return {
                    "top": clean[:6],
                    "bottom": list(reversed(clean[-6:])) if len(clean) > 6 else [],
                    "industries": len(clean),
                }

            level1 = [row for row in rows if row[0].endswith("0.SI")]
            level2 = [row for row in rows if not row[0].endswith("0.SI")]
            level1_bucket = _bucket(level1)
            total_amount = sum(float(row[3] or 0) for row in level1) or 1.0
            top5 = sum(float(row[3] or 0) for row in level1[:5])
            level1_bucket["concentration"] = round(top5 / total_amount * 100, 1)
            prev_date = None
            found = _optional(
                conn,
                "SELECT MAX(trade_date) FROM sw_daily WHERE trade_date < ?",
                [sector_date],
            )
            prev_date = found[0][0] if found else None
            if prev_date is not None:
                prev_rows = _optional(
                    conn,
                    "SELECT name, pct_change FROM sw_daily "
                    "WHERE trade_date = ? AND pct_change IS NOT NULL "
                    "  AND ts_code LIKE '801%.SI' AND ts_code LIKE '%0.SI' AND name NOT LIKE '申万%' "
                    "ORDER BY pct_change DESC LIMIT 5",
                    [prev_date],
                )
                today_map = {row[1]: float(row[2]) for row in level1}
                level1_bucket["continuity"] = [
                    {
                        "name": row[0],
                        "prev_pct": round(float(row[1]), 2),
                        "today_pct": round(today_map.get(row[0], 0.0), 2),
                    }
                    for row in prev_rows
                ]
            sectors = {
                "date": sector_date,
                "level1": level1_bucket,
                "level2": _bucket(level2),
                # 默认口径仍是一级，前端切换时用上面两块
                "top": _bucket(level1)["top"],
                "bottom": _bucket(level1)["bottom"],
                "concentration": level1_bucket["concentration"],
                "continuity": level1_bucket.get("continuity", []),
                "industries": len(level1),
            }

        distribution = _optional(
            conn,
            """
            SELECT COUNT(*) FILTER (WHERE pct >= 0.05),
                   COUNT(*) FILTER (WHERE pct >= 0.02 AND pct < 0.05),
                   COUNT(*) FILTER (WHERE pct > 0 AND pct < 0.02),
                   COUNT(*) FILTER (WHERE pct = 0),
                   COUNT(*) FILTER (WHERE pct < 0 AND pct > -0.02),
                   COUNT(*) FILTER (WHERE pct <= -0.02 AND pct > -0.05),
                   COUNT(*) FILTER (WHERE pct <= -0.05)
            FROM (
                SELECT d.close / NULLIF(d.pre_close, 0) - 1 AS pct
                FROM daily d JOIN symbols s ON s.symbol = d.symbol
                WHERE s.asset_type = 'stock' AND d.trade_date = ?
            )
            """,
            [latest],
        )
        buckets = ("涨≥5%", "涨2~5%", "涨0~2%", "平盘", "跌0~2%", "跌2~5%", "跌≥5%")
        raw_dist = distribution[0] if distribution else (0,) * len(buckets)
        distribution = [
            {"label": label, "count": int((raw_dist[index] if index < len(raw_dist) else 0) or 0)}
            for index, label in enumerate(buckets)
        ]

        money_flow = _optional(
            conn,
            """
            SELECT s.industry, SUM(m.net_mf_amount) AS net
            FROM moneyflow m JOIN symbols s ON s.symbol = split_part(m.ts_code, '.', 1)
            WHERE m.trade_date = ? AND s.industry IS NOT NULL AND s.industry <> ''
            GROUP BY 1 ORDER BY net DESC
            """,
            [money_date],
        )
        flow_rows = [
            {"name": row[0], "net": float(row[1] or 0)} for row in money_flow
        ] if money_date is not None else []

        north_date = None
        north: dict[str, Any] = {"date": None, "net": None, "hgt": None, "sgt": None}
        rows = _optional(conn, "SELECT MAX(trade_date) FROM moneyflow_hsgt", [])
        north_date = rows[0][0] if rows else None
        if north_date is not None:
            found = _optional(
                conn,
                "SELECT CAST(north_money AS DOUBLE), CAST(hgt AS DOUBLE), CAST(sgt AS DOUBLE) "
                "FROM moneyflow_hsgt WHERE trade_date = ?",
                [north_date],
            )
            if found:
                north = {
                    "date": north_date,
                    "net": float(found[0][0] or 0),
                    "hgt": float(found[0][1] or 0),
                    "sgt": float(found[0][2] or 0),
                }

        def _ret5(code: str):
            rows = _optional(
                conn,
                "SELECT close FROM index_daily WHERE ts_code = ? AND trade_date <= ? "
                "ORDER BY trade_date DESC LIMIT 6",
                [code, latest],
            )
            if len(rows) < 6 or not rows[5][0]:
                return None
            return round((float(rows[0][0]) / float(rows[5][0]) - 1) * 100, 2)

        large, small = _ret5("000300.SH"), _ret5("000852.SH")
        style = {
            "large_ret5": large,
            "small_ret5": small,
            "spread": round(large - small, 2) if large is not None and small is not None else None,
        }

        margin_rows = _optional(
            conn,
            "SELECT trade_date, SUM(CAST(rzye AS DOUBLE)) FROM margin_detail "
            "WHERE trade_date <= ? GROUP BY 1 ORDER BY 1 DESC LIMIT 6",
            [latest],
        )
        margin: dict[str, Any] = {"date": None, "balance": None, "change_5d_pct": None}
        if margin_rows:
            latest_margin = margin_rows[0]
            base = margin_rows[5] if len(margin_rows) > 5 else None
            margin = {
                "date": latest_margin[0],
                "balance": float(latest_margin[1] or 0),
                "change_5d_pct": round(
                    (float(latest_margin[1] or 0) / float(base[1]) - 1) * 100, 2
                )
                if base and base[1]
                else None,
            }

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
        "north": north,
        "margin": margin,
        "style": style,
        "sectors": sectors,
        "distribution": distribution,
        "money_by_industry": {
            "date": money_date,
            "top": flow_rows[:5],
            "bottom": list(reversed(flow_rows[-5:])) if len(flow_rows) > 5 else [],
        },
        "hot": hot,
        "hot_date": hot_date.isoformat() if hot_date else None,
    }
