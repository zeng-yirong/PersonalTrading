"""Intraday minute bars for the latest session, fetched on demand and cached.

The panel only stores daily bars, so "today's price line" has to come from a
public quote endpoint. Eastmoney's trends2 is the primary source (1-minute
close + 均价); if it fails we fall back to the cached copy and say so instead of
showing an empty chart.

Caching is deliberately short: 60 seconds while the market is open, and a long
TTL after the close (the session cannot change any more).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, time as _time
from typing import Any, Optional

import httpx

from app.db import connection, now_iso

SOURCE = "eastmoney"
URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
SINA_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/"
    "CN_MarketDataService.getKLineData"
)
FIELDS1 = "f1,f2,f3,f4,f5,f6,f7,f8"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}
OPEN_SECONDS = 60
CLOSED_SECONDS = 12 * 3600


def _secid(symbol: str) -> str:
    """Eastmoney's market prefix: 1 = 上交所, 0 = 深交所/北交所."""
    return ("1." if symbol.startswith(("6", "5", "9")) else "0.") + symbol


def _is_session_now() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return _time(9, 15) <= now.time() <= _time(15, 5)


def _parse(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or {}
    trends = data.get("trends") or []
    points = []
    for item in trends:
        parts = str(item).split(",")
        if len(parts) < 8:
            continue
        stamp, _open, close, _high, _low, volume, amount, avg = parts[:8]
        try:
            points.append(
                {
                    "time": stamp[11:16],
                    "price": float(close),
                    "avg": float(avg) if avg not in ("", "-") else None,
                    "volume": float(volume) if volume not in ("", "-") else 0.0,
                    "amount": float(amount) if amount not in ("", "-") else 0.0,
                }
            )
        except ValueError:
            continue
    code = str(data.get("code") or "")
    return {
        "symbol": code,
        "name": data.get("name") or "",
        "trade_date": trends[0].split(" ")[0] if trends else None,
        "pre_close": data.get("preClose") or data.get("preSettlement"),
        "points": points,
        "source": SOURCE,
        "fetched_at": now_iso(),
    }


def _fetch_eastmoney(symbol: str) -> Optional[dict[str, Any]]:
    params = {
        "secid": _secid(symbol),
        "fields1": FIELDS1,
        "fields2": FIELDS2,
        "iscr": 0,
        "ndays": 1,
    }
    for attempt in range(2):
        try:
            response = httpx.get(URL, params=params, timeout=8, headers=HEADERS)
            response.raise_for_status()
            payload = _parse(response.json())
            if payload["points"]:
                return payload
        except Exception:  # noqa: BLE001 - try the next source / attempt
            time.sleep(0.4 * (attempt + 1))
    return None


def _fetch_sina(symbol: str) -> Optional[dict[str, Any]]:
    prefix = "sh" if symbol.startswith(("6", "5", "9")) else "sz"
    params = {"symbol": f"{prefix}{symbol}", "scale": 1, "ma": "no", "datalen": 240}
    try:
        response = httpx.get(SINA_URL, params=params, timeout=10, headers=HEADERS)
        response.raise_for_status()
        text = response.text
        start, end = text.find("("), text.rfind(")")
        if start < 0 or end <= start:
            return None
        bars = json.loads(text[start + 1 : end])
        # 未知代码/北交所：新浪返回 var _x=(null)，不是列表。
        if not isinstance(bars, list):
            return None
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for bar in bars:
        try:
            close = float(bar["close"])
            # 新浪的 volume 单位是「股」，东财是「手」；统一成手。
            volume = float(bar.get("volume") or 0) / 100.0
            amount = float(bar.get("amount") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        stamp = str(bar.get("day") or "")
        if len(stamp) < 16:
            continue
        rows.append((stamp[:10], stamp[11:16], close, volume, amount))
    if not rows:
        return None
    # Sina may return more than one session; keep the most recent date only.
    last_date = max(row[0] for row in rows)
    points = []
    cum_value = 0.0
    cum_volume = 0.0
    for day, clock, close, volume, amount in rows:
        if day != last_date:
            continue
        cum_value += close * volume
        cum_volume += volume
        points.append(
            {
                "time": clock,
                "price": close,
                "avg": round(cum_value / cum_volume, 4) if cum_volume else None,
                "volume": volume,
                "amount": amount,
            }
        )
    if not points:
        return None
    return {
        "symbol": symbol,
        "name": "",
        "trade_date": last_date,
        "pre_close": None,
        "points": points,
        "source": "sina",
        "fetched_at": now_iso(),
    }


def fetch(symbol: str) -> Optional[dict[str, Any]]:
    """Return the latest session's minute bars, or the cached copy on failure."""
    payload = _fetch_eastmoney(symbol) or _fetch_sina(symbol)
    if payload is not None:
        _remember(symbol, payload)
        return payload
    fallback = cached(symbol)
    if fallback is not None:
        fallback["stale"] = True
    return fallback


def _ttl_seconds() -> int:
    return OPEN_SECONDS if _is_session_now() else CLOSED_SECONDS


def _remember(symbol: str, payload: dict[str, Any]) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO intraday_cache(symbol, trade_date, fetched_at, payload) "
            "VALUES(?, ?, ?, ?)",
            (
                symbol,
                payload.get("trade_date"),
                payload.get("fetched_at") or now_iso(),
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def cached(symbol: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT trade_date, fetched_at, payload FROM intraday_cache WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    stamp = datetime.fromisoformat(row["fetched_at"])
    if (datetime.now(stamp.tzinfo) - stamp).total_seconds() > _ttl_seconds():
        return None
    payload = json.loads(row["payload"])
    return payload


def latest_or_cached(symbol: str) -> Optional[dict[str, Any]]:
    """Cache-first read used by the detail endpoint (avoids a fetch per render)."""
    hit = cached(symbol)
    return hit if hit is not None else fetch(symbol)
