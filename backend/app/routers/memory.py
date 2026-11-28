"""Memory retrieval endpoints: inspect and maintain the decision memory."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.agents import memory

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def list_memory(
    symbol: str = "",
    q: str = "",
    as_of: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    if q.strip():
        return memory.search(q, as_of=as_of, limit=limit)
    if symbol.strip():
        return memory.for_symbol(symbol, as_of=as_of, limit=min(limit, 10))
    return memory.recent(limit)


@router.get("/stats")
async def memory_stats() -> dict:
    return memory.stats()


@router.post("/rebuild")
async def memory_rebuild() -> dict:
    return {"indexed": memory.rebuild()}
