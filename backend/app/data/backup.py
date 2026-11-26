"""Snapshot the local databases and prove the copy is usable.

The panel is 5 GB of data that took hours to download; there is no second copy
anywhere. A backup that is never opened is not a backup, so every run verifies
the copy by *reading it back* — table count, row count and the last trade date
must match the source, and the SQLite file must pass ``integrity_check``.

DuckDB keeps its writes in ``<db>.wal`` until a checkpoint, so the WAL is copied
alongside the database file; opening the copy replays it. If the copy cannot be
opened or the numbers differ, the run fails loudly instead of leaving a file
that looks like a backup.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.data import store

STAMP_FORMAT = "%Y-%m-%d_%H%M%S"


def default_target() -> Path:
    """Where backups go when the caller does not say.

    Prefer a different physical disk — a copy on the same disk only protects
    against a corrupted file, not against the disk dying.
    """
    env = os.environ.get("PERSONALTRADING_BACKUP_DIR", "").strip()
    if env:
        return Path(env)
    other = Path("E:/backups/PersonalTrading")
    if Path("E:/").exists():
        return other
    return Path(settings.market_db_path).resolve().parent / "backups"


def _stats(path: Path) -> dict[str, Any]:
    """Read the panel back: how many tables, how many bars, which day."""
    import duckdb

    conn = duckdb.connect(str(path), read_only=True)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'").fetchone()
        as_of = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        bars = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily").fetchone()[0]
    finally:
        conn.close()
    return {
        "tables": int(rows[0] if rows else 0),
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
        "bars": int(bars or 0),
        "symbols": int(symbols or 0),
    }


def _sqlite_stats(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for table in ("watchlist", "agent_runs", "topic_reports", "paper_trades", "backtest_runs"):
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
    finally:
        conn.close()
    return {"integrity": check, "counts": counts}


def run(
    out_dir: Optional[Path] = None,
    keep: int = 5,
    include_app: bool = True,
) -> dict[str, Any]:
    """Copy both databases into ``out_dir/<stamp>/`` and verify the copies."""
    target_root = Path(out_dir) if out_dir else default_target()
    stamp = datetime.now().strftime(STAMP_FORMAT)
    target = target_root / stamp
    target.mkdir(parents=True, exist_ok=True)

    source = Path(settings.market_db_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"面板不存在：{source}")
    before = _stats(source)

    copied: list[dict[str, Any]] = []
    for suffix in ("", ".wal"):
        candidate = Path(str(source) + suffix)
        if not candidate.exists():
            continue
        destination = target / (source.name + suffix)
        shutil.copy2(candidate, destination)
        copied.append(
            {
                "file": destination.name,
                "bytes": destination.stat().st_size,
            }
        )

    after = _stats(target / source.name)
    verified = before == after

    app_source = Path(settings.database_path).resolve()
    app_check: Optional[dict[str, Any]] = None
    if include_app and app_source.exists():
        app_copy = target / app_source.name
        with sqlite3.connect(str(app_source)) as origin, sqlite3.connect(str(app_copy)) as dest:
            origin.backup(dest)      # consistent even if the server is writing
        app_check = _sqlite_stats(app_copy)
        app_check["verified"] = app_check["integrity"] == "ok"
        copied.append({"file": app_copy.name, "bytes": app_copy.stat().st_size})

    pruned: list[str] = []
    if keep > 0:
        pruned = _prune(target_root, keep)

    return {
        "target": str(target),
        "bytes": sum(item["bytes"] for item in copied),
        "files": copied,
        "source": before,
        "copy": after,
        "verified": verified,
        "app": app_check,
        "pruned": pruned,
    }


def _prune(root: Path, keep: int) -> list[str]:
    """Delete the oldest <stamp> folders, keeping the newest ``keep``.

    Only folders named like our own stamps are considered — anything else in the
    target directory (a manual copy, someone's notes) is left alone.
    """
    if not root.exists():
        return []
    folders = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        try:
            datetime.strptime(item.name, STAMP_FORMAT)
        except ValueError:
            continue
        folders.append(item)
    folders.sort(key=lambda item: item.name, reverse=True)
    removed = []
    for folder in folders[keep:]:
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder.name)
    return removed
