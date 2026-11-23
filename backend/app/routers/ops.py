"""Operator endpoints: what was concluded, what it cost, and whether the data is current."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from app.data import fingerprint
from app.ops import health, ledger

router = APIRouter(prefix="/api", tags=["ops"])

KINDS = ("推演", "主题", "回测", "成交")


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
