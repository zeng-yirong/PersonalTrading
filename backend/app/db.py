"""SQLite persistence layer.

The project deliberately uses the standard library ``sqlite3`` module so a
local install has no database server dependency.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL DEFAULT 'stock',
    created_at TEXT NOT NULL,
    added_trade_date TEXT,
    added_close REAL,
    added_adj REAL,
    added_source TEXT
);

CREATE TABLE IF NOT EXISTS intraday_cache (
    symbol TEXT PRIMARY KEY,
    trade_date TEXT,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kline (
    symbol TEXT NOT NULL,
    period TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,
    source TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, period, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_kline_symbol_period
    ON kline(symbol, period, trade_date);

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    asset_type TEXT,
    report_date TEXT,
    revenue REAL,
    net_profit REAL,
    roe REAL,
    gross_margin REAL,
    debt_ratio REAL,
    eps REAL,
    bps REAL,
    pe REAL,
    pb REAL,
    total_mv REAL,
    source TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    model TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_symbol
    ON research_notes(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL,
    model TEXT,
    rounds INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    calls INTEGER DEFAULT 0,
    verdict TEXT,
    report TEXT,
    context_json TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_symbol
    ON agent_runs(symbol, started_at DESC);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    node TEXT NOT NULL,
    label TEXT,
    role TEXT,
    round INTEGER,
    status TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_run
    ON agent_messages(run_id, id);

-- ---------------------------------------------------------------- paper account
CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY,
    name TEXT,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    name TEXT,
    shares REAL NOT NULL,
    avg_cost REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    run_id INTEGER,
    note TEXT,
    entry_adj REAL              -- 建仓时的累计复权因子，用于拆股/分红后的估值
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_status
    ON paper_positions(status, symbol);

-- 每次技能被加载时留一行：哪个模块、哪个技能、哪一版。推演另有 skill_version
-- 列，这张表覆盖主题 / 选股 / 回测 / 复盘这些没有 run 表的地方。
CREATE TABLE IF NOT EXISTS skill_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL,
    ref_id INTEGER,
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_calls_module ON skill_calls(module, created_at);

-- 每周把全部预设策略跑一遍近一年回测，结果按超额排序留档。
CREATE TABLE IF NOT EXISTS preset_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    as_of TEXT,
    start TEXT,
    end TEXT,
    rebalance INTEGER,
    top_n INTEGER,
    rows_json TEXT NOT NULL
);

-- 策略库：策略是实体，有状态、有来源、有留档理由。
CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    query_json TEXT NOT NULL,
    params_json TEXT,
    source TEXT,                 -- preset | factor_combo | neighbourhood | template | manual
    horizon TEXT NOT NULL DEFAULT 'long',   -- short | mid | long（决定参数与门槛）
    status TEXT NOT NULL,        -- candidate | watch | champion | retired | paused
    created_at TEXT NOT NULL,
    promoted_at TEXT,
    retired_at TEXT,
    retire_reason TEXT,
    notes TEXT,
    provenance_json TEXT         -- 来源：origin / 依据 / 建立时的面板日期
);

CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);

-- 某条策略在某一批里的成绩（1 年 / 2 年两个窗口分别一行）。
CREATE TABLE IF NOT EXISTS strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    batch_id INTEGER,
    window TEXT NOT NULL,        -- 1y | 2y
    start TEXT,
    end TEXT,
    metrics_json TEXT NOT NULL,
    rank INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs ON strategy_runs(strategy_id, window, id);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    shares REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    amount REAL NOT NULL,
    trade_date TEXT NOT NULL,
    run_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_nav (
    trade_date TEXT PRIMARY KEY,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    total REAL NOT NULL,
    benchmark REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_reviews (
    run_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    action TEXT,
    position_pct REAL,
    conviction REAL,
    entry_date TEXT,
    entry_price REAL,
    d5_date TEXT,
    d5_return REAL,
    d5_excess REAL,
    d20_date TEXT,
    d20_return REAL,
    d20_excess REAL,
    outcome TEXT,
    updated_at TEXT NOT NULL
);

-- ------------------------------------------------------------------- backtest
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    spec_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    nav_json TEXT NOT NULL,
    periods_json TEXT NOT NULL,
    trades_json TEXT,
    warnings_json TEXT,
    source_run_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
    ON backtest_runs(created_at DESC);

-- --------------------------------------------------------------- topic reports
CREATE TABLE IF NOT EXISTS topic_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    title TEXT,
    scope TEXT,
    verdict TEXT,
    confidence INTEGER,
    model TEXT,
    as_of TEXT,
    payload_json TEXT,
    markdown TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    source_run_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_reports_created
    ON topic_reports(created_at DESC);

-- ---------------------------------------------------------------- factor runs
CREATE TABLE IF NOT EXISTS factor_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""


def _db_path() -> Path:
    path = Path(settings.database_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connection():
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    # 首次启动把 presets.json 导入为模板（幂等）；旧库把误标成 champion 的示例行改回
    # template。两者失败都不该挡住启动。
    try:
        from app.strategy import store as strategy_store

        strategy_store.seed_from_presets()
        strategy_store.backfill_legacy_presets()
    except Exception:  # noqa: BLE001 - 冷启动时策略表可能还没建好
        pass


def _migrate(conn) -> None:
    """Add columns that older databases predate.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so every
    column introduced after a workspace was first created needs an explicit
    ``ALTER TABLE`` here. Each one is guarded by ``PRAGMA table_info`` so the
    migration is idempotent.
    """
    migrations = (
        ("backtest_runs", "source_run_id", "INTEGER"),
        ("topic_reports", "source_run_id", "INTEGER"),
        ("agent_runs", "skill_version", "TEXT"),
        ("paper_positions", "entry_adj", "REAL"),
        ("strategies", "horizon", "TEXT DEFAULT 'long'"),
        ("strategies", "provenance_json", "TEXT"),
        ("watchlist", "added_trade_date", "TEXT"),
        ("watchlist", "added_close", "REAL"),
        ("watchlist", "added_adj", "REAL"),
        ("watchlist", "added_source", "TEXT"),
    )
    for table, column, kind in migrations:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- watchlist
def list_watchlist() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT symbol, name, asset_type, created_at, added_trade_date, "
            "added_close, added_adj, added_source FROM watchlist "
            "ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_watchlist(
    symbol: str,
    name: str,
    asset_type: str,
    added_trade_date: Optional[str] = None,
    added_close: Optional[float] = None,
    added_adj: Optional[float] = None,
    added_source: Optional[str] = None,
) -> dict[str, Any]:
    with connection() as conn:
        conn.execute(
            "INSERT INTO watchlist(symbol, name, asset_type, created_at, "
            "added_trade_date, added_close, added_adj, added_source) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, "
            "asset_type=excluded.asset_type, "
            "added_trade_date=COALESCE(added_trade_date, excluded.added_trade_date), "
            "added_close=COALESCE(added_close, excluded.added_close), "
            "added_adj=COALESCE(added_adj, excluded.added_adj), "
            "added_source=COALESCE(added_source, excluded.added_source)",
            (
                symbol,
                name,
                asset_type,
                now_iso(),
                added_trade_date,
                added_close,
                added_adj,
                added_source,
            ),
        )
    item = {
        "symbol": symbol,
        "name": name,
        "asset_type": asset_type,
        "added_trade_date": added_trade_date,
        "added_close": added_close,
        "added_adj": added_adj,
        "added_source": added_source,
    }
    return item


def set_watchlist_baseline(
    symbol: str,
    trade_date: Optional[str],
    close: Optional[float],
    adj: Optional[float],
    source: Optional[str],
) -> None:
    """回填旧自选（本功能上线前加入）的添加时基准价。"""
    with connection() as conn:
        conn.execute(
            "UPDATE watchlist SET added_trade_date=?, added_close=?, added_adj=?, "
            "added_source=? WHERE symbol=? AND added_close IS NULL",
            (trade_date, close, adj, source, symbol),
        )


def remove_watchlist(symbol: str) -> bool:
    with connection() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        return cur.rowcount > 0


# -------------------------------------------------------------------- kline
def load_kline(symbol: str, period: str, ttl_seconds: int) -> Optional[list[dict[str, Any]]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT trade_date, open, high, low, close, volume, amount, source, updated_at "
            "FROM kline WHERE symbol = ? AND period = ? ORDER BY trade_date ASC",
            (symbol, period),
        ).fetchall()
    if not rows:
        return None
    updated = rows[-1]["updated_at"]
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds()
    except (TypeError, ValueError):
        return None
    if age > ttl_seconds:
        return None
    return [
        {
            "trade_date": row["trade_date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "amount": row["amount"],
            "source": row["source"],
        }
        for row in rows
    ]


def save_kline(symbol: str, period: str, candles: Iterable[dict[str, Any]], source: str) -> None:
    timestamp = now_iso()
    with connection() as conn:
        conn.executemany(
            "INSERT INTO kline(symbol, period, trade_date, open, high, low, close, "
            "volume, amount, source, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, period, trade_date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume, amount=excluded.amount, "
            "source=excluded.source, updated_at=excluded.updated_at",
            [
                (
                    symbol,
                    period,
                    item["trade_date"],
                    item.get("open"),
                    item.get("high"),
                    item.get("low"),
                    item.get("close"),
                    item.get("volume"),
                    item.get("amount"),
                    source,
                    timestamp,
                )
                for item in candles
            ],
        )


# -------------------------------------------------------------- fundamentals
def load_fundamentals(symbol: str, ttl_seconds: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM fundamentals WHERE symbol = ?", (symbol,)
        ).fetchone()
    if not row:
        return None
    try:
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(row["updated_at"])
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    if age > ttl_seconds:
        return None
    return dict(row)


def save_fundamentals(symbol: str, data: dict[str, Any]) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO fundamentals(symbol, name, asset_type, report_date, revenue, "
            "net_profit, roe, gross_margin, debt_ratio, eps, bps, pe, pb, total_mv, "
            "source, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, "
            "asset_type=excluded.asset_type, report_date=excluded.report_date, "
            "revenue=excluded.revenue, net_profit=excluded.net_profit, roe=excluded.roe, "
            "gross_margin=excluded.gross_margin, debt_ratio=excluded.debt_ratio, "
            "eps=excluded.eps, bps=excluded.bps, pe=excluded.pe, pb=excluded.pb, "
            "total_mv=excluded.total_mv, source=excluded.source, "
            "updated_at=excluded.updated_at",
            (
                symbol,
                data.get("name"),
                data.get("asset_type"),
                data.get("report_date"),
                data.get("revenue"),
                data.get("net_profit"),
                data.get("roe"),
                data.get("gross_margin"),
                data.get("debt_ratio"),
                data.get("eps"),
                data.get("bps"),
                data.get("pe"),
                data.get("pb"),
                data.get("total_mv"),
                data.get("source"),
                now_iso(),
            ),
        )


# ------------------------------------------------------------- research notes
def save_note(symbol: str, model: str, content: str) -> dict[str, Any]:
    created = now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO research_notes(symbol, model, content, created_at) "
            "VALUES(?, ?, ?, ?)",
            (symbol, model, content, created),
        )
        note_id = cur.lastrowid
    return {
        "id": note_id,
        "symbol": symbol,
        "model": model,
        "content": content,
        "created_at": created,
    }


def list_notes(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, symbol, model, content, created_at FROM research_notes "
            "WHERE symbol = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
        return [dict(row) for row in rows]