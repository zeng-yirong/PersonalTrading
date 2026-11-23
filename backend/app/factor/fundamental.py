"""Fundamental / event factors: accruals, earnings surprise (SUE), PEAD, turnover vol.

These are the "slow" factors that the price-volume batches cannot express:

* **accruals** — (EPS − 经营现金流每股) / BPS, scaled by book value. High accruals
  mean earnings are not backed by cash (Sloan 1996).
* **SUE** — standardized unexpected earnings: the year-on-year EPS change divided
  by the standard deviation of the last 8 changes (Bernard & Thomas 1989).
* **PEAD** — cumulative abnormal return since the latest announcement, capped at
  120 days so it stays an *announcement* drift rather than momentum.
* **turnover vol** — 60-day turnover coefficient of variation.

All joins are as-of ``ann_date``: a report becomes usable on the day it was
announced, never on the period end date. ``bt_ext_fund`` is keyed by grid date.
"""

from __future__ import annotations

from app.factor.library import Factor

SOURCE = "fundamental"
TABLE = "bt_ext_fund"

DEFINITIONS: tuple[tuple[str, str, int, str], ...] = (
    ("fd_accrual", "应计利润", -1, "(EPS − 每股经营现金流) / BPS，越高盈余质量越差（Sloan 1996）"),
    ("fd_sue", "盈利惊喜 SUE", 1, "同比 EPS 变化 / 过去 8 次变化的标准差（Bernard & Thomas 1989）"),
    ("fd_turn_vol", "换手波动", -1, "60 日换手率标准差 / 均值"),
)


def factors() -> tuple[Factor, ...]:
    return tuple(
        Factor(
            name=name,
            label=label,
            category="基本面/事件",
            direction=direction,
            note=note,
            source=SOURCE,
        )
        for name, label, direction, note in DEFINITIONS
    )


FACTORS: tuple[Factor, ...] = factors()


def build_sql() -> str:
    return f"""
CREATE OR REPLACE TEMP TABLE {TABLE} AS
WITH fin AS (
    SELECT split_part(ts_code, '.', 1) AS symbol, ann_date, end_date, eps, ocfps, bps
    FROM fina_indicator_vip
    WHERE ann_date IS NOT NULL AND eps IS NOT NULL
),
ordered AS (
    SELECT *, lag(eps, 4) OVER (PARTITION BY symbol ORDER BY end_date) AS eps4
    FROM fin
),
surprise AS (
    SELECT symbol, ann_date, end_date,
           eps - eps4 AS surprise,
           (eps - ocfps) / NULLIF(bps, 0) AS accrual
    FROM ordered WHERE eps4 IS NOT NULL
),
sue AS (
    SELECT symbol, ann_date,
           surprise / NULLIF(
               stddev_samp(surprise) OVER (
                   PARTITION BY symbol ORDER BY end_date
                   ROWS BETWEEN 7 PRECEDING AND CURRENT ROW), 0) AS sue,
           accrual
    FROM surprise
),
grid AS (
    SELECT bd.d AS trade_date, s.symbol
    FROM bt_dates bd CROSS JOIN symbols s
    WHERE s.asset_type = 'stock'
),
latest AS (
    SELECT g.trade_date, g.symbol, s.sue, s.accrual, s.ann_date AS ann
    FROM grid g
    ASOF LEFT JOIN sue s ON s.symbol = g.symbol AND g.trade_date >= s.ann_date
),
px AS (
    SELECT l.trade_date, l.symbol, l.sue, l.accrual, l.ann,
           d.close * COALESCE(d.adj_factor, 1) AS c_now,
           da.close * COALESCE(da.adj_factor, 1) AS c_ann,
           m0.close AS m_now,
           m1.close AS m_ann
    FROM latest l
    LEFT JOIN daily d  ON d.symbol = l.symbol AND d.trade_date = l.trade_date
    LEFT JOIN daily da ON da.symbol = l.symbol AND da.trade_date = l.ann
    LEFT JOIN index_daily m0 ON m0.ts_code = '000300.SH' AND m0.trade_date = l.trade_date
    LEFT JOIN index_daily m1 ON m1.ts_code = '000300.SH' AND m1.trade_date = l.ann
),
turnover AS (
    SELECT symbol, trade_date,
           stddev_samp(turnover_rate) OVER w60
               / NULLIF(avg(turnover_rate) OVER w60, 0) AS turn_vol
    FROM daily_basic
    WHERE trade_date <= (SELECT MAX(d) FROM bt_dates)
    WINDOW w60 AS (PARTITION BY symbol ORDER BY trade_date
                   ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
)
SELECT p.trade_date, p.symbol,
    p.accrual AS "fd_accrual",
    p.sue     AS "fd_sue",
    t.turn_vol AS "fd_turn_vol"
FROM px p
LEFT JOIN turnover t ON t.symbol = p.symbol AND t.trade_date = p.trade_date
"""
