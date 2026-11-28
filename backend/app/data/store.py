"""DuckDB-backed market data store (P0 data foundation).

SQLite keeps small, transactional things (watchlist, notes, positions). The
market panel — millions of rows queried mostly as cross-sections by date —
lives here in DuckDB, which is columnar and scans ``WHERE trade_date = ?``
cheaply without needing an index on the date.

Prices stored in ``daily`` are **raw** (un-adjusted). ``adj_factor`` is the
cumulative adjustment factor, so:

* 后复权 (hfq): ``close * adj_factor``
* 前复权 (qfq): ``close * adj_factor / latest_adj_factor``
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

import duckdb

from app.config import settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol      VARCHAR PRIMARY KEY,
    ts_code     VARCHAR,
    name        VARCHAR,
    exchange    VARCHAR,
    asset_type  VARCHAR,
    board       VARCHAR,
    industry    VARCHAR,
    list_date   DATE,
    delist_date DATE,
    is_active   BOOLEAN,
    updated_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS calendar (
    cal_date DATE PRIMARY KEY,
    is_open  BOOLEAN,
    source   VARCHAR
);

CREATE TABLE IF NOT EXISTS daily (
    symbol     VARCHAR NOT NULL,
    trade_date DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    pre_close  DOUBLE,
    volume     DOUBLE,
    amount     DOUBLE,
    adj_factor DOUBLE,
    source     VARCHAR,
    updated_at TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS daily_basic (
    symbol        VARCHAR NOT NULL,
    trade_date    DATE    NOT NULL,
    pe            DOUBLE,
    pe_ttm        DOUBLE,
    pb            DOUBLE,
    ps_ttm        DOUBLE,
    dv_ttm        DOUBLE,
    total_mv      DOUBLE,
    circ_mv       DOUBLE,
    turnover_rate DOUBLE,
    volume_ratio  DOUBLE,
    source        VARCHAR,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS sync_log (
    job         VARCHAR,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    rows        BIGINT,
    status      VARCHAR,
    detail      VARCHAR
);

CREATE TABLE IF NOT EXISTS dataset_progress (
    dataset    VARCHAR,
    part       VARCHAR,
    rows       BIGINT,
    done       BOOLEAN,
    updated_at TIMESTAMP,
    PRIMARY KEY (dataset, part)
);
"""

DAILY_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "adj_factor",
    "source",
)

SYMBOL_COLUMNS = (
    "symbol",
    "ts_code",
    "name",
    "exchange",
    "asset_type",
    "board",
    "industry",
    "list_date",
    "delist_date",
    "is_active",
)

DAILY_BASIC_COLUMNS = (
    "symbol",
    "trade_date",
    "pe",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "total_mv",
    "circ_mv",
    "turnover_rate",
    "volume_ratio",
)


class StoreError(RuntimeError):
    pass


class StoreLocked(StoreError):
    """The file is held by another process (a running sync)."""


# DuckDB surfaces a busy file as an IOException whose text depends on the OS
# locale, so match the message instead of the exception type.
LOCK_HINTS = (
    "lock",
    "另一个程序正在使用此文件",
    "being used by another process",
    "cannot access the file",
    "could not set lock",
)


def _is_locked(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in LOCK_HINTS)


def db_path() -> Path:
    path = Path(settings.market_db_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = db_path()
    if read_only and not path.exists():
        raise StoreError(f"市场数据库不存在：{path}，请先运行 scripts/sync_market.py")
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error as exc:  # pragma: no cover - depends on local file state
        if _is_locked(exc):
            raise StoreLocked(f"市场数据库被其他进程占用：{path}") from exc
        raise StoreError(f"无法打开市场数据库 {path}：{exc}") from exc


@contextmanager
def session(read_only: bool = False, retries: int = 10) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a short-lived connection, waiting out another process's write lock.

    DuckDB allows one writer process per file. A long ``sync_market.py`` run holds
    that lock while it flushes; an API read arriving at that moment should wait a
    moment instead of surfacing a lock error to the user.
    """
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with _lock:
                conn = connect(read_only=read_only)
            break
        except StoreLocked as exc:
            last_error = exc
            time.sleep(0.2 * (attempt + 1))
    else:
        raise StoreError(
            f"市场数据库被其他进程占用（可能正在同步），已重试 {retries} 次：{last_error}"
        ) from last_error

    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    with session() as conn:
        conn.execute(SCHEMA)


# ------------------------------------------------------------------ utilities
def _rows_to_dataframe(rows: Sequence[Sequence[Any]], columns: Sequence[str]):
    """Build a DataFrame when pandas is available; it makes bulk loads vectorized."""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - pandas ships with akshare
        return None
    return pd.DataFrame([tuple(row) for row in rows], columns=list(columns))


def _quote(identifier: str) -> str:
    """Quote an identifier: Tushare has fields named ``limit``/``offset``."""
    return '"' + str(identifier).replace('"', '""') + '"'


def _conflict_clause(
    columns: Sequence[str],
    conflict: Optional[Sequence[str]],
    overrides: Optional[dict[str, str]] = None,
) -> str:
    if not conflict:
        return ""
    keys = list(conflict)
    overrides = overrides or {}
    assignments = [
        f"{_quote(col)} = {overrides[col]}"
        if col in overrides
        else f"{_quote(col)} = excluded.{_quote(col)}"
        for col in columns
        if col not in keys
    ]
    key_list = ", ".join(_quote(key) for key in keys)
    if not assignments:
        # Every column is part of the key: there is nothing to update.
        return f" ON CONFLICT ({key_list}) DO NOTHING"
    return f" ON CONFLICT ({key_list}) DO UPDATE SET {', '.join(assignments)}"


def _bulk_insert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    conflict: Optional[Sequence[str]] = None,
) -> int:
    if not rows:
        return 0
    column_list = ", ".join(_quote(col) for col in columns)
    suffix = _conflict_clause(columns, conflict)
    frame = _rows_to_dataframe(rows, columns)
    if frame is not None:
        conn.register("_incoming", frame)
        try:
            conn.execute(
                f"INSERT INTO {_quote(table)} ({column_list}) "
                f"SELECT {column_list} FROM _incoming{suffix}"
            )
        finally:
            conn.unregister("_incoming")
        return len(rows)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {_quote(table)} ({column_list}) VALUES ({placeholders}){suffix}",
        [tuple(row) for row in rows],
    )
    return len(rows)


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------- writes
def upsert_symbols(rows: Iterable[dict[str, Any]]) -> int:
    """Merge symbol metadata, never blanking a field the source omits.

    A spot snapshot knows the name and board but not the listing date; writing
    NULLs for the rest would erase what ``stock_basic`` already provided.
    """
    payload = []
    for item in rows:
        payload.append(
            (
                item["symbol"],
                item.get("ts_code"),
                item.get("name"),
                item.get("exchange"),
                item.get("asset_type", "stock"),
                item.get("board"),
                item.get("industry"),
                _as_date(item.get("list_date")),
                _as_date(item.get("delist_date")),
                bool(item.get("is_active", True)),
                datetime.now(),
            )
        )
    if not payload:
        return 0
    columns = SYMBOL_COLUMNS + ("updated_at",)
    with session() as conn:
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _stage_symbols ("
            "symbol VARCHAR, ts_code VARCHAR, name VARCHAR, exchange VARCHAR, "
            "asset_type VARCHAR, board VARCHAR, industry VARCHAR, list_date DATE, "
            "delist_date DATE, is_active BOOLEAN, updated_at TIMESTAMP)"
        )
        _bulk_insert(conn, "_stage_symbols", columns, payload)
        conn.execute(
            "INSERT INTO symbols ("
            + ", ".join(columns)
            + ") SELECT "
            + ", ".join(columns)
            + " FROM _stage_symbols"
            + _conflict_clause(
                columns,
                ("symbol",),
                {
                    "ts_code": "COALESCE(excluded.ts_code, symbols.ts_code)",
                    "name": "COALESCE(excluded.name, symbols.name)",
                    "exchange": "COALESCE(excluded.exchange, symbols.exchange)",
                    "asset_type": "COALESCE(excluded.asset_type, symbols.asset_type)",
                    "board": "COALESCE(excluded.board, symbols.board)",
                    "industry": "COALESCE(excluded.industry, symbols.industry)",
                    "list_date": "COALESCE(excluded.list_date, symbols.list_date)",
                    "delist_date": "COALESCE(excluded.delist_date, symbols.delist_date)",
                },
            )
        )
        conn.execute("DROP TABLE IF EXISTS _stage_symbols")
    return len(payload)


def backfill_symbols_from_daily(lookback_days: int = 10) -> int:
    """Add codes that trade in ``daily`` but never made it into ``symbols``.

    Tushare returns bars for every listed code, while ``symbols`` is filled by
    the Sina/Eastmoney universe pull. New ETFs and BJ listings therefore stayed
    invisible to breadth / detail / screener for weeks. One SQL pass closes the
    gap; names come from ``fund_basic`` when the code is an ETF.
    """
    with session() as conn:
        if not table_exists(conn, "daily"):
            return 0
        before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        fund_join = "LEFT JOIN (SELECT split_part(ts_code, '.', 1) AS symbol, name FROM fund_basic WHERE market = 'E') f ON f.symbol = d.symbol"
        name_expr = "COALESCE(f.name, d.symbol)"
        if not table_exists(conn, "fund_basic"):
            fund_join = ""
            name_expr = "d.symbol"
        conn.execute(
            f"""
            INSERT INTO symbols
                (symbol, ts_code, name, exchange, asset_type, board, industry,
                 list_date, delist_date, is_active, updated_at)
            SELECT d.symbol,
                   CASE
                       WHEN d.symbol LIKE '6%' OR d.symbol LIKE '5%' THEN d.symbol || '.SH'
                       WHEN d.symbol LIKE '4%' OR d.symbol LIKE '8%' OR d.symbol LIKE '92%'
                           THEN d.symbol || '.BJ'
                       ELSE d.symbol || '.SZ'
                   END,
                   {name_expr},
                   CASE
                       WHEN d.symbol LIKE '6%' OR d.symbol LIKE '5%' THEN 'SSE'
                       WHEN d.symbol LIKE '4%' OR d.symbol LIKE '8%' OR d.symbol LIKE '92%'
                           THEN 'BJSE'
                       ELSE 'SZSE'
                   END,
                   CASE WHEN regexp_matches(d.symbol, '^(15|16|18|5[0-8])')
                        THEN 'etf' ELSE 'stock' END,
                   CASE
                       WHEN regexp_matches(d.symbol, '^(15|16|18|5[0-8])') THEN NULL
                       WHEN d.symbol LIKE '688%' THEN '科创板'
                       WHEN d.symbol LIKE '30%' THEN '创业板'
                       WHEN d.symbol LIKE '4%' OR d.symbol LIKE '8%' OR d.symbol LIKE '92%'
                           THEN '北交所'
                       WHEN d.symbol LIKE '6%' THEN '沪市主板'
                       ELSE '深市主板'
                   END,
                   NULL, NULL, NULL, TRUE, now()
            FROM (
                SELECT DISTINCT symbol FROM daily
                WHERE trade_date >= (SELECT MAX(trade_date) FROM daily) - INTERVAL '{lookback_days} days'
            ) d
            LEFT JOIN symbols s ON s.symbol = d.symbol
            {fund_join}
            WHERE s.symbol IS NULL
            """
        )
        # 早先的推断把 5xxxxx 的场内 ETF 记成了深市，这里顺手纠正一次。
        conn.execute(
            "UPDATE symbols SET ts_code = symbol || '.SH', exchange = 'SSE', board = NULL "
            "WHERE asset_type = 'etf' AND symbol LIKE '5%' AND ts_code NOT LIKE '%.SH'"
        )
        conn.execute("UPDATE symbols SET board = NULL WHERE asset_type = 'etf'")
        after = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    return int(after - before)


def upsert_calendar(rows: Iterable[tuple[date, bool]], source: str = "sina") -> int:
    payload = [(_as_date(day), bool(is_open), source) for day, is_open in rows]
    payload = [row for row in payload if row[0] is not None]
    with session() as conn:
        return _bulk_insert(
            conn, "calendar", ("cal_date", "is_open", "source"), payload, conflict=("cal_date",)
        )


def upsert_daily(rows: Iterable[dict[str, Any]], source: str) -> dict[str, int]:
    """Insert daily bars, keeping any existing adjustment factor.

    A factor already stored is authoritative: a later raw-price refresh must not
    wipe it, otherwise every re-sync would silently de-adjust the history.
    """
    stamped = datetime.now()
    payload = []
    for item in rows:
        trade_date = _as_date(item.get("trade_date"))
        symbol = item.get("symbol")
        if not symbol or trade_date is None:
            continue
        payload.append(
            (
                symbol,
                trade_date,
                item.get("open"),
                item.get("high"),
                item.get("low"),
                item.get("close"),
                item.get("pre_close"),
                item.get("volume"),
                item.get("amount"),
                item.get("adj_factor"),
                item.get("source", source),
                stamped,
            )
        )
    if not payload:
        return {"rows": 0, "symbols": 0}

    with session() as conn:
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _stage_daily ("
            "symbol VARCHAR, trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, "
            "close DOUBLE, pre_close DOUBLE, volume DOUBLE, amount DOUBLE, "
            "adj_factor DOUBLE, source VARCHAR, updated_at TIMESTAMP)"
        )
        _bulk_insert(
            conn,
            "_stage_daily",
            DAILY_COLUMNS + ("updated_at",),
            [row for row in payload],
        )
        conn.execute(
            "INSERT INTO daily ("
            + ", ".join(DAILY_COLUMNS + ("updated_at",))
            + ") SELECT "
            + ", ".join(DAILY_COLUMNS + ("updated_at",))
            + " FROM _stage_daily"
            + _conflict_clause(
                DAILY_COLUMNS + ("updated_at",),
                ("symbol", "trade_date"),
                {
                    "pre_close": "COALESCE(excluded.pre_close, daily.pre_close)",
                    "amount": "COALESCE(excluded.amount, daily.amount)",
                    "adj_factor": "COALESCE(excluded.adj_factor, daily.adj_factor)",
                },
            )
        )
        symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM _stage_daily").fetchone()
        conn.execute("DROP TABLE IF EXISTS _stage_daily")
        return {"rows": len(payload), "symbols": int(symbols[0]) if symbols else 0}


def upsert_adj_factors(rows: Iterable[dict[str, Any]], source: str = "tushare") -> int:
    payload = []
    for item in rows:
        trade_date = _as_date(item.get("trade_date"))
        if item.get("symbol") and trade_date is not None:
            payload.append((item["symbol"], trade_date, item.get("adj_factor")))
    if not payload:
        return 0
    with session() as conn:
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _stage_adj "
            "(symbol VARCHAR, trade_date DATE, adj_factor DOUBLE)"
        )
        _bulk_insert(conn, "_stage_adj", ("symbol", "trade_date", "adj_factor"), payload)
        opened = conn.execute(
            "SELECT COUNT(*) FROM _stage_adj s JOIN daily d "
            "ON d.symbol = s.symbol AND d.trade_date = s.trade_date"
        ).fetchone()[0]
        conn.execute(
            "UPDATE daily SET adj_factor = s.adj_factor, updated_at = ? "
            "FROM _stage_adj s "
            "WHERE daily.symbol = s.symbol AND daily.trade_date = s.trade_date "
            "AND s.adj_factor IS NOT NULL",
            [datetime.now()],
        )
        conn.execute("DROP TABLE IF EXISTS _stage_adj")
        # Factors whose bar has not been synced yet are dropped: re-running the
        # sync after the bars arrive writes them again.
        return int(opened)


def upsert_daily_basic(rows: Iterable[dict[str, Any]], source: str = "tushare") -> int:
    payload = []
    for item in rows:
        trade_date = _as_date(item.get("trade_date"))
        if not item.get("symbol") or trade_date is None:
            continue
        payload.append(
            tuple(
                [item["symbol"], trade_date]
                + [item.get(col) for col in DAILY_BASIC_COLUMNS[2:]]
                + [item.get("source", source)]
            )
        )
    if not payload:
        return 0
    with session() as conn:
        return _bulk_insert(
            conn,
            "daily_basic",
            DAILY_BASIC_COLUMNS + ("source",),
            payload,
            conflict=("symbol", "trade_date"),
        )


def log_sync(job: str, started_at: datetime, rows: int, status: str, detail: str = "") -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO sync_log VALUES (?, ?, ?, ?, ?, ?)",
            [job, started_at, datetime.now(), int(rows), status, detail[:2000]],
        )


# ---------------------------------------------------------------------- reads
def is_ready() -> bool:
    """True when the panel has at least one symbol and one bar."""
    path = db_path()
    if not path.exists():
        return False
    try:
        with session(read_only=True) as conn:
            row = conn.execute(
                "SELECT (SELECT COUNT(*) FROM symbols), (SELECT COUNT(*) FROM daily)"
            ).fetchone()
    except duckdb.CatalogException:
        return False  # file exists but the schema has not been created yet
    return bool(row) and row[0] > 0 and row[1] > 0


def get_calendar(start: Optional[date] = None, end: Optional[date] = None) -> list[dict[str, Any]]:
    sql = "SELECT cal_date, is_open FROM calendar WHERE 1 = 1"
    params: list[Any] = []
    if start:
        sql += " AND cal_date >= ?"
        params.append(start)
    if end:
        sql += " AND cal_date <= ?"
        params.append(end)
    sql += " ORDER BY cal_date"
    with session(read_only=True) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"cal_date": row[0], "is_open": bool(row[1])} for row in rows]


def get_trade_days(start: Optional[date] = None, end: Optional[date] = None) -> list[date]:
    sql = "SELECT cal_date FROM calendar WHERE is_open"
    params: list[Any] = []
    if start:
        sql += " AND cal_date >= ?"
        params.append(start)
    if end:
        sql += " AND cal_date <= ?"
        params.append(end)
    sql += " ORDER BY cal_date"
    with session(read_only=True) as conn:
        return [row[0] for row in conn.execute(sql, params).fetchall()]


def latest_trade_date() -> Optional[date]:
    with session(read_only=True) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()
    return row[0] if row and row[0] else None


def get_universe(
    asset_type: Optional[str] = None,
    active_only: bool = True,
    exchange: Optional[str] = None,
    board: Optional[str] = None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT symbol, ts_code, name, exchange, asset_type, board, industry, "
        "list_date, delist_date, is_active FROM symbols WHERE 1 = 1"
    )
    params: list[Any] = []
    if active_only:
        sql += " AND is_active"
    if asset_type:
        sql += " AND asset_type = ?"
        params.append(asset_type)
    if exchange:
        sql += " AND exchange = ?"
        params.append(exchange)
    if board:
        sql += " AND board = ?"
        params.append(board)
    sql += " ORDER BY symbol"
    with session(read_only=True) as conn:
        rows = conn.execute(sql, params).fetchall()
    keys = (
        "symbol",
        "ts_code",
        "name",
        "exchange",
        "asset_type",
        "board",
        "industry",
        "list_date",
        "delist_date",
        "is_active",
    )
    return [dict(zip(keys, row)) for row in rows]


def get_symbol(symbol: str) -> Optional[dict[str, Any]]:
    with session(read_only=True) as conn:
        row = conn.execute(
            "SELECT symbol, ts_code, name, exchange, asset_type, board, industry, "
            "list_date, delist_date, is_active FROM symbols WHERE symbol = ?",
            [symbol],
        ).fetchone()
    if not row:
        return None
    keys = (
        "symbol",
        "ts_code",
        "name",
        "exchange",
        "asset_type",
        "board",
        "industry",
        "list_date",
        "delist_date",
        "is_active",
    )
    return dict(zip(keys, row))


def get_daily(
    symbol: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    adjust: str = "none",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Daily bars for one symbol, optionally adjusted to ``qfq`` or ``hfq``."""
    sql = (
        "SELECT trade_date, open, high, low, close, pre_close, volume, amount, "
        "adj_factor FROM daily WHERE symbol = ?"
    )
    params: list[Any] = [symbol]
    if start:
        sql += " AND trade_date >= ?"
        params.append(start)
    if end:
        sql += " AND trade_date <= ?"
        params.append(end)
    sql += " ORDER BY trade_date DESC LIMIT ?"
    params.append(int(limit))
    with session(read_only=True) as conn:
        rows = conn.execute(sql, params).fetchall()

    rows.reverse()
    if adjust not in {"qfq", "hfq"}:
        return [
            {
                "trade_date": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "pre_close": row[5],
                "volume": row[6],
                "amount": row[7],
                "adj_factor": row[8],
            }
            for row in rows
        ]

    factors = [row[8] for row in rows if row[8] is not None]
    anchor = 1.0
    if adjust == "qfq":
        # Anchor on the symbol's *latest* factor, not the last bar in the window:
        # otherwise a request with a smaller `limit`/`end` silently rebases the
        # series and two calls disagree with each other.
        with session(read_only=True) as conn:
            row = conn.execute(
                "SELECT MAX(adj_factor) FROM daily "
                "WHERE symbol = ? AND adj_factor IS NOT NULL",
                [symbol],
            ).fetchone()
        if row and row[0]:
            anchor = float(row[0])
    output = []
    for row in rows:
        factor = row[8]
        ratio = 1.0
        if factor is not None and anchor:
            ratio = (factor / anchor) if adjust == "qfq" else factor
        output.append(
            {
                "trade_date": row[0],
                "open": _scale(row[1], ratio),
                "high": _scale(row[2], ratio),
                "low": _scale(row[3], ratio),
                "close": _scale(row[4], ratio),
                "pre_close": _scale(row[5], ratio),
                "volume": row[6],
                "amount": row[7],
                "adj_factor": factor,
            }
        )
    return output


def _scale(value: Optional[float], ratio: float) -> Optional[float]:
    if value is None:
        return None
    return round(value * ratio, 4)


def get_recent_bars(
    symbols: Sequence[str], limit: int = 8
) -> dict[str, list[dict[str, Any]]]:
    """最近 N 根日线（按标的升序排列），一次查询取多个自选，避免 N 次连接。"""
    codes = [str(symbol) for symbol in dict.fromkeys(symbols) if symbol]
    if not codes:
        return {}
    marks = ",".join("?" for _ in codes)
    sql = (
        "SELECT symbol, trade_date, close, pre_close, adj_factor FROM ("
        "  SELECT symbol, trade_date, close, pre_close, adj_factor, "
        "         row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn "
        f"  FROM daily WHERE symbol IN ({marks})"
        ") WHERE rn <= ? ORDER BY symbol, trade_date"
    )
    with session(read_only=True) as conn:
        rows = conn.execute(sql, [*codes, int(limit)]).fetchall()
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        output.setdefault(row[0], []).append(
            {
                "trade_date": row[1],
                "close": row[2],
                "pre_close": row[3],
                "adj_factor": row[4],
            }
        )
    return output


def get_snapshot(
    trade_date: Optional[date] = None, asset_type: Optional[str] = None, limit: int = 10000
) -> list[dict[str, Any]]:
    """Cross-section: every symbol's bar (plus valuation) on one date."""
    target = trade_date or latest_trade_date()
    if target is None:
        return []
    sql = (
        "SELECT d.symbol, s.name, s.asset_type, s.industry, d.trade_date, d.open, "
        "d.high, d.low, d.close, d.pre_close, d.volume, d.amount, d.adj_factor, "
        "b.pe_ttm, b.pb, b.total_mv, b.circ_mv, b.turnover_rate "
        "FROM daily d LEFT JOIN symbols s ON s.symbol = d.symbol "
        "LEFT JOIN daily_basic b ON b.symbol = d.symbol AND b.trade_date = d.trade_date "
        "WHERE d.trade_date = ?"
    )
    params: list[Any] = [target]
    if asset_type:
        sql += " AND s.asset_type = ?"
        params.append(asset_type)
    sql += " ORDER BY d.amount DESC NULLS LAST LIMIT ?"
    params.append(int(limit))
    with session(read_only=True) as conn:
        rows = conn.execute(sql, params).fetchall()
    keys = (
        "symbol",
        "name",
        "asset_type",
        "industry",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume",
        "amount",
        "adj_factor",
        "pe_ttm",
        "pb",
        "total_mv",
        "circ_mv",
        "turnover_rate",
    )
    return [dict(zip(keys, row)) for row in rows]


def get_coverage() -> dict[str, Any]:
    empty = {
        "ready": False,
        "symbols": 0,
        "bars": 0,
        "first_date": None,
        "last_date": None,
        "with_adj_factor": 0,
        "open_days": 0,
    }
    if not db_path().exists():
        return empty
    try:
        with session(read_only=True) as conn:
            row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
            symbols = int(row[0]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
                "COUNT(adj_factor) FROM daily"
            ).fetchone()
            bars, first_date, last_date, with_factor = row if row else (0, None, None, 0)
            calendar_days = conn.execute(
                "SELECT COUNT(*) FROM calendar WHERE is_open"
            ).fetchone()[0]
    except duckdb.CatalogException:
        return empty  # file exists, schema not created yet
    return {
        "ready": symbols > 0 and bars > 0,
        "symbols": symbols,
        "bars": int(bars or 0),
        "first_date": first_date,
        "last_date": last_date,
        "with_adj_factor": int(with_factor or 0),
        "open_days": int(calendar_days or 0),
    }


def get_sync_log(limit: int = 10) -> list[dict[str, Any]]:
    if not db_path().exists():
        return []
    try:
        with session(read_only=True) as conn:
            rows = conn.execute(
                "SELECT job, started_at, finished_at, rows, status, detail "
                "FROM sync_log ORDER BY started_at DESC LIMIT ?",
                [int(limit)],
            ).fetchall()
    except duckdb.CatalogException:
        return []
    keys = ("job", "started_at", "finished_at", "rows", "status", "detail")
    return [dict(zip(keys, row)) for row in rows]


def last_bar_dates(symbols: Optional[Sequence[str]] = None) -> dict[str, date]:
    """Latest stored bar date per symbol, for incremental sync planning."""
    if not db_path().exists():
        return {}
    try:
        with session(read_only=True) as conn:
            if symbols:
                placeholders = ", ".join("?" for _ in symbols)
                rows = conn.execute(
                    "SELECT symbol, MAX(trade_date) FROM daily "
                    f"WHERE symbol IN ({placeholders}) GROUP BY symbol",
                    list(symbols),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT symbol, MAX(trade_date) FROM daily GROUP BY symbol"
                ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {row[0]: row[1] for row in rows if row[1] is not None}


def bar_spans(
    symbols: Optional[Sequence[str]] = None,
) -> dict[str, tuple[int, Optional[date]]]:
    """Per symbol: (bar count, earliest trade date).

    Incremental sync uses both, because a symbol with 60 snapshot bars is *not*
    deep history — counting rows alone would skip the backfill.
    """
    if not db_path().exists():
        return {}
    try:
        with session(read_only=True) as conn:
            if symbols:
                placeholders = ", ".join("?" for _ in symbols)
                rows = conn.execute(
                    "SELECT symbol, COUNT(*), MIN(trade_date) FROM daily "
                    f"WHERE symbol IN ({placeholders}) GROUP BY symbol",
                    list(symbols),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT symbol, COUNT(*), MIN(trade_date) FROM daily GROUP BY symbol"
                ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {row[0]: (int(row[1]), row[2]) for row in rows}


# ------------------------------------------------------- generic dataset tables
def table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return {row[1]: row[2] for row in rows}


def table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def ensure_dataset_table(name: str, columns: dict[str, str], keys: Sequence[str]) -> None:
    """Create the table, or add any columns a newer field set introduced."""
    with session() as conn:
        if not table_exists(conn, name):
            definitions = ", ".join(f'"{col}" {kind}' for col, kind in columns.items())
            key_list = ", ".join(f'"{key}"' for key in keys)
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" '
                f"({definitions}, PRIMARY KEY ({key_list}))"
            )
            return
        existing = table_columns(conn, name)
        for col, kind in columns.items():
            if col not in existing:
                conn.execute(f'ALTER TABLE "{name}" ADD COLUMN "{col}" {kind}')


def write_dataset(
    name: str,
    columns: Sequence[str],
    keys: Sequence[str],
    rows: Sequence[dict[str, Any]],
) -> int:
    """Upsert rows into a dataset table created by :func:`ensure_dataset_table`."""
    if not rows:
        return 0
    payload = [tuple(row.get(col) for col in columns) for row in rows]
    with session() as conn:
        return _bulk_insert(conn, name, columns, payload, conflict=keys)


def dataset_progress(dataset: str) -> dict[str, tuple[int, bool]]:
    if not db_path().exists():
        return {}
    try:
        with session(read_only=True) as conn:
            rows = conn.execute(
                "SELECT part, rows, done FROM dataset_progress WHERE dataset = ?",
                [dataset],
            ).fetchall()
    except duckdb.CatalogException:
        return {}
    return {row[0]: (int(row[1] or 0), bool(row[2])) for row in rows}


def mark_progress(dataset: str, parts: Sequence[tuple[str, int, bool]]) -> None:
    if not parts:
        return
    stamped = datetime.now()
    payload = [(dataset, part, int(rows), bool(done), stamped) for part, rows, done in parts]
    with session() as conn:
        _bulk_insert(
            conn,
            "dataset_progress",
            ("dataset", "part", "rows", "done", "updated_at"),
            payload,
            conflict=("dataset", "part"),
        )


def apply_adj_factors(trade_date: Optional[date] = None) -> int:
    """Copy ``adj_factor.ts_code`` factors onto ``daily.symbol`` rows.

    Done once per dataset instead of per batch: 2400 batched UPDATEs against a
    multi-million-row table would take longer than the download itself.
    """
    where_date = " AND s.trade_date = ?" if trade_date else ""
    params: list[Any] = [datetime.now()]
    if trade_date:
        params.append(trade_date)
    with session() as conn:
        if not table_exists(conn, "adj_factor"):
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM daily d JOIN adj_factor s "
            "ON d.symbol = split_part(s.ts_code, '.', 1) AND d.trade_date = s.trade_date "
            "WHERE s.adj_factor IS NOT NULL"
            + (" AND s.trade_date = ?" if trade_date else ""),
            [trade_date] if trade_date else [],
        ).fetchone()
        conn.execute(
            "UPDATE daily SET adj_factor = s.adj_factor, updated_at = ? "
            "FROM adj_factor s "
            "WHERE daily.symbol = split_part(s.ts_code, '.', 1) "
            "AND daily.trade_date = s.trade_date "
            "AND s.adj_factor IS NOT NULL" + where_date,
            params,
        )
    return int(row[0]) if row else 0


def clear_progress(dataset: str) -> int:
    with session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM dataset_progress WHERE dataset = ?", [dataset]
        ).fetchone()
        conn.execute("DELETE FROM dataset_progress WHERE dataset = ?", [dataset])
    return int(row[0]) if row else 0


def dataset_stats() -> list[dict[str, Any]]:
    """Row counts per table, for the status command."""
    if not db_path().exists():
        return []
    try:
        with session(read_only=True) as conn:
            rows = conn.execute(
                "SELECT table_name, estimated_size FROM duckdb_tables() "
                "WHERE schema_name = 'main' ORDER BY table_name"
            ).fetchall()
    except duckdb.Error:
        return []
    return [{"table": row[0], "rows": int(row[1] or 0)} for row in rows]
