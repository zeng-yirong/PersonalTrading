"""Persistence for backtest runs.

One row per run, with the equity curve and the per-period detail stored as JSON.
The panel can rebuild the same result from the spec, so what is stored here is
history and comparison, not a cache: re-running the same spec is the point.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.db import connection, now_iso

MAX_RUNS = 200


def save(result: dict[str, Any]) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO backtest_runs(title, spec_json, metrics_json, nav_json, "
            "periods_json, trades_json, warnings_json, source_run_id, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.get("title") or "",
                json.dumps(result["spec"], ensure_ascii=False),
                json.dumps(result["metrics"], ensure_ascii=False),
                json.dumps(result["nav"], ensure_ascii=False),
                json.dumps(result["periods"], ensure_ascii=False),
                json.dumps(result.get("trades") or [], ensure_ascii=False),
                json.dumps(result.get("warnings") or [], ensure_ascii=False),
                result.get("source_run_id"),
                now_iso(),
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.execute(
            "DELETE FROM backtest_runs WHERE id NOT IN "
            "(SELECT id FROM backtest_runs ORDER BY id DESC LIMIT ?)",
            (MAX_RUNS,),
        )
    return run_id


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, title, spec_json, metrics_json, source_run_id, created_at "
            "FROM backtest_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    output = []
    for row in rows:
        spec = _loads(row["spec_json"])
        metrics = _loads(row["metrics_json"])
        output.append(
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "source_run_id": row["source_run_id"],
                "spec": {
                    "start": spec.get("start"),
                    "end": spec.get("end"),
                    "rebalance": spec.get("rebalance"),
                    "top_n": spec.get("top_n"),
                },
                "metrics": {
                    key: metrics.get(key)
                    for key in (
                        "total_return_pct",
                        "cagr_pct",
                        "benchmark_return_pct",
                        "excess_cagr_pct",
                        "max_drawdown_pct",
                        "sharpe",
                        "hit_rate_pct",
                        "periods",
                        "trades",
                    )
                },
            }
        )
    return output


def get_run(run_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "spec": _loads(row["spec_json"]),
        "metrics": _loads(row["metrics_json"]),
        "nav": _loads(row["nav_json"]),
        "periods": _loads(row["periods_json"]),
        "trades": _loads(row["trades_json"]),
        "warnings": _loads(row["warnings_json"]),
    }


def delete_run(run_id: int) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
        return cursor.rowcount > 0


def _loads(raw: Optional[str]) -> Any:
    if not raw:
        return [] if raw == "" else {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}
