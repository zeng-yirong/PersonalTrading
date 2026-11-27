"""工作日收盘后的三段式：复盘刷新 → 生成今日复盘 → 自动推演。

以前是三个独立的定时任务（18:15 / 18:20 / 18:30），合并的理由：

* 它们之间有**顺序依赖**（刷新完才有新判定；日更写完才能写复盘）；
* 三次唤醒、三次汇报，中间任何一步失败都要人肉串起来看；
* 合并后仍是一天一条消息，但能一句话交代"今天闭环跑完了没有"。

每一步失败都不阻断下一步（刷不新不影响写复盘，复盘失败也不影响推演），
最后用退出码表示"有没有任何一步失败"。

    python scripts/daily_chain.py                 # 定时任务用
    python scripts/daily_chain.py --skip-run      # 只做复盘
    python scripts/daily_chain.py --force-brief   # 强制重算今日复盘
    python scripts/daily_chain.py --no-wait       # 不等待面板写完
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
from app.market.freshness import wait_until_fresh  # noqa: E402
from app.ops import review as review_job  # noqa: E402
from app.topic import service as topic_service  # noqa: E402
from app.topic.service import TopicError  # noqa: E402
from app.agents.llm import LLMError  # noqa: E402


def step_review() -> dict:
    try:
        data = review_job.run(200)
    except Exception as exc:  # noqa: BLE001 - 单步失败不阻断后续
        return {"ok": False, "line": f"复盘刷新：[失败] {type(exc).__name__}: {exc}"}
    refreshed = data.get("refreshed") or {}
    line = (
        f"复盘刷新：写入 {refreshed.get('written', 0)} 条｜"
        f"待验证 {data.get('pending', 0)} / 已判定 {data.get('judged', 0)}"
    )
    if data.get("judged"):
        line += f"（命中率 {data.get('hit_rate_pct')}%）"
    return {"ok": True, "line": line, "data": data}


async def step_brief(force: bool, wait: bool, tries: int, interval: float) -> dict:
    if wait:
        state = wait_until_fresh(tries=tries, interval=interval)
        if not state["ready"]:
            print(
                "[警告] 面板可能还没写完，仍然生成：" + "；".join(state["missing"]),
                file=sys.stderr,
            )
        elif state["attempts"]:
            print(f"面板等了 {state['waited_seconds']} 秒才写完")
    try:
        result = await topic_service.daily_brief(force=force)
    except (TopicError, LLMError) as exc:
        return {"ok": False, "line": f"今日复盘：[失败] {exc}"}

    record = result.get("report") or {}
    if result.get("cached"):
        return {
            "ok": True,
            "line": f"今日复盘：已存在 #{record.get('id')} {record.get('title') or ''}".rstrip(),
            "cached": True,
        }
    body = record.get("report") or {}
    action = body.get("action") or {}
    usage = record.get("usage") or {}
    line = (
        f"今日复盘：#{record.get('id')} {body.get('stage')}·{body.get('stance')}"
        f"·{action.get('position_range') or '—'}"
        f"（{usage.get('prompt_tokens', 0)}→{usage.get('completion_tokens', 0)} tokens）"
    )
    if body.get("fallback"):
        line += " ⚠ 规则版"
    return {"ok": True, "line": line, "fallback": bool(body.get("fallback"))}


async def step_run(rounds: int, if_due: bool) -> dict:
    if if_due:
        done = daily.runs_today()
        if done:
            return {"ok": True, "line": f"自动推演：今天已有 {done} 次，跳过"}
    try:
        target = daily.choose()
    except LookupError as exc:
        return {"ok": False, "line": f"自动推演：[失败] {exc}"}
    result = await daily.run_once(target.symbol, rounds)
    if not result.get("ok"):
        return {"ok": False, "line": f"自动推演：[失败] {result.get('error')}"}
    suffix = "（含降级节点）" if result.get("errors") else ""
    return {"ok": True, "line": f"自动推演：{daily.describe(result)}{suffix}", "run_id": result.get("run_id")}


async def main_async(args: argparse.Namespace) -> int:
    init_db()
    steps: list[dict] = []

    steps.append(step_review())
    steps.append(
        await step_brief(args.force_brief, not args.no_wait, args.wait_tries, args.wait_seconds)
    )
    if not args.skip_run:
        steps.append(await step_run(args.rounds, if_due=True))

    print()
    for item in steps:
        print(item["line"])
    failed = [item for item in steps if not item["ok"]]
    if failed:
        print(f"\n{len(failed)}/{len(steps)} 步失败，其余已照常完成。", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 收盘后三段式")
    parser.add_argument("--skip-run", action="store_true", help="不做自动推演")
    parser.add_argument("--force-brief", action="store_true", help="强制重算今日复盘")
    parser.add_argument("--rounds", type=int, default=1, help="推演的辩论轮数")
    parser.add_argument("--no-wait", action="store_true", help="不等面板写完")
    parser.add_argument("--wait-tries", type=int, default=3)
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
