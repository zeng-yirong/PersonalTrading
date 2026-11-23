"""PEAD once it is promoted: the SQL both builders share.

``fd_pead`` is the only fundamental factor that passed all three gates, so it now
lives in the panel (``bt_feat`` and the screener snapshot) rather than in a
separate temp table. Both builders need exactly the same join — latest
announcement per symbol, the first tradable bar on/after that date, the market over
the same window — so it is written once here and parameterised by the dates to
produce.

Announcements often land on a weekend (e.g. 2026-10-31 is a Saturday), so the
entry price comes from the **next open trading day**, not from the ann_date
itself; otherwise the factor silently drops every weekend report.
"""

from __future__ import annotations

from datetime import date

MAX_DRIFT_DAYS = 120


def cte(dates_sql: str) -> str:
    """A ``pead(trade_date, symbol, fd_pead)`` CTE.

    ``dates_sql`` must produce one column ``trade_date``; the caller decides
    whether that is the backtest grid or a single snapshot date.
    """
    return f"""
pead AS (
    WITH dates AS ({dates_sql}),
    ann AS (
        SELECT split_part(ts_code, '.', 1) AS symbol, ann_date
        FROM fina_indicator_vip
        WHERE ann_date IS NOT NULL
    ),
    entry AS (
        SELECT a.symbol, a.ann_date, MIN(d.trade_date) AS entry_date
        FROM ann a
        JOIN daily d ON d.symbol = a.symbol
            AND d.trade_date BETWEEN a.ann_date AND a.ann_date + 10
        GROUP BY 1, 2
    ),
    grid AS (
        SELECT dates.trade_date, s.symbol
        FROM dates CROSS JOIN symbols s
        WHERE s.asset_type = 'stock'
    ),
    latest AS (
        SELECT g.trade_date, g.symbol, e.ann_date, e.entry_date
        FROM grid g
        ASOF LEFT JOIN entry e ON e.symbol = g.symbol AND g.trade_date >= e.ann_date
    ),
    px AS (
        SELECT l.trade_date, l.symbol, l.ann_date, l.entry_date,
               d.close  * COALESCE(d.adj_factor, 1)  AS c_now,
               de.close * COALESCE(de.adj_factor, 1) AS c_entry,
               m0.close AS m_now,
               m1.close AS m_entry
        FROM latest l
        LEFT JOIN daily d  ON d.symbol = l.symbol AND d.trade_date = l.trade_date
        LEFT JOIN daily de ON de.symbol = l.symbol AND de.trade_date = l.entry_date
        LEFT JOIN index_daily m0 ON m0.ts_code = '000300.SH' AND m0.trade_date = l.trade_date
        LEFT JOIN index_daily m1 ON m1.ts_code = '000300.SH' AND m1.trade_date = l.entry_date
    )
    SELECT trade_date, symbol,
        CASE
            WHEN entry_date IS NULL OR c_entry IS NULL OR m_entry IS NULL OR c_now IS NULL
                THEN NULL
            WHEN trade_date - entry_date > {MAX_DRIFT_DAYS} THEN NULL
            ELSE ((c_now / c_entry - 1) - (m_now / m_entry - 1)) * 100
        END AS fd_pead
    FROM px
)
"""


def dates_for_backtest() -> str:
    return "SELECT d AS trade_date FROM bt_dates"


def dates_for_snapshot(latest: date) -> str:
    return f"SELECT DATE '{latest.isoformat()}' AS trade_date"
