from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class WatchlistAdd(BaseModel):
    symbol: str = Field(..., description="6 位股票/ETF 代码")
    name: Optional[str] = None


class WatchlistItem(BaseModel):
    symbol: str
    name: str
    asset_type: str


class SearchResult(BaseModel):
    symbol: str
    name: str
    asset_type: str
    exchange: str
    market: str


class ResearchNote(BaseModel):
    id: int
    symbol: str
    model: Optional[str]
    content: str
    created_at: str