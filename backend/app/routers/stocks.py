from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.fundamentals import get_fundamentals
from app.services.indicators import compute_all
from app.services.market import MarketDataError, get_kline, get_quote
from app.services.symbols import normalize_code, search_stocks

router = APIRouter(prefix="/api", tags=["stocks"])


@router.get("/search")
async def search(q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=20)):
    return await search_stocks(q, limit=limit)


@router.get("/stocks/{symbol}/quote")
async def quote(symbol: str):
    try:
        return await get_quote(normalize_code(symbol))
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/stocks/{symbol}/chart")
async def chart(
    symbol: str,
    period: str = Query("day", pattern="^(day|week|month)$"),
    limit: int = Query(250, ge=30, le=500),
):
    code = normalize_code(symbol)
    try:
        candles = await get_kline(code, period, limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "symbol": code,
        "period": period,
        "candles": candles,
        "indicators": compute_all(candles),
    }


@router.get("/stocks/{symbol}/fundamentals")
async def fundamentals(symbol: str):
    try:
        return await get_fundamentals(normalize_code(symbol))
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc