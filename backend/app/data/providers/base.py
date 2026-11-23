"""Data-source providers for the market panel.

Each provider exposes the same small surface:

* ``trade_calendar`` – trading days (and whether each date is open)
* ``stock_universe`` – code, name, board, industry
* ``daily_bars`` – raw OHLCV for a symbol or a whole trade date
* ``adj_factors`` – cumulative adjustment factors
* ``daily_basic`` – valuation / turnover / market cap
* ``spot_snapshot`` – today's cross-section of the whole market

Providers declare :class:`Capabilities` so the sync layer can pick the best
available source instead of hard-coding a priority order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.services.symbols import asset_type, exchange


class ProviderError(RuntimeError):
    """Raised when a provider cannot serve a request."""


class ProviderUnavailable(ProviderError):
    """Raised when the provider's dependency or credential is missing."""


@dataclass(frozen=True)
class Capabilities:
    calendar: bool = False
    universe: bool = False
    history: bool = False
    whole_market_day: bool = False
    adj_factors: bool = False
    daily_basic: bool = False
    spot: bool = False


@dataclass
class ProviderInfo:
    name: str
    label: str
    capabilities: Capabilities
    note: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


BOARD_PREFIXES = (
    (("688", "689"), "科创板"),
    (("300", "301"), "创业板"),
    (("60",), "沪市主板"),
    (("000", "001", "002", "003"), "深市主板"),
    (("8", "4", "92"), "北交所"),
)


def board_of(code: str) -> str:
    if asset_type(code) == "etf":
        return "ETF"
    for prefixes, label in BOARD_PREFIXES:
        if code.startswith(prefixes):
            return label
    return "其他"


def ts_code(code: str) -> str:
    suffix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}[exchange(code)]
    return f"{code}.{suffix}"


def normalize_board(market: Optional[str], code: str) -> str:
    """Tushare's ``market`` field is authoritative when present."""
    text = (market or "").strip()
    if text in {"主板", "创业板", "科创板", "北交所"}:
        return {"主板": "沪市主板" if exchange(code) == "SH" else "深市主板"}.get(text, text)
    return board_of(code)


class Provider:
    """Interface only; concrete providers override what they support."""

    name: str = "base"
    label: str = "base"
    capabilities: Capabilities = Capabilities()

    def info(self) -> ProviderInfo:
        return ProviderInfo(self.name, self.label, self.capabilities)

    def available(self) -> tuple[bool, str]:
        return False, "未实现"

    # ------------------------------------------------------------ capabilities
    def trade_calendar(
        self, start: Optional[date] = None, end: Optional[date] = None
    ) -> list[tuple[date, bool]]:
        raise ProviderUnavailable(f"{self.label} 不支持交易日历")

    def stock_universe(self, include_delisted: bool = False) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持股票列表")

    def daily_bars(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        adjust: str = "none",
    ) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持单只历史行情")

    def whole_market_day(self, trade_date: date) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持全市场单日行情")

    def adj_factors(
        self, symbol: str, start: Optional[date] = None, end: Optional[date] = None
    ) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持复权因子")

    def daily_basic(self, trade_date: date) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持估值指标")

    def spot_snapshot(self) -> list[dict[str, Any]]:
        raise ProviderUnavailable(f"{self.label} 不支持全市场快照")
