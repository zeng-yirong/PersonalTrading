"""Read-only market panel API (P0 data foundation).

Every endpoint reads the local DuckDB panel; syncing happens through
``scripts/sync_market.py`` so a slow data crawl can never block an HTTP request.

DuckDB calls are blocking (and may wait on a sync's write lock), so they run in
the thread pool rather than on the event loop.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.data import store
from app.data.providers import list_providers

router = APIRouter(prefix="/api/market", tags=["market"])

ADJUST_PATTERN = "^(none|qfq|hfq)$"


def _parse_date(value: Optional[str], field: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} 需要 YYYY-MM-DD 格式") from exc


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


async def _require_panel() -> None:
    if not await run_in_threadpool(store.is_ready):
        raise HTTPException(
            status_code=503,
            detail=(
                "本地市场面板尚未建立。请运行 "
                "`python scripts/sync_market.py all` 初始化后再试。"
            ),
        )


@router.get("/status")
async def status() -> dict:
    coverage = await run_in_threadpool(store.get_coverage)
    return {
        "ready": coverage["ready"],
        "db_path": str(store.db_path()),
        "coverage": {key: _iso(value) for key, value in coverage.items()},
        "providers": list_providers(),
        "recent_sync": [
            {
                "job": item["job"],
                "status": item["status"],
                "rows": item["rows"],
                "finished_at": _iso(item["finished_at"]),
                "detail": item["detail"],
            }
            for item in store.get_sync_log(5)
        ],
    }


@router.get("/overview")
async def overview() -> dict:
    """Today's market: indices, breadth, sectors, money flow, hot stocks."""
    from app.market import overview as overview_module

    await _require_panel()
    return await run_in_threadpool(overview_module.overview)


@router.get("/calendar")
async def calendar(
    start: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD"),
) -> dict:
    days = await run_in_threadpool(
        store.get_calendar, _parse_date(start, "start"), _parse_date(end, "end")
    )
    if not days:
        raise HTTPException(status_code=503, detail="交易日历为空，请先运行 sync_market.py calendar")
    return {
        "count": len(days),
        "days": [{**item, "cal_date": _iso(item["cal_date"])} for item in days],
    }


@router.get("/trade-days")
async def trade_days(
    start: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(250, ge=1, le=5000),
) -> dict:
    days = await run_in_threadpool(
        store.get_trade_days, _parse_date(start, "start"), _parse_date(end, "end")
    )
    return {"count": len(days), "days": [day.isoformat() for day in days[-limit:]]}


@router.get("/universe")
async def universe(
    asset_type: Optional[str] = Query(None, pattern="^(stock|etf)$"),
    exchange: Optional[str] = Query(None, pattern="^(SH|SZ|BJ)$"),
    board: Optional[str] = None,
    active_only: bool = True,
) -> dict:
    await _require_panel()
    items = await run_in_threadpool(
        store.get_universe,
        asset_type,
        active_only,
        exchange,
        board,
    )
    return {
        "count": len(items),
        "symbols": [
            {**item, "list_date": _iso(item["list_date"]), "delist_date": _iso(item["delist_date"])}
            for item in items
        ],
    }


@router.get("/snapshot")
async def snapshot(
    trade_date: Optional[str] = Query(None, description="YYYY-MM-DD，默认最新"),
    asset_type: Optional[str] = Query(None, pattern="^(stock|etf)$"),
    limit: int = Query(10000, ge=1, le=10000),
) -> dict:
    await _require_panel()
    target = _parse_date(trade_date, "trade_date")
    rows = await run_in_threadpool(store.get_snapshot, target, asset_type, limit)
    if not rows:
        raise HTTPException(status_code=404, detail="该日期没有截面数据")
    return {
        "trade_date": _iso(rows[0]["trade_date"]),
        "count": len(rows),
        "rows": [{**row, "trade_date": _iso(row["trade_date"])} for row in rows],
    }


@router.get("/symbols/{symbol}")
async def symbol_info(symbol: str) -> dict:
    await _require_panel()
    item = await run_in_threadpool(store.get_symbol, symbol)
    if not item:
        raise HTTPException(status_code=404, detail=f"本地面板没有 {symbol}")
    return {**item, "list_date": _iso(item["list_date"]), "delist_date": _iso(item["delist_date"])}


@router.get("/daily/{symbol}")
async def daily(
    symbol: str,
    start: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD"),
    adjust: str = Query("none", pattern=ADJUST_PATTERN),
    limit: int = Query(500, ge=10, le=5000),
) -> dict:
    await _require_panel()
    bars = await run_in_threadpool(
        store.get_daily,
        symbol,
        _parse_date(start, "start"),
        _parse_date(end, "end"),
        adjust,
        limit,
    )
    if not bars:
        raise HTTPException(status_code=404, detail=f"本地面板没有 {symbol} 的日线数据")
    has_factors = any(bar["adj_factor"] is not None for bar in bars)
    payload = {
        "symbol": symbol,
        "adjust": adjust,
        "factors_available": has_factors,
        "count": len(bars),
        "bars": [{**bar, "trade_date": _iso(bar["trade_date"])} for bar in bars],
    }
    if adjust != "none" and not has_factors:
        # Better to say it loudly than to hand back raw prices labelled as adjusted.
        payload["warning"] = (
            "该标的尚无复权因子，返回的是未复权价格。"
            "配置有效的 Tushare token 后重跑 history 可补齐。"
        )
    return payload
