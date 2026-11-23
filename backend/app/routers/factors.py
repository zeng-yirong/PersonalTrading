"""Factor mining endpoints: library, evaluation run, history."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.data.store import StoreError
from app.factor import service
from app.factor.evaluate import FactorError

router = APIRouter(prefix="/api/factors", tags=["factors"])


class RunModel(BaseModel):
    start: Optional[date] = None
    end: Optional[date] = None
    rebalance: int = 20
    horizon: int = 20
    factors: list[str] = Field(default_factory=list)
    oos_months: int = 0
    source: str = "panel"
    incremental: bool = False


@router.get("/library")
async def library() -> dict[str, Any]:
    return await run_in_threadpool(service.meta)


@router.post("/run")
async def run_factors(payload: RunModel) -> dict[str, Any]:
    try:
        return await run_in_threadpool(service.run, payload.model_dump())
    except FactorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StoreError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"市场数据库正被占用（可能正在同步），请稍后重试：{exc}",
        ) from exc


@router.get("/runs")
async def runs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    items = await run_in_threadpool(service.history, limit)
    return {"count": len(items), "runs": items}


@router.get("/runs/{run_id}")
async def run_detail(run_id: int) -> dict[str, Any]:
    result = await run_in_threadpool(service.detail, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"因子评估 {run_id} 不存在")
    return result
