from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db import add_watchlist, list_watchlist, remove_watchlist
from app.schemas import WatchlistAdd
from app.services import watchlist as watchlist_service
from app.services.symbols import asset_type, normalize_code, search_stocks

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
async def get_watchlist() -> list[dict]:
    return list_watchlist()


@router.get("/metrics")
async def get_watchlist_metrics() -> list[dict]:
    """现价 / 今日 / 近 5 日 / 添加自选后涨跌幅；行情取不到时退回面板。"""
    return await watchlist_service.build_metrics()


@router.post("")
async def create_watchlist_item(payload: WatchlistAdd) -> dict:
    code = normalize_code(payload.symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")

    name = (payload.name or "").strip()
    if not name:
        try:
            results = await search_stocks(code, limit=5)
            exact = next((item for item in results if item["symbol"] == code), None)
            name = exact["name"] if exact else code
        except Exception:
            name = code

    baseline = await watchlist_service.baseline_for(code)
    return add_watchlist(code, name, asset_type(code), **baseline)


@router.delete("/{symbol}")
async def delete_watchlist_item(symbol: str) -> dict:
    code = normalize_code(symbol)
    if not code:
        raise HTTPException(status_code=400, detail="股票代码无效")
    if not remove_watchlist(code):
        raise HTTPException(status_code=404, detail="自选股不存在")
    return {"ok": True, "symbol": code}