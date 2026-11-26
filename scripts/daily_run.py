"""每天自动跑一次多智能体推演——验证回路需要样本，而样本不会自己长出来。

    python scripts/daily_run.py --pick            # 只看今天轮到谁，不调用模型
    python scripts/daily_run.py                   # 真的跑一次
    python scripts/daily_run.py --if-due          # 今天已经跑过就跳过（定时任务用）
    python scripts/daily_run.py --symbol 600519   # 指定标的
    python scripts/daily_run.py --rounds 2

选股规则是**确定性轮转**，不是"挑一个今天想看的"：有自选就用自选，自选为空就用
当日成交额前 50 的 A 股（剔除 ST 与北交所），按「日期序数 % 池大小」轮转，并跳过
最近 5 天推演过的标的。同一天重跑会得到同一只，省得复盘时说不清样本怎么来的。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import runner  # noqa: E402
from app.data import store  # noqa: E402
from app.db import connection, init_db, list_watchlist  # noqa: E402

RECENT_DAYS = 5
POOL_FALLBACK = 50


def _recent_symbols(days: int = RECENT_DAYS) -> set[str]:
    """最近几天已经推演过的标的：轮转时要避开，别连着两天研究同一只。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM agent_runs WHERE started_at >= ?", (since,)
        ).fetchall()
    return {row[0] for row in rows}


def _runs_today() -> int:
    with connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE started_at LIKE ?",
                (date.today().isoformat() + "%",),
            ).fetchone()[0]
        )


def candidate_pool() -> tuple[list[dict], str]:
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


def pick(pool: list[dict], today: date, skip: set[str]) -> dict:
    if not pool:
        raise SystemExit("候选池为空：既没有自选，面板也没有当日截面")
    index = today.toordinal() % len(pool)
    for step in range(len(pool)):
        item = pool[(index + step) % len(pool)]
        if item["symbol"] not in skip:
            return item
    return pool[index]          # 全推过了就用轮到的那只


async def _run(symbol: str, rounds: int | None) -> int:
    result = await runner.run_research(symbol, rounds)
    final = result.get("final") or {}
    if final.get("type") == "error":
        print(f"[失败] {final.get('message')}", file=sys.stderr)
        return 1
    verdict = final.get("verdict") or {}
    usage = final.get("usage") or {}
    name = final.get("name") or (store.get_symbol(symbol) or {}).get("name") or ""
    print(
        f"✅ 今日推演：{symbol} {name}｜裁定 {verdict.get('decision') or '—'} "
        f"{verdict.get('final_position_pct')}%｜{usage.get('calls', 0)} 次调用 "
        f"{usage.get('total_tokens', 0):,} tokens｜{(final.get('duration_ms') or 0) / 1000:.0f}s"
        f"｜记录 #{final.get('run_id')}"
    )
    for item in final.get("errors") or []:
        print(f"  ! {item}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 每日自动推演")
    parser.add_argument("--symbol", help="指定标的（默认按轮转规则挑）")
    parser.add_argument("--rounds", type=int, default=1, help="多空辩论轮数，默认 1（省 token）")
    parser.add_argument("--if-due", action="store_true", help="今天已经跑过就跳过")
    parser.add_argument("--pick", action="store_true", help="只打印今天轮到谁")
    args = parser.parse_args(argv)

    init_db()
    if args.if_due:
        done = _runs_today()
        if done:
            print(f"今天已有 {done} 次推演，跳过（--if-due）")
            return 0

    if args.symbol:
        target = {"symbol": args.symbol, "name": ""}
        source = "指定"
    else:
        pool, source = candidate_pool()
        target = pick(pool, date.today(), _recent_symbols())
    print(f"候选池：{source}｜今天轮到 {target['symbol']} {target.get('name') or ''}".rstrip())
    if args.pick:
        return 0

    return asyncio.run(_run(target["symbol"], args.rounds))


if __name__ == "__main__":
    raise SystemExit(main())
