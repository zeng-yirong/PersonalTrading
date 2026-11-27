"""策略库 API：列表、创建、状态迁移、克隆、单条重跑。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.strategy import store

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


class CreateModel(BaseModel):
    name: str
    query: dict[str, Any]
    source: str = "manual"
    notes: str = ""
    params: Optional[dict[str, Any]] = None


class StatusModel(BaseModel):
    status: str
    reason: str = ""


@router.get("")
async def list_all(
    status: str = Query("", description="candidate / watch / champion / retired / paused"),
) -> dict[str, Any]:
    items = await run_in_threadpool(store.list_strategies, status)
    enriched = await run_in_threadpool(lambda: [store.with_metrics(item) for item in items])
    # 同状态内按近一年超额排：库里最该先看到的是"最近跑得好的那条"。
    order = {"champion": 0, "watch": 1, "candidate": 2, "paused": 3, "retired": 4}
    enriched.sort(
        key=lambda item: (
            order.get(item["status"], 9),
            -float((item.get("year1") or {}).get("excess_cagr_pct") or -999.0),
            item["name"],
        )
    )
    return {"count": len(enriched), "strategies": enriched}


@router.get("/{strategy_id}")
async def detail(strategy_id: str) -> dict[str, Any]:
    item = await run_in_threadpool(store.get, strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")
    return await run_in_threadpool(store.with_metrics, item)


@router.post("")
async def create(payload: CreateModel) -> dict[str, Any]:
    try:
        item = await run_in_threadpool(
            lambda: store.create(
                payload.name,
                payload.query,
                source=payload.source,
                params=payload.params,
                notes=payload.notes,
            )
        )
    except store.StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.post("/{strategy_id}/status")
async def change_status(strategy_id: str, payload: StatusModel) -> dict[str, Any]:
    try:
        item = await run_in_threadpool(
            lambda: store.set_status(strategy_id, payload.status, payload.reason)
        )
    except store.StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.post("/{strategy_id}/clone")
async def clone(strategy_id: str, name: str = Query("", description="副本名称（可选）")) -> dict[str, Any]:
    try:
        return await run_in_threadpool(store.clone, strategy_id, name)
    except store.StrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{strategy_id}/run")
async def run_one(strategy_id: str) -> dict[str, Any]:
    """单独重跑一条（近一年），并写入 strategy_runs。"""
    from app.screener import ranking

    item = await run_in_threadpool(store.get, strategy_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id} 不存在")

    def _run() -> dict[str, Any]:
        from app.backtest import service as backtest_service

        result = backtest_service.run(
            {
                "query": item["query"],
                "rebalance": int((item.get("params") or {}).get("rebalance") or 20),
                "top_n": int((item.get("params") or {}).get("top_n") or 10),
                "title": item["name"],
            },
            persist=False,
        )
        metrics = ranking._metrics_of(result)
        spec = result.get("spec") or {}
        store.save_run(
            strategy_id,
            "1y",
            metrics,
            start=spec.get("start") or "",
            end=spec.get("end") or "",
        )
        return {"strategy": item, "metrics": metrics, "spec": spec}

    return await run_in_threadpool(_run)
