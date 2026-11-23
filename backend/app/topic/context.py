"""Market context for a thematic research question.

The model gets numbers, not vibes. Everything in the payload is queried from the
local panel on the same read connection, and every block carries the date it was
actually published: the panel's daily bars can be a day ahead of the SW industry
indices, and pretending otherwise would turn a stale row into a "today" claim.

The topic text only decides *which slices to detail* (industries / concepts that
appear in it); the model is still given the whole cross-section so it can judge a
theme against the market instead of in isolation.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from app.data import store

# Main indices, in the order a reader expects them.
INDEX_CODES: tuple[tuple[str, str], ...] = (
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
    ("000688.SH", "科创50"),
    ("899050.BJ", "北证50"),
)

# 申万一级行业指数代码是固定的 31 个；用代码而不是名字匹配，避免 L2/L3 混进来。
SW_L1: dict[str, str] = {
    "801010.SI": "农林牧渔", "801030.SI": "基础化工", "801040.SI": "钢铁",
    "801050.SI": "有色金属", "801080.SI": "电子", "801110.SI": "家用电器",
    "801120.SI": "食品饮料", "801130.SI": "纺织服饰", "801140.SI": "轻工制造",
    "801150.SI": "医药生物", "801160.SI": "公用事业", "801170.SI": "交通运输",
    "801180.SI": "房地产", "801200.SI": "商贸零售", "801210.SI": "社会服务",
    "801230.SI": "综合", "801710.SI": "建筑材料", "801720.SI": "建筑装饰",
    "801730.SI": "电力设备", "801740.SI": "国防军工", "801750.SI": "计算机",
    "801760.SI": "传媒", "801770.SI": "通信", "801780.SI": "银行",
    "801790.SI": "非银金融", "801880.SI": "汽车", "801890.SI": "机械设备",
    "801950.SI": "煤炭", "801960.SI": "石油石化", "801970.SI": "环保",
    "801980.SI": "美容护理",
}

WINDOW_DAYS = 200
INDUSTRY_FIELDS = ("industry", "ret20", "up_ratio", "mf_net_20d")


def build(topic: str) -> dict[str, Any]:
    """Assemble the payload a thematic report is written from."""
    with store.session(read_only=True) as conn:
        as_of = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if as_of is None:
            raise RuntimeError("面板为空，请先运行 scripts/sync_market.py")
        start = as_of - timedelta(days=WINDOW_DAYS)
        industries = _industry_rollup(conn, start, as_of)
        industries = _attach_flow(conn, as_of, industries)
        matched = match_topic(topic, [item["industry"] for item in industries],
                              _concept_names(conn, as_of))
        payload = {
            "as_of": as_of.isoformat(),
            "topic": topic.strip(),
            "market": {
                "indices": _indices(conn, as_of),
                "breadth": _breadth(conn, start, as_of),
                "turnover": _turnover(conn, as_of),
                "limits": _limits(conn, as_of),
                "margin": _margin(conn, as_of),
                "north": _north(conn, as_of),
                "hot_concepts": _hot_concepts(conn, as_of),
            },
            "industries": _compact_industries(industries, matched["industries"]),
            "industry_ranking": {
                "top20": _ranking(industries, "ret20", 10),
                "bottom20": _ranking(industries, "ret20", 10, reverse=False),
            },
            "sw_l1": _sw_l1(conn, as_of),
            "matched": matched,
            "data_dates": _data_dates(conn, as_of),
        }
        if matched["industries"]:
            payload["industry_detail"] = _industry_detail(
                conn, start, as_of, matched["industries"], industries
            )
        payload["data_gaps"] = _gaps(payload)
        return payload


# ------------------------------------------------------------------ matching
def match_topic(
    topic: str, industries: list[str], concepts: list[str]
) -> dict[str, list[str]]:
    """Substring match in both directions; a topic may name zero or many sectors."""
    text = (topic or "").strip()
    matched_industries = [
        name for name in industries if name and (name in text or _contains(text, name))
    ]
    matched_concepts = [
        name for name in concepts
        if name and len(name) >= 2 and (name in text or (len(text) >= 2 and text in name))
    ]
    return {
        "industries": sorted(set(matched_industries)),
        "concepts": sorted(set(matched_concepts))[:8],
    }


def _contains(text: str, name: str) -> bool:
    """True when the topic is a meaningful fragment of a sector name (e.g. 白酒→白酒Ⅱ)."""
    if len(text) < 2 or len(text) > len(name):
        return False
    return text in name


# -------------------------------------------------------------------- blocks
def _indices(conn, as_of: date) -> list[dict[str, Any]]:
    codes = [code for code, _ in INDEX_CODES]
    placeholders = ", ".join("?" for _ in codes)
    rows = conn.execute(
        f"SELECT ts_code, trade_date, close FROM index_daily "
        f"WHERE ts_code IN ({placeholders}) AND trade_date <= ? ORDER BY ts_code, trade_date",
        [*codes, as_of],
    ).fetchall()
    series: dict[str, list[tuple[date, float]]] = {}
    for code, day, close in rows:
        if close is None:
            continue
        series.setdefault(code, []).append((day, float(close)))

    output = []
    for code, name in INDEX_CODES:
        values = series.get(code) or []
        if not values:
            continue
        closes = [value for _, value in values]
        output.append(
            {
                "name": name,
                "code": code,
                "date": values[-1][0].isoformat(),
                "close": round(closes[-1], 2),
                "ret_1d": _ret(closes, 1),
                "ret_5d": _ret(closes, 5),
                "ret_20d": _ret(closes, 20),
                "ret_60d": _ret(closes, 60),
            }
        )
    return output


def _ret(closes: list[float], back: int) -> Optional[float]:
    if len(closes) <= back:
        return None
    base = closes[-1 - back]
    if not base:
        return None
    return round((closes[-1] / base - 1) * 100, 2)


def _breadth(conn, start: date, as_of: date) -> dict[str, Any]:
    row = conn.execute(
        """
        WITH w AS (
            SELECT d.symbol, d.trade_date, d.close,
                   COALESCE(d.pre_close, LAG(d.close) OVER (
                       PARTITION BY d.symbol ORDER BY d.trade_date)) AS prev,
                   AVG(d.close) OVER (PARTITION BY d.symbol ORDER BY d.trade_date
                                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
                   AVG(d.close) OVER (PARTITION BY d.symbol ORDER BY d.trade_date
                                      ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60
            FROM daily d JOIN symbols s USING (symbol)
            WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
        )
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE close > prev) AS up,
               COUNT(*) FILTER (WHERE close < prev) AS down,
               COUNT(*) FILTER (WHERE close > ma20) AS above_ma20,
               COUNT(*) FILTER (WHERE close > ma60) AS above_ma60
        FROM w WHERE trade_date = ?
        """,
        [start, as_of, as_of],
    ).fetchone()
    total, up, down, above20, above60 = (int(value or 0) for value in row)
    return {
        "stocks": total,
        "up": up,
        "down": down,
        "flat": max(0, total - up - down),
        "up_ratio": round(up / total * 100, 2) if total else 0.0,
        "above_ma20_ratio": round(above20 / total * 100, 2) if total else 0.0,
        "above_ma60_ratio": round(above60 / total * 100, 2) if total else 0.0,
    }


def _turnover(conn, as_of: date) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT d.trade_date, SUM(d.amount)
        FROM daily d JOIN symbols s USING (symbol)
        WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1 DESC LIMIT 20
        """,
        [as_of - timedelta(days=40), as_of],
    ).fetchall()
    if not rows:
        return {}
    today_amount = float(rows[0][1] or 0)
    average = sum(float(row[1] or 0) for row in rows[1:]) / max(1, len(rows) - 1)
    return {
        "date": rows[0][0].isoformat(),
        "amount_yi": round(today_amount / 1e8, 1),
        "amount_20d_avg_yi": round(average / 1e8, 1),
        "ratio_to_20d_avg": round(today_amount / average, 2) if average else None,
        "last5_yi": [round(float(row[1] or 0) / 1e8, 1) for row in reversed(rows[:5])],
    }


def _limits(conn, as_of: date) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT trade_date,
               COUNT(*) FILTER (WHERE "limit" = 'U') AS up,
               COUNT(*) FILTER (WHERE "limit" = 'D') AS down
        FROM limit_list_d
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1 DESC
        """,
        [as_of - timedelta(days=40), as_of],
    ).fetchall()
    if not rows:
        return {}
    latest_up = int(rows[0][1] or 0)
    latest_down = int(rows[0][2] or 0)
    last5 = rows[:5]
    return {
        "date": rows[0][0].isoformat(),
        "limit_up": latest_up,
        "limit_down": latest_down,
        "limit_up_5d_avg": round(sum(int(row[1] or 0) for row in last5) / len(last5), 1),
        "limit_down_5d_avg": round(sum(int(row[2] or 0) for row in last5) / len(last5), 1),
    }


def _margin(conn, as_of: date) -> dict[str, Any]:
    """Margin balance on the latest *complete* day.

    A sync that stops halfway leaves a date with a third of the usual symbols;
    summing it would read as a 50% deleveraging that never happened. Days whose
    symbol count is far below the recent median are skipped and reported.
    """
    rows = conn.execute(
        """
        SELECT trade_date, COUNT(*) AS n, SUM(rzye) AS total
        FROM margin_detail
        WHERE trade_date BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1 DESC LIMIT 30
        """,
        [as_of - timedelta(days=60), as_of],
    ).fetchall()
    if not rows:
        return {}
    counts = sorted(int(row[1] or 0) for row in rows)
    median = counts[len(counts) // 2]
    complete = [row for row in rows if int(row[1] or 0) >= median * 0.95]
    if not complete:
        return {}
    latest = complete[0]
    base = complete[20] if len(complete) > 20 else complete[-1]
    total = float(latest[2] or 0)
    base_total = float(base[2] or 0)
    skipped = [row[0].isoformat() for row in rows
               if row[0] > latest[0] and int(row[1] or 0) < median * 0.95]
    return {
        "date": latest[0].isoformat(),
        "balance_yi": round(total / 1e8, 1),
        "chg_20d_pct": round((total / base_total - 1) * 100, 2) if base_total else None,
        "stocks": int(latest[1] or 0),
        "skipped_partial_days": skipped,
    }


def _north(conn, as_of: date) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT trade_date, SUM(net_amount)
        FROM hsgt_top10
        WHERE trade_date BETWEEN ? AND ? AND net_amount IS NOT NULL
        GROUP BY 1 ORDER BY 1 DESC
        """,
        [as_of - timedelta(days=40), as_of],
    ).fetchall()
    if not rows:
        return {}
    recent = rows[:20]
    return {
        "date": rows[0][0].isoformat(),
        "top10_net_20d_yi": round(sum(float(row[1] or 0) for row in recent) / 1e4, 2),
        "note": "仅沪深股通十大成交股口径，不是全市场北向净流入",
    }


def _hot_concepts(conn, as_of: date) -> list[dict[str, Any]]:
    latest = conn.execute(
        "SELECT MAX(trade_date) FROM limit_cpt_list WHERE trade_date <= ?", [as_of]
    ).fetchone()[0]
    if not latest:
        return []
    rows = conn.execute(
        """
        SELECT name, up_nums, up_stat, days, pct_chg
        FROM limit_cpt_list WHERE trade_date = ?
        ORDER BY up_nums DESC NULLS LAST LIMIT 12
        """,
        [latest],
    ).fetchall()
    return [
        {
            "name": row[0],
            "limit_up_n": int(row[1] or 0),
            "stat": row[2],
            "days": int(row[3] or 0),
            "pct_chg": round(float(row[4]), 2) if row[4] is not None else None,
            "date": latest.isoformat(),
        }
        for row in rows
    ]


def _industry_rollup(conn, start: date, as_of: date) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH w AS (
            SELECT s.industry, s.symbol, d.trade_date,
                   d.close * COALESCE(d.adj_factor, 1) AS c_adj,
                   LAG(d.close * COALESCE(d.adj_factor, 1), 5) OVER w AS c5,
                   LAG(d.close * COALESCE(d.adj_factor, 1), 20) OVER w AS c20,
                   LAG(d.close * COALESCE(d.adj_factor, 1), 60) OVER w AS c60
            FROM daily d JOIN symbols s USING (symbol)
            WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
            WINDOW w AS (PARTITION BY d.symbol ORDER BY d.trade_date)
        )
        SELECT industry,
               COUNT(*) AS stocks,
               ROUND(AVG(c_adj / NULLIF(c5, 0) - 1) * 100, 2) AS ret5,
               ROUND(AVG(c_adj / NULLIF(c20, 0) - 1) * 100, 2) AS ret20,
               ROUND(AVG(c_adj / NULLIF(c60, 0) - 1) * 100, 2) AS ret60,
               ROUND(100.0 * COUNT(*) FILTER (WHERE c_adj > c5) / COUNT(*), 1) AS up_ratio
        FROM w
        WHERE trade_date = ? AND c5 IS NOT NULL AND industry IS NOT NULL
        GROUP BY industry
        """,
        [start, as_of, as_of],
    ).fetchall()
    return [
        {
            "industry": row[0],
            "stocks": int(row[1] or 0),
            "ret5": _num(row[2]),
            "ret20": _num(row[3]),
            "ret60": _num(row[4]),
            "up_ratio": _num(row[5]),
            "pe_median": None,
            "mf_net_20d": None,
        }
        for row in rows
    ]


def _attach_flow(conn, as_of: date, industries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flow = {
        row[0]: float(row[1] or 0)
        for row in conn.execute(
            """
            SELECT s.industry, SUM(m.net_mf_amount)
            FROM moneyflow m JOIN symbols s ON s.ts_code = m.ts_code
            WHERE s.asset_type = 'stock' AND m.trade_date BETWEEN ? AND ?
            GROUP BY 1
            """,
            [as_of - timedelta(days=40), as_of],
        ).fetchall()
    }
    valuation = {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT s.industry,
                   ROUND(MEDIAN(b.pe_ttm) FILTER (WHERE b.pe_ttm > 0), 1)
            FROM daily_basic b JOIN symbols s USING (symbol)
            WHERE s.asset_type = 'stock' AND b.trade_date = (
                SELECT MAX(trade_date) FROM daily_basic WHERE trade_date <= ?)
            GROUP BY 1
            """,
            [as_of],
        ).fetchall()
    }
    for item in industries:
        value = flow.get(item["industry"])
        item["mf_net_20d"] = round(value / 1e4, 1) if value is not None else None
        pe = valuation.get(item["industry"])
        item["pe_median"] = float(pe) if pe is not None else None
    return sorted(industries, key=lambda item: item["ret20"] or -999, reverse=True)


def _ranking(industries: list[dict[str, Any]], field: str, limit: int, reverse: bool = True):
    ordered = sorted(
        (item for item in industries if item.get(field) is not None),
        key=lambda item: item[field],
        reverse=reverse,
    )
    return [
        {"name": item["industry"], field: item[field], "stocks": item["stocks"]}
        for item in ordered[:limit]
    ]


def _compact_industries(
    industries: list[dict[str, Any]], matched: list[str]
) -> list[dict[str, Any]]:
    """The two ends of the ranking plus whatever the topic names.

    Sending all 110 industries costs about a third of the prompt budget without
    changing a macro answer; the extremes and the matched sector carry the signal.
    """
    ordered = [item for item in industries if item.get("ret20") is not None]
    ordered.sort(key=lambda item: item["ret20"], reverse=True)
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(item: dict[str, Any]) -> None:
        name = item["industry"]
        if name in seen:
            return
        seen.add(name)
        chosen.append(
            {
                "name": name,
                "stocks": item["stocks"],
                "ret20": item["ret20"],
                "up_ratio_5d": item["up_ratio"],
                "mf_net_20d": item["mf_net_20d"],
            }
        )

    for item in ordered[:15]:
        push(item)
    for item in ordered[-15:]:
        push(item)
    by_name = {item["industry"]: item for item in industries}
    for name in matched:
        if name in by_name:
            push(by_name[name])
    return chosen


def _sw_l1(conn, as_of: date) -> list[dict[str, Any]]:
    codes = list(SW_L1)
    placeholders = ", ".join("?" for _ in codes)
    rows = conn.execute(
        f"""
        SELECT s.ts_code, s.name, s.trade_date, s.pct_change, s.pe, s.pb, s.amount
        FROM sw_daily s
        WHERE s.ts_code IN ({placeholders})
          AND s.trade_date = (SELECT MAX(trade_date) FROM sw_daily WHERE trade_date <= ?)
        ORDER BY s.pct_change DESC NULLS LAST
        """,
        [*codes, as_of],
    ).fetchall()
    return [
        {
            "name": row[1] or SW_L1.get(row[0], row[0]),
            "code": row[0],
            "date": row[2].isoformat(),
            "pct_chg": _num(row[3]),
            "pe": _num(row[4]),
            "pb": _num(row[5]),
        }
        for row in rows
    ]


def _industry_detail(
    conn,
    start: date,
    as_of: date,
    names: list[str],
    rollup: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stats = {item["industry"]: item for item in rollup}
    placeholders = ", ".join("?" for _ in names)
    rows = conn.execute(
        f"""
        WITH w AS (
            SELECT s.industry, s.symbol, s.name, d.trade_date, d.close,
                   d.close * COALESCE(d.adj_factor, 1) AS c_adj,
                   LAG(d.close * COALESCE(d.adj_factor, 1), 5) OVER pw AS c5,
                   LAG(d.close * COALESCE(d.adj_factor, 1), 20) OVER pw AS c20,
                   AVG(d.amount) OVER (PARTITION BY d.symbol ORDER BY d.trade_date
                                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS amt20
            FROM daily d JOIN symbols s USING (symbol)
            WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
              AND s.industry IN ({placeholders})
            WINDOW pw AS (PARTITION BY d.symbol ORDER BY d.trade_date)
        )
        SELECT industry, symbol, name, close,
               ROUND((c_adj / NULLIF(c5, 0) - 1) * 100, 2) AS ret5,
               ROUND((c_adj / NULLIF(c20, 0) - 1) * 100, 2) AS ret20,
               ROUND(amt20 / 1e8, 2) AS amount_yi
        FROM w WHERE trade_date = ?
        """,
        [start, as_of, *names, as_of],
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for industry, symbol, name, close, ret5, ret20, amount in rows:
        grouped.setdefault(industry, []).append(
            {
                "symbol": symbol,
                "name": name,
                "close": _num(close),
                "ret5": _num(ret5),
                "ret20": _num(ret20),
                "amount_yi": _num(amount),
            }
        )
    output = []
    for industry, items in grouped.items():
        ordered = sorted(items, key=lambda item: item["ret20"] or -999, reverse=True)
        item = stats.get(industry) or {}
        output.append(
            {
                "industry": industry,
                "stats": {
                    "stocks": item.get("stocks"),
                    "ret5": item.get("ret5"),
                    "ret20": item.get("ret20"),
                    "ret60": item.get("ret60"),
                    "up_ratio_5d": item.get("up_ratio"),
                    "pe_median": item.get("pe_median"),
                    "mf_net_20d": item.get("mf_net_20d"),
                },
                "leaders": ordered[:5],
                "laggards": ordered[-3:][::-1] if len(ordered) > 5 else [],
                "count": len(ordered),
            }
        )
    return output


def _concept_names(conn, as_of: date) -> list[str]:
    latest = conn.execute(
        "SELECT MAX(trade_date) FROM limit_cpt_list WHERE trade_date <= ?", [as_of]
    ).fetchone()[0]
    names: list[str] = []
    if latest:
        names.extend(
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT name FROM limit_cpt_list WHERE trade_date = ?", [latest]
            ).fetchall()
            if row[0]
        )
    hot_latest = conn.execute(
        "SELECT MAX(trade_date) FROM ths_hot WHERE data_type = '概念板块' AND trade_date <= ?",
        [as_of],
    ).fetchone()[0]
    if hot_latest:
        names.extend(
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT ts_name FROM ths_hot WHERE data_type = '概念板块' "
                "AND trade_date = ?",
                [hot_latest],
            ).fetchall()
            if row[0]
        )
    return names


def _data_dates(conn, as_of: date) -> dict[str, Any]:
    tables = {
        "daily": "daily",
        "daily_basic": "daily_basic",
        "moneyflow": "moneyflow",
        "margin_detail": "margin_detail",
        "limit_list_d": "limit_list_d",
        "sw_daily": "sw_daily",
        "index_daily": "index_daily",
        "hsgt_top10": "hsgt_top10",
    }
    output: dict[str, Any] = {"daily": as_of.isoformat()}
    for label, table in tables.items():
        if label == "daily":
            continue
        try:
            value = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
        except Exception:  # noqa: BLE001 - table may be absent on a partial panel
            continue
        output[label] = value[0].isoformat() if value and value[0] else None
    return output


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _gaps(payload: dict[str, Any]) -> list[str]:
    """Data that is absent or stale, stated once here instead of invented later."""
    gaps: list[str] = []
    market = payload.get("market") or {}
    dates = payload.get("data_dates") or {}
    as_of = payload.get("as_of")

    if not market.get("north"):
        gaps.append("北向资金：近 20 日 hsgt_top10 的 net_amount 全为空，本报告无法给出北向净流入")
    margin = market.get("margin") or {}
    if margin.get("skipped_partial_days"):
        gaps.append(
            "两融余额："
            + "、".join(margin["skipped_partial_days"])
            + f" 数据不完整（少于正常标的数），已回退到 {margin.get('date')}"
        )
    for key, label in (
        ("sw_daily", "申万行业指数"),
        ("limit_list_d", "涨跌停统计"),
    ):
        value = dates.get(key)
        if value and as_of and value < as_of:
            gaps.append(f"{label}：最新只到 {value}，比个股面板（{as_of}）晚")
    if not payload.get("industries"):
        gaps.append("行业聚合为空，无法做行业内比较")
    if not market.get("hot_concepts"):
        gaps.append("题材热度：limit_cpt_list 没有当日数据")
    return gaps


def summary(payload: dict[str, Any]) -> dict[str, Any]:
    """The compact header the UI shows without running a model."""
    market = payload.get("market") or {}
    return {
        "as_of": payload.get("as_of"),
        "breadth": market.get("breadth"),
        "turnover": market.get("turnover"),
        "limits": market.get("limits"),
        "margin": market.get("margin"),
        "matched_industries": (payload.get("matched") or {}).get("industries") or [],
        "matched_concepts": (payload.get("matched") or {}).get("concepts") or [],
        "industry_count": len(payload.get("industries") or []),
        "concept_count": len(market.get("hot_concepts") or []),
    }
