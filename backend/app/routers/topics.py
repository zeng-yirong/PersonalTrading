"""Thematic research endpoints: topic → market snapshot → memo.

``/context`` returns the snapshot without calling a model, which makes it easy to
check what the report would be based on (and is what the tests pin down).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.agents.llm import LLMError
from app.config import settings
from app.data import store
from app.topic import service
from app.topic.service import TopicError

router = APIRouter(prefix="/api/topics", tags=["topics"])

EXAMPLES = (
    "市场情绪现在处于什么位置",
    "白酒行业还能拿吗",
    "AI 算力这条线还有多久",
    "大盘会不会继续跌",
)


class ResearchModel(BaseModel):
    topic: str
    model: str = ""
    source_run_id: Optional[int] = None


@router.get("/meta")
async def meta() -> dict[str, Any]:
    try:
        with store.session(read_only=True) as conn:
            as_of = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
            industries = conn.execute(
                "SELECT industry, COUNT(*) FROM symbols "
                "WHERE asset_type = 'stock' AND industry IS NOT NULL "
                "GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - panel may not exist yet
        raise HTTPException(status_code=503, detail=f"市场面板不可用：{exc}") from exc
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "model": settings.agent_model or settings.deepseek_model,
        "llm_available": bool(settings.deepseek_api_key),
        "examples": list(EXAMPLES),
        "industries": [{"name": row[0], "stocks": int(row[1])} for row in industries],
    }


@router.get("/context")
async def preview(topic: str = Query(..., min_length=2, max_length=80)) -> dict[str, Any]:
    try:
        return await run_in_threadpool(service.preview, topic)
    except TopicError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/research")
async def research(payload: ResearchModel) -> dict[str, Any]:
    try:
        return await service.research(
            payload.topic, model=payload.model, source_run_id=payload.source_run_id
        )
    except TopicError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/reports")
async def reports(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    items = await run_in_threadpool(service.history, limit)
    return {"count": len(items), "reports": items}


@router.get("/reports/{report_id}")
async def report(report_id: int) -> dict[str, Any]:
    item = await run_in_threadpool(service.detail, report_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"主题报告 {report_id} 不存在")
    return item
