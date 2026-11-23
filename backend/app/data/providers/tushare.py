"""Tushare Pro provider (raw HTTP, no ``tushare`` package dependency).

Works against the official endpoint and against Tushare-compatible gateways:
``PERSONALTRADING_TUSHARE_BASE_URL`` selects the host, everything else is the
same protocol.

A whole-market day is a single request (``daily(trade_date=...)``), which is why
this provider is the preferred one for building the panel: ten years of history
is a few thousand calls, not a few thousand *stocks* times N.

The shared sliding-window limiter keeps us under the gateway's 200/min ceiling
even when the backfill runs several datasets concurrently.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date, datetime
from typing import Any, Optional, Sequence

import httpx

from app.config import settings
from app.data.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderUnavailable,
    normalize_board,
    ts_code,
)
from app.services.symbols import asset_type, exchange, normalize_code

API_URL = "https://api.tushare.pro"

ERROR_HINTS = {
    2003: "该接口未开通权限（api not purchased）",
    40001: "参数错误",
    40002: "权限不足（账号积分不够），可在 Tushare 个人中心查看接口所需积分",
    40101: "token 无效，请到 https://tushare.pro/user/token 重新复制",
    40203: "请求频率超限，请降低并发或稍后重试",
    42901: "已超 token 每日请求上限，需等到次日配额恢复",
    50301: "上游数据源临时不可用",
    50302: "上游触发保护，临时限流",
}

# Errors that mean "the token itself is bad" — safe to cache for the process.
PERMANENT_CODES = {40101, 40002}
# Errors that will pass on their own — never cache, and stop a backfill early.
QUOTA_CODES = {42901}
# Upstream hiccups: worth a short backoff, then give up on that part.
RETRYABLE_CODES = {40203, 50301, 50302}


class QuotaExceeded(ProviderError):
    """Daily request quota is gone: retrying today is pointless."""


class RateLimiter:
    """Sliding one-minute window shared by every call in the process."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, int(per_minute))
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] > 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.per_minute:
                    self._calls.append(now)
                    return
                wait = 60.0 - (now - self._calls[0]) + 0.05
            time.sleep(max(0.05, wait))


_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(settings.tushare_rate_per_minute)
    return _limiter


def _ymd(value: Optional[date]) -> str:
    return value.strftime("%Y%m%d") if value else ""


def _parse_ymd(text: Any) -> Optional[date]:
    raw = str(text or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TushareProvider(Provider):
    name = "tushare"
    label = "Tushare Pro"
    capabilities = Capabilities(
        calendar=True,
        universe=True,
        history=True,
        whole_market_day=True,
        adj_factors=True,
        daily_basic=True,
    )

    # token -> (ok, reason); a rejected token costs one request per process.
    _preflight_cache: dict[str, tuple[bool, str]] = {}

    def __init__(
        self,
        token: Optional[str] = None,
        timeout: float = 60.0,
        base_url: Optional[str] = None,
    ) -> None:
        self.token = (token if token is not None else settings.tushare_token).strip()
        self.timeout = timeout
        self.base_url = (base_url or settings.tushare_base_url or API_URL).rstrip("/")

    def available(self) -> tuple[bool, str]:
        if not self.token:
            return False, "未配置 PERSONALTRADING_TUSHARE_TOKEN"
        cached = self._preflight_cache.get(self.token)
        if cached is not None:
            return cached

        today = datetime.now().strftime("%Y%m%d")
        payload = {
            "api_name": "trade_cal",
            "token": self.token,
            "params": {"exchange": "SSE", "start_date": today, "end_date": today},
            "fields": "cal_date",
        }
        try:
            with httpx.Client(timeout=20) as client:
                body = client.post(self.base_url, json=payload).json()
        except Exception as exc:  # noqa: BLE001 - network layer
            # A network hiccup says nothing about the token: report it without
            # caching, or one blip would disable Tushare for the process lifetime.
            return False, f"Tushare 探测失败：{exc}"
        code = body.get("code")
        if code == 0:
            result = (True, "")
        else:
            hint = ERROR_HINTS.get(code, str(body.get("msg") or ""))
            result = (False, f"Tushare {code}：{hint}")
        # Only a definitive token problem is worth caching; quota and rate
        # errors resolve by themselves and must not stick to the process.
        if code == 0 or code in PERMANENT_CODES:
            self._preflight_cache[self.token] = result
        return result

    def query(
        self, api_name: str, params: Optional[dict[str, Any]] = None, fields: str = ""
    ) -> list[dict[str, Any]]:
        """Public generic call, used by the dataset backfill engine."""
        return self._call(api_name, params or {}, fields)

    def token_status(self) -> Optional[dict[str, Any]]:
        try:
            rows = self._call("token_status", {}, "")
        except ProviderError:
            return None
        return rows[0] if rows else None

    def _call(self, api_name: str, params: dict[str, Any], fields: str = "") -> list[dict[str, Any]]:
        if not self.token:
            raise ProviderUnavailable("未配置 PERSONALTRADING_TUSHARE_TOKEN")
        payload = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields,
        }
        attempts = 3
        for attempt in range(1, attempts + 1):
            get_rate_limiter().acquire()
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(self.base_url, json=payload)
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - network layer
                if attempt == attempts:
                    raise ProviderError(f"Tushare {api_name} 请求失败：{exc}") from exc
                time.sleep(2 * attempt)
                continue

            code = body.get("code")
            if code == 0:
                break
            hint = ERROR_HINTS.get(code, str(body.get("msg") or ""))
            if code in QUOTA_CODES:
                # No retry: the quota resets tomorrow, not in five seconds.
                raise QuotaExceeded(f"Tushare {api_name} 返回 {code}：{hint}")
            # 40203 = rate limited, 50301/50302 = upstream hiccup; all worth a
            # short backoff. 40101 is permanent, 2003 is not purchased.
            if code in RETRYABLE_CODES and attempt < attempts:
                time.sleep(5 * attempt)
                continue
            raise ProviderError(f"Tushare {api_name} 返回 {code}：{hint}")

        data = body.get("data") or {}
        names = data.get("fields") or []
        records = []
        for item in data.get("items") or []:
            records.append(dict(zip(names, item)))
        return records

    # ------------------------------------------------------------ capabilities
    def trade_calendar(
        self, start: Optional[date] = None, end: Optional[date] = None
    ) -> list[tuple[date, bool]]:
        rows = self._call(
            "trade_cal",
            {"exchange": "SSE", "start_date": _ymd(start), "end_date": _ymd(end)},
            "cal_date,is_open",
        )
        output: list[tuple[date, bool]] = []
        for row in rows:
            day = _parse_ymd(row.get("cal_date"))
            if day:
                output.append((day, str(row.get("is_open")) == "1"))
        output.sort()
        return output

    def stock_universe(self, include_delisted: bool = False) -> list[dict[str, Any]]:
        statuses: Sequence[str] = ("L", "D", "P") if include_delisted else ("L",)
        rows: list[dict[str, Any]] = []
        for status in statuses:
            rows.extend(
                self._call(
                    "stock_basic",
                    {"exchange": "", "list_status": status},
                    "ts_code,symbol,name,area,industry,market,list_date,delist_date,is_hs",
                )
            )

        output = []
        for row in rows:
            code = normalize_code(row.get("symbol") or row.get("ts_code"))
            if not code:
                continue
            list_date = _parse_ymd(row.get("list_date"))
            delist_date = _parse_ymd(row.get("delist_date"))
            output.append(
                {
                    "symbol": code,
                    "ts_code": row.get("ts_code") or ts_code(code),
                    "name": row.get("name"),
                    "exchange": exchange(code),
                    "asset_type": asset_type(code),
                    "board": normalize_board(row.get("market"), code),
                    "industry": row.get("industry"),
                    "list_date": list_date,
                    "delist_date": delist_date,
                    "is_active": delist_date is None,
                }
            )
        return output

    def whole_market_day(self, trade_date: date) -> list[dict[str, Any]]:
        rows = self._call(
            "daily",
            {"trade_date": _ymd(trade_date)},
            "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
        )
        output = []
        for row in rows:
            code = normalize_code(row.get("ts_code"))
            day = _parse_ymd(row.get("trade_date")) or trade_date
            if not code:
                continue
            output.append(
                {
                    "symbol": code,
                    "trade_date": day,
                    "open": _number(row.get("open")),
                    "high": _number(row.get("high")),
                    "low": _number(row.get("low")),
                    "close": _number(row.get("close")),
                    "pre_close": _number(row.get("pre_close")),
                    # Tushare: vol 手 -> 股, amount 千元 -> 元
                    "volume": (_number(row.get("vol")) or 0) * 100.0,
                    "amount": (_number(row.get("amount")) or 0) * 1000.0,
                    "source": self.name,
                }
            )
        return output

    def daily_bars(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        adjust: str = "none",
    ) -> list[dict[str, Any]]:
        code = normalize_code(symbol)
        rows = self._call(
            "daily",
            {"ts_code": ts_code(code), "start_date": _ymd(start), "end_date": _ymd(end)},
            "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
        )
        output = []
        for row in rows:
            day = _parse_ymd(row.get("trade_date"))
            if not day:
                continue
            output.append(
                {
                    "symbol": code,
                    "trade_date": day,
                    "open": _number(row.get("open")),
                    "high": _number(row.get("high")),
                    "low": _number(row.get("low")),
                    "close": _number(row.get("close")),
                    "pre_close": _number(row.get("pre_close")),
                    "volume": (_number(row.get("vol")) or 0) * 100.0,
                    "amount": (_number(row.get("amount")) or 0) * 1000.0,
                    "source": self.name,
                }
            )
        output.sort(key=lambda item: item["trade_date"])
        return output

    def adj_factors(
        self, symbol: str, start: Optional[date] = None, end: Optional[date] = None
    ) -> list[dict[str, Any]]:
        code = normalize_code(symbol)
        rows = self._call(
            "adj_factor",
            {"ts_code": ts_code(code), "start_date": _ymd(start), "end_date": _ymd(end)},
            "ts_code,trade_date,adj_factor",
        )
        output = []
        for row in rows:
            day = _parse_ymd(row.get("trade_date"))
            factor = _number(row.get("adj_factor"))
            if day and factor:
                output.append({"symbol": code, "trade_date": day, "adj_factor": factor})
        output.sort(key=lambda item: item["trade_date"])
        return output

    def daily_basic(self, trade_date: date) -> list[dict[str, Any]]:
        rows = self._call(
            "daily_basic",
            {"trade_date": _ymd(trade_date)},
            "ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps_ttm,"
            "dv_ttm,total_mv,circ_mv",
        )
        output = []
        for row in rows:
            code = normalize_code(row.get("ts_code"))
            day = _parse_ymd(row.get("trade_date")) or trade_date
            if not code:
                continue
            output.append(
                {
                    "symbol": code,
                    "trade_date": day,
                    "turnover_rate": _number(row.get("turnover_rate")),
                    "volume_ratio": _number(row.get("volume_ratio")),
                    "pe": _number(row.get("pe")),
                    "pe_ttm": _number(row.get("pe_ttm")),
                    "pb": _number(row.get("pb")),
                    "ps_ttm": _number(row.get("ps_ttm")),
                    "dv_ttm": _number(row.get("dv_ttm")),
                    # 万元 -> 元
                    "total_mv": (_number(row.get("total_mv")) or 0) * 10000.0 or None,
                    "circ_mv": (_number(row.get("circ_mv")) or 0) * 10000.0 or None,
                }
            )
        return output
