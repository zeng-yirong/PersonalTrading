"""每周把策略库里的 champion 跑一遍（近一年 + 近两年），按超额排序留档。

两个约束写在代码里：

* **同一口径**：所有策略共用同一段区间、同一调仓周期、同一持仓数、同一份费用模型，
  否则排名毫无意义；
* **失败不吞**：某个策略跑空（命中太少或报错）也进结果，标 ``failed`` 排最后——
  "今年这条策略选不出票"是可见的事实，不是悄悄消失。

排名字段是**超额年化**（相对沪深300）：只看绝对收益的话，普涨年份所有策略都"很好"。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from app.db import connection, now_iso
from app.strategy import store as strategy_store

KEEP_BATCHES = 20
RANK_FIELD = "excess_cagr_pct"
WINDOWS = {"1y": 365, "2y": 730}


def _metrics_of(result: Optional[dict[str, Any]]) -> dict[str, Any]:
    metrics = (result or {}).get("metrics") or {}
    return {
        "periods": metrics.get("periods"),
        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "excess_cagr_pct": metrics.get(RANK_FIELD),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "hit_rate_pct": metrics.get("hit_rate_pct"),
        "exposure_pct": metrics.get("exposure_pct"),
        "trades": metrics.get("trades"),
        "empty_periods": metrics.get("empty_periods"),
    }


def _sort_key(row: dict[str, Any]) -> tuple:
    value = row.get(RANK_FIELD)
    if row["status"] != "ok" or value is None:
        return (1, 0.0)
    return (0, -float(value))


def _panel_end() -> Optional[date]:
    from app.backtest import service as backtest_service

    try:
        _first, last, _coverage = backtest_service.panel_bounds()
        return last
    except Exception:  # noqa: BLE001 - 面板不可用时让 backtest 自己报错
        return None


def run_all(rebalance: int = 20, top_n: int = 10, persist: bool = True) -> dict[str, Any]:
    """跑全部 champion（两个窗口），按近一年超额排序；写入 strategy_runs + 批次。"""
    from app.backtest import service as backtest_service

    strategies = strategy_store.list_strategies("champion")
    rows: list[dict[str, Any]] = []
    end = _panel_end()

    for strategy in strategies:
        params = strategy.get("params") or {}
        base = {
            "query": strategy["query"],
            "rebalance": int(params.get("rebalance") or rebalance),
            "top_n": int(params.get("top_n") or top_n),
            "title": strategy["name"],
        }
        result: Optional[dict[str, Any]] = None
        error = ""
        try:
            result = backtest_service.run({**base, "end": end.isoformat()} if end else base,
                                          persist=False)
        except Exception as exc:  # noqa: BLE001 - 一条策略失败不打断整批
            error = f"{type(exc).__name__}: {exc}"

        metrics = _metrics_of(result)
        spec = (result or {}).get("spec") or {}
        row = {
            "id": strategy["id"],
            "label": strategy["name"],
            "status": "ok" if result else "failed",
            "error": error,
            "start": spec.get("start"),
            "end": spec.get("end"),
            **metrics,
        }

        long_metrics: dict[str, Any] = {}
        if result and end:
            start_2y = (end - timedelta(days=WINDOWS["2y"])).isoformat()
            try:
                long_result = backtest_service.run(
                    {**base, "start": start_2y, "end": end.isoformat()}, persist=False
                )
                long_metrics = _metrics_of(long_result)
            except Exception as exc:  # noqa: BLE001
                long_metrics = {"error": f"{type(exc).__name__}: {exc}"}

        row["year2"] = long_metrics
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
        _persist_runs(batch)
    return batch


def _persist_runs(batch: dict[str, Any]) -> None:
    for row in batch["rows"]:
        if row["status"] != "ok":
            continue
        strategy_store.save_run(
            row["id"],
            "1y",
            {key: row.get(key) for key in (
                "periods", "total_return_pct", "cagr_pct", RANK_FIELD,
                "max_drawdown_pct", "hit_rate_pct", "exposure_pct", "trades", "empty_periods",
            )},
            batch_id=batch.get("id"),
            rank=row.get("rank"),
            start=row.get("start") or "",
            end=row.get("end") or "",
        )
        long_metrics = row.get("year2") or {}
        if long_metrics.get("excess_cagr_pct") is not None or long_metrics.get("periods"):
            strategy_store.save_run(
                row["id"],
                "2y",
                long_metrics,
                batch_id=batch.get("id"),
                rank=row.get("rank"),
            )


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
    """给选股页：champion 清单 + 最近一批排名。"""
    champions = strategy_store.list_strategies("champion")
    return {
        "presets": [
            {"id": item["id"], "label": item["name"], "text": item.get("notes") or ""}
            for item in champions
        ],
        "batch": latest(),
        "rank_field": RANK_FIELD,
    }
