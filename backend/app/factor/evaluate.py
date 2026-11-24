"""Factor evaluation: rank correlation and quantile spreads on the real panel.

A factor is only worth keeping if it separates future returns. This module builds
the same point-in-time cross-section a backtest trades (``bt_feat``), attaches each
name's forward return at the chosen horizon, and reports:

* **IC** — per-date Spearman rank correlation between factor value and forward
  return. Spearman rather than Pearson: a factor's scale is arbitrary, its ordering
  is not.
* **ICIR / t 值** — mean IC over its standard deviation, and how many standard
  errors that mean is away from zero. A factor with |ICIR| < 0.1 needs a lot of
  dates before the t 值 says anything.
* **分层收益** — mean forward return of each quintile, and the top-minus-bottom
  spread. Monotonic quintiles matter: an IC driven by one extreme bucket is a
  selection effect, not a factor.

Forward returns are computed from adjusted closes (`close × adj_factor`), so a
dividend is a return and a split is not a crash. Dates without enough forward
data are dropped, never filled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.backtest import features
from app.data import store
from app.factor import academic, alpha158, fundamental, gtja, library

HORIZONS = (5, 10, 20, 60)
MIN_CROSS_SECTION = 30     # per date, below this a rank correlation is noise
MIN_DATES = 8              # below this the summary is not reported
QUANTILES = 5
INCREMENTAL_THRESHOLD = 0.15   # residual ICIR needed to count as "new information"

# source -> temp table holding its columns; ``panel`` lives in bt_feat itself.
EXTERNAL_TABLES = {
    "alpha158": "bt_ext",
    "academic": academic.TABLE,
    "gtja": gtja.TABLE,
    "fundamental": fundamental.TABLE,
}


class FactorError(ValueError):
    """Invalid factor request — surfaced as HTTP 400."""


@dataclass
class Spec:
    start: date
    end: date
    rebalance: int = 20
    horizon: int = 20
    factors: list[library.Factor] = field(default_factory=list)
    oos_months: int = 0
    incremental: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "rebalance": self.rebalance,
            "horizon": self.horizon,
            "factors": [factor.name for factor in self.factors],
            "oos_months": self.oos_months,
            "incremental": self.incremental,
        }


def run(spec: Spec) -> dict[str, Any]:
    if spec.oos_months:
        return _run_with_oos(spec)
    with store.session() as conn:
        results, grid = _window(conn, spec, spec.start, spec.end)
    return _package(spec, results, grid)


def _run_with_oos(spec: Spec) -> dict[str, Any]:
    """Evaluate in-sample and out-of-sample separately, then gate each factor."""
    split = spec.end - timedelta(days=30 * spec.oos_months)
    if split <= spec.start:
        raise FactorError(
            f"样本外 {spec.oos_months} 个月太长：开始日 {spec.start} 之后没有足够的样本内区间"
        )
    with store.session() as conn:
        inside, inside_grid = _window(conn, spec, spec.start, split, purge=True)
        outside, outside_grid = _window(conn, spec, split + timedelta(days=1), spec.end)

    outside_by_name = {item["name"]: item for item in outside}
    merged = []
    for item in inside:
        oos = outside_by_name.get(item["name"])
        passed, reason = _gate(item, oos)
        merged.append(
            {
                **item,
                "out_of_sample": _brief(oos) if oos else None,
                "passed": passed,
                "gate_reason": reason,
            }
        )
    merged.sort(key=lambda item: (not item["passed"], -abs(item["icir"])))
    return {
        "config": spec.to_dict(),
        "lookback": {"start": spec.start.isoformat(), "end": split.isoformat()},
        "out_of_sample_period": {"start": split.isoformat(), "end": spec.end.isoformat()},
        "sections": len(inside_grid),
        "oos_sections": len(outside_grid),
        "evaluated_dates": max((item["n_dates"] for item in merged), default=0),
        "oos_evaluated_dates": max(
            (item["out_of_sample"]["n_dates"] for item in merged if item["out_of_sample"]),
            default=0,
        ),
        "gate_passed": sum(1 for item in merged if item["passed"]),
        "factors": merged,
    }


def _gate(inside: dict[str, Any], outside: Optional[dict[str, Any]]) -> tuple[bool, str]:
    """The promotion rule from goat.md, applied mechanically.

    A factor whose IC has zero variance is perfect separation (every period the
    same sign and magnitude), not weak evidence — ``ic/std`` is undefined there, so
    the strength is judged by the mean itself.
    """
    if outside is None:
        return False, "样本外没有足够的截面"
    if not math.isfinite(inside["ic_mean"]) or not math.isfinite(outside["ic_mean"]):
        return False, "IC 无效（因子取值过少或全为常数）"
    inside_strength = _strength(inside)
    outside_strength = _strength(outside)
    inside_t = abs(inside["t_stat"]) if inside["ic_std"] > 0 else 99.0
    if inside_strength < 0.3:
        return False, "样本内 |ICIR| < 0.3"
    if inside_t < 3:
        return False, "样本内 |t| < 3"
    if (inside["ic_mean"] > 0) != (outside["ic_mean"] > 0):
        return False, "样本外方向相反"
    if outside_strength < 0.3:
        return False, "样本外 |ICIR| < 0.3"
    return True, "两段同号，且都达到 ICIR 0.3 门槛"


def _strength(item: dict[str, Any]) -> float:
    if item["ic_std"] == 0:
        return abs(item["ic_mean"]) * 10.0
    return abs(item["icir"])


def _brief(item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if item is None:
        return None
    return {
        "ic_mean": item["ic_mean"],
        "icir": item["icir"],
        "t_stat": item["t_stat"],
        "n_dates": item["n_dates"],
        "positive_ratio": item["positive_ratio"],
    }


def _window(conn, spec: Spec, start: date, end: date, purge: bool = False):
    days = _trade_days(conn, start, end)
    if len(days) < spec.rebalance + spec.horizon + 5:
        raise FactorError(
            f"区间太短：{len(days)} 个交易日，至少需要调仓周期 + 持有期 + 5 天"
        )
    grid = days[:: spec.rebalance]
    if purge:
        # 样本内最后几期的前瞻收益会跨到样本外：直接丢掉这些调仓日，
        # 否则样本内 IC 里混进了样本外的价格。
        index_of = {day: index for index, day in enumerate(days)}
        grid = [day for day in grid if index_of[day] + spec.horizon < len(days)]
    features.build(conn, grid, end)
    sources = {factor.source for factor in spec.factors}
    if "alpha158" in sources:
        conn.execute(alpha158.build_sql())
    if "academic" in sources:
        conn.execute(academic.build_sql())
    if "gtja" in sources:
        conn.execute(gtja.build_sql())
    if "fundamental" in sources:
        conn.execute(fundamental.build_sql())
    _build_forward(conn, spec.horizon, days[0], end)

    panel = _panel_frame(conn) if spec.incremental else None
    results = []
    for factor in spec.factors:
        item = _evaluate(conn, factor, spec.horizon)
        if item is None:
            continue
        if spec.incremental:
            item["incremental"] = _incremental(conn, factor, panel, item["ic_mean"])
        results.append(item)
    return results, grid


def _package(spec: Spec, results: list[dict[str, Any]], grid: list[date]) -> dict[str, Any]:
    if not results:
        raise FactorError("没有任何因子在区间内拿到足够的截面（每期至少 30 只）")
    ranked = sorted(
        results,
        key=lambda item: (not item["usable"], -abs(item["icir"])),
    )
    return {
        "config": spec.to_dict(),
        "sections": len(grid),
        "evaluated_dates": max(item["n_dates"] for item in ranked),
        "factors": ranked,
    }


# ------------------------------------------------------------------ plumbing
def _trade_days(conn, start: date, end: date) -> list[date]:
    rows = conn.execute(
        "SELECT cal_date FROM calendar WHERE is_open AND cal_date BETWEEN ? AND ? "
        "ORDER BY cal_date",
        [start, end],
    ).fetchall()
    return [row[0] for row in rows]


def _build_forward(conn, horizon: int, start: date, end: date) -> None:
    """Forward return per (symbol, grid date), using adjusted closes."""
    upper = end + timedelta(days=max(120, horizon * 3))
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE bt_fwd AS
        WITH bars AS (
            SELECT symbol, trade_date,
                   close * COALESCE(adj_factor, 1) AS c,
                   LEAD(close * COALESCE(adj_factor, 1), {int(horizon)}) OVER (
                       PARTITION BY symbol ORDER BY trade_date) AS cf
            FROM daily
            WHERE trade_date >= ? AND trade_date <= ?
        )
        SELECT symbol, trade_date, cf / NULLIF(c, 0) - 1 AS fwd
        FROM bars
        WHERE trade_date IN (SELECT d FROM bt_dates) AND cf IS NOT NULL
        """,
        [start, upper],
    )


def _base_cte(factor: library.Factor) -> str:
    """Cross-section joined with forward returns; the factor column is whitelisted."""
    table = EXTERNAL_TABLES.get(factor.source)
    if table is None:
        join = ""
        column = f't."{factor.name}"'
    else:
        join = f"JOIN {table} x ON x.symbol = t.symbol AND x.trade_date = t.trade_date"
        column = f'x."{factor.name}"'
    return f"""
        SELECT t.trade_date, t.symbol, t.industry,
               {column}::DOUBLE AS v, f.fwd::DOUBLE AS r
        FROM bt_feat t
        JOIN bt_fwd f ON f.symbol = t.symbol AND f.trade_date = t.trade_date
        {join}
        WHERE t.asset_type = 'stock' AND t.name NOT LIKE '%ST%'
          AND {column} IS NOT NULL AND f.fwd IS NOT NULL
    """


def _coverage_sql(factor: library.Factor) -> str:
    table = EXTERNAL_TABLES.get(factor.source)
    if table is None:
        return (
            f'SELECT AVG(CASE WHEN t."{factor.name}" IS NOT NULL THEN 1.0 ELSE 0.0 END) '
            "FROM bt_feat t WHERE t.asset_type = 'stock' AND t.name NOT LIKE '%ST%'"
        )
    return (
        f'SELECT AVG(CASE WHEN x."{factor.name}" IS NOT NULL THEN 1.0 ELSE 0.0 END) '
        f"FROM bt_feat t JOIN {table} x ON x.symbol = t.symbol AND x.trade_date = t.trade_date "
        "WHERE t.asset_type = 'stock' AND t.name NOT LIKE '%ST%'"
    )


def _distinct_sql(factor: library.Factor) -> str:
    table = EXTERNAL_TABLES.get(factor.source)
    if table is None:
        return (
            f'SELECT COUNT(DISTINCT "{factor.name}") FROM bt_feat '
            "WHERE asset_type = 'stock'"
        )
    return f'SELECT COUNT(DISTINCT "{factor.name}") FROM {table}'


def _evaluate(conn, factor: library.Factor, horizon: int) -> Optional[dict[str, Any]]:
    base = _base_cte(factor)
    # Average ranks for ties: ``rank()`` alone would break ties by scan order,
    # which turns a sparse factor (90% zeros) into a meaningless correlation.
    ic_rows = conn.execute(
        f"""
        WITH x AS ({base}),
        y AS (
            SELECT trade_date, r,
                   {_avg_rank("v")} AS rv,
                   {_avg_rank("r")} AS rr
            FROM x
        )
        SELECT trade_date, COUNT(*) AS n, corr(rv, rr) AS ic
        FROM y GROUP BY 1 HAVING COUNT(*) >= {MIN_CROSS_SECTION} ORDER BY 1
        """
    ).fetchall()
    if not ic_rows:
        return None

    dates = [row[0] for row in ic_rows]
    ics = [
        float(row[2])
        for row in ic_rows
        if row[2] is not None and math.isfinite(float(row[2]))
    ]
    if len(ics) < MIN_DATES:
        return None
    n_dates = len(ics)
    mean = float(np.mean(ics))
    std = float(np.std(ics)) if n_dates > 1 else 0.0
    icir = mean / std if std else 0.0

    coverage = conn.execute(_coverage_sql(factor)).fetchone()[0]

    distinct = conn.execute(_distinct_sql(factor)).fetchone()[0]
    # Quintiles over a count factor with a handful of distinct values are not
    # quintiles at all — report the rank correlation and leave the buckets out.
    quantiles = (
        _quantiles(conn, base, horizon) if int(distinct or 0) >= QUANTILES * 2 else []
    )
    spread = None
    if quantiles:
        spread = quantiles[-1]["mean_pct"] - quantiles[0]["mean_pct"]

    return {
        "name": factor.name,
        "label": factor.label,
        "category": factor.category,
        "direction": factor.direction,
        "expected": {1: "越大越好", -1: "越小越好", 0: "无先验"}[factor.direction],
        "coverage": round(float(coverage or 0), 4),
        "distinct": int(distinct or 0),
        "source": factor.source,
        "usable": bool(float(coverage or 0) >= 0.2 and n_dates >= MIN_DATES),
        "horizon": horizon,
        "n_dates": n_dates,
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 4),
        "t_stat": round(icir * math.sqrt(n_dates), 2),
        "positive_ratio": round(
            sum(1 for value in ics if value > 0) / n_dates * 100, 1
        ),
        "ic_series": [
            {"date": day.isoformat(), "ic": round(float(ic), 4)}
            for day, ic in zip(dates, [row[2] for row in ic_rows])
            if ic is not None and math.isfinite(float(ic))
        ],
        "quantiles": quantiles,
        "spread_pct": round(spread, 4) if spread is not None else None,
        "aligned": _aligned(factor.direction, mean),
    }


def _avg_rank(column: str) -> str:
    """Standard average rank for ties: min_rank + (tie_count - 1) / 2."""
    return (
        f"(rank() OVER (PARTITION BY trade_date ORDER BY {column})"
        f" + (count(*) OVER (PARTITION BY trade_date, {column}) - 1) / 2.0)"
    )


def _quantiles(conn, base: str, horizon: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        WITH x AS ({base}),
        y AS (
            SELECT trade_date, r, ntile({QUANTILES}) OVER (
                PARTITION BY trade_date ORDER BY v) AS q
            FROM x
        ),
        per_date AS (
            SELECT trade_date, q, AVG(r) AS period_return, COUNT(*) AS n
            FROM y GROUP BY 1, 2
        )
        SELECT q, AVG(period_return) * 100 AS mean_pct,
               MEDIAN(period_return) * 100 AS median_pct,
               ROUND(AVG(n), 1) AS avg_names, COUNT(*) AS periods
        FROM per_date GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [
        {
            "q": int(row[0]),
            "mean_pct": round(float(row[1]), 4),
            "median_pct": round(float(row[2]), 4),
            "avg_names": float(row[3] or 0),
            "periods": int(row[4] or 0),
        }
        for row in rows
    ]


def _aligned(direction: int, ic_mean: float) -> Optional[bool]:
    if direction == 0 or ic_mean == 0:
        return None
    return (direction > 0) == (ic_mean > 0)


# ------------------------------------------------------------ incremental IC
def _panel_frame(conn) -> pd.DataFrame:
    """One row per (date, symbol) with the controls used for residualisation."""
    return conn.execute(
        """
        SELECT t.trade_date, t.symbol, t.industry, t.total_mv, t.turnover_rate,
               t.ret20, f.fwd
        FROM bt_feat t
        JOIN bt_fwd f ON f.symbol = t.symbol AND f.trade_date = t.trade_date
        WHERE t.asset_type = 'stock' AND t.name NOT LIKE '%ST%' AND f.fwd IS NOT NULL
        """
    ).df()


def _incremental(
    conn, factor: library.Factor, panel: pd.DataFrame, raw_ic_mean: float
) -> dict[str, Any]:
    """IC of the factor after stripping size, turnover, momentum and industry.

    A raw IC mostly says "this factor is a size or liquidity proxy". What matters
    for a new import is whether anything is left once those are regressed out.
    """
    table = "bt_feat" if factor.source == "panel" else EXTERNAL_TABLES[factor.source]
    values = conn.execute(
        f'SELECT trade_date, symbol, "{factor.name}" AS v FROM {table}'
    ).df()
    frame = panel.merge(values, on=["trade_date", "symbol"], how="inner")
    frame = frame.dropna(subset=["v", "fwd", "total_mv", "turnover_rate", "ret20"])
    frame["size"] = np.log(frame["total_mv"].clip(lower=1.0))

    ics = []
    for _, block in frame.groupby("trade_date", sort=True):
        value = _residual_ic(block)
        if value is not None and math.isfinite(value):
            ics.append(value)
    if len(ics) < MIN_DATES:
        return {
            "n_dates": len(ics),
            "ic_mean": None,
            "icir": None,
            "t_stat": None,
            "passed": False,
            "reason": f"残差 IC 只有 {len(ics)} 期，样本不足",
        }
    mean = float(np.mean(ics))
    std = float(np.std(ics)) if len(ics) > 1 else 0.0
    icir = mean / std if std else 0.0
    same_sign = (mean > 0) == (raw_ic_mean > 0)
    strength = abs(mean) * 10.0 if std == 0 else abs(icir)
    passed = bool(abs(strength) >= INCREMENTAL_THRESHOLD and same_sign)
    if passed:
        reason = "扣除规模/换手/动量与行业后仍有信息"
    elif not same_sign:
        reason = "残差方向与原始方向相反"
    else:
        reason = f"残差 |ICIR| < {INCREMENTAL_THRESHOLD}"
    return {
        "n_dates": len(ics),
        "ic_mean": round(mean, 4),
        "icir": round(icir, 4),
        "t_stat": round(icir * math.sqrt(len(ics)), 2),
        "passed": passed,
        "reason": reason,
    }


def _residual_ic(block: pd.DataFrame) -> Optional[float]:
    if len(block) < MIN_CROSS_SECTION:
        return None
    v = block["v"].rank().to_numpy(dtype=float)
    r = block["fwd"].rank().to_numpy(dtype=float)
    controls = np.column_stack(
        [block[column].rank().to_numpy(dtype=float)
         for column in ("size", "turnover_rate", "ret20")]
    )
    codes = block["industry"].fillna("NA").astype("category").cat.codes.to_numpy()
    counts = np.bincount(codes)

    def demean(values: np.ndarray) -> np.ndarray:
        sums = np.bincount(codes, weights=values)
        return values - (sums / counts)[codes]

    v = demean(v)
    r = demean(r)
    controls = np.column_stack(
        [demean(controls[:, index]) for index in range(controls.shape[1])]
    )
    design = np.column_stack([np.ones(len(v)), controls])
    beta, *_ = np.linalg.lstsq(design, v, rcond=None)
    residual = v - design @ beta
    if residual.std() < 1e-9:
        # 因子完全由控制变量解释（典型：市值的单调变换）→ 没有增量信息
        return 0.0
    if r.std() == 0:
        return None
    return float(np.corrcoef(residual, r)[0, 1])
