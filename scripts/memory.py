"""记忆检索的 CLI：查看、搜索、重建索引。

    python scripts/memory.py stats
    python scripts/memory.py recent --limit 10
    python scripts/memory.py search 白酒 --as-of 2026-11-27
    python scripts/memory.py rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents import memory  # noqa: E402
from app.db import init_db  # noqa: E402


def _print(items: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        print("没有匹配的记忆。")
        return
    for item in items:
        print(
            f"{item.get('as_of') or '—'}｜{item.get('kind')} #{item.get('ref_id')}"
            f"｜{item.get('symbol') or '—'}｜{item.get('decision') or '—'}"
            f"｜事后：{item.get('outcome') or '待验证'}｜{item.get('title') or ''}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="记忆检索 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="索引条数与已判定数")
    recent = sub.add_parser("recent", help="最近的记忆")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="全文检索")
    search.add_argument("text")
    search.add_argument("--as-of")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")

    sub.add_parser("rebuild", help="从源表重建索引")

    args = parser.parse_args(argv)
    init_db()

    if args.command == "stats":
        print(json.dumps(memory.stats(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "rebuild":
        print(f"重建完成：{memory.rebuild()} 条")
        return 0
    if args.command == "recent":
        _print(memory.recent(args.limit), args.json)
        return 0
    _print(memory.search(args.text, as_of=args.as_of, limit=args.limit), args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
