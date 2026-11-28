"""经验记忆 CLI（P8-0）：抽取、查看候选、检查影子召回。

    python scripts/experience.py extract
    python scripts/experience.py stats
    python scripts/experience.py list --status candidate
    python scripts/experience.py recalls --run-id 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import experience  # noqa: E402
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
