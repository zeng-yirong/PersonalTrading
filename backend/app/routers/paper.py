"""Paper account: virtual positions settled on panel closes, plus run review."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.paper import review as review_module
from app.paper import service

router = APIRouter(prefix="/api/paper", tags=["paper"])


class TradeModel(BaseModel):
    symbol: str
    side: Optional[str] = None          # buy / sell
    shares: Optional[float] = None
    amount: Optional[float] = None
    note: str = ""


class ResetModel(BaseModel):
    initial_cash: float = service.DEFAULT_CASH


class FromRunModel(BaseModel):
    fraction: float = 1.0


@router.get("/summary")
async def summary() -> dict:
    return await run_in_threadpool(service.summary)


@router.post("/account/reset")
async def reset(payload: ResetModel) -> dict:
    try:
        return await run_in_threadpool(service.reset, payload.initial_cash)
    except service.PaperError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/trade")
async def trade(payload: TradeModel) -> dict:
    if not payload.side:
        raise HTTPException(status_code=400, detail="需要 side（buy / sell）")
    try:
        return await run_in_threadpool(
            service.trade,
            payload.symbol,
            payload.side,
            payload.shares,
            payload.amount,
            None,
            payload.note,
        )
    except service.PaperError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/from-run/{run_id}")
async def trade_from_run(run_id: int, payload: FromRunModel) -> dict:
    try:
        return await run_in_threadpool(service.trade_from_run, run_id, payload.fraction)
    except service.PaperError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/positions")
async def positions() -> dict:
    items = await run_in_threadpool(service.positions)
    return {"count": len(items), "positions": items}


@router.get("/nav")
async def nav() -> dict:
    series = await run_in_threadpool(service.nav_series)
    return {"count": len(series), "series": series}


@router.get("/trades")
async def trades(limit: int = Query(50, ge=1, le=500)) -> dict:
    items = await run_in_threadpool(service.trades, limit)
    return {"count": len(items), "trades": items}


@router.post("/review/refresh")
async def refresh_reviews(limit: int = Query(200, ge=1, le=1000)) -> dict:
    return await run_in_threadpool(review_module.refresh, limit)


@router.get("/reviews")
async def reviews(limit: int = Query(50, ge=1, le=500)) -> dict:
    items = await run_in_threadpool(review_module.list_reviews, limit)
    return {"count": len(items), "reviews": items}


@router.get("/review/stats")
async def review_stats() -> dict:
    return await run_in_threadpool(review_module.stats)
