"""Persistence for factor evaluations (SQLite)."""

from __future__ import annotations

import json
from typing import Any, Optional

from app.db import connection, now_iso

MAX_RUNS = 100


def save(result: dict[str, Any]) -> int:
    summary = {key: value for key, value in result.items() if key not in {"id", "created_at"}}
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO factor_runs(created_at, spec_json, summary_json) VALUES(?, ?, ?)",
            (
                now_iso(),
                json.dumps(result.get("config") or {}, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.execute(
            "DELETE FROM factor_runs WHERE id NOT IN "
            "(SELECT id FROM factor_runs ORDER BY id DESC LIMIT ?)",
            (MAX_RUNS,),
        )
    return run_id


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, spec_json, summary_json FROM factor_runs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    output = []
    for row in rows:
        spec = json.loads(row["spec_json"] or "{}")
        summary = json.loads(row["summary_json"] or "{}")
        factors = summary.get("factors") or []
        top = factors[0] if factors else {}
        output.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "spec": spec,
                "sections": summary.get("sections"),
                "evaluated_dates": summary.get("evaluated_dates"),
                "top_factor": {
                    "name": top.get("name"),
                    "label": top.get("label"),
                    "icir": top.get("icir"),
                    "ic_mean": top.get("ic_mean"),
                },
            }
        )
    return output


def get(run_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, created_at, spec_json, summary_json FROM factor_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    summary = json.loads(row["summary_json"] or "{}")
    summary["id"] = row["id"]
    summary["created_at"] = row["created_at"]
    summary["config"] = json.loads(row["spec_json"] or "{}")
    return summary
