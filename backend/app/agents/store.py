"""Persistence for multi-agent research runs (SQLite, next to watchlist/notes).

A run is one row in ``agent_runs`` plus one row per node in ``agent_messages``.
The structured payloads are stored verbatim, which is what makes the later
"memory / review" phase possible: you can ask, weeks later, whether the bull or
the bear was right, without re-running an LLM.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.db import connection, now_iso


def create_run(
    symbol: str,
    name: str,
    model: str,
    rounds: int,
    context: dict[str, Any],
    skill_version: str = "",
) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO agent_runs(symbol, name, status, model, rounds, started_at, "
            "context_json, skill_version) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                name,
                "running",
                model,
                rounds,
                now_iso(),
                json.dumps(context, ensure_ascii=False, default=str),
                skill_version or None,
            ),
        )
        return int(cursor.lastrowid)


def finish_run(
    run_id: int,
    status: str,
    *,
    verdict: Optional[dict[str, Any]] = None,
    report: Optional[str] = None,
    error: Optional[str] = None,
    usage: Optional[dict[str, int]] = None,
    duration_ms: Optional[int] = None,
) -> None:
    usage = usage or {}
    with connection() as conn:
        conn.execute(
            "UPDATE agent_runs SET status = ?, finished_at = ?, duration_ms = ?, "
            "prompt_tokens = ?, completion_tokens = ?, calls = ?, verdict = ?, "
            "report = ?, error = ? WHERE id = ?",
            (
                status,
                now_iso(),
                duration_ms,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("calls", 0),
                json.dumps(verdict, ensure_ascii=False, default=str) if verdict else None,
                report,
                error,
                run_id,
            ),
        )


def append_message(
    run_id: int,
    node: str,
    label: Optional[str],
    role: Optional[str],
    round_no: Optional[int],
    status: str,
    payload: Any,
) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_messages(run_id, node, label, role, round, status, payload_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                node,
                label,
                role,
                round_no,
                status,
                json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
                now_iso(),
            ),
        )


def _run_row_to_dict(row: Any, *, include_report: bool = False) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "status": row["status"],
        "model": row["model"],
        "rounds": row["rounds"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": row["duration_ms"],
        "usage": {
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "calls": row["calls"],
        },
        "verdict": json.loads(row["verdict"]) if row["verdict"] else None,
        "error": row["error"],
        "skill_version": row["skill_version"] if "skill_version" in row.keys() else None,
    }
    if include_report:
        item["report"] = row["report"]
        item["context"] = json.loads(row["context_json"]) if row["context_json"] else None
    return item


def list_runs(symbol: Optional[str] = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = "SELECT * FROM agent_runs"
    params: list[Any] = []
    if symbol:
        sql += " WHERE symbol = ?"
        params.append(symbol)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_run_row_to_dict(row) for row in rows]


def get_run(run_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        item = _run_row_to_dict(row, include_report=True)
        messages = conn.execute(
            "SELECT node, label, role, round, status, payload_json, created_at "
            "FROM agent_messages WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    item["messages"] = [
        {
            "node": message["node"],
            "label": message["label"],
            "role": message["role"],
            "round": message["round"],
            "status": message["status"],
            "payload": json.loads(message["payload_json"]) if message["payload_json"] else None,
            "created_at": message["created_at"],
        }
        for message in messages
    ]
    return item
