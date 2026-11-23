"""Free market-data access: Sina first, Eastmoney as a fallback.

Only public, undocumented HTTP endpoints are used. Calls are cached and
rate-limited because these endpoints are shared public infrastructure.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from app.config import settings
from app.db import load_kline, save_kline
from app.services.symbols import (
    asset_type,
    eastmoney_secid,
    exchange,
    normalize_code,
    sina_symbol,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SINA_HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://finance.sina.com.cn"}
EM_HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://quote.eastmoney.com/"}

_SINA_LINE = re.compile(r'hq_str_([a-z]{2}\d{6})="([^"]*)"')

_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_request_lock = asyncio.Lock()
_last_request_at = 0.0


class MarketDataError(RuntimeError):
    pass


async def _throttle() -> None:
    global _last_request_at
    async with _request_lock:
        wait = 0.12 - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def _get_text(url: str, headers: dict[str, str], encoding: str = "utf-8") -> str:
    await _throttle()
    async with httpx.AsyncClient(timeout=settings.http_timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        if encoding.lower() in {"gbk", "gb2312"}:
            return response.content.decode("gbk", errors="ignore")
        return response.text


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- quotes
def _parse_sina_quote(code: str, payload: str) -> Optional[dict[str, Any]]:
    fields = payload.split(",")
    if len(fields) < 32:
        return None
    name = fields[0].strip()
    open_price = _to_float(fields[1])
    pre_close = _to_float(fields[2])
    price = _to_float(fields[3])
    high = _to_float(fields[4])
    low = _to_float(fields[5])
    volume_shares = _to_float(fields[8])
    amount = _to_float(fields[9])
    change = None
    pct_chg = None
    if price is not None and pre_close:
        change = round(price - pre_close, 4)
        pct_chg = round(change / pre_close * 100, 4)
    return {
        "symbol": code,
        "name": name,
        "asset_type": asset_type(code),
        "exchange": exchange(code),
        "price": price,
        "open": open_price,
        "pre_close": pre_close,
        "high": high,
        "low": low,
        "change": change,
        "pct_chg": pct_chg,
        "volume": (volume_shares / 100.0) if volume_shares is not None else None,
        "amount": amount,
        "trade_date": fields[30].strip() or None,
        "quote_time": fields[31].strip() or None,
        "source": "sina",
    }


async def _sina_quote(code: str) -> Optional[dict[str, Any]]:
    text = await _get_text(
        f"https://hq.sinajs.cn/list={sina_symbol(code)}", SINA_HEADERS, "gbk"
    )
    match = _SINA_LINE.search(text)
    if not match:
        return None
    return _parse_sina_quote(code, match.group(2))


async def _eastmoney_quote(code: str) -> Optional[dict[str, Any]]:
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={eastmoney_secid(code)}&fltt=2&invt=2"
        "&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f168,f162,f167,f116,f117"
    )
    text = await _get_text(url, EM_HEADERS)
    payload = json.loads(text)
    data = payload.get("data") or {}
    if not data:
        return None
    price = _to_float(data.get("f43"))
    pre_close = _to_float(data.get("f60"))
    change = _to_float(data.get("f169"))
    pct_chg = _to_float(data.get("f170"))
    if change is None and price is not None and pre_close:
        change = round(price - pre_close, 4)
    if pct_chg is None and change is not None and pre_close:
        pct_chg = round(change / pre_close * 100, 4)
    return {
        "symbol": code,
        "name": str(data.get("f58") or code),
        "asset_type": asset_type(code),
        "exchange": exchange(code),
        "price": price,
        "open": _to_float(data.get("f46")),
        "pre_close": pre_close,
        "high": _to_float(data.get("f44")),
        "low": _to_float(data.get("f45")),
        "change": change,
        "pct_chg": pct_chg,
        "volume": _to_float(data.get("f47")),
        "amount": _to_float(data.get("f48")),
        "turnover_rate": _to_float(data.get("f168")),
        "pe": _to_float(data.get("f162")),
        "pb": _to_float(data.get("f167")),
        "total_mv": _to_float(data.get("f116")),
        "circ_mv": _to_float(data.get("f117")),
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "quote_time": datetime.now().strftime("%H:%M:%S"),
        "source": "eastmoney",
    }


async def get_quote(symbol: str) -> dict[str, Any]:
    code = normalize_code(symbol)
    if not code:
        raise MarketDataError("股票代码无效")
    cached = _quote_cache.get(code)
    if cached and time.time() - cached[0] < settings.quote_ttl:
        return cached[1]

    errors: list[str] = []
    for loader in (_sina_quote, _eastmoney_quote):
        try:
            quote = await loader(code)
            if quote:
                _quote_cache[code] = (time.time(), quote)
                return quote
        except Exception as exc:
            errors.append(f"{loader.__name__}: {exc}")
    raise MarketDataError("实时行情获取失败：" + "；".join(errors))


async def get_valuation(symbol: str) -> dict[str, Any]:
    """Valuation fields are only exposed by the Eastmoney quote endpoint."""
    code = normalize_code(symbol)
    try:
        quote = await _eastmoney_quote(code)
        if quote:
            return {
                "pe": quote.get("pe"),
                "pb": quote.get("pb"),
                "total_mv": quote.get("total_mv"),
                "circ_mv": quote.get("circ_mv"),
                "turnover_rate": quote.get("turnover_rate"),
            }
    except Exception:
        pass
    return {"pe": None, "pb": None, "total_mv": None, "circ_mv": None, "turnover_rate": None}


# ---------------------------------------------------------------------- kline
def _resample(candles: list[dict[str, Any]], period: str, limit: int) -> list[dict[str, Any]]:
    if period == "day":
        return candles[-limit:]

    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in candles:
        try:
            day = datetime.strptime(item["trade_date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        key = (day.year, day.isocalendar().week) if period == "week" else (day.year, day.month)
        buckets.setdefault(key, []).append(item)

    merged: list[dict[str, Any]] = []
    for key in sorted(buckets):
        group = buckets[key]
        merged.append(
            {
                "trade_date": group[-1]["trade_date"],
                "open": group[0]["open"],
                "high": max((g["high"] for g in group if g["high"] is not None), default=None),
                "low": min((g["low"] for g in group if g["low"] is not None), default=None),
                "close": group[-1]["close"],
                "volume": sum(g["volume"] or 0 for g in group),
                "amount": sum(g["amount"] or 0 for g in group),
            }
        )
    return merged[-limit:]


async def _sina_kline(code: str, datalen: int) -> list[dict[str, Any]]:
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={sina_symbol(code)}&scale=240&ma=no&datalen={datalen}"
    )
    text = await _get_text(url, SINA_HEADERS)
    rows = json.loads(text)
    result: list[dict[str, Any]] = []
    for row in rows or []:
        result.append(
            {
                "trade_date": str(row.get("day") or "")[:10],
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "volume": (_to_float(row.get("volume")) or 0) / 100.0,
                "amount": 0.0,
            }
        )
    return [item for item in result if item["trade_date"] and item["close"] is not None]


async def get_kline(symbol: str, period: str = "day", limit: int = 250) -> list[dict[str, Any]]:
    code = normalize_code(symbol)
    if not code:
        raise MarketDataError("股票代码无效")
    period = period if period in {"day", "week", "month"} else "day"
    limit = max(30, min(int(limit), 500))

    cached = load_kline(code, period, settings.kline_ttl)
    if cached:
        return cached[-limit:]

    multiplier = {"day": 1, "week": 6, "month": 25}[period]
    datalen = min(1023, max(limit * multiplier + 30, 120))
    try:
        candles = await _sina_kline(code, datalen)
    except Exception as exc:
        raise MarketDataError(f"历史行情获取失败：{exc}") from exc

    if not candles:
        raise MarketDataError("历史行情为空")
    candles = _resample(candles, period, limit)
    save_kline(code, period, candles, "sina")
    return candles