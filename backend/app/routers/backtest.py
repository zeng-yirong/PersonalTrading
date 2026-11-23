"""Backtest endpoints: run a screener query over history, keep the runs.

The request reuses the screener's query model on purpose: whatever the screener
accepts, the backtest accepts, and the field whitelist is validated in one place.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.backtest import service
from app.backtest.spec import BacktestError
from app.data.store import StoreError
from app.routers.screener import QueryModel

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class RunModel(BaseModel):
    query: QueryModel
    start: Optional[date] = None
    end: Optional[date] = None
    rebalance: int = 20
    top_n: int = 10
    initial_cash: float = 1_000_000
    slippage_bps: float = 5
    benchmark: str = "000300.SH"
    title: str = ""
    persist: bool = True
    source_run_id: Optional[int] = None


@router.get("/meta")
async def meta() -> dict[str, Any]:
    return await run_in_threadpool(service.meta)


@router.post("/run")
async def run_backtest(payload: RunModel) -> dict[str, Any]:
    body = payload.model_dump()
    persist = bool(body.pop("persist", True))
    try:
        return await run_in_threadpool(service.run, body, persist)
    except BacktestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StoreError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"市场数据库正被占用（可能正在同步），请稍后重试：{exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
async def runs(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    items = await run_in_threadpool(service.history, limit)
    return {"count": len(items), "runs": items}


@router.get("/runs/{run_id}")
async def run_detail(run_id: int) -> dict[str, Any]:
    result = await run_in_threadpool(service.detail, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"回测 {run_id} 不存在")
    return result
