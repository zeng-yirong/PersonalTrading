"""AKShare provider.

AKShare is a moving target: names change between minor releases (e.g.
``tool_trade_date_hist_sina`` -> ``calendar_trade_date_hist_sina``). Every call
here goes through a name-tolerant lookup so an upgrade does not break sync.

Only the trading calendar is used in P0: it covers past days *and* future days,
which the panel needs to decide "is today a trading day".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from app.data.providers.base import Capabilities, Provider, ProviderError, ProviderUnavailable

CALENDAR_NAMES = ("calendar_trade_date_hist_sina", "tool_trade_date_hist_sina")


def _load_module():
    try:
        import akshare  # noqa: F401
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ProviderUnavailable("未安装 akshare：pip install akshare") from exc
    import akshare as ak

    return ak


def _resolve(module: Any, names: tuple[str, ...], label: str) -> Callable:
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    raise ProviderUnavailable(f"当前 akshare 版本找不到 {label}（尝试过 {', '.join(names)}）")


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class AkShareProvider(Provider):
    name = "akshare"
    label = "AKShare"
    capabilities = Capabilities(calendar=True)

    def available(self) -> tuple[bool, str]:
        try:
            _load_module()
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, ""

    def trade_calendar(
        self, start: Optional[date] = None, end: Optional[date] = None
    ) -> list[tuple[date, bool]]:
        ak = _load_module()
        fn = _resolve(ak, CALENDAR_NAMES, "交易日历接口")
        try:
            frame = fn()
        except Exception as exc:  # noqa: BLE001 - third-party
            raise ProviderError(f"AKShare 交易日历失败：{exc}") from exc

        column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
        open_days = sorted(
            day for day in (_as_date(value) for value in frame[column].tolist()) if day
        )
        if not open_days:
            raise ProviderError("AKShare 交易日历为空")

        first, last = open_days[0], open_days[-1]
        lo = max(start, first) if start else first
        hi = min(end, last) if end else last
        open_set = set(open_days)
        output: list[tuple[date, bool]] = []
        cursor = lo
        while cursor <= hi:
            output.append((cursor, cursor in open_set))
            cursor += timedelta(days=1)
        return output
