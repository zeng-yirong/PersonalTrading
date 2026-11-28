"""经验记忆（P8-0）：只看候选/影子召回，不参与决策。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.agents import experience

router = APIRouter(prefix="/api/experiences", tags=["experiences"])


@router.get("")
async def list_experiences(
    status: str = "",
    scope: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    return experience.list_experiences(status=status, scope=scope, q=q, limit=limit)


@router.get("/stats")
async def experience_stats() -> dict:
    return experience.stats()


@router.get("/recalls")
async def experience_recalls(
    run_id: Optional[int] = None, limit: int = Query(50, ge=1, le=200)
) -> list[dict]:
    return experience.recalls(limit=limit, run_id=run_id)


@router.post("/extract")
async def extract_experiences() -> dict:
    return experience.extract()


@router.get("/{experience_id}")
async def experience_detail(experience_id: int) -> dict:
    item = experience.details(experience_id)
    if item is None:
        raise HTTPException(status_code=404, detail="经验卡不存在")
    return item
