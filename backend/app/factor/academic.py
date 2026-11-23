"""Academic factors: MAX, skewness, beta, idiosyncratic vol, Amihud, 12-1 momentum.

These come from the published factor literature rather than from a vendor's
formula list, so each definition is stated in the note. They are intentionally
different in kind from the Alpha158 batch (which is price-volume geometry): if a
factor here survives the three gates, it is adding a *different* axis.

Construction is in ``bt_ext_acad``, keyed by (trade_date, symbol), on the same
grid dates as ``bt_feat``; the market leg is 沪深300.
"""

from __future__ import annotations

from app.factor.library import Factor

SOURCE = "academic"
TABLE = "bt_ext_acad"

# (name, label, direction, note, expression template)
# ``w`` is substituted with the window clause; ``mkt`` is the market return column.
DEFINITIONS: tuple[tuple[str, str, int, str], ...] = (
    ("ac_max_20", "20日最大单日涨幅", -1, "MAX 效应：近期最大单日涨幅（Bali 2011）"),
    ("ac_max_60", "60日最大单日涨幅", -1, "MAX 效应，60 日窗口"),
    ("ac_skew_60", "60日收益偏度", -1, "日收益偏度，右偏（彩票型）预期收益更低"),
    ("ac_beta_60", "60日市场Beta", 0, "对沪深300 的回归斜率（日收益）"),
    ("ac_ivol_60", "60日特质波动", -1, "总波动 × √(1 − R²) 的近似特质波动率"),
    ("ac_amihud_20", "20日Amihud非流动性", -1, "平均 |收益| / 成交额 × 1e9（Amihud 2002）"),
    ("ac_turn_trend", "换手率20/120", -1, "20 日平均换手 / 120 日平均换手，短期拥挤度"),
    ("ac_mom_12_1", "12-1动量", 1, "t−20 到 t−250 的涨幅（剔除最近一个月）"),
    ("ac_downvol_60", "60日下行波动", -1, "仅负收益的标准差（下行风险）"),
)


def _expr(name: str) -> str:
    """Expression bodies; ``over(w)`` frames are inlined by the builder."""
    mapping = {
        "ac_max_20": "max(ret) OVER (w20)",
        "ac_max_60": "max(ret) OVER (w60)",
        "ac_skew_60": "skewness(ret) OVER (w60)",
        "ac_beta_60": "regr_slope(ret, mkt_ret) OVER (w60)",
        "ac_ivol_60": (
            "stddev_samp(ret) OVER (w60)"
            " * sqrt(greatest(1 - regr_r2(ret, mkt_ret) OVER (w60), 0))"
        ),
        "ac_amihud_20": "avg(abs(ret) / NULLIF(amount, 0)) OVER (w20) * 1e9",
        "ac_turn_trend": (
            "avg(turnover_rate) OVER (w20) / NULLIF(avg(turnover_rate) OVER (w120), 0)"
        ),
        "ac_mom_12_1": (
            "lag(c_adj, 20) OVER (PARTITION BY symbol ORDER BY trade_date)"
            " / NULLIF(lag(c_adj, 250) OVER (PARTITION BY symbol ORDER BY trade_date), 0) - 1"
        ),
        "ac_downvol_60": (
            "sqrt(avg(CASE WHEN ret < 0 THEN ret * ret END) OVER (w60))"
        ),
    }
    return mapping[name]


FRAMES = {
    "w20": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW",
    "w60": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW",
    "w120": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW",
}


def factors() -> tuple[Factor, ...]:
    return tuple(
        Factor(
            name=name,
            label=label,
            category="学术因子",
            direction=direction,
            note=note,
            source=SOURCE,
        )
        for name, label, direction, note in DEFINITIONS
    )


FACTORS: tuple[Factor, ...] = factors()


def select_columns(indent: str = "        ") -> str:
    parts = []
    for name, _, _, _ in DEFINITIONS:
        expression = _expr(name)
        for window, definition in FRAMES.items():
            expression = expression.replace(f"OVER ({window})", f"OVER ({definition})")
        parts.append(f'{expression} AS "{name}"')
    return (",\n" + indent).join(parts)


def build_sql() -> str:
    columns = select_columns()
    return f"""
CREATE OR REPLACE TEMP TABLE {TABLE} AS
WITH mkt AS (
    SELECT trade_date,
           close / NULLIF(lag(close) OVER (ORDER BY trade_date), 0) - 1 AS mkt_ret
    FROM index_daily
    WHERE ts_code = '000300.SH'
      AND trade_date >= (SELECT MIN(d) FROM bt_dates) - INTERVAL 400 DAY
      AND trade_date <= (SELECT MAX(d) FROM bt_dates)
),
bars AS (
    SELECT d.symbol, d.trade_date,
           d.close * COALESCE(d.adj_factor, 1) AS c_adj,
           d.amount,
           b.turnover_rate,
           m.mkt_ret,
           d.close * COALESCE(d.adj_factor, 1)
               / NULLIF(lag(d.close * COALESCE(d.adj_factor, 1))
                        OVER (PARTITION BY d.symbol ORDER BY d.trade_date), 0) - 1 AS ret
    FROM daily d
    LEFT JOIN daily_basic b ON b.symbol = d.symbol AND b.trade_date = d.trade_date
    LEFT JOIN mkt m ON m.trade_date = d.trade_date
    WHERE d.trade_date >= (SELECT MIN(d) FROM bt_dates) - INTERVAL 400 DAY
      AND d.trade_date <= (SELECT MAX(d) FROM bt_dates)
),
scored AS (
    SELECT symbol, trade_date,
        {columns}
    FROM bars
)
SELECT s.* FROM scored s
JOIN bt_dates bd ON bd.d = s.trade_date
"""
