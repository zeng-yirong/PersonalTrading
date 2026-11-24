"""Persistence for thematic reports (SQLite, next to the notes and runs)."""

from __future__ import annotations

import json
from typing import Any, Optional

from app.db import connection, now_iso

MAX_REPORTS = 100


def save(report: dict[str, Any]) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, "
            "as_of, payload_json, markdown, prompt_tokens, completion_tokens, source_run_id, "
            "created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report["topic"],
                report.get("title") or "",
                report.get("scope") or "",
                report.get("verdict") or "",
                int(report.get("confidence") or 0),
                report.get("model") or "",
                report.get("as_of") or "",
                json.dumps(report.get("report") or {}, ensure_ascii=False),
                report.get("markdown") or "",
                int(report.get("prompt_tokens") or 0),
                int(report.get("completion_tokens") or 0),
                report.get("source_run_id"),
                now_iso(),
            ),
        )
        report_id = int(cursor.lastrowid)
        conn.execute(
            "DELETE FROM topic_reports WHERE id NOT IN "
            "(SELECT id FROM topic_reports ORDER BY id DESC LIMIT ?)",
            (MAX_REPORTS,),
        )
    return report_id


def list_reports(limit: int = 20) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, topic, title, scope, verdict, confidence, model, as_of, created_at "
            "FROM topic_reports ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get(report_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM topic_reports WHERE id = ?", (report_id,)
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["report"] = json.loads(item.pop("payload_json") or "{}")
    return item


def latest_for_topic(topic: str) -> Optional[dict[str, Any]]:
    """Newest report for an exact topic text (used by the one-click daily brief)."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM topic_reports WHERE topic = ? ORDER BY id DESC LIMIT 1",
            (topic,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["report"] = json.loads(item.pop("payload_json") or "{}")
    return item
