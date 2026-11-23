"""Historical cross-sections for the backtest: the same fields, evaluated as of.

The screener answers "what matches *today*". A backtest has to answer the same
question on every rebalance date of the past, and it has to do so without seeing
the future:

* prices and technical fields use **adjusted** prices (``close * adj_factor``) so
  a dividend or split is a return, not a fake gap;
* fundamentals are joined on ``ann_date`` (the day the market learned the number),
  never on ``end_date`` (the period the number describes);
* event counts (涨停 / 龙虎榜 / 北向) only look back, never forward;
* ``pe_pct_3y`` is *not* produced: a 3-year rolling percentile per row costs far
  more than the rest of the table and is rejected up-front rather than faked.

One materialised table holds every (rebalance date, symbol) row; selecting the
matching names is then a single filtered scan with a row_number per date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from app.factor import pead as pead_module
from app.factor import promoted

FEATURE_TABLE = "bt_feat"
LOOKBACK_DAYS = 420  # enough calendar days for a 250-bar window

# The subset of the screener catalogue this module cannot reproduce historically.
# The key is the screener field name; the value says what it costs to add.
UNSUPPORTED: dict[str, str] = {
    "pe_pct_3y": "三年 PE 分位需要逐行滚动三年窗口，回测暂不计算（会慢一个数量级）",
}


def _sql(lookback: date, end: date) -> str:
    """Feature table SQL. ``lookback`` bounds the window function input."""
    lo = lookback.isoformat()
    hi = end.isoformat()
    promoted_columns = promoted.select_columns(indent="        ")
    pead_cte = pead_module.cte(pead_module.dates_for_backtest())
    return f"""
CREATE OR REPLACE TEMP TABLE {FEATURE_TABLE} AS
WITH bars AS (
    SELECT symbol, trade_date, open, high, low, close, amount, volume, adj_factor,
           close * COALESCE(adj_factor, 1) AS c_adj,
           open  * COALESCE(adj_factor, 1) AS o_adj,
           high  * COALESCE(adj_factor, 1) AS h_adj,
           low   * COALESCE(adj_factor, 1) AS l_adj,
           close * COALESCE(adj_factor, 1)
               / NULLIF(lag(close * COALESCE(adj_factor, 1))
                        OVER (PARTITION BY symbol ORDER BY trade_date), 0) - 1 AS ret,
           row_number() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn
    FROM daily
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
win AS (
    SELECT *,
        LAG(c_adj, 1) OVER w  AS c1,
        LAG(c_adj, 5) OVER w  AS c5,
        LAG(c_adj, 20) OVER w AS c20,
        LAG(c_adj, 60) OVER w AS c60,
        AVG(c_adj) OVER w20   AS ma20,
        AVG(c_adj) OVER w60   AS ma60,
        MAX(h_adj) OVER w250  AS hi52,
        AVG(amount) OVER w20  AS avg_amt20,
        {promoted_columns}
    FROM bars
    WINDOW w    AS (PARTITION BY symbol ORDER BY trade_date),
           w5   AS (PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
           w20  AS (PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
           w60  AS (PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
           w250 AS (PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 249 PRECEDING AND CURRENT ROW)
),
xref AS (
    SELECT w.*, s.ts_code, s.name, s.industry, s.board, s.asset_type
    FROM win w
    JOIN bt_dates bd ON bd.d = w.trade_date
    JOIN symbols s USING (symbol)
),
basic AS (
    SELECT symbol, trade_date, pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv,
           turnover_rate, volume_ratio
    FROM daily_basic
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
factor AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, trade_date,
           rsi_12 AS rsi12, macd AS macd_hist, boll_upper, boll_lower,
           kdj_k, kdj_d, kdj_j, cci
    FROM stk_factor
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
flow AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, trade_date,
           net_mf_amount AS mf_net_1d,
           buy_elg_amount - sell_elg_amount AS elg_net_1d,
           SUM(net_mf_amount) OVER (
               PARTITION BY ts_code ORDER BY trade_date
               ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS mf_net_20d
    FROM moneyflow
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
margin AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, trade_date, rzye,
           LAG(rzye, 20) OVER (PARTITION BY ts_code ORDER BY trade_date) AS rzye_20
    FROM margin_detail
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
chips AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, trade_date, winner_rate
    FROM cyq_perf
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
),
limits AS (
    SELECT c.symbol, c.trade_date,
           COUNT(*) FILTER (WHERE e."limit" = 'U') AS limit_up_90d,
           COUNT(*) FILTER (WHERE e."limit" = 'U'
                            AND e.trade_date >= c.trade_date - 7) AS limit_up_5d
    FROM xref c
    JOIN limit_list_d e ON e.ts_code = c.ts_code
        AND e.trade_date BETWEEN c.trade_date - 90 AND c.trade_date
    GROUP BY 1, 2
),
lhb AS (
    SELECT c.symbol, c.trade_date, COUNT(*) AS lhb_90d
    FROM xref c
    JOIN top_list e ON e.ts_code = c.ts_code
        AND e.trade_date BETWEEN c.trade_date - 90 AND c.trade_date
    GROUP BY 1, 2
),
north AS (
    SELECT c.symbol, c.trade_date, COUNT(*) AS north_90d
    FROM xref c
    JOIN hsgt_top10 e ON e.ts_code = c.ts_code
        AND e.trade_date BETWEEN c.trade_date - 90 AND c.trade_date
    GROUP BY 1, 2
),
hot AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, trade_date, MIN(rank) AS hot_rank
    FROM ths_hot
    WHERE trade_date BETWEEN DATE '{lo}' AND DATE '{hi}'
    GROUP BY 1, 2
),
fin AS (
    SELECT ts_code, ann_date, roe, grossprofit_margin AS gross_margin,
           netprofit_margin AS net_margin, debt_to_assets AS debt_ratio,
           or_yoy AS revenue_yoy, netprofit_yoy AS profit_yoy
    FROM (
        SELECT *, row_number() OVER (
                   PARTITION BY ts_code, ann_date ORDER BY end_date DESC) AS rn
        FROM fina_indicator_vip
        WHERE ann_date IS NOT NULL
    ) WHERE rn = 1
),
{pead_cte}
SELECT
    c.trade_date, c.symbol, c.ts_code, c.name, c.industry, c.board, c.asset_type,
    c.close, c.open, c.o_adj, c.adj_factor, c.volume,
    ROUND((c.c_adj / NULLIF(c.c1, 0) - 1) * 100, 4)  AS pct_chg,
    ROUND((c.c_adj / NULLIF(c.c5, 0) - 1) * 100, 4)  AS ret5,
    ROUND((c.c_adj / NULLIF(c.c20, 0) - 1) * 100, 4) AS ret20,
    ROUND((c.c_adj / NULLIF(c.c60, 0) - 1) * 100, 4) AS ret60,
    c.amount, c.avg_amt20 AS avg_amount_20d,
    b.turnover_rate, b.volume_ratio,
    ROUND((c.c_adj / NULLIF(c.ma20, 0) - 1) * 100, 4) AS gap_ma20,
    ROUND((c.c_adj / NULLIF(c.ma60, 0) - 1) * 100, 4) AS gap_ma60,
    ROUND((c.c_adj / NULLIF(c.hi52, 0) - 1) * 100, 4) AS drawdown_52w,
    f.rsi12, f.macd_hist,
    CASE WHEN f.boll_upper > f.boll_lower
         THEN (c.close - f.boll_lower) / (f.boll_upper - f.boll_lower) END AS boll_pos,
    f.kdj_k, f.kdj_d, f.kdj_j, f.cci,
    c.a_vstd_20, c.a_vstd_5, c.a_qtl20_60, c.a_corr_20, c.a_corr_5,
    c.a_mingap_60, c.a_kbar_kup,
    pe.fd_pead,
    b.pe_ttm, b.pb, b.ps_ttm, b.dv_ttm, b.total_mv, b.circ_mv,
    NULL::DOUBLE AS pe_pct_3y,
    fin.roe, fin.gross_margin, fin.net_margin, fin.debt_ratio,
    fin.revenue_yoy, fin.profit_yoy,
    fl.mf_net_1d, fl.mf_net_20d, fl.elg_net_1d,
    m.rzye AS margin_bal,
    ROUND((m.rzye / NULLIF(m.rzye_20, 0) - 1) * 100, 4) AS margin_chg_20d,
    ch.winner_rate AS chip_winner,
    COALESCE(lim.limit_up_90d, 0) AS limit_up_90d,
    COALESCE(lim.limit_up_5d, 0)  AS limit_up_5d,
    COALESCE(lhb.lhb_90d, 0)      AS lhb_90d,
    COALESCE(north.north_90d, 0)  AS north_90d,
    hot.hot_rank
FROM xref c
LEFT JOIN basic   b   ON b.symbol = c.symbol AND b.trade_date = c.trade_date
LEFT JOIN factor  f   ON f.symbol = c.symbol AND f.trade_date = c.trade_date
LEFT JOIN flow    fl  ON fl.symbol = c.symbol AND fl.trade_date = c.trade_date
LEFT JOIN margin  m   ON m.symbol = c.symbol AND m.trade_date = c.trade_date
LEFT JOIN chips   ch  ON ch.symbol = c.symbol AND ch.trade_date = c.trade_date
LEFT JOIN limits  lim ON lim.symbol = c.symbol AND lim.trade_date = c.trade_date
LEFT JOIN lhb     lhb ON lhb.symbol = c.symbol AND lhb.trade_date = c.trade_date
LEFT JOIN north   north ON north.symbol = c.symbol AND north.trade_date = c.trade_date
LEFT JOIN hot     hot ON hot.symbol = c.symbol AND hot.trade_date = c.trade_date
ASOF LEFT JOIN fin ON fin.ts_code = c.ts_code AND c.trade_date >= fin.ann_date
LEFT JOIN pead pe ON pe.symbol = c.symbol AND pe.trade_date = c.trade_date
"""


def lookback_start(start: date) -> date:
    return start - timedelta(days=LOOKBACK_DAYS)


def build(conn, dates: Iterable[date], end: date) -> None:
    """Materialise ``bt_feat`` inside the caller's connection."""
    ordered = sorted({d for d in dates})
    if not ordered:
        raise ValueError("没有可用的调仓日")
    lo = lookback_start(ordered[0])
    conn.execute("CREATE OR REPLACE TEMP TABLE bt_dates (d DATE)")
    conn.execute("INSERT INTO bt_dates VALUES " + ", ".join("(?)" for _ in ordered),
                 list(ordered))
    conn.execute(_sql(lo, end))


def coverage(conn) -> dict[str, Any]:
    """How much of each block is actually populated across the cross-sections."""
    row = conn.execute(f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT trade_date) AS dates,
               COUNT(pe_ttm) AS pe,
               COUNT(roe) AS fin,
               COUNT(mf_net_20d) AS flow,
               COUNT(chip_winner) AS chip,
               COUNT(margin_bal) AS margin,
               COUNT(rsi12) AS tech,
               COUNT(hot_rank) AS hot
        FROM {FEATURE_TABLE}
    """).fetchone()
    rows = int(row[0] or 0)
    names = ("pe", "fin", "flow", "chip", "margin", "tech", "hot")
    shares = {name: (round(int(value or 0) / rows, 3) if rows else 0.0)
              for name, value in zip(names, row[2:])}
    return {"rows": rows, "dates": int(row[1] or 0), "shares": shares}


def select_picks(conn, query, top_n: int) -> dict[date, list[dict[str, Any]]]:
    """Apply the screener query to every date, keeping the top ``top_n`` names.

    Ranking happens inside SQL (one row_number per trade_date), so a 5-year,
    20-day schedule costs one scan instead of one query per date.
    """
    where, params = query.where()
    order = query.order_columns()
    sql = f"""
        SELECT * FROM (
            SELECT *, row_number() OVER (PARTITION BY trade_date ORDER BY {order}) AS _rank
            FROM {FEATURE_TABLE}
            WHERE {where}
        ) WHERE _rank <= ?
        ORDER BY trade_date, _rank
    """
    rows = conn.execute(sql, params + [int(top_n)]).fetchall()
    columns = [item[0] for item in conn.description]
    picks: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(zip(columns, row))
        picks.setdefault(item["trade_date"], []).append(item)
    return picks
