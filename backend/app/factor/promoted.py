"""Factors promoted from external sources into the panel itself.

These passed the three gates (raw IC, out-of-sample, incremental IC) in
``alpha158.py``. Promotion means the formula now lives in ``bt_feat`` (and in the
screener snapshot), so the same column can be filtered, backtested and evaluated
— research and trading read one number, not two.

The SQL below is the single source of truth for both builders: they only have to
provide a relation with the columns ``c_adj, o_adj, h_adj, l_adj, volume, ret, rn,
symbol, trade_date``.
"""

from __future__ import annotations

from app.factor.library import Factor

# name -> (label, sql template). Placeholders are window frames.
EXPRESSIONS: dict[str, str] = {
    "a_vstd_20": "stddev_samp(volume) OVER (w20) / NULLIF(avg(volume) OVER (w20), 0)",
    "a_vstd_5": "stddev_samp(volume) OVER (w5) / NULLIF(avg(volume) OVER (w5), 0)",
    # 百分数字段与面板口径一致（ret20 等也是 ×100），否则筛选器里 0.1 会被读成 0.1%
    "a_qtl20_60": "(quantile_cont(c_adj, 0.2) OVER (w60) / NULLIF(c_adj, 0) - 1) * 100",
    "a_corr_20": "corr(c_adj, ln(volume + 1)) OVER (w20)",
    "a_corr_5": "corr(c_adj, ln(volume + 1)) OVER (w5)",
    "a_mingap_60": "(c_adj / NULLIF(min(l_adj) OVER (w60), 0) - 1) * 100",
    "a_kbar_kup": "(h_adj - o_adj) / NULLIF(o_adj, 0) * 100",
}

# The windows the expressions above refer to; both builders must define them.
WINDOWS: dict[str, str] = {
    "w5": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW",
    "w20": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW",
    "w60": "PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW",
}

# Labels/types for the screener catalogue: (name, label, kind, unit, description).
CATALOGUE: tuple[tuple[str, str, str, str, str], ...] = (
    ("a_vstd_20", "量能波动20日", "ratio", "", "20 日成交量标准差 / 均值（Alpha158 VSTD20）"),
    ("a_vstd_5", "量能波动5日", "ratio", "", "5 日成交量标准差 / 均值（Alpha158 VSTD5）"),
    ("a_qtl20_60", "60日20分位偏离", "ratio", "%", "60 日收盘 20 分位 / 当前价 − 1（Alpha158 QTLD60）"),
    ("a_corr_20", "量价相关20日", "ratio", "", "20 日收盘与对数成交量的相关系数（Alpha158 CORR20）"),
    ("a_corr_5", "量价相关5日", "ratio", "", "5 日收盘与对数成交量的相关系数（Alpha158 CORR5）"),
    ("a_mingap_60", "距60日低点", "ratio", "%", "当前价 / 60 日最低价 − 1（Alpha158 LOW60）"),
    ("a_kbar_kup", "上影线占比", "ratio", "%", "（最高 − 开盘）/ 开盘（Alpha158 KUP）"),
)


def factors() -> list[Factor]:
    """Promoted factors, now evaluated as panel columns."""
    return [
        Factor(
            name=name,
            label=label,
            category="外部因子·已晋级",
            direction=0,
            note=description,
            source="panel",
        )
        for name, label, _kind, _unit, description in CATALOGUE
    ]


def select_columns(indent: str = "        ") -> str:
    """``name AS name`` list with window frames inlined.

    Named windows are not used: DuckDB refuses to reuse a named window that
    carries a frame when the aggregate is ``corr`` / ``quantile_cont``, so each
    expression carries its own full frame clause.
    """
    parts = []
    for name, expression in EXPRESSIONS.items():
        for window, definition in WINDOWS.items():
            expression = expression.replace(f"OVER ({window})", f"OVER ({definition})")
        parts.append(f'{expression} AS "{name}"')
    return (",\n" + indent).join(parts)


def window_clause() -> str:
    """Kept for callers that still declare the three windows explicitly."""
    return ",\n           ".join(
        f"{name} AS ({definition})" for name, definition in WINDOWS.items()
    )
