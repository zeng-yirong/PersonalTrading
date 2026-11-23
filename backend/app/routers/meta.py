from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": "PersonalTrading",
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@router.get("/meta")
async def meta() -> dict:
    return {
        "app": "PersonalTrading",
        "version": "0.1.0",
        "model": settings.deepseek_model,
        "llm_configured": bool(settings.deepseek_api_key),
        "markets": ["A股", "ETF"],
        "data_sources": ["新浪财经", "东方财富"],
        "disclaimer": "目标价、期望收益与买卖动作均为模型推算，不保证准确；仅供个人研究使用，据此操作风险自担。",
    }