"""策略库：实体、状态机、以及"最近一批成绩"的读取。

策略以前只是 `presets.json` 里的常量——不能新增、不能下架、不能留档。这里把它变成
一张表，并把"每周跑一次"的结果写进 `strategy_runs`（1 年 / 2 年两个窗口各一行）。

状态机刻意做窄：只能沿 candidate → watch → champion 升级，反向降级，任何状态可淘汰
（必须带理由），淘汰后可恢复为 watch。**没有自动迁移**——每周排名只出建议，
改状态一律带 reason 留档。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from app.db import connection, now_iso
from app.screener.presets import PRESETS

STATUSES = ("candidate", "watch", "champion", "retired", "paused")
UP = {"candidate": "watch", "watch": "champion", "retired": "watch"}
DOWN = {"champion": "watch", "watch": "candidate"}


class StrategyError(ValueError):
    """非法状态迁移 / 找不到策略。"""


def seed_from_presets() -> int:
    """把 presets.json 导入为 champion（幂等：已存在就跳过）。"""
    written = 0
    with connection() as conn:
        for preset in PRESETS:
            exists = conn.execute(
                "SELECT 1 FROM strategies WHERE id = ?", (preset.id,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO strategies(id, name, query_json, params_json, source, horizon, "
                "status, created_at, promoted_at, notes) "
                "VALUES(?, ?, ?, ?, 'preset', ?, 'champion', ?, ?, ?)",
                (
                    preset.id,
                    preset.label,
                    json.dumps(preset.query, ensure_ascii=False),
                    json.dumps({}),
                    preset.horizon,
                    now_iso(),
                    now_iso(),
                    preset.text,
                ),
            )
            written += 1
    return written


def _row_to_strategy(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "query": json.loads(row["query_json"] or "{}"),
        "horizon": row["horizon"] if "horizon" in row.keys() else "long",
        "params": json.loads(row["params_json"] or "{}"),
        "source": row["source"],
        "status": row["status"],
        "created_at": row["created_at"],
        "promoted_at": row["promoted_at"],
        "retired_at": row["retired_at"],
        "retire_reason": row["retire_reason"],
        "notes": row["notes"],
    }


def list_strategies(status: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM strategies"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY CASE status WHEN 'champion' THEN 0 WHEN 'watch' THEN 1 " \
           "WHEN 'candidate' THEN 2 WHEN 'paused' THEN 3 ELSE 4 END, name"
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_strategy(row) for row in rows]


def get(strategy_id: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    return _row_to_strategy(row) if row else None


def create(
    name: str,
    query: dict[str, Any],
    *,
    source: str = "manual",
    status: str = "candidate",
    params: Optional[dict[str, Any]] = None,
    notes: str = "",
    strategy_id: Optional[str] = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise StrategyError(f"状态只能是 {'/'.join(STATUSES)}")
    identifier = strategy_id or f"cand-{date.today():%Y%m%d}-{_next_index()}"
    with connection() as conn:
        conn.execute(
            "INSERT INTO strategies(id, name, query_json, params_json, source, status, "
            "created_at, promoted_at, notes) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                name,
                json.dumps(query, ensure_ascii=False),
                json.dumps(params or {"rebalance": 20, "top_n": 10}),
                source,
                status,
                now_iso(),
                now_iso() if status == "champion" else None,
                notes,
            ),
        )
    return get(identifier) or {}


def _next_index() -> int:
    with connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM strategies WHERE id LIKE 'cand-%'").fetchone()
    return int(row[0] or 0) + 1


def set_status(strategy_id: str, status: str, reason: str = "") -> dict[str, Any]:
    current = get(strategy_id)
    if current is None:
        raise StrategyError(f"策略 {strategy_id} 不存在")
    if status not in STATUSES:
        raise StrategyError(f"状态只能是 {'/'.join(STATUSES)}")
    if status == current["status"]:
        return current
    allowed = {UP.get(current["status"]), DOWN.get(current["status"]), "retired", "paused"}
    if status not in allowed:
        raise StrategyError(
            f"不允许从 {current['status']} 直接改成 {status}"
            f"（可改为：{'/'.join(sorted(item for item in allowed if item))}）"
        )
    with connection() as conn:
        conn.execute(
            "UPDATE strategies SET status = ?, "
            "promoted_at = CASE WHEN ? = 'champion' THEN ? ELSE promoted_at END, "
            "retired_at = CASE WHEN ? = 'retired' THEN ? ELSE retired_at END, "
            "retire_reason = CASE WHEN ? = 'retired' THEN ? ELSE retire_reason END "
            "WHERE id = ?",
            (status, status, now_iso(), status, now_iso(), status, reason or "人工淘汰", strategy_id),
        )
    return get(strategy_id) or {}


def clone(strategy_id: str, name: str = "") -> dict[str, Any]:
    source = get(strategy_id)
    if source is None:
        raise StrategyError(f"策略 {strategy_id} 不存在")
    return create(
        name or f"{source['name']}（副本）",
        source["query"],
        source="manual",
        status="candidate",
        params=source.get("params") or {},
        notes=f"克隆自 {strategy_id}",
    )


# ------------------------------------------------------------------ 成绩
def save_run(
    strategy_id: str,
    window: str,
    metrics: dict[str, Any],
    *,
    batch_id: Optional[int] = None,
    rank: Optional[int] = None,
    start: str = "",
    end: str = "",
) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO strategy_runs(strategy_id, batch_id, window, start, end, "
            "metrics_json, rank, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                strategy_id,
                batch_id,
                window,
                start,
                end,
                json.dumps(metrics, ensure_ascii=False),
                rank,
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def latest_run(strategy_id: str, window: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_runs WHERE strategy_id = ? AND window = ? "
            "ORDER BY id DESC LIMIT 1",
            (strategy_id, window),
        ).fetchone()
    return _run_to_dict(row) if row else None


def trend(strategy_id: str, window: str = "1y", limit: int = 4) -> list[float]:
    """最近 N 批的超额年化（旧 → 新），用于表格里的趋势。"""
    with connection() as conn:
        rows = conn.execute(
            "SELECT metrics_json FROM strategy_runs WHERE strategy_id = ? AND window = ? "
            "ORDER BY id DESC LIMIT ?",
            (strategy_id, window, limit),
        ).fetchall()
    values: list[float] = []
    for row in reversed(rows):
        metrics = json.loads(row["metrics_json"] or "{}")
        value = metrics.get("excess_cagr_pct")
        if value is not None:
            values.append(round(float(value), 2))
    return values


def _run_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "batch_id": row["batch_id"],
        "window": row["window"],
        "start": row["start"],
        "end": row["end"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "rank": row["rank"],
        "created_at": row["created_at"],
    }


def with_metrics(strategy: dict[str, Any]) -> dict[str, Any]:
    """表格一行需要的全部数据：1 年 / 2 年成绩、4 周趋势、最后复核日。"""
    one = latest_run(strategy["id"], "1y")
    two = latest_run(strategy["id"], "2y")
    return {
        **strategy,
        "year1": (one or {}).get("metrics"),
        "year2": (two or {}).get("metrics"),
        "trend": trend(strategy["id"]),
        "last_checked": (one or {}).get("created_at"),
        "rank": (one or {}).get("rank"),
    }
