"""每日自动推演的编排：选哪只、跑一次、怎么汇报。

放在 ``app`` 里而不是脚本里，是因为收盘后的三段式（复盘刷新 → 生成复盘 → 推演）
也要用同一套规则。选股是**确定性轮转**，不是"挑一只今天想看的"：同一天重跑得到
同一只，复盘时说得清样本从哪来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from app.agents import runner
from app.data import store
from app.db import connection, list_watchlist

RECENT_DAYS = 5
POOL_FALLBACK = 50


@dataclass(frozen=True)
class Target:
    symbol: str
    name: str
    source: str

    def line(self) -> str:
        return f"{self.source}｜今天轮到 {self.symbol} {self.name}".rstrip()


def recent_symbols(days: int = RECENT_DAYS, today: Optional[date] = None) -> set[str]:
    """最近几天推演过的标的：轮转时避开，别连着两天研究同一只。"""
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM agent_runs WHERE started_at >= ?", (since,)
        ).fetchall()
    return {row[0] for row in rows}


def runs_today(today: Optional[date] = None) -> int:
    today = today or date.today()
    with connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE started_at LIKE ?",
                (today.isoformat() + "%",),
            ).fetchone()[0]
        )


def candidate_pool() -> tuple[list[dict[str, Any]], str]:
    watch = list_watchlist()
    if watch:
        return (
            [{"symbol": item["symbol"], "name": item.get("name")} for item in watch],
            "自选",
        )
    rows = store.get_snapshot(asset_type="stock", limit=POOL_FALLBACK + 10)
    rows = [
        row
        for row in rows
        if (row.get("symbol") or "")[:1] in "0123" and "ST" not in (row.get("name") or "")
    ]
    return rows[:POOL_FALLBACK], "当日成交额前 50（自选为空）"


def pick(pool: list[dict[str, Any]], today: date, skip: set[str]) -> dict[str, Any]:
    if not pool:
        raise LookupError("候选池为空：既没有自选，面板也没有当日截面")
    index = today.toordinal() % len(pool)
    for step in range(len(pool)):
        item = pool[(index + step) % len(pool)]
        if item["symbol"] not in skip:
            return item
    return pool[index]          # 全推过了就用轮到的那只


def choose(symbol: str = "", today: Optional[date] = None) -> Target:
    today = today or date.today()
    if symbol:
        name = (store.get_symbol(symbol) or {}).get("name") or ""
        return Target(symbol, name, "指定")
    pool, source = candidate_pool()
    item = pick(pool, today, recent_symbols(today=today))
    return Target(item["symbol"], item.get("name") or "", source)


async def run_once(symbol: str, rounds: Optional[int] = 1) -> dict[str, Any]:
    """跑一次推演，返回可直接打印/汇报的结构。"""
    result = await runner.run_research(symbol, rounds)
    final = result.get("final") or {}
    if final.get("type") == "error":
        return {"ok": False, "error": str(final.get("message") or "运行失败"), "symbol": symbol}
    verdict = final.get("verdict") or {}
    usage = final.get("usage") or {}
    return {
        "ok": True,
        "symbol": symbol,
        "name": final.get("name") or (store.get_symbol(symbol) or {}).get("name") or "",
        "decision": verdict.get("decision") or "—",
        "position_pct": verdict.get("final_position_pct"),
        "calls": usage.get("calls", 0),
        "tokens": usage.get("total_tokens", 0),
        "seconds": round((final.get("duration_ms") or 0) / 1000),
        "run_id": final.get("run_id"),
        "errors": final.get("errors") or [],
    }


def describe(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"[失败] {result.get('symbol')}：{result.get('error')}"
    return (
        f"{result['symbol']} {result['name']}｜裁定 {result['decision']} "
        f"{result['position_pct']}%｜{result['calls']} 次调用 "
        f"{result['tokens']:,} tokens｜{result['seconds']}s｜记录 #{result['run_id']}"
    )
