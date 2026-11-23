"""Qlib Alpha158-style factors, re-expressed for cross-sectional ranking.

Alpha158 is an **ML feature set**: it is designed to be fed to a model, so it
contains raw price levels (``OPEN0``, ``CLOSE0``) that carry no cross-sectional
information — ranking every stock by its share price is meaningless. This module
keeps the formula families that are **scale-free** (returns, ratios, correlations,
counts) and drops the levels.

The formulas follow the published Qlib definitions (MIT). Provenance is recorded
per factor so a promoted factor can be traced back to its paper and formula.

Factors are computed in ``bt_ext``, keyed by (trade_date, symbol), on the same
grid dates as ``bt_feat``. Only the daily OHLCV + adjustment factor is required;
no new data source is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.factor import promoted
from app.factor.library import Factor

WINDOWS = (5, 20, 60)
SOURCE = "alpha158"

# (operator, label, direction, note) — the note is the formula, kept short.
OPERATORS: tuple[tuple[str, str, int, str], ...] = (
    ("roc", "区间涨幅", 0, "close_t / close_{t-w} - 1"),
    ("magap", "均线偏离", 0, "close_t / MA_w(close) - 1"),
    ("std", "波动率", -1, "STD_w(close) / MA_w(close)，越高越不稳"),
    ("maxgap", "距区间高点", 0, "close_t / MAX_w(high) - 1"),
    ("mingap", "距区间低点", 0, "close_t / MIN_w(low) - 1"),
    ("rsv", "区间位置", 0, "(close − MIN_w(low)) / (MAX_w(high) − MIN_w(low))"),
    ("qtl80", "80 分位偏离", 0, "QTL80_w(close) / close_t - 1"),
    ("qtl20", "20 分位偏离", 0, "QTL20_w(close) / close_t - 1"),
    ("beta", "趋势斜率", 0, "REGR_SLOPE(close, t) / MA_w(close)，按价格标准化"),
    ("rsqr", "趋势拟合度", 0, "REGR_R2(close, t)，越高越像单边趋势"),
    ("corr", "量价相关", 0, "CORR_w(close, ln(volume+1))"),
    ("cntd", "涨跌天数差", 1, "w 日内上涨占比 − 下跌占比"),
    ("sumd", "收益不对称", 1, "w 日内正收益均值 − 负收益均值，按波动标准化"),
    ("vma", "量能偏离", 0, "volume_t / MA_w(volume) - 1"),
    ("vstd", "量能波动", 0, "STD_w(volume) / MA_w(volume)"),
    ("wvma", "量价冲击", 0, "STD_w(|ret| × volume) / MA_w(|ret| × volume)"),
)


def _frame(window: int) -> str:
    return f"ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW"


def _expr(operator: str, window: int) -> str:
    w = window
    frame = _frame(w)
    window_clause = f"OVER (PARTITION BY symbol ORDER BY trade_date {frame})"
    if operator == "roc":
        return f"c_adj / NULLIF(lag(c_adj, {w}) OVER (PARTITION BY symbol ORDER BY trade_date), 0) - 1"
    if operator == "magap":
        return f"c_adj / NULLIF(avg(c_adj) {window_clause}, 0) - 1"
    if operator == "std":
        return f"stddev_samp(c_adj) {window_clause} / NULLIF(avg(c_adj) {window_clause}, 0)"
    if operator == "maxgap":
        return f"c_adj / NULLIF(max(h_adj) {window_clause}, 0) - 1"
    if operator == "mingap":
        return f"c_adj / NULLIF(min(l_adj) {window_clause}, 0) - 1"
    if operator == "rsv":
        return (
            f"(c_adj - min(l_adj) {window_clause}) "
            f"/ NULLIF(max(h_adj) {window_clause} - min(l_adj) {window_clause}, 0)"
        )
    if operator == "qtl80":
        return f"quantile_cont(c_adj, 0.8) {window_clause} / NULLIF(c_adj, 0) - 1"
    if operator == "qtl20":
        return f"quantile_cont(c_adj, 0.2) {window_clause} / NULLIF(c_adj, 0) - 1"
    if operator == "beta":
        return f"regr_slope(c_adj, rn) {window_clause} / NULLIF(avg(c_adj) {window_clause}, 0)"
    if operator == "rsqr":
        return f"regr_r2(c_adj, rn) {window_clause}"
    if operator == "corr":
        return f"corr(c_adj, ln(volume + 1)) {window_clause}"
    if operator == "cntd":
        return (
            f"(sum(CASE WHEN ret > 0 THEN 1.0 ELSE 0 END) {window_clause}"
            f" - sum(CASE WHEN ret < 0 THEN 1.0 ELSE 0 END) {window_clause}) / {w}.0"
        )
    if operator == "sumd":
        return (
            f"(sum(greatest(ret, 0)) {window_clause}"
            f" - sum(greatest(-ret, 0)) {window_clause})"
            f" / NULLIF(stddev_samp(ret) {window_clause} + 1e-9, 0)"
        )
    if operator == "vma":
        return f"volume / NULLIF(avg(volume) {window_clause}, 0) - 1"
    if operator == "vstd":
        return f"stddev_samp(volume) {window_clause} / NULLIF(avg(volume) {window_clause}, 0)"
    if operator == "wvma":
        return (
            f"stddev_samp(abs(ret) * volume) {window_clause}"
            f" / NULLIF(avg(abs(ret) * volume) {window_clause}, 0)"
        )
    raise ValueError(f"未知算子 {operator}")


KBAR: tuple[tuple[str, str, int, str], ...] = (
    ("kmid", "实体位置", 0, "(close − open) / open"),
    ("klen", "实体长度", 0, "(high − low) / open"),
    ("kmid2", "实体占比", 0, "(close − open) / (high − low)"),
    ("kup", "上影线", 0, "(high − open) / open"),
    ("kup2", "上影占比", 0, "(high − open) / (high − low)"),
    ("klow", "下影线", 0, "(open − low) / open"),
    ("klow2", "下影占比", 0, "(open − low) / (high − low)"),
    ("ksft", "收盘偏移", 0, "(2 × close − high − low) / open"),
    ("ksft2", "收盘偏移占比", 0, "(2 × close − high − low) / (high − low)"),
)


def _kbar_expr(operator: str) -> str:
    body = {
        "kmid": "(c_adj - o_adj) / NULLIF(o_adj, 0)",
        "klen": "(h_adj - l_adj) / NULLIF(o_adj, 0)",
        "kmid2": "(c_adj - o_adj) / NULLIF(h_adj - l_adj, 0)",
        "kup": "(h_adj - o_adj) / NULLIF(o_adj, 0)",
        "kup2": "(h_adj - o_adj) / NULLIF(h_adj - l_adj, 0)",
        "klow": "(o_adj - l_adj) / NULLIF(o_adj, 0)",
        "klow2": "(o_adj - l_adj) / NULLIF(h_adj - l_adj, 0)",
        "ksft": "(2 * c_adj - h_adj - l_adj) / NULLIF(o_adj, 0)",
        "ksft2": "(2 * c_adj - h_adj - l_adj) / NULLIF(h_adj - l_adj, 0)",
    }[operator]
    return body


def definitions() -> list[Factor]:
    """The importable factor list, with provenance in the note.

    Factors already promoted into ``bt_feat`` are not re-imported: they are panel
    columns now, and evaluating them here would create a second source of truth.
    """
    output: list[Factor] = []
    for operator, label, direction, formula in KBAR:
        name = f"a_kbar_{operator}"
        if name in promoted.EXPRESSIONS:
            continue
        output.append(
            Factor(
                name=name,
                label=f"K线·{label}",
                category="外部因子",
                direction=direction,
                note=f"Alpha158 KBAR：{formula}",
                source=SOURCE,
            )
        )
    for operator, label, direction, formula in OPERATORS:
        for window in WINDOWS:
            name = f"a_{operator}_{window}"
            if name in promoted.EXPRESSIONS:
                continue
            output.append(
                Factor(
                    name=name,
                    label=f"{label}{window}日",
                    category="外部因子",
                    direction=direction,
                    note=f"Alpha158 {operator.upper()}{window}：{formula}",
                    source=SOURCE,
                )
            )
    return output


FACTORS: tuple[Factor, ...] = tuple(definitions())


def build_sql() -> str:
    """Create ``bt_ext`` on the caller's connection (grid dates only)."""
    columns = []
    for operator, _, _, _ in KBAR:
        name = f"a_kbar_{operator}"
        if name in promoted.EXPRESSIONS:
            continue
        columns.append(f'{_kbar_expr(operator)} AS "{name}"')
    for operator, _, _, _ in OPERATORS:
        for window in WINDOWS:
            name = f"a_{operator}_{window}"
            if name in promoted.EXPRESSIONS:
                continue
            columns.append(f'{_expr(operator, window)} AS "{name}"')
    column_sql = ",\n    ".join(columns)
    return f"""
CREATE OR REPLACE TEMP TABLE bt_ext AS
WITH bars AS (
    SELECT d.symbol, d.trade_date, d.open, d.close, d.high, d.low, d.volume,
           d.open * COALESCE(d.adj_factor, 1)  AS o_adj,
           d.close * COALESCE(d.adj_factor, 1) AS c_adj,
           d.high * COALESCE(d.adj_factor, 1)  AS h_adj,
           d.low * COALESCE(d.adj_factor, 1)   AS l_adj,
           d.close * COALESCE(d.adj_factor, 1)
               / NULLIF(lag(d.close * COALESCE(d.adj_factor, 1))
                        OVER (PARTITION BY d.symbol ORDER BY d.trade_date), 0) - 1 AS ret,
           row_number() OVER (PARTITION BY d.symbol ORDER BY d.trade_date) AS rn
    FROM daily d
    WHERE d.trade_date >= (SELECT MIN(d) FROM bt_dates) - INTERVAL 200 DAY
      AND d.trade_date <= (SELECT MAX(d) FROM bt_dates)
),
scored AS (
    SELECT symbol, trade_date,
    {column_sql}
    FROM bars
)
SELECT s.* FROM scored s
JOIN bt_dates bd ON bd.d = s.trade_date
"""
