"""Memory: what did we conclude about this name before, and how did it turn out?

The rules (goat.md 3.1) are about honesty as much as recall:

* only **past conclusions** are retrieved — never prices, never "facts";
* every item carries its date and its post-hoc outcome, so "上次怎么看" and
  "后来怎样" travel together;
* the block handed to the model is explicitly labelled 历史结论（不是事实）,
  because a model that treats an old opinion as today's data will happily
  restate it as evidence.

Storage is a plain index table plus an FTS5 (trigram) table, so Chinese substring
search works without segmenting words. The index is rebuilt lazily from
``agent_runs`` / ``topic_reports`` — the records themselves stay the single
source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.db import connection, now_iso

MAX_BODY = 600
LABEL = "历史结论（不是事实）"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS memory_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        ref_id INTEGER NOT NULL,
        symbol TEXT,
        title TEXT,
        body TEXT NOT NULL,
        as_of TEXT,
        decision TEXT,
        outcome TEXT,
        updated_at TEXT,
        UNIQUE(kind, ref_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memory_symbol ON memory_index(symbol, as_of)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
        USING fts5(body, title, tokenize='trigram')""",
)


def ensure_schema() -> None:
    with connection() as conn:
        for statement in _SCHEMA:
            conn.execute(statement)


def _clip(text: str, limit: int = MAX_BODY) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:limit]


def _run_row(row: Any) -> tuple:
    verdict = json.loads(row["verdict"]) if row["verdict"] else {}
    decision = str(verdict.get("decision") or "")
    pct = verdict.get("final_position_pct")
    title = f"{row['name'] or row['symbol']} 推演"
    body = _clip(
        f"{title}｜裁定 {decision} {pct if pct is not None else '—'}%｜"
        f"{(row['report'] or '')[:400]}"
    )
    return (
        "推演",
        int(row["id"]),
        row["symbol"],
        title,
        body,
        (row["started_at"] or "")[:10],
        f"{decision} {pct if pct is not None else ''}%".strip(),
    )


def _topic_row(row: Any) -> tuple:
    title = str(row["title"] or row["topic"] or "")
    body = _clip(f"{row['topic']}｜{title}｜{row['verdict'] or ''}｜{row['markdown'] or ''}")
    return (
        "主题",
        int(row["id"]),
        None,
        title,
        body,
        str(row["as_of"] or ""),
        f"{row['verdict'] or ''} {row['confidence'] or ''}".strip(),
    )


def sync(limit: int = 500) -> int:
    """Index runs and topic reports that are not in the index yet."""
    ensure_schema()
    written = 0
    with connection() as conn:
        known = {
            (row["kind"], row["ref_id"])
            for row in conn.execute("SELECT kind, ref_id FROM memory_index").fetchall()
        }
        runs = conn.execute(
            "SELECT id, symbol, name, verdict, report, started_at FROM agent_runs "
            "WHERE status IN ('ok', 'partial') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        topics = conn.execute(
            "SELECT id, topic, title, verdict, confidence, as_of, markdown FROM topic_reports "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

        for row in runs:
            if ("推演", int(row["id"])) in known:
                continue
            item = _run_row(row)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_index(kind, ref_id, symbol, title, body, as_of, "
                "decision, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (*item, now_iso()),
            )
            if cursor.lastrowid:
                conn.execute(
                    "INSERT INTO memory_fts(rowid, body, title) VALUES(?, ?, ?)",
                    (cursor.lastrowid, item[4], item[3]),
                )
                written += 1

        for row in topics:
            if ("主题", int(row["id"])) in known:
                continue
            item = _topic_row(row)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_index(kind, ref_id, symbol, title, body, as_of, "
                "decision, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (*item, now_iso()),
            )
            if cursor.lastrowid:
                conn.execute(
                    "INSERT INTO memory_fts(rowid, body, title) VALUES(?, ?, ?)",
                    (cursor.lastrowid, item[4], item[3]),
                )
                written += 1
    return written


def _row_to_item(row: Any) -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "ref_id": row["ref_id"],
        "symbol": row["symbol"],
        "title": row["title"],
        "as_of": row["as_of"],
        "decision": row["decision"],
        "outcome": row["outcome"] if "outcome" in row.keys() else None,
    }


def for_symbol(symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    """Past conclusions about one name, newest first, with their review outcome."""
    ensure_schema()
    with connection() as conn:
        rows = conn.execute(
            "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
            "r.outcome AS outcome FROM memory_index m "
            "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
            "WHERE m.kind = '推演' AND m.symbol = ? ORDER BY m.as_of DESC, m.id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def search(text: str, limit: int = 3) -> list[dict[str, Any]]:
    """Free-text recall over stored conclusions (FTS5 trigram, LIKE fallback)."""
    query = (text or "").strip()
    if not query:
        return []
    ensure_schema()
    with connection() as conn:
        rows: list[Any] = []
        if len(query) >= 3:
            try:
                rows = conn.execute(
                    "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, NULL AS outcome "
                    "FROM memory_fts f JOIN memory_index m ON m.id = f.rowid "
                    "WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts) LIMIT ?",
                    (query, limit),
                ).fetchall()
            except Exception:  # noqa: BLE001 - 查询语法出错就退回 LIKE
                rows = []
        if not rows:
            rows = conn.execute(
                "SELECT kind, ref_id, symbol, title, as_of, decision, NULL AS outcome "
                "FROM memory_index WHERE body LIKE ? OR title LIKE ? "
                "ORDER BY as_of DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
    return [_row_to_item(row) for row in rows]


def render(items: list[dict[str, Any]]) -> str:
    """Prompt block for the decision layer (empty when there is nothing)."""
    if not items:
        return ""
    lines = [
        f"【{LABEL}】以下是你过去对同一标的的判断，可能已被证伪；"
        "只能用于对照，不能当作今天的数字或事实："
    ]
    for item in items:
        outcome = item.get("outcome") or "待验证"
        lines.append(
            f"- {item.get('as_of') or '—'}｜{item.get('decision') or '—'}｜事后：{outcome}"
            f"｜{str(item.get('title') or '')[:40]}"
        )
    return "\n".join(lines)


def stats() -> dict[str, Any]:
    ensure_schema()
    with connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0]
        runs = conn.execute(
            "SELECT COUNT(*) FROM memory_index WHERE kind = '推演'"
        ).fetchone()[0]
        judged = conn.execute(
            "SELECT COUNT(*) FROM memory_index WHERE kind = '推演' AND outcome IS NOT NULL"
        ).fetchone()[0]
    return {
        "indexed": int(total or 0),
        "runs": int(runs or 0),
        "judged": int(judged or 0),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
