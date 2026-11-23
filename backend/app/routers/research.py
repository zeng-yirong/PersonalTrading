from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.db import list_notes
from app.services.research import ResearchError, generate_research
from app.services.symbols import normalize_code

router = APIRouter(prefix="/api", tags=["research"])


@router.post("/stocks/{symbol}/research")
async def create_research(symbol: str):
    try:
        return await generate_research(normalize_code(symbol))
    except ResearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"研究笔记生成失败：{exc}") from exc


@router.get("/research-notes")
async def get_notes(symbol: str, limit: int = Query(5, ge=1, le=20)):
    return list_notes(normalize_code(symbol), limit=limit)