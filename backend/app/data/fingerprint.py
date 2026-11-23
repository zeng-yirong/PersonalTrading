"""One identity for the panel snapshot, cheap enough to embed in every report.

The DuckDB file is too large for git, so "which data was this run based on" is
answered by a short digest instead: the last trading day plus the row count of
``daily`` and the max date of each key table. Two digests that differ mean two
different datasets even when the file name is the same.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime
from typing import Any, Optional

from app.data import store

CACHE_SECONDS = 300.0
KEY_TABLES = (
    "daily",
    "daily_basic",
    "moneyflow",
    "margin_detail",
    "stk_factor",
    "index_daily",
)

_cache: Optional[tuple[float, dict[str, Any]]] = None
_lock = threading.Lock()


def current(force: bool = False) -> dict[str, Any]:
    global _cache
    with _lock:
        if not force and _cache and time.time() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        value = _compute()
        _cache = (time.time(), value)
        return value


def _compute() -> dict[str, Any]:
    with store.session(read_only=True) as conn:
        row = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily"
        ).fetchone()
        first, last, rows = (row if row else (None, None, 0))
        parts = [f"daily:{last}:{int(rows or 0)}"]
        for table in KEY_TABLES[1:]:
            if not store.table_exists(conn, table):
                parts.append(f"{table}:missing")
                continue
            value = conn.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
            parts.append(f"{table}:{value[0] if value else None}")
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return {
        "first_date": first.isoformat() if first else None,
        "as_of": last.isoformat() if last else None,
        "bars": int(rows or 0),
        "digest": digest,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
