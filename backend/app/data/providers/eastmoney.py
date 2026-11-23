"""Eastmoney provider: per-symbol history with adjustment factors.

``push2his.eastmoney.com`` serves the same daily series with ``fqt=0`` (raw),
``fqt=1`` (前复权) and ``fqt=2`` (后复权). Fetching raw + hfq lets us derive the
cumulative adjustment factor exactly: ``factor = hfq_close / raw_close``.

The whole-market ``clist`` endpoint on this host is intermittently refused, so
this provider deliberately does not claim ``spot`` or ``whole_market_day``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import httpx

from app.data.providers.base import Capabilities, Provider, ProviderError
from app.services.symbols import eastmoney_secid, normalize_code

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

FQT = {"none": 0, "qfq": 1, "hfq": 2}


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class EastmoneyProvider(Provider):
    name = "eastmoney"
    label = "东方财富"
    capabilities = Capabilities(history=True, adj_factors=True)

    def __init__(self, timeout: float = 25.0) -> None:
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        return True, ""

    def _kline(
        self,
        code: str,
        start: Optional[date],
        end: Optional[date],
        adjust: str,
        limit: int = 10000,
    ) -> list[list[str]]:
        params = {
            "secid": eastmoney_secid(code),
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": 101,
            "fqt": FQT.get(adjust, 0),
            "beg": start.strftime("%Y%m%d") if start else "0",
            "end": end.strftime("%Y%m%d") if end else "20500101",
            "lmt": limit,
        }
        try:
            with httpx.Client(timeout=self.timeout, headers=HEADERS) as client:
                response = client.get(KLINE_URL, params=params)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - network layer
            raise ProviderError(f"东财 K 线请求失败（{code}）：{exc}") from exc
        data = body.get("data") or {}
        return data.get("klines") or []

    @staticmethod
    def _row_to_bar(code: str, row: str) -> Optional[dict[str, Any]]:
        parts = row.split(",")
        if len(parts) < 7:
            return None
        day, open_, close, high, low, volume, amount = parts[:7]
        change = _number(parts[9]) if len(parts) > 9 else None
        close_value = _number(close)
        return {
            "symbol": code,
            "trade_date": day,
            "open": _number(open_),
            "high": _number(high),
            "low": _number(low),
            "close": close_value,
            "pre_close": round(close_value - change, 4)
            if close_value is not None and change is not None
            else None,
            # Eastmoney volume is in 手
            "volume": (_number(volume) or 0) * 100.0,
            "amount": _number(amount),
        }

    def daily_bars(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        adjust: str = "none",
    ) -> list[dict[str, Any]]:
        code = normalize_code(symbol)
        rows = self._kline(code, start, end, adjust)
        bars = [bar for bar in (self._row_to_bar(code, row) for row in rows) if bar]
        bars.sort(key=lambda item: item["trade_date"])
        for bar in bars:
            bar["source"] = self.name
        return bars

    def daily_with_factors(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        """Raw bars with an ``adj_factor`` derived from the 后复权 series."""
        code = normalize_code(symbol)
        raw_rows = self._kline(code, start, end, "none")
        if not raw_rows:
            return []
        hfq_rows = self._kline(code, start, end, "hfq")

        hfq_close: dict[str, float] = {}
        for row in hfq_rows:
            parts = row.split(",")
            if len(parts) >= 3:
                value = _number(parts[2])
                if value:
                    hfq_close[parts[0]] = value

        bars = []
        for row in raw_rows:
            bar = self._row_to_bar(code, row)
            if not bar:
                continue
            adjusted = hfq_close.get(bar["trade_date"])
            if adjusted is not None and bar["close"]:
                bar["adj_factor"] = round(adjusted / bar["close"], 8)
            bars.append(bar)
        bars.sort(key=lambda item: item["trade_date"])
        for bar in bars:
            bar.setdefault("adj_factor", None)
            bar["source"] = self.name
        return bars

    def adj_factors(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "symbol": bar["symbol"],
                "trade_date": bar["trade_date"],
                "adj_factor": bar["adj_factor"],
            }
            for bar in self.daily_with_factors(symbol, start, end)
            if bar.get("adj_factor")
        ]
