"""Market sentiment series: one number per trading day, no model involved.

The daily brief needs a *history* to compare against ("今天 38 分，昨天 32，五天前
22"). Asking the model to remember that is both expensive and unreliable, so the
series is computed from the panel:

* **上涨占比** — breadth on the day (stocks only, ETFs excluded)
* **站上 20 日均线占比** — how much of the market is in an uptrend
* **涨跌停对比** — limit-up vs limit-down counts
* **量能** — turnover vs its 20-day average
* **板块扩散度** — share of industries closing up

A weighted 0–100 score is deliberately simple and reproducible; the model only
writes prose around these numbers.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from app.data import store

LOOKBACK_DAYS = 40
SERIES_DAYS = 30


def _num(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _label(score: float, change: Optional[float], prev: Optional[float] = None) -> str:
    # 昨天还在 55 分以上、今天掉了 10 分以上，先判「退潮」而不是「修复」。
    if prev is not None and prev >= 55 and change is not None and change <= -10:
        return "退潮"
    if score < 25:
        return "冰点"
    if score < 45:
        return "修复"
    if score < 62:
        return "中性"
    if score < 78:
        return "偏热"
    return "亢奋"


def _breadth(conn, start: date, end: date) -> dict[date, dict[str, Any]]:
    rows = conn.execute(
        """
        WITH w AS (
            SELECT d.symbol, d.trade_date, d.close,
                   COALESCE(d.pre_close, LAG(d.close) OVER (
                       PARTITION BY d.symbol ORDER BY d.trade_date)) AS prev,
                   AVG(d.close) OVER (PARTITION BY d.symbol ORDER BY d.trade_date
                                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
            FROM daily d JOIN symbols s USING (symbol)
            WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
        )
        SELECT trade_date, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE close > prev) AS up,
               COUNT(*) FILTER (WHERE close > ma20) AS above20
        FROM w GROUP BY 1 ORDER BY 1
        """,
        [start, end],
    ).fetchall()
    output: dict[date, dict[str, Any]] = {}
    for day, total, up, above20 in rows:
        total = int(total or 0)
        output[day] = {
            "stocks": total,
            "up": int(up or 0),
            "above_ma20": int(above20 or 0),
            "up_ratio": round(int(up or 0) / total * 100, 2) if total else 0.0,
            "above_ma20_ratio": round(int(above20 or 0) / total * 100, 2) if total else 0.0,
        }
    return output


def _limits(conn, start: date, end: date) -> dict[date, dict[str, int]]:
    rows = conn.execute(
        """
        SELECT trade_date,
               COUNT(*) FILTER (WHERE "limit" = 'U'),
               COUNT(*) FILTER (WHERE "limit" = 'D')
        FROM limit_list_d WHERE trade_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1
        """,
        [start, end],
    ).fetchall()
    return {
        day: {"limit_up": int(up or 0), "limit_down": int(down or 0)}
        for day, up, down in rows
    }


def _amount(conn, start: date, end: date) -> dict[date, float]:
    rows = conn.execute(
        """
        SELECT d.trade_date, SUM(d.amount)
        FROM daily d JOIN symbols s USING (symbol)
        WHERE s.asset_type = 'stock' AND d.trade_date BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1
        """,
        [start, end],
    ).fetchall()
    return {day: float(total or 0) for day, total in rows}


def _sectors(conn, start: date, end: date) -> dict[date, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trade_date,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE pct_change > 0) AS up,
               MAX(pct_change) AS top,
               MIN(pct_change) AS bottom
        FROM sw_daily WHERE trade_date BETWEEN ? AND ? AND pct_change IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
        [start, end],
    ).fetchall()
    output = {}
    for day, total, up, top, bottom in rows:
        total = int(total or 0)
        output[day] = {
            "industries": total,
            "industries_up": int(up or 0),
            "sector_up_ratio": round(int(up or 0) / total * 100, 2) if total else 0.0,
            "top_pct": _num(top),
            "bottom_pct": _num(bottom),
        }
    return output


def _score(row: dict[str, Any]) -> float:
    up_ratio = float(row.get("up_ratio") or 0)
    above = float(row.get("above_ma20_ratio") or 0)
    sectors = float(row.get("sector_up_ratio") or 0)
    limit_up = int(row.get("limit_up") or 0)
    limit_down = int(row.get("limit_down") or 0)
    limit_score = 50.0
    if limit_up + limit_down > 0:
        limit_score = limit_up / (limit_up + limit_down) * 100
    ratio = row.get("amount_ratio")
    amount_score = 50.0 if ratio is None else _clamp((float(ratio) - 0.5) / 1.5 * 100)
    return round(
        up_ratio * 0.35 + above * 0.25 + limit_score * 0.15 + amount_score * 0.10 + sectors * 0.15,
        1,
    )


def series(days: int = SERIES_DAYS) -> dict[str, Any]:
    """最近 ``days`` 个交易日的情绪序列 + 当日分项 + 阶段判断。"""
    with store.session(read_only=True) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if latest is None:
            return {"as_of": None, "points": [], "score": None}
        start = latest - timedelta(days=LOOKBACK_DAYS * 2)
        breadth = _breadth(conn, start, latest)
        limits = _limits(conn, start, latest)
        amounts = _amount(conn, start, latest)
        sectors = _sectors(conn, start, latest)

    dates = sorted(set(breadth) | set(amounts))
    points = []
    for index, day in enumerate(dates):
        row = {**breadth.get(day, {}), **limits.get(day, {}), **sectors.get(day, {})}
        total = float(amounts.get(day) or 0)
        window = [float(amounts[d] or 0) for d in dates[max(0, index - 19) : index + 1]]
        average = sum(window) / len(window) if window else 0.0
        row["date"] = day.isoformat()
        row["amount_yi"] = round(total / 1e8, 1)
        row["amount_ratio"] = round(total / average, 2) if average else None
        row["score"] = _score(row)
        points.append(row)

    points = points[-days:]
    if not points:
        return {"as_of": latest.isoformat(), "points": [], "score": None}

    today = points[-1]
    prev = points[-2] if len(points) > 1 else None
    five = points[-6] if len(points) > 5 else points[0]
    change = round(today["score"] - prev["score"], 1) if prev else None
    change5 = round(today["score"] - five["score"], 1) if len(points) > 1 else None

    return {
        "as_of": today["date"],
        "score": today["score"],
        "prev_score": prev["score"] if prev else None,
        "change": change,
        "change_5d": change5,
        "stage": _label(today["score"], change, prev["score"] if prev else None),
        "components": {
            "up_ratio": today.get("up_ratio"),
            "above_ma20_ratio": today.get("above_ma20_ratio"),
            "limit_up": today.get("limit_up"),
            "limit_down": today.get("limit_down"),
            "amount_yi": today.get("amount_yi"),
            "amount_ratio": today.get("amount_ratio"),
            "sector_up_ratio": today.get("sector_up_ratio"),
            "industries": today.get("industries"),
        },
        "points": points,
    }
