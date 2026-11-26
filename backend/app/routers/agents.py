"""Multi-agent research API: SSE stream, history, status."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.agents import memory, runner, store as agent_store
from app.agents.llm import ChatClient
from app import skills
from app.config import settings
from app.data import store as panel_store
from app.services.symbols import normalize_code

router = APIRouter(prefix="/api/agents", tags=["agents"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Nginx and friends buffer by default, which would defeat streaming.
    "X-Accel-Buffering": "no",
}


def _sse(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"


@router.get("/status")
async def status() -> dict:
    client = ChatClient()
    ok, reason = client.available()
    return {
        "llm_available": ok,
        "reason": reason,
        "model": client.model,
        "debate_rounds": settings.agent_debate_rounds,
        "panel_ready": panel_store.is_ready(),
        "skill_version": skills.label(),
        "skills": skills.describe(),
        "memory": memory.stats(),
        "coverage": {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in panel_store.get_coverage().items()
        },
    }


@router.get("/analyze/{symbol}")
async def analyze_stream(
    symbol: str,
    rounds: Optional[int] = Query(None, ge=1, le=4),
) -> StreamingResponse:
    """SSE stream: one event per node start/finish, then a final report."""
    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")

    async def generate():
        try:
            async for event in runner.stream_research(code, rounds):
                yield _sse(event)
        except asyncio.CancelledError:  # client disconnected
            raise
        except Exception as exc:  # noqa: BLE001 - last line of defence for SSE
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/analyze/{symbol}")
async def analyze_once(
    symbol: str,
    rounds: Optional[int] = Query(None, ge=1, le=4),
) -> dict:
    """Same run without streaming, for scripts and smoke tests."""
    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")
    result = await runner.run_research(code, rounds)
    final = result.get("final") or {}
    if final.get("type") == "error":
        raise HTTPException(status_code=502, detail=final.get("message"))
    return final


@router.get("/runs")
async def list_runs(
    symbol: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    code = normalize_code(symbol) if symbol else None
    items = agent_store.list_runs(code, limit)
    return {"count": len(items), "runs": items}


@router.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict:
    item = agent_store.get_run(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return item
