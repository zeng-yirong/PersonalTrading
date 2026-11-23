"""Market-wide and sector context for the 大盘 / 板块 analysts.

Both answer "where are we" rather than "is this stock good": the market regime
sets the baseline (a stock's +10% means little in a +8% tape) and the sector view
tells you whether the move is idiosyncratic or just rotation.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import duckdb

from app.data import store

MAIN_INDICES = (
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("399006.SZ", "创业板指"),
)

# Shenwan level-1 industry codes (the 31 broad sectors used for rotation).
SW_LEVEL1 = (
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
)


def _rows(sql: str, params: list[Any]) -> list[tuple]:
    """Missing optional tables degrade to "no data", not a crashed run."""
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


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:+.{digits}f}%"


def _unit(value: Any, unit: str, digits: int = 2) -> str:
    if value is None:
        return "数据缺失"
    return f"{value:,.{digits}f}{unit}"


def _index_returns(ts_code: str, days: int = 60) -> list[float]:
    rows = _rows(
        "SELECT close FROM index_daily WHERE ts_code = ? ORDER BY trade_date DESC LIMIT ?",
        [ts_code, days + 1],
    )
    return [row[0] for row in rows if row[0] is not None][::-1]


def _latest_complete_date(
    table: str, *, coverage: float = 0.8, lookback: int = 25
) -> Optional[date]:
    """Most recent date whose row count looks complete.

    Tushare publishes margin data with a lag, so "the latest date" is often a
    half-filled file (2019 rows instead of 4491). Summing it produced a fake
    -54% two-week change, so pick the newest date that has near-median coverage.
    """
    rows = _rows(
        f"SELECT trade_date, COUNT(*) FROM {table} "
        f"GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
        [lookback],
    )
    if not rows:
        return None
    counts = sorted(row[1] for row in rows)
    median = counts[len(counts) // 2]
    for row in rows:
        if row[1] >= median * coverage:
            return row[0]
    return rows[-1][0]


# ------------------------------------------------------------------- market
def build_market_regime() -> dict[str, Any]:
    end = store.latest_trade_date()
    indices = []
    for ts_code, label in MAIN_INDICES:
        closes = _index_returns(ts_code, 65)
        if len(closes) < 2:
            continue
        def ret(offset: int) -> Optional[float]:
            return (closes[-1] / closes[-1 - offset] - 1.0) * 100.0 if len(closes) > offset else None

        indices.append(
            {
                "name": label,
                "ts_code": ts_code,
                "close": closes[-1],
                "return_5d": ret(5),
                "return_20d": ret(20),
                "return_60d": ret(60),
            }
        )

    breadth = None
    turnover = None
    margin = None
    margin_date = _latest_complete_date("margin_detail")
    breadth_date = _latest_complete_date("limit_list_d")
    if end:
        row = _one(
            "SELECT COUNT(*) FILTER (WHERE \"limit\" = 'U'), "
            "COUNT(*) FILTER (WHERE \"limit\" = 'D'), "
            "COUNT(*) FILTER (WHERE \"limit\" = 'Z') "
            "FROM limit_list_d WHERE trade_date = ?",
            [breadth_date or end],
        )
        if row and (row[0] or row[1]):
            breadth = {
                "as_of": (breadth_date or end).isoformat(),
                "limit_up": row[0],
                "limit_down": row[1],
                "broken": row[2],
            }

        row = _one(
            "SELECT SUM(amount), COUNT(*) FROM daily WHERE trade_date = ?", [end]
        )
        row20 = _one(
            "SELECT AVG(total) FROM (SELECT SUM(amount) AS total FROM daily "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20)",
            [],
        )
        if row and row[0]:
            turnover = {
                "amount": row[0],
                "avg_amount_20d": row20[0] if row20 else None,
                "ratio_vs_20d": (row[0] / row20[0]) if row20 and row20[0] else None,
            }

        row = _one("SELECT SUM(rzye) FROM margin_detail WHERE trade_date = ?", [margin_date])
        old = _one(
            "SELECT SUM(rzye) FROM margin_detail WHERE trade_date = ("
            "SELECT MAX(trade_date) FROM margin_detail WHERE trade_date <= ?)",
            [margin_date - timedelta(days=30)],
        )
        if row and row[0]:
            margin = {
                "as_of": margin_date.isoformat() if margin_date else None,
                "balance": row[0],
                "change_20d_pct": (row[0] / old[0] - 1.0) * 100.0 if old and old[0] else None,
            }

    return {
        "as_of": end.isoformat() if end else None,
        "indices": indices,
        "breadth": breadth,
        "turnover": turnover,
        "margin": margin,
    }


def render_market_regime(regime: dict[str, Any]) -> str:
    lines = ["【指数表现】"]
    for item in regime["indices"]:
        lines.append(
            f"  {item['name']} 收 {_f(item['close'])}，5/20/60 日 "
            f"{_pct(item['return_5d'])} / {_pct(item['return_20d'])} / {_pct(item['return_60d'])}"
        )
    breadth = regime.get("breadth") or {}
    if breadth:
        lines.append(
            f"【市场宽度·{breadth.get('as_of', '')}】涨停 {breadth['limit_up']} 家、"
            f"跌停 {breadth['limit_down']} 家、炸板 {breadth['broken']} 家"
        )
    turnover = regime.get("turnover") or {}
    if turnover:
        lines.append(
            f"【成交额】{_f(turnover['amount'] / 1e8, 0)} 亿元，"
            f"20 日均值 {_f((turnover['avg_amount_20d'] or 0) / 1e8, 0)} 亿元，"
            f"量比 {_f(turnover['ratio_vs_20d'])}"
        )
    margin = regime.get("margin") or {}
    if margin:
        lines.append(
            f"【两融·{margin.get('as_of', '')}】融资余额 {_f(margin['balance'] / 1e8, 0)} 亿元，"
            f"近 20 日变化 {_pct(margin['change_20d_pct'])}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------- sector
def build_sector(industry: Optional[str], symbol: str) -> dict[str, Any]:
    rotation = []
    for ts_code in SW_LEVEL1:
        rows = _rows(
            "SELECT trade_date, close, name FROM sw_daily WHERE ts_code = ? "
            "ORDER BY trade_date DESC LIMIT 21",
            [ts_code],
        )
        if len(rows) < 2:
            continue
        closes = [row[1] for row in rows if row[1] is not None][::-1]
        if len(closes) < 2:
            continue
        rotation.append(
            {
                "ts_code": ts_code,
                "name": rows[0][2],
                "return_5d": (closes[-1] / closes[-6] - 1.0) * 100.0 if len(closes) > 5 else None,
                "return_20d": (closes[-1] / closes[0] - 1.0) * 100.0,
            }
        )
    ranked = [item for item in rotation if item["return_20d"] is not None]
    ranked.sort(key=lambda item: item["return_20d"], reverse=True)

    peer = None
    if industry:
        end = store.latest_trade_date()
        row = _one(
            "SELECT COUNT(*), MEDIAN(b.pe_ttm), MEDIAN(b.pb), MEDIAN(b.total_mv) "
            "FROM symbols s JOIN daily_basic b "
            "ON b.symbol = s.symbol AND b.trade_date = ? "
            "WHERE s.industry = ? AND s.asset_type = 'stock'",
            [end, industry],
        )
        own = _one(
            "SELECT pe_ttm, pb FROM daily_basic WHERE symbol = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            [symbol],
        )
        up_ratio = _one(
            "SELECT AVG(CASE WHEN d.close > d.pre_close THEN 1.0 ELSE 0.0 END) "
            "FROM symbols s JOIN daily d ON d.symbol = s.symbol AND d.trade_date = ? "
            "WHERE s.industry = ? AND s.asset_type = 'stock'",
            [end, industry],
        )
        if row and row[0]:
            peer = {
                "industry": industry,
                "count": int(row[0]),
                "median_pe_ttm": row[1],
                "median_pb": row[2],
                "median_mv": row[3],
                "own_pe_ttm": own[0] if own else None,
                "own_pb": own[1] if own else None,
                "up_ratio": up_ratio[0] if up_ratio else None,
            }

    return {
        "rotation_top": ranked[:5],
        "rotation_bottom": ranked[-5:] if len(ranked) > 5 else [],
        "rotation_count": len(ranked),
        "peer": peer,
    }


def render_sector(sector: dict[str, Any]) -> str:
    lines = []
    if sector["rotation_top"]:
        top = "；".join(
            f"{item['name']} {_pct(item['return_20d'])}" for item in sector["rotation_top"]
        )
        lines.append(f"【强势板块·近20日】{top}")
    if sector["rotation_bottom"]:
        bottom = "；".join(
            f"{item['name']} {_pct(item['return_20d'])}" for item in sector["rotation_bottom"]
        )
        lines.append(f"【弱势板块·近20日】{bottom}")
    lines.append(f"（申万一级共 {sector['rotation_count']} 个板块纳入排名）")

    peer = sector.get("peer")
    if peer:
        lines.append(
            f"【同业对比·{peer['industry']}】共 {peer['count']} 只；行业中位 PE(TTM) "
            f"{_f(peer['median_pe_ttm'])}、PB {_f(peer['median_pb'])}、"
            f"市值 {_f((peer['median_mv'] or 0) / 1e8, 0)} 亿元；"
            f"本股 PE {_f(peer['own_pe_ttm'])}、PB {_f(peer['own_pb'])}；"
            f"当日上涨占比 {_unit((peer['up_ratio'] or 0) * 100, '%', 0)}"
        )
    else:
        lines.append("【同业对比】行业分类缺失，无法做同业内比较")
    return "\n".join(lines)
