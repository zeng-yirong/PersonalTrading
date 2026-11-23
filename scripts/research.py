"""Run the multi-agent research team from the command line.

    python scripts/research.py 600519
    python scripts/research.py 600519 --rounds 3 --report out.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import runner  # noqa: E402


async def main_async(args: argparse.Namespace) -> int:
    report = ""
    status = 0
    async for event in runner.stream_research(args.symbol, args.rounds):
        kind = event.get("type")
        if kind == "stage":
            print(f"… {event['message']}", flush=True)
        elif kind == "run_start":
            print(
                f"\n开始研究 {event['name']}（{event['symbol']}），截止 {event['as_of']}，"
                f"模型 {event['model']}，辩论 {event['rounds']} 轮\n",
                flush=True,
            )
        elif kind == "node_start":
            print(f"  → {event['label']} …", end="", flush=True)
        elif kind == "node_end":
            mark = "✓" if event.get("status") == "ok" else "✗"
            detail = ""
            payload = event.get("payload")
            if isinstance(payload, dict):
                if "stance" in payload:
                    detail = f" {payload.get('stance')} {payload.get('score')}"
                elif "verdict" in payload and "decision" in payload:
                    detail = f" {payload.get('decision')} {payload.get('final_position_pct')}%"
                elif "action" in payload:
                    detail = f" {payload.get('action')} {payload.get('position_pct')}%"
            if event.get("status") != "ok" and event.get("message"):
                detail = f" {event['message'][:80]}"
            print(f" {mark}{detail}", flush=True)
        elif kind == "run_end":
            report = event.get("report", "")
            usage = event.get("usage") or {}
            print(
                f"\n完成：{usage.get('calls', 0)} 次调用，{usage.get('total_tokens', 0)} tokens，"
                f"{event.get('duration_ms', 0) / 1000:.1f}s，记录 #{event.get('run_id')}",
                flush=True,
            )
            for item in event.get("errors") or []:
                print(f"  ! {item}", flush=True)
        elif kind == "error":
            print(f"\n[失败] {event.get('message')}", file=sys.stderr, flush=True)
            status = 1

    if report:
        if args.report:
            Path(args.report).write_text(report, encoding="utf-8")
            print(f"报告已写入 {args.report}")
        else:
            print("\n" + report)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 多智能体研究")
    parser.add_argument("symbol", help="股票代码，如 600519")
    parser.add_argument("--rounds", type=int, default=None, help="多空辩论轮数（默认取配置，1-4）")
    parser.add_argument("--report", help="把 markdown 报告写到指定文件")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
