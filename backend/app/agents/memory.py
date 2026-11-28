"""Memory: what did we conclude about this name before, and how did it turn out?

The rules (goat.md 3.1) are about honesty as much as recall:

* only **past conclusions** are retrieved — never prices, never "facts";
* every item carries its date and its post-hoc outcome, so "上次怎么看" and
  "后来怎样" travel together;
* the block handed to the model is explicitly labelled 历史结论（不是事实）,
  because a model that treats an old opinion as today's data will happily
  restate it as evidence;
* retrieval is **as-of bounded**: a replay of 2026-11-26 may never see a
  conclusion written on 2026-11-30.

Storage is a plain index table plus an FTS5 (trigram) table, so Chinese substring
search works without segmenting words. The index is rebuilt lazily from
``agent_runs`` / ``topic_reports`` — the records themselves stay the single
source of truth.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from app.config import settings
from app.db import connection, now_iso

MAX_BODY = 600
LABEL = "历史结论（不是事实）"
TOPIC_LABEL = "历史报告（不是事实）"
PEER_LABEL = "同行业对照（不是事实，不是本股历史）"
WARNING = "⚠ 最近 3 次同标的判断均被证伪，以下只作反面样本。"

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
    "CREATE INDEX IF NOT EXISTS idx_memory_kind_asof ON memory_index(kind, as_of DESC)",
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


def _iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _as_of(row: Any) -> Optional[str]:
    """Prefer the panel as-of written at run creation; fall back to old data."""
    keys = row.keys()
    value = row["as_of"] if "as_of" in keys else None
    if value:
        return _iso(value)
    raw = row["context_json"] if "context_json" in keys else None
    if raw:
        try:
            ctx = json.loads(raw)
            if isinstance(ctx, dict) and ctx.get("as_of"):
                return _iso(ctx["as_of"])
        except (TypeError, ValueError):
            pass
    return _iso(row["started_at"] if "started_at" in keys else None)


def _run_item(row: Any, group: str = "本股历史") -> dict[str, Any]:
    verdict = json.loads(row["verdict"]) if row["verdict"] else {}
    decision = str(verdict.get("decision") or "")
    pct = verdict.get("final_position_pct")
    title = f"{row['name'] or row['symbol']} 推演"
    body = _clip(
        f"{title}｜裁定 {decision} {pct if pct is not None else '—'}%｜"
        f"{(row['report'] or '')[:400]}"
    )
    return {
        "kind": "推演",
        "ref_id": int(row["id"]),
        "symbol": row["symbol"],
        "title": title,
        "body": body,
        "as_of": _as_of(row),
        "decision": f"{decision} {pct if pct is not None else ''}%".strip(),
        "outcome": None,
        "group": group,
    }


def _topic_item(row: Any, group: str = "历史报告") -> dict[str, Any]:
    title = str(row["title"] or row["topic"] or "")
    body = _clip(f"{row['topic']}｜{title}｜{row['verdict'] or ''}｜{row['markdown'] or ''}")
    return {
        "kind": "主题",
        "ref_id": int(row["id"]),
        "symbol": None,
        "title": title,
        "body": body,
        "as_of": _iso(row["as_of"]),
        "decision": f"{row['verdict'] or ''} {row['confidence'] or ''}".strip(),
        "outcome": None,
        "group": group,
    }


def sync(limit: Optional[int] = None) -> int:
    """Index runs and topic reports that are not in the index yet.

    ``limit`` is kept for tests/callers that want a bounded pass; production
    indexing walks the whole table so old conclusions are never stranded.
    """
    ensure_schema()
    written = 0
    with connection() as conn:
        known = {
            (row["kind"], row["ref_id"]): row["as_of"]
            for row in conn.execute(
                "SELECT kind, ref_id, as_of FROM memory_index"
            ).fetchall()
        }
        run_sql = (
            "SELECT id, symbol, name, verdict, report, started_at, as_of, context_json "
            "FROM agent_runs WHERE status IN ('ok', 'partial') ORDER BY id"
        )
        run_params: list[Any] = []
        if limit:
            run_sql += " LIMIT ?"
            run_params.append(int(limit))
        runs = conn.execute(run_sql, run_params).fetchall()
        topic_sql = (
            "SELECT id, topic, title, verdict, confidence, as_of, markdown "
            "FROM topic_reports ORDER BY id"
        )
        topic_params: list[Any] = []
        if limit:
            topic_sql += " LIMIT ?"
            topic_params.append(int(limit))
        topics = conn.execute(topic_sql, topic_params).fetchall()

        for row in runs:
            item = _run_item(row)
            key = ("推演", int(row["id"]))
            if key in known:
                # Panel as-of backfilled after the index was built: keep the
                # retrieval date honest without a full rebuild.
                if _iso(known[key]) != item["as_of"]:
                    conn.execute(
                        "UPDATE memory_index SET as_of=?, updated_at=? "
                        "WHERE kind='推演' AND ref_id=?",
                        (item["as_of"], now_iso(), item["ref_id"]),
                    )
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_index(kind, ref_id, symbol, title, body, as_of, "
                "decision, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["kind"],
                    item["ref_id"],
                    item["symbol"],
                    item["title"],
                    item["body"],
                    item["as_of"],
                    item["decision"],
                    now_iso(),
                ),
            )
            if cursor.lastrowid:
                conn.execute(
                    "INSERT INTO memory_fts(rowid, body, title) VALUES(?, ?, ?)",
                    (cursor.lastrowid, item["body"], item["title"]),
                )
                written += 1

        for row in topics:
            item = _topic_item(row)
            key = ("主题", int(row["id"]))
            if key in known:
                if _iso(known[key]) != item["as_of"]:
                    conn.execute(
                        "UPDATE memory_index SET as_of=?, updated_at=? "
                        "WHERE kind='主题' AND ref_id=?",
                        (item["as_of"], now_iso(), item["ref_id"]),
                    )
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO memory_index(kind, ref_id, symbol, title, body, as_of, "
                "decision, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item["kind"],
                    item["ref_id"],
                    None,
                    item["title"],
                    item["body"],
                    item["as_of"],
                    item["decision"],
                    now_iso(),
                ),
            )
            if cursor.lastrowid:
                conn.execute(
                    "INSERT INTO memory_fts(rowid, body, title) VALUES(?, ?, ?)",
                    (cursor.lastrowid, item["body"], item["title"]),
                )
                written += 1
    return written


def rebuild() -> int:
    """Drop and rebuild the index from the source tables (idempotent)."""
    ensure_schema()
    with connection() as conn:
        conn.execute("DELETE FROM memory_fts")
        conn.execute("DELETE FROM memory_index")
    return sync()


def _row_to_item(row: Any, group: str = "检索结果") -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "ref_id": row["ref_id"],
        "symbol": row["symbol"],
        "title": row["title"],
        "as_of": row["as_of"],
        "decision": row["decision"],
        "outcome": row["outcome"] if "outcome" in row.keys() else None,
        "group": group,
    }


def _wrong(item: dict[str, Any]) -> bool:
    return "错误" in str(item.get("outcome") or "")


def _trim(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep at most ``limit`` items; demote a symbol whose last three calls failed."""
    if not items:
        return []
    recent = items[:3]
    if len(recent) == 3 and all(_wrong(item) for item in recent):
        item = dict(items[0])
        item["warning"] = True
        return [item]

    chosen: list[dict[str, Any]] = []
    wrong_count = 0
    for item in items:
        if _wrong(item):
            if wrong_count >= 2:
                continue
            wrong_count += 1
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def for_symbol(
    symbol: str,
    as_of: Optional[str] = None,
    limit: int = 3,
    window_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Past conclusions about one name, newest first, with their review outcome.

    ``as_of`` is the panel date of the current analysis: anything written after
    it is invisible. Without ``as_of`` the window is unbounded, which preserves
    the old behaviour for direct callers; production always passes it.
    """
    ensure_schema()
    sql = (
        "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
        "r.outcome AS outcome FROM memory_index m "
        "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
        "WHERE m.kind = '推演' AND m.symbol = ?"
    )
    params: list[Any] = [symbol]
    cutoff = _iso(as_of)
    days = settings.memory_window_days if window_days is None else int(window_days)
    if cutoff:
        sql += " AND date(m.as_of) <= date(?)"
        params.append(cutoff)
        if days > 0:
            sql += " AND date(m.as_of) >= date(?, ?)"
            params.extend([cutoff, f"-{days} days"])
    sql += " ORDER BY m.as_of DESC, m.id DESC LIMIT ?"
    params.append(max(1, limit) + 2)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [{**_row_to_item(row), "group": "本股历史"} for row in rows]
    return _trim(items, max(1, limit))


def for_topic(
    keyword: str,
    as_of: Optional[str] = None,
    limit: int = 2,
    window_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Past topic reports matching a keyword, bounded by ``as_of``."""
    text = (keyword or "").strip()
    if not text:
        return []
    ensure_schema()
    sql = (
        "SELECT id, topic, title, verdict, confidence, as_of FROM topic_reports "
        "WHERE (topic LIKE ? OR title LIKE ?)"
    )
    params: list[Any] = [f"%{text}%", f"%{text}%"]
    cutoff = _iso(as_of)
    days = settings.memory_topic_window_days if window_days is None else int(window_days)
    if cutoff:
        sql += " AND date(as_of) <= date(?)"
        params.append(cutoff)
        if days > 0:
            sql += " AND date(as_of) >= date(?, ?)"
            params.extend([cutoff, f"-{days} days"])
    sql += " ORDER BY as_of DESC, id DESC LIMIT ?"
    params.append(max(1, limit))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "kind": "主题",
            "ref_id": int(row["id"]),
            "symbol": None,
            "title": str(row["title"] or row["topic"] or ""),
            "as_of": _iso(row["as_of"]),
            "decision": f"{row['verdict'] or ''} {row['confidence'] or ''}".strip(),
            "outcome": None,
            "group": "历史报告",
        }
        for row in rows
    ]


def for_industry(
    industry: Optional[str],
    as_of: Optional[str],
    exclude_symbol: Optional[str] = None,
    limit: int = 2,
    window_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Peer conclusions for the same industry — explicitly *not* this name's history."""
    name = (industry or "").strip()
    if not name:
        return []
    try:
        from app.data import store

        with store.session(read_only=True) as conn:
            rows = conn.execute(
                "SELECT symbol FROM symbols WHERE industry = ? AND symbol != ? LIMIT 300",
                [name, exclude_symbol or ""],
            ).fetchall()
    except Exception:  # noqa: BLE001 - panel may be unavailable
        return []
    symbols = [row[0] for row in rows if row[0]]
    if not symbols:
        return []

    ensure_schema()
    marks = ",".join("?" for _ in symbols)
    sql = (
        "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
        "r.outcome AS outcome FROM memory_index m "
        "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
        f"WHERE m.kind = '推演' AND m.symbol IN ({marks})"
    )
    params: list[Any] = list(symbols)
    cutoff = _iso(as_of)
    days = settings.memory_window_days if window_days is None else int(window_days)
    if cutoff:
        sql += " AND date(m.as_of) <= date(?)"
        params.append(cutoff)
        if days > 0:
            sql += " AND date(m.as_of) >= date(?, ?)"
            params.extend([cutoff, f"-{days} days"])
    sql += " ORDER BY m.as_of DESC, m.id DESC LIMIT ?"
    params.append(max(1, limit))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{**_row_to_item(row), "group": "同行业对照"} for row in rows]


def search(
    text: str, as_of: Optional[str] = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Free-text recall over stored conclusions (FTS5 trigram, LIKE fallback)."""
    query = (text or "").strip()
    if not query:
        return []
    ensure_schema()
    limit = max(1, min(int(limit), 100))
    cutoff = _iso(as_of)
    with connection() as conn:
        rows: list[Any] = []
        if len(query) >= 3:
            sql = (
                "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
                "r.outcome AS outcome FROM memory_fts f "
                "JOIN memory_index m ON m.id = f.rowid "
                "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
                "WHERE memory_fts MATCH ?"
            )
            params: list[Any] = [query]
            if cutoff:
                sql += " AND date(m.as_of) <= date(?)"
                params.append(cutoff)
            sql += (
                " ORDER BY bm25(memory_fts) + "
                "0.01 * MAX(0, julianday('now') - julianday(m.as_of)), m.as_of DESC LIMIT ?"
            )
            params.append(limit)
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception:  # noqa: BLE001 - query syntax error -> LIKE
                rows = []
        if not rows:
            sql = (
                "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
                "r.outcome AS outcome FROM memory_index m "
                "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
                "WHERE (m.body LIKE ? OR m.title LIKE ?)"
            )
            params = [f"%{query}%", f"%{query}%"]
            if cutoff:
                sql += " AND date(m.as_of) <= date(?)"
                params.append(cutoff)
            sql += " ORDER BY m.as_of DESC, m.id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(row) for row in rows]


def recent(limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    with connection() as conn:
        rows = conn.execute(
            "SELECT m.kind, m.ref_id, m.symbol, m.title, m.as_of, m.decision, "
            "r.outcome AS outcome FROM memory_index m "
            "LEFT JOIN agent_reviews r ON r.run_id = m.ref_id AND m.kind = '推演' "
            "ORDER BY m.as_of DESC, m.id DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def _line(item: dict[str, Any]) -> str:
    if item.get("kind") == "主题":
        return (
            f"- {item.get('as_of') or '—'}｜{item.get('decision') or '—'}"
            f"｜{str(item.get('title') or '')[:40]}"
        )
    outcome = item.get("outcome") or "待验证"
    return (
        f"- {item.get('as_of') or '—'}｜{item.get('decision') or '—'}｜事后：{outcome}"
        f"｜{str(item.get('title') or '')[:40]}"
    )


def _cap(lines: list[str]) -> str:
    cap = max(200, int(settings.memory_block_chars))
    while len("\n".join(lines)) > cap and len(lines) > 3:
        lines.pop()
    return "\n".join(lines)


def render(items: list[dict[str, Any]]) -> str:
    """Prompt block for the decision layer (empty when there is nothing)."""
    if not items:
        return ""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(str(item.get("group") or "本股历史"), []).append(item)

    lines: list[str] = []
    first = True
    for group, group_items in groups.items():
        if group == "同行业对照":
            label = PEER_LABEL
        else:
            label = LABEL
        if first:
            lines.append(
                f"【{label}】以下是你过去对同一标的的判断，可能已被证伪；"
                "只能用于对照，不能当作今天的数字或事实："
            )
            first = False
        else:
            lines.append(f"【{label}】")
        lines.extend(_line(item) for item in group_items)
    if any(item.get("warning") for item in items):
        lines.insert(1, WARNING)
    return _cap(lines)


def render_topics(items: list[dict[str, Any]]) -> str:
    """Prompt block for thematic research (separate label from stock decisions)."""
    if not items:
        return ""
    lines = [
        f"【{TOPIC_LABEL}】以下是与本主题相关的历史报告，可能已被证伪；"
        "只能用于对照，不能当作今天的数字或事实："
    ]
    lines.extend(_line(item) for item in items)
    return _cap(lines)


def memory_ids(items: list[dict[str, Any]]) -> list[str]:
    return [f"{item.get('kind')}:{item.get('ref_id')}" for item in items]


def stats() -> dict[str, Any]:
    ensure_schema()
    with connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memory_index").fetchone()[0]
        runs = conn.execute(
            "SELECT COUNT(*) FROM memory_index WHERE kind = '推演'"
        ).fetchone()[0]
        topics = conn.execute(
            "SELECT COUNT(*) FROM memory_index WHERE kind = '主题'"
        ).fetchone()[0]
        judged = conn.execute(
            "SELECT COUNT(*) FROM memory_index m JOIN agent_reviews r "
            "ON r.run_id = m.ref_id AND m.kind = '推演' "
            "WHERE r.outcome IS NOT NULL"
        ).fetchone()[0]
        bounds = conn.execute(
            "SELECT MIN(as_of), MAX(as_of) FROM memory_index"
        ).fetchone()
    return {
        "indexed": int(total or 0),
        "runs": int(runs or 0),
        "topics": int(topics or 0),
        "judged": int(judged or 0),
        "oldest": bounds[0] if bounds else None,
        "newest": bounds[1] if bounds else None,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
