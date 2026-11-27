"""每周把全部预设策略跑一遍近一年回测，按结果排序留档。

两个约束写在代码里：

* **同一口径**：所有预设共用同一段区间、同一调仓周期、同一持仓数、同一份费用
  模型，否则排名毫无意义；
* **失败不吞**：某个预设跑空（命中太少或报错）也进结果，标 ``空仓/失败`` 排在最后，
  这样"今年这条策略选不出票"是可见的事实，而不是悄悄消失。

排名字段是**超额年化**（相对沪深300），其次才是区间收益——只看绝对收益的话，
普涨年份所有策略都"很好"。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from app.db import connection, now_iso
from app.screener.presets import PRESETS

KEEP_BATCHES = 20
RANK_FIELD = "excess_cagr_pct"


def _row(preset, result: Optional[dict[str, Any]], error: str = "") -> dict[str, Any]:
    metrics = (result or {}).get("metrics") or {}
    spec = (result or {}).get("spec") or {}
    return {
        "id": preset.id,
        "label": preset.label,
        "text": preset.text,
        "status": "ok" if result else "failed",
        "error": error,
        "start": spec.get("start"),
        "end": spec.get("end"),
        "periods": metrics.get("periods"),
        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "excess_cagr_pct": metrics.get(excess_field()),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "hit_rate_pct": metrics.get("hit_rate_pct"),
        "exposure_pct": metrics.get("exposure_pct"),
        "trades": metrics.get("trades"),
        "empty_periods": metrics.get("empty_periods"),
    }


def excess_field() -> str:
    return RANK_FIELD


def _sort_key(row: dict[str, Any]) -> tuple:
    value = row.get(RANK_FIELD)
    if row["status"] != "ok" or value is None:
        return (1, 0.0)          # 失败/无数据排最后
    return (0, -float(value))    # 超额高的在前


def run_all(rebalance: int = 20, top_n: int = 10, persist: bool = True) -> dict[str, Any]:
    """跑全部预设，按超额年化排序；返回这一批的完整结果。"""
    from app.backtest import service as backtest_service

    rows: list[dict[str, Any]] = []
    for preset in PRESETS:
        payload = {
            "query": preset.query,
            "rebalance": rebalance,
            "top_n": top_n,
            "title": preset.label,
        }
        try:
            result = backtest_service.run(payload, persist=False)
        except Exception as exc:  # noqa: BLE001 - 一个策略失败不该打断整批
            rows.append(_row(preset, None, f"{type(exc).__name__}: {exc}"))
            continue
        row = _row(preset, result)
        row["warnings"] = result.get("warnings") or []
        rows.append(row)

    rows.sort(key=_sort_key)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    batch = {
        "created_at": now_iso(),
        "as_of": rows[0].get("end") if rows else None,
        "start": rows[0].get("start") if rows else None,
        "end": rows[0].get("end") if rows else None,
        "rebalance": rebalance,
        "top_n": top_n,
        "rows": rows,
    }
    if persist:
        batch["id"] = save(batch)
    return batch


def save(batch: dict[str, Any]) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO preset_batches(created_at, as_of, start, end, rebalance, top_n, "
            "rows_json) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                batch["created_at"],
                batch.get("as_of"),
                batch.get("start"),
                batch.get("end"),
                batch.get("rebalance"),
                batch.get("top_n"),
                json.dumps(batch.get("rows") or [], ensure_ascii=False),
            ),
        )
        batch_id = int(cursor.lastrowid)
        conn.execute(
            "DELETE FROM preset_batches WHERE id NOT IN "
            "(SELECT id FROM preset_batches ORDER BY id DESC LIMIT ?)",
            (KEEP_BATCHES,),
        )
    return batch_id


def latest() -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, created_at, as_of, start, end, rebalance, top_n, rows_json "
            "FROM preset_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "as_of": row["as_of"],
        "start": row["start"],
        "end": row["end"],
        "rebalance": row["rebalance"],
        "top_n": row["top_n"],
        "rows": json.loads(row["rows_json"] or "[]"),
    }


def describe() -> dict[str, Any]:
    """给前端：预设清单 + 最近一批排名。"""
    return {
        "presets": [
            {
                "id": preset.id,
                "label": preset.label,
                "text": preset.text,
            }
            for preset in PRESETS
        ],
        "batch": latest(),
        "rank_field": RANK_FIELD,
    }
