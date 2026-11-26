"""Operator endpoints: what was concluded, what it cost, and whether the data is current."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.data import fingerprint, store
from app.data.backfill import BackfillError
from app.data.backfill import run as run_backfill
from app.data.datasets import BY_NAME
from app.ops import health, ledger
from app.ops import review as review_job

router = APIRouter(prefix="/api", tags=["ops"])

KINDS = ("推演", "主题", "回测", "成交")


@router.get("/ledger/chain/{run_id}")
async def ledger_chain(run_id: int) -> dict[str, Any]:
    payload = await run_in_threadpool(ledger.chain, run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"推演 #{run_id} 不存在")
    return payload


@router.get("/ledger/review")
async def review_status() -> dict[str, Any]:
    return await run_in_threadpool(review_job.status)


@router.post("/ledger/review/refresh")
async def review_refresh(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    return await run_in_threadpool(review_job.run, limit)


@router.post("/ledger/review/commentary")
async def review_commentary() -> dict[str, Any]:
    """按 review-desk 技能复核复盘命中率（rubric 先跑，模型只做解释）。"""
    from app.agents import commentary
    from app.topic import service as topic_service

    payload = await run_in_threadpool(topic_service.daily_review, 8)
    return await commentary.comment_review(payload)


@router.get("/ledger")
async def ledger_entries(
    limit: int = Query(50, ge=1, le=200),
    kind: str = Query("", description="推演 / 主题 / 回测 / 成交"),
) -> dict[str, Any]:
    items = await run_in_threadpool(ledger.entries, limit, kind if kind in KINDS else "")
    return {"count": len(items), "entries": items}


@router.get("/ledger/summary")
async def ledger_summary() -> dict[str, Any]:
    return await run_in_threadpool(ledger.summary)


@router.get("/usage")
async def usage(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    return await run_in_threadpool(ledger.usage, days)


@router.get("/data/health")
async def data_health() -> dict[str, Any]:
    return await run_in_threadpool(health.snapshot)


@router.get("/data/fingerprint")
async def data_fingerprint(force: bool = Query(False)) -> dict[str, Any]:
    return await run_in_threadpool(fingerprint.current, force)


def _last_trade_days(days: int) -> list[str]:
    end = date.today()
    start = end - timedelta(days=days * 3 + 10)
    return [day.isoformat() for day in store.get_trade_days(start, end)][-days:]


@router.post("/data/repair")
async def data_repair(
    dataset: str = Query(..., description="数据集名，如 margin_detail"),
    days: int = Query(5, ge=1, le=10, description="补最近 N 个交易日"),
) -> dict[str, Any]:
    """一键补数：把某张表最近 N 个交易日的缺口重新拉一遍。

    只接受「按日」数据集——按代码/按报告期的回填代价完全不同，不该由一个按钮触发。
    """
    spec = BY_NAME.get(dataset)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"未知数据集：{dataset}")
    if spec.mode != "date":
        raise HTTPException(
            status_code=400,
            detail=f"{dataset} 的拉取方式是 {spec.mode}，不支持一键补数，请用 sync_market.py backfill",
        )
    keys = _last_trade_days(days)
    if not keys:
        raise HTTPException(status_code=409, detail="交易日历为空，先跑 sync_market.py calendar")
    try:
        summary = await run_in_threadpool(
            lambda: run_backfill([dataset], years=1, workers=2, dates=keys)
        )
    except BackfillError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item = summary[0] if summary else {}
    return {
        "dataset": dataset,
        "label": spec.label,
        "dates": keys,
        "parts": item.get("parts", 0),
        "rows": item.get("rows", 0),
        "status": item.get("status", "unknown"),
        "errors": item.get("errors") or [],
    }
