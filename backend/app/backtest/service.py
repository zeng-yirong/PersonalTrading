"""Backtest orchestration: spec → panel → simulation → stored run.

Everything that touches the panel happens inside **one** DuckDB connection, so the
cross-section, the bars used for fills and the benchmark all describe the same
snapshot of the database. The simulation itself is pure Python.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any, Optional

from app.backtest import data, engine, features, store
from app.backtest import spec as spec_module
from app.data import store as panel
from app.fees import COMMISSION_MIN, COMMISSION_RATE, LOT, STAMP_RATE, TRANSFER_RATE
from app.screener import query as screen_query

# DuckDB allows a single writer; two simultaneous backtests from the UI would
# otherwise collide on the temporary feature table.
RUN_LOCK = threading.Lock()

MIN_SIGNALS = 3


class BacktestError(spec_module.BacktestError):
    """Kept as one name for the router: spec and runtime errors are both 400."""


def panel_bounds() -> tuple[Optional[date], Optional[date], int]:
    with panel.session(read_only=True) as conn:
        row = conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT symbol) FROM daily"
        ).fetchone()
    if not row or row[1] is None:
        return None, None, 0
    return row[0], row[1], int(row[2] or 0)


def meta() -> dict[str, Any]:
    first, last, symbols = panel_bounds()
    described = spec_module.describe()
    described["panel"] = {
        "start": first.isoformat() if first else None,
        "end": last.isoformat() if last else None,
        "symbols": symbols,
    }
    described["fees"] = {
        "commission_rate": COMMISSION_RATE,
        "commission_min": COMMISSION_MIN,
        "stamp_rate": STAMP_RATE,
        "transfer_rate": TRANSFER_RATE,
        "lot": LOT,
        "settlement": "信号日收盘计算，次日开盘成交",
    }
    described["screener_fields"] = len(screen_query.field_catalog())
    return described


def run(payload: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    first, last, _ = panel_bounds()
    spec, warnings = spec_module.from_dict(payload, first, last)
    source_run_id = _source_run(payload.get("source_run_id"))
    result = execute(spec, warnings, persist=persist, source_run_id=source_run_id)
    return result


def _source_run(raw: Any) -> Optional[int]:
    """Optional link back to the research run this backtest came from."""
    if raw in (None, ""):
        return None
    try:
        run_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"source_run_id 必须是整数：{raw}") from exc
    from app.agents import store as agent_store

    if agent_store.get_run(run_id) is None:
        raise BacktestError(f"推演 #{run_id} 不存在")
    return run_id


def execute(spec, warnings: list[str], persist: bool = True,
            source_run_id: Optional[int] = None) -> dict[str, Any]:
    days = panel.get_trade_days(spec.start, spec.end)
    if len(days) < 20:
        raise BacktestError("区间内交易日太少，无法回测")
    signals = engine.schedule(days, spec.rebalance)
    if len(signals) < MIN_SIGNALS:
        raise BacktestError(
            f"按 {spec.rebalance} 日调仓只能得到 {len(signals)} 次调仓，"
            "请放宽区间或缩短调仓周期"
        )

    with RUN_LOCK:
        with panel.session() as conn:
            features.build(conn, signals, spec.end)
            picks = features.select_picks(conn, spec.query, spec.top_n)
            coverage = features.coverage(conn)
            symbols = {item["symbol"] for rows in picks.values() for item in rows}
            ts_codes = {item["ts_code"] for rows in picks.values() for item in rows}
            bars = data.load_bars(conn, symbols, days[0], spec.end)
            limits = data.load_limits(conn, ts_codes, days[0], spec.end)
            benchmark = data.load_benchmark(conn, spec.benchmark, days)

    _add_warnings(warnings, spec, picks, coverage, benchmark)
    simulated = engine.simulate(spec, days, picks, bars, limits, benchmark, warnings)

    result: dict[str, Any] = {
        "title": spec.title or spec.query.title or "选股条件回测",
        "spec": spec.to_dict(),
        "coverage": coverage,
        "metrics": simulated["metrics"],
        "nav": simulated["nav"],
        "periods": simulated["periods"],
        "trades": simulated["trades"],
        "warnings": warnings,
        "source_run_id": source_run_id,
    }
    if persist:
        run_id = store.save(result)
        result["id"] = run_id
        result["created_at"] = _created_at(run_id)
    return result


def _add_warnings(
    warnings: list[str],
    spec,
    picks: dict[date, list[dict[str, Any]]],
    coverage: dict[str, Any],
    benchmark: dict[date, float],
) -> None:
    total = sum(len(rows) for rows in picks.values())
    if total == 0:
        warnings.append("所有调仓日都没有符合条件的标的，策略全程空仓")
    shares = coverage.get("shares") or {}
    if shares.get("fin", 1) < 0.5:
        warnings.append(
            f"财务字段覆盖率仅 {shares.get('fin', 0) * 100:.0f}%，依赖 ROE/成长的筛选在早期日期会被大量跳过"
        )
    if not benchmark:
        warnings.append(f"基准 {spec.benchmark} 没有行情，超额收益无法计算")
    if coverage.get("dates", 0) and coverage["dates"] < 6:
        warnings.append(f"只有 {coverage['dates']} 个调仓截面，统计意义有限，建议拉长区间")


def _created_at(run_id: int) -> Optional[str]:
    record = store.get_run(run_id)
    return record["created_at"] if record else None


def history(limit: int = 20) -> list[dict[str, Any]]:
    return store.list_runs(limit)


def detail(run_id: int) -> Optional[dict[str, Any]]:
    return store.get_run(run_id)
