"""经验记忆 CLI（P8-0）：抽取、查看候选、检查影子召回。

    python scripts/experience.py extract
    python scripts/experience.py stats
    python scripts/experience.py list --status candidate
    python scripts/experience.py recalls --run-id 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import experience  # noqa: E402
from app.agents import replay  # noqa: E402
from app.db import init_db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="经验记忆 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("extract", help="从已复盘记录抽取候选经验")
    sub.add_parser("stats", help="经验卡与影子召回统计")

    listing = sub.add_parser("list", help="列出经验卡")
    listing.add_argument("--status", default="")
    listing.add_argument("--scope", default="")
    listing.add_argument("--q", default="")
    listing.add_argument("--limit", type=int, default=50)

    recalls = sub.add_parser("recalls", help="影子召回记录")
    recalls.add_argument("--run-id", type=int)
    recalls.add_argument("--limit", type=int, default=50)

    status = sub.add_parser("status", help="人工晋级 / 退役")
    status.add_argument("--id", type=int, required=True)
    status.add_argument("--status", required=True, choices=["candidate", "active", "retired"])
    status.add_argument("--reason", required=True)

    graduate = sub.add_parser("graduate", help="固化到 skill / guard 后退役")
    graduate.add_argument("--id", type=int, required=True)
    graduate.add_argument("--target", required=True)
    graduate.add_argument("--reason", required=True)

    sub.add_parser("evaluation", help="影子召回精确率")
    demo = sub.add_parser("demo", help="写入 / 清除演示经验卡")
    demo.add_argument("--clear", action="store_true")
    paired = sub.add_parser("paired", help="experience on/off 配对（花钱）")
    paired.add_argument("--live", action="store_true")
    paired.add_argument("--limit", type=int, default=30)
    paired.add_argument("--model", default="")

    args = parser.parse_args(argv)
    init_db()

    if args.command == "extract":
        print(json.dumps(experience.extract(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "stats":
        print(json.dumps(experience.stats(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "list":
        items = experience.list_experiences(
            status=args.status, scope=args.scope, q=args.q, limit=args.limit
        )
        for item in items:
            print(
                f"{item['status']:<9} {item['scope']}/{item['scope_key']} "
                f"n={item['evidence_count']} conf={item['confidence']} "
                f"as_of={item['as_of']}｜{item['lesson']}"
            )
        if not items:
            print("没有匹配的经验卡。")
        return 0
    if args.command == "status":
        try:
            item = experience.change_status(args.id, args.status, args.reason)
        except ValueError as exc:
            print(f"失败：{exc}")
            return 1
        if item is None:
            print("失败：经验卡不存在")
            return 1
        print(f"已更新：{item['status']}｜{item['lesson']}")
        return 0
    if args.command == "graduate":
        try:
            item = experience.graduate(args.id, args.target, args.reason)
        except ValueError as exc:
            print(f"失败：{exc}")
            return 1
        if item is None:
            print("失败：经验卡不存在")
            return 1
        print(f"已固化到 {item['graduated_to']} 并退役：{item['lesson']}")
        return 0
    if args.command == "evaluation":
        payload = {
            "preflight": experience.preflight(),
            "shadow": experience.evaluation(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "demo":
        if args.clear:
            print(f"已清除 {experience.clear_demo()} 张演示卡及其召回记录")
            return 0
        items = experience.seed_demo()
        for item in items:
            print(
                f"#{item['id']} {item['status']:<9} {item['scope']}/{item['scope_key']}"
                f"｜{item['lesson']}"
            )
        print("打开「运维 → 经验」即可看到；测试后执行 `python scripts/experience.py demo --clear`。")
        return 0
    if args.command == "paired":
        gate = experience.preflight()
        print(f"active {gate['active_cards']}｜已判定 {gate['judged_runs']}/30｜"
              f"可配对 {gate['pairable_runs']}/30｜影子召回 {gate['shadow_recalls']}")
        if gate["active_cards"] < 1 or gate["judged_runs"] < 30 or gate["pairable_runs"] < 30:
            print("样本不足：不跑配对，也不下结论。")
            return 0
        if not args.live:
            print("门槛已过；加 --live 会跑 experience on/off（每条约 10 次调用）。")
            return 0
        payload = asyncio.run(
            replay.run_paired(limit=args.limit, model=args.model, layer="experience")
        )
        summary = payload["summary"]
        print(f"配对 {summary['n']} 条｜调用 {payload['calls'].get('calls', 0)} 次")
        for key in ("brier", "excess"):
            block = summary[key]
            print(f"{key}: mean={block['mean']} CI=[{block['low']}, {block['high']}]")
        return 0
    rows = experience.recalls(limit=args.limit, run_id=args.run_id)
    for row in rows:
        print(
            f"run={row['run_id']} exp={row['experience_id']} "
            f"score={row['score']} status={row['status']} injected={row['injected']}"
            f"｜{row['lesson']}"
        )
    if not rows:
        print("没有影子召回记录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
