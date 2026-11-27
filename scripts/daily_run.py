"""每天自动跑一次多智能体推演——验证回路需要样本，而样本不会自己长出来。

    python scripts/daily_run.py --pick            # 只看今天轮到谁，不调用模型
    python scripts/daily_run.py                   # 真的跑一次
    python scripts/daily_run.py --if-due          # 今天已经跑过就跳过（定时任务用）
    python scripts/daily_run.py --symbol 600519   # 指定标的
    python scripts/daily_run.py --rounds 2

选股规则见 :mod:`app.agents.daily`：自选（为空则当日成交额前 50）按日期序数轮转，
跳过最近 5 天推过的标的。收盘后的三段式用 `scripts/daily_chain.py`，它复用同一套规则。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import daily  # noqa: E402
from app.db import init_db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 每日自动推演")
    parser.add_argument("--symbol", help="指定标的（默认按轮转规则挑）")
    parser.add_argument("--rounds", type=int, default=1, help="多空辩论轮数，默认 1（省 token）")
    parser.add_argument("--if-due", action="store_true", help="今天已经跑过就跳过")
    parser.add_argument("--pick", action="store_true", help="只打印今天轮到谁")
    args = parser.parse_args(argv)

    init_db()
    if args.if_due:
        done = daily.runs_today()
        if done:
            print(f"今天已有 {done} 次推演，跳过（--if-due）")
            return 0

    target = daily.choose(args.symbol or "")
    print(f"候选池：{target.line()}")
    if args.pick:
        return 0

    result = asyncio.run(daily.run_once(target.symbol, args.rounds))
    print(f"✅ 今日推演：{daily.describe(result)}")
    for item in result.get("errors") or []:
        print(f"  ! {item}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
