"""Fundamental snapshot for A-shares and ETFs.

A-share data comes from Eastmoney's public data-centre endpoint. ETFs do not
have corporate financial statements, so only quote-based fields are returned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from app.config import settings
from app.db import load_fundamentals, save_fundamentals
from app.services.market import (
    EM_HEADERS,
    MarketDataError,
    _to_float,
    get_quote,
    get_valuation,
)
from app.services.symbols import asset_type, exchange, normalize_code

FIN_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _secucode(code: str) -> str:
    return f"{code}.{exchange(code)}"


async def _fetch_financial_row(code: str) -> Optional[dict[str, Any]]:
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "filter": f'(SECUCODE="{_secucode(code)}")',
        "pageNumber": "1",
        "pageSize": "4",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            response = await client.get(FIN_URL, params=params, headers=EM_HEADERS)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None

    rows = ((payload.get("result") or {}).get("data")) or []
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get("REPORT_DATE") or ""))


async def get_fundamentals(symbol: str) -> dict[str, Any]:
    code = normalize_code(symbol)
    if not code:
        raise MarketDataError("股票代码无效")

    cached = load_fundamentals(code, settings.fundamentals_ttl)
    if cached:
        cached.pop("_id", None)
        return cached

    quote = await get_quote(code)
    kind = asset_type(code)

    if kind == "etf":
        data = {
            "symbol": code,
            "name": quote.get("name"),
            "asset_type": "etf",
            "report_date": None,
            "revenue": None,
            "net_profit": None,
            "roe": None,
            "gross_margin": None,
            "debt_ratio": None,
            "eps": None,
            "bps": None,
            "pe": None,
            "pb": None,
            "total_mv": None,
            "source": quote.get("source", "sina"),
        }
        save_fundamentals(code, data)
        return data

    row = await _fetch_financial_row(code)
    valuation = await get_valuation(code)
    report_date = None
    revenue = net_profit = roe = gross_margin = debt_ratio = eps = bps = None
    if row:
        report_date = str(row.get("REPORT_DATE") or "")[:10] or None
        revenue = _to_float(row.get("TOTALOPERATEREVE"))
        net_profit = _to_float(row.get("PARENTNETPROFIT"))
        roe = _to_float(row.get("ROEJQ"))
        gross_margin = _to_float(row.get("XSMLL"))
        debt_ratio = _to_float(row.get("ZCFZL"))
        eps = _to_float(row.get("EPSJB"))
        bps = _to_float(row.get("BPS"))

    data = {
        "symbol": code,
        "name": quote.get("name"),
        "asset_type": "stock",
        "report_date": report_date,
        "revenue": revenue,
        "net_profit": net_profit,
        "roe": roe,
        "gross_margin": gross_margin,
        "debt_ratio": debt_ratio,
        "eps": eps,
        "bps": bps,
        "pe": valuation.get("pe"),
        "pb": valuation.get("pb"),
        "total_mv": valuation.get("total_mv"),
        "source": "eastmoney" if row else quote.get("source", "sina"),
    }
    save_fundamentals(code, data)
    return data