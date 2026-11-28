"""PersonalTrading backend entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.data.store import StoreError
from app.db import init_db
from app.routers import (
    agents,
    backtest,
    experiences,
    factors,
    market,
    memory,
    meta,
    ops,
    paper,
    research,
    screener,
    stocks,
    strategy,
    topics,
    watchlist,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="PersonalTrading",
    version="0.1.0",
    description="本地个人 A 股 / ETF 研究助手",
    lifespan=lifespan,
)


@app.exception_handler(StoreError)
async def _store_unavailable(_, exc: StoreError) -> JSONResponse:
    """面板不可用（被锁 / 未初始化）应该是 503，而不是 500。"""
    return JSONResponse(status_code=503, content={"detail": f"市场面板不可用：{exc}"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router, prefix="/api")
app.include_router(watchlist.router)
app.include_router(stocks.router)
app.include_router(research.router)
app.include_router(market.router)
app.include_router(agents.router)
app.include_router(screener.router)
app.include_router(backtest.router)
app.include_router(topics.router)
app.include_router(factors.router)
app.include_router(paper.router)
app.include_router(strategy.router)
app.include_router(ops.router)
app.include_router(memory.router)
app.include_router(experiences.router)


@app.get("/")
async def root() -> dict:
    return {
        "app": "PersonalTrading",
        "docs": "/docs",
        "health": "/api/health",
        "disclaimer": "目标价与操作建议为模型推算，不保证准确；仅供个人研究使用。",
    }


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )