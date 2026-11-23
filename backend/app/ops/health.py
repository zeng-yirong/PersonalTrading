"""Data health: which tables are current, which lag, which day is incomplete.

A sync that fails quietly is worse than one that fails loudly, so every table gets
a status and the recent non-ok sync jobs are listed. The same partial-day rule the
topic research uses is applied here: a date whose symbol count is far below the
recent median is a broken day, not a market event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.data import fingerprint, store

# (table, label, expected lag in trading days, date column)
TABLES: tuple[tuple[str, str, int, str], ...] = (
    ("daily", "日线", 0, "trade_date"),
    ("daily_basic", "每日指标", 0, "trade_date"),
    ("moneyflow", "资金流", 0, "trade_date"),
    ("stk_factor", "技术因子", 0, "trade_date"),
    ("cyq_perf", "筹码分布", 1, "trade_date"),
    ("margin_detail", "两融", 1, "trade_date"),
    ("limit_list_d", "涨跌停", 1, "trade_date"),
    ("index_daily", "指数", 1, "trade_date"),
    ("sw_daily", "申万行业", 1, "trade_date"),
    ("limit_cpt_list", "涨停概念", 1, "trade_date"),
    ("ths_hot", "热榜", 1, "trade_date"),
    ("fina_indicator_vip", "财务指标", 120, "ann_date"),
)


def snapshot() -> dict[str, Any]:
    with store.session(read_only=True) as conn:
        as_of = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if as_of is None:
            return {
                "as_of": None,
                "tables": [],
                "issues": ["面板为空，请先运行 scripts/sync_market.py"],
                "checked_at": _now(),
            }
        tables = [_table(conn, name, label, lag, column) for name, label, lag, column in TABLES]
        issues, partial = _partial_days(conn)
        recent = _recent_sync(conn)

    stale = [item for item in tables if item["status"] == "stale"]
    empty = [item for item in tables if item["status"] == "empty"]
    issues = [
        f"{item['label']}（{item['table']}）滞后 {item['lag_days']} 个交易日" for item in stale
    ] + [f"{item['label']}（{item['table']}）没有数据" for item in empty] + issues
    return {
        "as_of": as_of.isoformat(),
        "checked_at": _now(),
        "tables": tables,
        "partial_days": partial,
        "recent_sync": recent,
        "issues": issues,
        "ok": not issues,
        "fingerprint": fingerprint.current(),
    }


def _table(conn, name: str, label: str, expected_lag: int, column: str) -> dict[str, Any]:
    if not store.table_exists(conn, name):
        return {
            "table": name, "label": label, "last_date": None, "rows": 0,
            "lag_days": None, "status": "missing", "expected_lag": expected_lag,
        }
    row = conn.execute(f'SELECT MAX("{column}"), COUNT(*) FROM "{name}"').fetchone()
    last = row[0] if row else None
    rows = int(row[1] or 0) if row else 0
    if last is None or rows == 0:
        return {
            "table": name, "label": label, "last_date": None, "rows": rows,
            "lag_days": None, "status": "empty", "expected_lag": expected_lag,
        }
    lag = conn.execute(
        "SELECT COUNT(*) FROM calendar WHERE is_open AND cal_date > ? AND cal_date <= ?",
        [last, conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]],
    ).fetchone()[0]
    status = "ok" if int(lag) <= expected_lag else "stale"
    return {
        "table": name,
        "label": label,
        "last_date": last.isoformat(),
        "rows": rows,
        "lag_days": int(lag),
        "status": status,
        "expected_lag": expected_lag,
    }


def _partial_days(conn) -> tuple[list[str], list[dict[str, Any]]]:
    """Flag dates whose row count is far below the recent median."""
    partial: list[dict[str, Any]] = []
    issues: list[str] = []
    for table, label in (("margin_detail", "两融"), ("daily", "日线")):
        if not store.table_exists(conn, table):
            continue
        rows = conn.execute(
            f'SELECT trade_date, COUNT(*) AS n FROM "{table}" '
            "GROUP BY 1 ORDER BY 1 DESC LIMIT 15"
        ).fetchall()
        if len(rows) < 3:
            continue
        counts = sorted(int(row[1]) for row in rows)
        median = counts[len(counts) // 2]
        for row in rows:
            if int(row[1]) < median * 0.9:
                partial.append(
                    {
                        "table": table,
                        "date": row[0].isoformat(),
                        "rows": int(row[1]),
                        "expected": median,
                    }
                )
                issues.append(f"{label} {row[0]} 只有 {int(row[1])} 行（正常约 {median}），同步可能中断")
    return issues, partial


def _recent_sync(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT job, status, rows, started_at FROM sync_log ORDER BY started_at DESC LIMIT 20"
    ).fetchall()
    output = []
    for row in rows:
        output.append(
            {
                "job": row[0],
                "status": row[1],
                "rows": int(row[2] or 0),
                "started_at": str(row[3]),
            }
        )
    return output


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
