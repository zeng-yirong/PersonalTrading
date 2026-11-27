"""Backtest specification: validate everything before a single SQL statement runs.

The rule is the screener's rule: a closed catalogue, explicit bounds, and errors
that say what to do instead. Nothing here touches the panel.

Defaults are chosen so that one call with a query and no dates still produces a
usable one-year, monthly-rebalanced, ten-name portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from app.backtest.features import UNSUPPORTED
from app.screener import query as screen_query
from app.screener.fields import BY_NAME
from app.screener.query import QueryError

DEFAULT_REBALANCE = 20
DEFAULT_TOP_N = 10
DEFAULT_CASH = 1_000_000.0
DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_BENCHMARK = "000300.SH"

MAX_TOP_N = 50
MAX_REBALANCE = 250
MAX_YEARS = 5
MIN_WINDOW_DAYS = 60

REBALANCE_CHOICES = (5, 10, 20, 60)


class BacktestError(ValueError):
    """Raised for an invalid spec — surfaced to the caller as HTTP 400."""


@dataclass
class Spec:
    query: screen_query.ScreenQuery
    start: date
    end: date
    rebalance: int = DEFAULT_REBALANCE
    top_n: int = DEFAULT_TOP_N
    initial_cash: float = DEFAULT_CASH
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    benchmark: str = DEFAULT_BENCHMARK
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title or self.query.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "rebalance": self.rebalance,
            "top_n": self.top_n,
            "initial_cash": self.initial_cash,
            "slippage_bps": self.slippage_bps,
            "benchmark": self.benchmark,
            "query": self.query.to_dict(),
        }


def _date(raw: Any, label: str) -> Optional[date]:
    if raw in (None, ""):
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise BacktestError(f"{label} 不是合法日期（要 YYYY-MM-DD）：{raw}") from exc


def _number(raw: Any, default: float, label: str) -> float:
    """Numeric field where 0 is a legal value (so ``or`` would be wrong)."""
    if raw in (None, ""):
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise BacktestError(f"{label} 必须是数字：{raw}") from exc


def _reject_unsupported(query: screen_query.ScreenQuery) -> None:
    used = {item.field for item in query.filters}
    used |= {str(item.get("field")) for item in query.sort if item.get("field")}
    for name in sorted(used):
        if name in UNSUPPORTED:
            raise BacktestError(f"回测不支持字段 {name}：{UNSUPPORTED[name]}")


def from_dict(
    payload: dict[str, Any],
    panel_start: Optional[date] = None,
    panel_end: Optional[date] = None,
) -> tuple[Spec, list[str]]:
    """Parse and clamp a request; returns the spec plus human-readable warnings."""
    if not isinstance(payload, dict):
        raise BacktestError("请求必须是一个 JSON 对象")
    warnings: list[str] = []

    raw_query = payload.get("query")
    if raw_query is None:
        query_payload = {key: value for key, value in payload.items()
                         if key in {"filters", "sort", "universe", "title", "explanation"}}
    elif isinstance(raw_query, dict):
        query_payload = raw_query
    else:
        raise BacktestError("query 必须是一个对象")

    try:
        query = screen_query.ScreenQuery.from_dict(query_payload)
    except QueryError as exc:
        raise BacktestError(str(exc)) from exc
    _reject_unsupported(query)
    query.limit = MAX_TOP_N  # the backtest caps positions itself

    end = _date(payload.get("end"), "end") or panel_end
    if end is None:
        raise BacktestError("面板里还没有行情，无法回测")
    start = _date(payload.get("start"), "start")
    if start is None:
        start = end - timedelta(days=365)

    if panel_end and end > panel_end:
        warnings.append(f"回测结束日 {end} 晚于面板最新日期，已截断到 {panel_end}")
        end = panel_end
    if panel_start and start < panel_start:
        warnings.append(f"回测开始日 {start} 早于面板最早日期，已截断到 {panel_start}")
        start = panel_start
    if start >= end:
        raise BacktestError(f"开始日 {start} 必须早于结束日 {end}")
    if (end - start).days < MIN_WINDOW_DAYS:
        raise BacktestError(f"回测区间太短（{(end - start).days} 天），至少需要 {MIN_WINDOW_DAYS} 天")
    if (end - start).days > 365 * MAX_YEARS:
        raise BacktestError(f"回测区间最长 {MAX_YEARS} 年")

    rebalance = _number(payload.get("rebalance"), DEFAULT_REBALANCE, "调仓周期")
    rebalance = int(rebalance)
    if not 1 <= rebalance <= MAX_REBALANCE:
        raise BacktestError(f"调仓周期必须在 1-{MAX_REBALANCE} 个交易日之间")

    top_n = int(_number(payload.get("top_n"), DEFAULT_TOP_N, "持仓数量"))
    if not 1 <= top_n <= MAX_TOP_N:
        raise BacktestError(f"持仓数量必须在 1-{MAX_TOP_N} 之间")

    initial_cash = _number(payload.get("initial_cash"), DEFAULT_CASH, "初始资金")
    if not 10_000 <= initial_cash <= 1e9:
        raise BacktestError("初始资金必须在 1 万到 10 亿之间")

    slippage = _number(payload.get("slippage_bps"), DEFAULT_SLIPPAGE_BPS, "滑点")
    if not 0 <= slippage <= 200:
        raise BacktestError("滑点必须在 0-200 个基点之间")

    benchmark = str(payload.get("benchmark") or DEFAULT_BENCHMARK).strip()

    for item in query.filters:
        if item.field not in BY_NAME:  # pragma: no cover - validated by from_dict
            raise BacktestError(f"未知字段 {item.field}")

    spec = Spec(
        query=query,
        start=start,
        end=end,
        rebalance=rebalance,
        top_n=top_n,
        initial_cash=initial_cash,
        slippage_bps=slippage,
        benchmark=benchmark,
        title=str(payload.get("title") or query.title or ""),
    )
    return spec, warnings


def describe() -> dict[str, Any]:
    """What the UI needs to render a sensible form."""
    return {
        "defaults": {
            "rebalance": DEFAULT_REBALANCE,
            "top_n": DEFAULT_TOP_N,
            "initial_cash": DEFAULT_CASH,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
            "benchmark": DEFAULT_BENCHMARK,
            "lookback_years": 1,
        },
        "limits": {
            "top_n": MAX_TOP_N,
            "rebalance": MAX_REBALANCE,
            "max_years": MAX_YEARS,
            "min_window_days": MIN_WINDOW_DAYS,
        },
        "rebalance_choices": list(REBALANCE_CHOICES),
        "unsupported_fields": UNSUPPORTED,
        "supported_fields": [name for name in BY_NAME if name not in UNSUPPORTED],
    }
