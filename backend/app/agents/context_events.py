"""Event and attention context for the 事件驱动 / 热度 analysts.

The gateway grants no news text, no announcement bodies and no social posts, so
these two roles are built on what actually exists:

* **events** – structured corporate events (forecast, express, dividend, lock-up
  expiry, insider trades, block trades, 龙虎榜), i.e. the *facts* behind a headline;
* **attention** – heat rankings (同花顺/东财热榜), concept limit-up lists, turnover,
  and shareholder-count change, i.e. a real proxy for A-share crowd attention.

Inventing a news narrative from nothing would be worse than saying "no news data".
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import duckdb

from app.data import store


def _rows(sql: str, params: list[Any]) -> list[tuple]:
    """Missing optional datasets (not yet backfilled) read as "no records"."""
    try:
        with store.session(read_only=True) as conn:
            return conn.execute(sql, params).fetchall()
    except duckdb.CatalogException:
        return []


def _one(sql: str, params: list[Any]) -> Optional[tuple]:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _f(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}"
    return str(value)


def _unit(value: Any, unit: str, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:,.{digits}f}{unit}"


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:+.{digits}f}%"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # A few datasets keep dates as 'YYYYMMDD' text.
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


# ------------------------------------------------------------------- events
def build_events(ts_code: str) -> dict[str, Any]:
    end = store.latest_trade_date() or date.today()

    forecast = _rows(
        "SELECT ann_date, end_date, type, p_change_min, p_change_max FROM forecast_vip "
        "WHERE ts_code = ? ORDER BY end_date DESC LIMIT 3",
        [ts_code],
    )
    express = _rows(
        "SELECT ann_date, end_date, revenue, n_income FROM express_vip "
        "WHERE ts_code = ? ORDER BY end_date DESC LIMIT 2",
        [ts_code],
    )
    dividends = _rows(
        "SELECT ann_date, end_date, div_proc, cash_div_tax, stk_div, ex_date FROM dividend "
        "WHERE ts_code = ? ORDER BY ann_date DESC LIMIT 4",
        [ts_code],
    )
    # The gateway only serves lock-ups up to the data date — there is no forward
    # announcement here — so report the recent window instead of an empty "future".
    floats = _one(
        "SELECT COUNT(*), SUM(float_share), MAX(TRY_CAST(strptime(float_date, '%Y%m%d') AS DATE)) "
        "FROM share_float WHERE ts_code = ? "
        "AND TRY_CAST(strptime(float_date, '%Y%m%d') AS DATE) BETWEEN ? AND ?",
        [ts_code, end - timedelta(days=90), end],
    )
    insider = _one(
        "SELECT COUNT(*), SUM(CASE WHEN in_de = 'DE' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN in_de = 'IN' THEN 1 ELSE 0 END) FROM stk_holdertrade "
        "WHERE ts_code = ? AND ann_date >= ?",
        [ts_code, end - timedelta(days=180)],
    )
    blocks = _one(
        "SELECT COUNT(*), SUM(amount) FROM block_trade WHERE ts_code = ? AND trade_date >= ?",
        [ts_code, end - timedelta(days=90)],
    )
    surveys = _one(
        "SELECT COUNT(*), MAX(surv_date) FROM stk_surv WHERE ts_code = ? AND surv_date >= ?",
        [ts_code, end - timedelta(days=180)],
    )

    return {
        "as_of": end.isoformat(),
        "forecasts": [
            {
                "ann_date": _iso(row[0]),
                "end_date": _iso(row[1]),
                "type": row[2],
                "change_min": row[3],
                "change_max": row[4],
            }
            for row in forecast
        ],
        "express": [
            {
                "ann_date": _iso(row[0]),
                "end_date": _iso(row[1]),
                "revenue": row[2],
                "net_income": row[3],
            }
            for row in express
        ],
        "dividends": [
            {
                "ann_date": _iso(row[0]),
                "end_date": _iso(row[1]),
                "progress": row[2],
                "cash_div": row[3],
                "stock_div": row[4],
                "ex_date": _iso(row[5]),
            }
            for row in dividends
        ],
        "lockup_90d": {
            "count": floats[0] if floats else 0,
            "shares": floats[1] if floats else None,
            "latest_date": _iso(floats[2]) if floats else None,
        },
        "insider": {
            "count": insider[0] if insider else 0,
            "sell_count": insider[1] if insider else 0,
            "buy_count": insider[2] if insider else 0,
        },
        "block_trades": {
            "count": blocks[0] if blocks else 0,
            "amount": blocks[1] if blocks else None,
        },
        "surveys": {
            "count": surveys[0] if surveys else 0,
            "latest": _iso(surveys[1]) if surveys else None,
        },
    }


def render_events(events: dict[str, Any]) -> str:
    lines = []
    if events["forecasts"]:
        latest = events["forecasts"][0]
        lines.append(
            f"【业绩预告】{latest['end_date']} 报告期，{latest['type']}，"
            f"变动 {_pct(latest['change_min'])} ~ {_pct(latest['change_max'])}"
            f"（公告 {latest['ann_date']}）"
        )
        if len(events["forecasts"]) > 1:
            history = "；".join(
                f"{item['end_date']} {item['type']}" for item in events["forecasts"][1:]
            )
            lines.append(f"【往期预告】{history}")
    else:
        lines.append("【业绩预告】近三期无记录")

    if events["express"]:
        latest = events["express"][0]
        lines.append(
            f"【业绩快报】{latest['end_date']}，营收 {_f(latest['revenue'], 0)}，"
            f"净利 {_f(latest['net_income'], 0)}"
        )

    if events["dividends"]:
        latest = events["dividends"][0]
        lines.append(
            f"【分红】最近一次 {latest['end_date']} {latest['progress']}，"
            f"每股派现 {_f(latest['cash_div'], 3)} 元，送股 {_f(latest['stock_div'], 3)}，"
            f"除权日 {latest['ex_date'] or '未定'}"
        )
    else:
        lines.append("【分红】近四次无记录")

    lockup = events.get("lockup_90d") or {}
    if lockup.get("count"):
        lines.append(
            f"【限售解禁·近90日】{lockup['count']} 批，合计 "
            f"{_f((lockup['shares'] or 0) / 1e8, 2)} 亿股，最近一次 {lockup['latest_date']}"
        )
    else:
        lines.append("【限售解禁·近90日】无解禁记录（数据源不提供未来解禁预告）")
    insider = events["insider"]
    if insider["count"]:
        lines.append(
            f"【股东增减持·近半年】{insider['count']} 条，减持 {insider['sell_count']} 条、"
            f"增持 {insider['buy_count']} 条"
        )
    else:
        lines.append("【股东增减持·近半年】无记录")

    blocks = events["block_trades"]
    if blocks["count"]:
        # Tushare reports block-trade amount in 万元.
        amount_yuan = (blocks["amount"] or 0) * 10000
        lines.append(
            f"【大宗交易·近90日】{blocks['count']} 笔，合计 {_f(amount_yuan / 1e8, 2)} 亿元"
        )
    else:
        lines.append("【大宗交易·近90日】无记录")

    surveys = events["surveys"]
    lines.append(
        f"【机构调研·近半年】{surveys['count']} 次"
        + (f"，最近 {surveys['latest']}" if surveys["latest"] else "")
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- attention
def build_attention(ts_code: str, symbol: str) -> dict[str, Any]:
    end = store.latest_trade_date() or date.today()
    hot = {}
    for table, label in (("ths_hot", "同花顺"), ("dc_hot", "东方财富")):
        row = _one(
            f"SELECT rank, hot, concept, data_type FROM {table} "
            f"WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1",
            [ts_code],
        )
        days = _one(
            f"SELECT COUNT(DISTINCT trade_date) FROM {table} "
            f"WHERE ts_code = ? AND trade_date >= ?",
            [ts_code, end - timedelta(days=20)],
        )
        hot[label] = {
            "rank": row[0] if row else None,
            "hot_value": row[1] if row else None,
            "concept": row[2] if row else None,
            "data_type": row[3] if row else None,
            "days_in_20d": days[0] if days else 0,
        }

    concepts = _rows(
        "SELECT name, up_nums, cons_nums, pct_chg FROM limit_cpt_list "
        "WHERE trade_date = ? ORDER BY rank LIMIT 6",
        [end],
    )
    holders = _rows(
        "SELECT end_date, holder_num FROM stk_holdernumber "
        "WHERE ts_code = ? ORDER BY end_date DESC LIMIT 2",
        [ts_code],
    )
    holder_change = None
    if len(holders) == 2 and holders[1][1]:
        holder_change = (holders[0][1] / holders[1][1] - 1.0) * 100.0

    return {
        "as_of": end.isoformat(),
        "hot": hot,
        "market_concepts": [
            {"name": row[0], "limit_up": row[1], "streak": row[2], "pct_chg": row[3]}
            for row in concepts
        ],
        "holder_number": {
            "latest_period": _iso(holders[0][0]) if holders else None,
            "count": holders[0][1] if holders else None,
            "change_pct": holder_change,
        },
    }


def render_attention(attention: dict[str, Any]) -> str:
    lines = []
    for label, item in attention["hot"].items():
        if item["rank"]:
            concept = ""
            raw = item.get("concept")
            if raw:
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, list) and parsed:
                        concept = f"，关联概念：{'、'.join(str(x) for x in parsed[:3])}"
                except (json.JSONDecodeError, TypeError):
                    concept = f"，关联概念：{str(raw)[:30]}"
            hot_value = (
                f"，热度值 {_f(item['hot_value'], 0)}" if item.get("hot_value") else ""
            )
            lines.append(
                f"【{label}热榜】当前第 {int(item['rank'])} 名{hot_value}，"
                f"近 20 日上榜 {item['days_in_20d']} 天{concept}"
            )
        else:
            lines.append(f"【{label}热榜】近 20 日未上榜（{item['days_in_20d']} 天）")

    if attention["market_concepts"]:
        top = "；".join(
            f"{item['name']}（涨停 {item['limit_up']} 家，{item['streak']}，{_pct(item['pct_chg'])}）"
            for item in attention["market_concepts"][:4]
        )
        lines.append(f"【今日涨停概念】{top}")

    holder = attention["holder_number"]
    if holder["count"]:
        lines.append(
            f"【股东户数】{holder['latest_period']} 为 {_f(holder['count'], 0)} 户，"
            f"环比 {_pct(holder['change_pct'])}（户数下降通常意味着筹码集中）"
        )
    else:
        lines.append("【股东户数】无记录")
    return "\n".join(lines)
