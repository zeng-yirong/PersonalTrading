"""Stock screener: natural language or structured JSON → cross-section query."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agents.llm import LLMError
from app.screener import nl, query as screen_query, snapshot
from app.screener.query import QueryError

router = APIRouter(prefix="/api/screener", tags=["screener"])


class FilterModel(BaseModel):
    field: str
    op: str
    value: Any = None


class UniverseModel(BaseModel):
    asset_type: str = "stock"
    boards: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    exclude_st: bool = True


class QueryModel(BaseModel):
    title: str = ""
    explanation: str = ""
    universe: UniverseModel = Field(default_factory=UniverseModel)
    filters: list[FilterModel] = Field(default_factory=list)
    sort: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 30


class AskModel(BaseModel):
    text: str
    limit: int = 30


@router.get("/fields")
async def fields() -> dict:
    return {
        "count": len(screen_query.field_catalog()),
        "fields": screen_query.field_catalog(),
        "operators": screen_query.describe_ops(),
    }


@router.get("/meta")
async def meta() -> dict:
    info = await run_in_threadpool(snapshot.ensure_built)
    return {
        "as_of": info["trade_date"],
        "symbols": info["symbols"],
        "cached": info.get("cached", False),
        "sources": info.get("sources"),
    }


@router.post("/refresh")
async def refresh() -> dict:
    info = await run_in_threadpool(snapshot.build)
    return info


@router.post("/parse")
async def parse(payload: AskModel) -> dict:
    try:
        parsed = await nl.parse(payload.text, limit=payload.limit)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return parsed.to_dict()


@router.post("/run")
async def run_query(payload: QueryModel) -> dict:
    try:
        parsed = screen_query.ScreenQuery.from_dict(payload.model_dump())
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await run_in_threadpool(screen_query.run, parsed)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ask")
async def ask(payload: AskModel) -> dict:
    """Natural language in, result rows out — the one call the UI needs."""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="请描述筛选条件")
    try:
        parsed = await nl.parse(payload.text, limit=payload.limit)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # The DuckDB query is blocking; keep it off the event loop so SSE streams
    # and other requests are not stalled behind a full-market scan.
    try:
        return await run_in_threadpool(screen_query.run, parsed)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
