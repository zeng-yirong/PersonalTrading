"""经验记忆（P8-0）：只看候选/影子召回，不参与决策。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.agents import experience

router = APIRouter(prefix="/api/experiences", tags=["experiences"])


class StatusChange(BaseModel):
    status: str
    reason: str = ""


class Graduate(BaseModel):
    target: str
    reason: str = ""


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


@router.get("/evaluation")
async def experience_evaluation(limit: int = Query(500, ge=1, le=2000)) -> dict:
    return {"preflight": experience.preflight(), "shadow": experience.evaluation(limit=limit)}


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


@router.post("/{experience_id}/status")
async def experience_status(experience_id: int, payload: StatusChange) -> dict:
    try:
        item = experience.change_status(experience_id, payload.status, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="经验卡不存在")
    return item


@router.post("/{experience_id}/graduate")
async def experience_graduate(experience_id: int, payload: Graduate) -> dict:
    try:
        item = experience.graduate(experience_id, payload.target, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="经验卡不存在")
    return item
