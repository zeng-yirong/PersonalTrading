from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.fundamentals import get_fundamentals
from app.services.indicators import compute_all
from app.services.market import MarketDataError, get_kline, get_quote
from app.services.symbols import normalize_code, search_stocks

router = APIRouter(prefix="/api", tags=["stocks"])


@router.get("/stocks/{symbol}/detail")
async def detail(symbol: str):
    from starlette.concurrency import run_in_threadpool

    from app.services import detail as detail_service

    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")
    payload = await run_in_threadpool(detail_service.detail, code)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"没有 {code} 这只股票")
    return payload


@router.get("/stocks/{symbol}/history")
async def history(
    symbol: str,
    span: str = Query("1y", alias="range"),
    period: str = Query("day"),
):
    from starlette.concurrency import run_in_threadpool

    from app.services import detail as detail_service

    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")
    if span not in detail_service.RANGES:
        raise HTTPException(
            status_code=400, detail=f"range 只能是 {'/'.join(detail_service.RANGES)}"
        )
    if period not in detail_service.PERIODS:
        raise HTTPException(
            status_code=400, detail=f"period 只能是 {'/'.join(detail_service.PERIODS)}"
        )
    payload = await run_in_threadpool(detail_service.history, code, span, period)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"没有 {code} 这只股票")
    return payload


@router.get("/stocks/{symbol}/intraday")
async def intraday(symbol: str):
    from starlette.concurrency import run_in_threadpool

    from app.data import intraday as intraday_module

    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")
    payload = await run_in_threadpool(intraday_module.fetch, code)
    if payload is None:
        raise HTTPException(status_code=404, detail="没有取到分时数据")
    return payload


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