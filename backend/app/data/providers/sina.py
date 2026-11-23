"""Sina provider: whole-market cross-sections and raw daily history.

Sina's ``Market_Center.getHQNodeData`` endpoint returns the entire A-share (and
ETF) market paginated at 100 symbols per page, including price, volume, PE/PB,
market cap and turnover. That makes it the most reliable way to build a
whole-market snapshot here: Eastmoney's ``clist`` endpoint is intermittently
refused, and its ``push2his`` history endpoint starts refusing connections
after a few dozen symbols.

``getKLineData`` serves up to 1023 daily bars (about four years). Its prices are
**raw** (un-adjusted) — verified against Eastmoney's ``fqt=0`` series — so it
composes cleanly with the panel's ``adj_factor`` design.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.data.providers.base import Capabilities, Provider, ProviderError, board_of
from app.services.symbols import asset_type, exchange, normalize_code, sina_symbol

MARKET_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}

MAX_KLINE_BARS = 1023

# Sina's payload is JSON with unquoted keys.
_KEY_QUOTE = re.compile(r"([{,])\s*([A-Za-z_]\w*)\s*:")

PAGE_SIZE = 100
MAX_PAGES = 200
THROTTLE_SECONDS = 0.12


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_payload(text: str) -> list[dict[str, Any]]:
    body = (text or "").strip()
    if not body or body in {"null", "[]"}:
        return []
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_KEY_QUOTE.sub(r'\1"\2":', body))
    except json.JSONDecodeError as exc:
        raise ProviderError(f"新浪返回内容无法解析：{body[:120]}") from exc


class SinaProvider(Provider):
    name = "sina"
    label = "新浪财经"
    capabilities = Capabilities(universe=True, spot=True, history=True)

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        return True, ""

    def _page(self, client: httpx.Client, node: str, page: int, num: int = PAGE_SIZE) -> list[dict]:
        params = {
            "page": page,
            "num": num,
            "sort": "symbol",
            "asc": 1,
            "node": node,
            "_s_r_a": "page",
        }
        response = client.get(MARKET_URL, params=params, headers=HEADERS)
        response.raise_for_status()
        response.encoding = "gbk"
        return _parse_payload(response.text)

    def _sweep(self, node: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout, headers=HEADERS) as client:
            for page in range(1, MAX_PAGES + 1):
                time.sleep(THROTTLE_SECONDS)
                try:
                    rows = self._page(client, node, page)
                except Exception as exc:  # noqa: BLE001 - network layer
                    if collected:
                        break  # keep what we have; a later sync retries the rest
                    raise ProviderError(f"新浪 {node} 第 {page} 页失败：{exc}") from exc
                if not rows:
                    break
                collected.extend(rows)
        return collected

    # ------------------------------------------------------------ capabilities
    def daily_bars(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        adjust: str = "none",
    ) -> list[dict[str, Any]]:
        """Raw daily bars; Sina caps a response at 1023 bars (~4 years)."""
        code = normalize_code(symbol)
        if not code:
            raise ProviderError("股票代码无效")

        datalen = MAX_KLINE_BARS
        if start:
            # Trading days are ~70% of calendar days; leave a small margin.
            needed = int((datetime.now().date() - start).days * 0.72) + 30
            datalen = max(30, min(MAX_KLINE_BARS, needed))

        time.sleep(THROTTLE_SECONDS)
        params = {"symbol": sina_symbol(code), "scale": 240, "ma": "no", "datalen": datalen}
        try:
            with httpx.Client(timeout=self.timeout, headers=HEADERS) as client:
                response = client.get(KLINE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 - network layer
            raise ProviderError(f"新浪 K 线请求失败（{code}）：{exc}") from exc

        bars: list[dict[str, Any]] = []
        for row in payload or []:
            day = str(row.get("day") or "")[:10]
            try:
                trade_date = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                continue
            close = _number(row.get("close"))
            if close is None:
                continue
            bars.append(
                {
                    "symbol": code,
                    "trade_date": trade_date,
                    "open": _number(row.get("open")),
                    "high": _number(row.get("high")),
                    "low": _number(row.get("low")),
                    "close": close,
                    "pre_close": None,
                    "volume": _number(row.get("volume")),
                    "amount": None,
                    "adj_factor": None,
                    "source": self.name,
                }
            )
        if start:
            bars = [bar for bar in bars if bar["trade_date"] >= start]
        if end:
            bars = [bar for bar in bars if bar["trade_date"] <= end]
        bars.sort(key=lambda item: item["trade_date"])
        return bars

    def stock_universe(self, include_delisted: bool = False) -> list[dict[str, Any]]:
        symbols: list[dict[str, Any]] = []
        for node in ("hs_a", "etf_hq_fund"):
            for row in self._sweep(node):
                code = normalize_code(row.get("code") or row.get("symbol"))
                if not code:
                    continue
                symbols.append(
                    {
                        "symbol": code,
                        "ts_code": None,
                        "name": str(row.get("name") or code).strip(),
                        "exchange": exchange(code),
                        "asset_type": asset_type(code),
                        "board": board_of(code),
                        "industry": None,
                        "list_date": None,
                        "delist_date": None,
                        "is_active": True,
                    }
                )
        return symbols

    def spot_snapshot(self) -> list[dict[str, Any]]:
        """Today's cross-section for every listed A-share and ETF."""
        now = datetime.now()
        today = date(now.year, now.month, now.day)
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in ("hs_a", "etf_hq_fund"):
            for row in self._sweep(node):
                code = normalize_code(row.get("code") or row.get("symbol"))
                if not code or code in seen:
                    continue
                price = _number(row.get("trade"))
                volume = _number(row.get("volume"))
                # A suspended symbol prints trade=0/volume=0; storing it would
                # inject a fake zero bar into the panel.
                if not price or not volume:
                    continue
                seen.add(code)
                pre_close = _number(row.get("settlement"))
                change = _number(row.get("pricechange"))
                if pre_close in (None, 0) and price is not None and change is not None:
                    pre_close = price - change
                total_mv = _number(row.get("mktcap"))
                circ_mv = _number(row.get("nmc"))
                pe = _number(row.get("per"))
                pb = _number(row.get("pb"))
                output.append(
                    {
                        "symbol": code,
                        "name": str(row.get("name") or code).strip(),
                        "asset_type": asset_type(code),
                        "board": board_of(code),
                        "trade_date": today,
                        "open": _number(row.get("open")),
                        "high": _number(row.get("high")),
                        "low": _number(row.get("low")),
                        "close": price,
                        "pre_close": pre_close,
                        "volume": volume,
                        "amount": _number(row.get("amount")),
                        "pe": pe or None,
                        "pb": pb or None,
                        # Sina reports 市值 in 万元
                        "total_mv": total_mv * 10000.0 if total_mv else None,
                        "circ_mv": circ_mv * 10000.0 if circ_mv else None,
                        "turnover_rate": _number(row.get("turnoverratio")),
                        "source": self.name,
                    }
                )
        return output
