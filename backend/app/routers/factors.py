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


@router.post("/runs/{run_id}/commentary")
async def run_commentary(
    run_id: int, name: str = Query("", description="因子名，默认取 |ICIR| 最大的")
) -> dict[str, Any]:
    """按 strategy-review 技能复核单个因子（三道门 rubric + 模型解释）。"""
    from app.agents import commentary
    from app.factor import store as factor_store

    run = await run_in_threadpool(factor_store.get, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"因子评估 {run_id} 不存在")
    rows = [row for row in (run.get("factors") or []) if isinstance(row, dict)]
    if not rows:
        raise HTTPException(status_code=400, detail="这次评估没有因子结果")
    if name:
        rows = [row for row in rows if row.get("name") == name]
        if not rows:
            raise HTTPException(status_code=404, detail=f"这次评估里没有因子 {name}")
    row = max(rows, key=lambda item: abs(float(item.get("icir") or 0)))
    payload = await commentary.comment_factor(
        commentary.factor_metrics(row),
        f"{row.get('name')} {row.get('label') or ''}".strip(),
        ref_id=run_id,
    )
    payload["run_id"] = run_id
    payload["factor"] = row.get("name")
    return payload
