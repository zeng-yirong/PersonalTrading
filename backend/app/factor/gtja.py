"""GTJA Alpha191 formulas that need only daily OHLCV (+ VWAP).

Source: 国泰君安《数量化专题之九十三：基于短周期价量特征的多因子选股体系》(2017),
formulas as listed in the public reference table (see README for the link).

Two deliberate simplifications, both stated rather than hidden:

* **RANK is dropped.** The report wraps many factors in a cross-sectional
  ``RANK``; IC is a Spearman rank correlation, so ranking first and correlating
  later is the same number. Dropping it keeps the factor computable per symbol.
  (Formulas that *multiply* two ranks are not in this batch.)
* **SMA(...,n,m) is not implemented.** It is a recursive average; only factors
  that use plain windows are included here. TSRANK-based factors are also out
  for now — they need a second pass we have not built.

``VWAP`` is derived as ``amount / volume`` (both in raw units, same day).
"""

from __future__ import annotations

from app.factor.library import Factor

SOURCE = "gtja"
TABLE = "bt_ext_gtja"

# (name, label, direction, note) — note carries the formula.
DEFINITIONS: tuple[tuple[str, str, int, str], ...] = (
    ("gtja2", "实体重心变化", 0, "-Δ(((C−L)−(H−C))/(H−L),1)"),
    ("gtja3", "上下影净额6日", 0, "SUM(C−区间调整,6)：涨则减下影，跌则减上影"),
    ("gtja4", "均线状态切换", 0, "MA8±STD8 与 MA2 的区间判断，配合量比给 ±1"),
    ("gtja8", "均价重心变化4日", 0, "-Δ(0.2×(H+L)/2 + 0.8×VWAP,4)"),
    ("gtja11", "影线量能6日", 0, "SUM(((C−L)−(H−C))/(H−L)×V,6)"),
    ("gtja13", "几何均价偏离", 0, "√(H×L) − VWAP"),
    ("gtja14", "5日价差", 0, "C − DELAY(C,5)"),
    ("gtja15", "开盘缺口", 0, "OPEN / DELAY(C,1) − 1"),
    ("gtja17", "开收跳动平方和", 0, "-[(O−O₋₁)² + (C−C₋₁)²]"),
    ("gtja19", "5日最大绝对波动", 0, "-MAX(MAX(|ΔC₁|,|ΔC₂|),5)"),
    ("gtja20", "5日最小绝对波动", 0, "-MIN(MIN(|ΔC₁|,|ΔC₂|),5)"),
    ("gtja24", "实体占比日变化", 0, "-Δ((C−O)/(H−L),1)"),
    ("gtja26", "实体占比3日变化", 0, "-Δ((C−O)/(H−L),3)"),
    ("gtja33", "放量1日", 0, "-MAX(ΔV,1)"),
    ("gtja35", "放量2日", 0, "-MAX(ΔV,2)"),
    ("gtja39", "放量4日", 0, "-MAX(ΔV,4)"),
    ("gtja41", "放量5日", 0, "-MAX(ΔV,5)"),
    ("gtja94", "阳线实体", 0, "-MAX(C−O,0)"),
    ("gtja95", "阴线实体", 0, "-MIN(C−O,0)"),
    ("gtja96", "振幅", 0, "-MAX(H−L,0)"),
    ("gtja118", "实体扩张", 0, "-Δ(MAX(C−O,0),1)"),
    ("gtja120", "振幅扩张", 0, "-Δ(MAX(H−L,0),1)"),
    ("gtja150", "5日上涨天数", 0, "-COUNT(C>DELAY(C,1),5)"),
    ("gtja151", "5日下跌天数", 0, "-COUNT(C<DELAY(C,1),5)"),
    ("gtja171", "下影溢价5次方", 0, "-((C−L)/(H−C))×(O/C)⁵"),
    ("gtja173", "下影溢价", 0, "-((C−L)/(H−C))×(O/C)"),
    ("gtja174", "上影溢价5次方", 0, "-((H−C)/(C−L))×(C/O)⁵"),
    ("gtja176", "上影溢价", 0, "-((H−C)/(C−L))×(C/O)"),
    ("gtja181", "影线比最大值", 0, "-MAX((C−L)/(H−C), (H−C)/(C−L))"),
    ("gtja182", "影线比最小值", 0, "-MIN((C−L)/(H−C), (H−C)/(C−L))"),
    ("gtja187", "影线比变化", 0, "-Δ(MAX((C−L)/(H−C),(H−C)/(C−L)),1)"),
)


def factors() -> tuple[Factor, ...]:
    return tuple(
        Factor(
            name=name,
            label=label,
            category="GTJA191",
            direction=direction,
            note=note,
            source=SOURCE,
        )
        for name, label, direction, note in DEFINITIONS
    )


FACTORS: tuple[Factor, ...] = factors()


def _frame(window: int) -> str:
    return f"ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW"


def build_sql() -> str:
    return f"""
CREATE OR REPLACE TEMP TABLE {TABLE} AS
WITH base AS (
    SELECT symbol, trade_date, open, high, low, close, volume,
           amount / NULLIF(volume, 0) AS vwap,
           lag(close, 1)  OVER (PARTITION BY symbol ORDER BY trade_date) AS c1,
           lag(open, 1)   OVER (PARTITION BY symbol ORDER BY trade_date) AS o1,
           lag(close, 2)  OVER (PARTITION BY symbol ORDER BY trade_date) AS c2,
           lag(close, 3)  OVER (PARTITION BY symbol ORDER BY trade_date) AS c3,
           lag(close, 5)  OVER (PARTITION BY symbol ORDER BY trade_date) AS c5,
           lag(high, 1)   OVER (PARTITION BY symbol ORDER BY trade_date) AS h1,
           lag(low, 1)    OVER (PARTITION BY symbol ORDER BY trade_date) AS l1,
           lag(volume, 1) OVER (PARTITION BY symbol ORDER BY trade_date) AS v1,
           lag(volume, 2) OVER (PARTITION BY symbol ORDER BY trade_date) AS v2,
           lag(volume, 3) OVER (PARTITION BY symbol ORDER BY trade_date) AS v3,
           lag(volume, 4) OVER (PARTITION BY symbol ORDER BY trade_date) AS v4,
           lag(volume, 5) OVER (PARTITION BY symbol ORDER BY trade_date) AS v5
    FROM daily
    WHERE trade_date >= (SELECT MIN(d) FROM bt_dates) - INTERVAL 120 DAY
      AND trade_date <= (SELECT MAX(d) FROM bt_dates)
),
row_terms AS (
    SELECT *,
           (close - low - (high - close)) / NULLIF(high - low, 0) AS body_ratio,
           (close - open) / NULLIF(high - low, 0)                 AS co_ratio,
           CASE
               WHEN close = c1 THEN 0
               WHEN close > c1 THEN close - least(low, c1)
               ELSE close - greatest(high, c1)
           END AS shadow_term,
           ((close - low) - (high - close)) / NULLIF(high - low, 0) * volume AS shadow_volume,
           greatest(abs(close - c1), abs(close - c2)) AS abs_move_max,
           least(abs(close - c1), abs(close - c2))    AS abs_move_min,
           greatest(volume - v1, 0) AS up_vol_1,
           greatest(volume - v2, 0) AS up_vol_2,
           greatest(volume - v4, 0) AS up_vol_4,
           greatest(volume - v5, 0) AS up_vol_5,
           greatest(close - open, 0) AS body_up,
           least(close - open, 0)    AS body_down,
           greatest(high - low, 0)   AS range_up,
           (close - low) / NULLIF(high - close, 0) AS lower_shadow_ratio,
           (high - close) / NULLIF(close - low, 0) AS upper_shadow_ratio
    FROM base
)
SELECT symbol, trade_date,
    -1 * (body_ratio - lag(body_ratio, 1) OVER w)                    AS "gtja2",
    sum(shadow_term) OVER w6                                         AS "gtja3",
    CASE
        WHEN (avg(close) OVER w8 + stddev_samp(close) OVER w8) < (avg(close) OVER w2) THEN -1
        WHEN (avg(close) OVER w2) < (avg(close) OVER w8 - stddev_samp(close) OVER w8) THEN 1
        WHEN volume >= avg(volume) OVER w20 THEN 1
        ELSE -1
    END                                                              AS "gtja4",
    -1 * (0.2 * (high + low) / 2 + 0.8 * vwap
          - lag(0.2 * (high + low) / 2 + 0.8 * vwap, 4) OVER w)      AS "gtja8",
    sum(shadow_volume) OVER w6                                       AS "gtja11",
    sqrt(high * low) - vwap                                          AS "gtja13",
    close - c5                                                       AS "gtja14",
    open / NULLIF(c1, 0) - 1                                         AS "gtja15",
    -1 * ((open - o1) * (open - o1) + (close - c1) * (close - c1))   AS "gtja17",
    -1 * max(abs_move_max) OVER w5                                   AS "gtja19",
    -1 * min(abs_move_min) OVER w5                                   AS "gtja20",
    -1 * (co_ratio - lag(co_ratio, 1) OVER w)                        AS "gtja24",
    -1 * (co_ratio - lag(co_ratio, 3) OVER w)                        AS "gtja26",
    -1 * up_vol_1                                                    AS "gtja33",
    -1 * up_vol_2                                                    AS "gtja35",
    -1 * up_vol_4                                                    AS "gtja39",
    -1 * up_vol_5                                                    AS "gtja41",
    -1 * body_up                                                      AS "gtja94",
    -1 * body_down                                                   AS "gtja95",
    -1 * range_up                                                    AS "gtja96",
    -1 * (body_up - lag(body_up, 1) OVER w)                          AS "gtja118",
    -1 * (range_up - lag(range_up, 1) OVER w)                        AS "gtja120",
    -1 * sum(CASE WHEN close > c1 THEN 1 ELSE 0 END) OVER w5         AS "gtja150",
    -1 * sum(CASE WHEN close < c1 THEN 1 ELSE 0 END) OVER w5         AS "gtja151",
    -1 * lower_shadow_ratio * power(open / NULLIF(close, 0), 5)      AS "gtja171",
    -1 * lower_shadow_ratio * (open / NULLIF(close, 0))              AS "gtja173",
    -1 * upper_shadow_ratio * power(close / NULLIF(open, 0), 5)      AS "gtja174",
    -1 * upper_shadow_ratio * (close / NULLIF(open, 0))              AS "gtja176",
    -1 * greatest(lower_shadow_ratio, upper_shadow_ratio)            AS "gtja181",
    -1 * least(lower_shadow_ratio, upper_shadow_ratio)               AS "gtja182",
    -1 * (greatest(lower_shadow_ratio, upper_shadow_ratio)
          - lag(greatest(lower_shadow_ratio, upper_shadow_ratio), 1) OVER w) AS "gtja187"
FROM row_terms
WINDOW w   AS (PARTITION BY symbol ORDER BY trade_date),
       w2  AS (PARTITION BY symbol ORDER BY trade_date {_frame(2)}),
       w5  AS (PARTITION BY symbol ORDER BY trade_date {_frame(5)}),
       w6  AS (PARTITION BY symbol ORDER BY trade_date {_frame(6)}),
       w8  AS (PARTITION BY symbol ORDER BY trade_date {_frame(8)}),
       w20 AS (PARTITION BY symbol ORDER BY trade_date {_frame(20)})
"""
