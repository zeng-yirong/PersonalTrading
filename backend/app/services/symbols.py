"""Stock / ETF symbol helpers and Eastmoney symbol search."""

from __future__ import annotations

import re
from typing import Any

import httpx

SH_PREFIXES = ("60", "68", "90", "50", "51", "52", "56", "58", "11")
SZ_PREFIXES = ("00", "30", "20", "15", "16", "12")
BJ_PREFIXES = ("8", "4", "92")
ETF_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")


def normalize_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    return digits[-6:].zfill(6)


def exchange(code: str) -> str:
    if code.startswith(SH_PREFIXES):
        return "SH"
    if code.startswith(BJ_PREFIXES):
        return "BJ"
    return "SZ"


def asset_type(code: str) -> str:
    return "etf" if code.startswith(ETF_PREFIXES) else "stock"


def sina_symbol(code: str) -> str:
    return f"{exchange(code).lower()}{code}"


def eastmoney_secid(code: str) -> str:
    return f"{'1' if exchange(code) == 'SH' else '0'}.{code}"


def market_label(code: str) -> str:
    ex = exchange(code)
    if ex == "SH":
        return "沪市"
    if ex == "BJ":
        return "北交所"
    return "深市"


def _classify(item: dict[str, Any]) -> str | None:
    code = normalize_code(item.get("Code") or item.get("UnifiedCode"))
    if not code:
        return None
    classify = str(item.get("Classify") or "")
    security_type = str(item.get("SecurityTypeName") or "")
    if "Index" in classify or "指数" in security_type:
        return None
    if classify in {"AStock", "Fund", "ETF"} or "ETF" in security_type.upper():
        return asset_type(code)
    if "A" in security_type or "基金" in security_type:
        return asset_type(code)
    return None


async def search_stocks(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Search A-shares and ETFs through Eastmoney's public suggestion API."""
    query = query.strip()
    if not query:
        return []
    url = "https://searchapi.eastmoney.com/api/suggest/get"
    params = {
        "input": query,
        "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": str(max(limit * 3, 10)),
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []

    rows = ((payload.get("QuotationCodeTable") or {}).get("Data")) or []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        kind = _classify(item)
        if not kind:
            continue
        code = normalize_code(item.get("Code") or item.get("UnifiedCode"))
        if not code or code in seen:
            continue
        seen.add(code)
        results.append(
            {
                "symbol": code,
                "name": str(item.get("Name") or code),
                "asset_type": kind,
                "exchange": exchange(code),
                "market": market_label(code),
            }
        )
        if len(results) >= limit:
            break
    return results