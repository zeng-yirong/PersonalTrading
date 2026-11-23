"""Operator CLI: decision ledger, token usage, data health.

    python scripts/ops.py ledger --limit 20
    python scripts/ops.py ledger --kind 推演
    python scripts/ops.py summary
    python scripts/ops.py usage --days 30
    python scripts/ops.py health
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import init_db  # noqa: E402
from app.ops import health as data_health  # noqa: E402
from app.ops import ledger  # noqa: E402


def _width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(_width(headers[i]), max((_width(row[i]) for row in rows), default=0))
        for i in range(len(headers))
    ]
    print("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(row))))


def cmd_ledger(args: argparse.Namespace) -> int:
    items = ledger.entries(args.limit, args.kind)
    if not items:
        print("账本还是空的：先跑一次推演 / 主题 / 回测")
        return 0
    rows = [
        [
            item["kind"],
            str(item["created_at"])[:16],
            item["title"][:24],
            item["as_of"] or "—",
            str(item["stance"] or "—")[:18],
            "—" if item["score"] is None else f"{item['score']:.2f}",
            str(item["review"] or "—")[:12],
            f"{item['tokens']:,}" if item["tokens"] else "—",
        ]
        for item in items
    ]
    _table(["类型", "时间", "标题", "数据日", "结论", "仓位/分数", "复盘", "tokens"], rows)
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    summary = ledger.summary()
    counts = summary["counts"]
    reviews = summary["reviews"]
    tokens = summary["tokens"]
    print("记录：", "｜".join(f"{key} {value}" for key, value in counts.items()))
    rate = reviews["hit_rate_pct"]
    print(
        f"复盘：{reviews['reviewed']} 条，正确 {reviews['correct']} / 错误 {reviews['wrong']}"
        f" / 待验证 {reviews['pending']}"
        + (f"，命中率 {rate}%" if rate is not None else "")
    )
    print(f"tokens：推演 {tokens['agent']:,}，主题 {tokens['topic']:,}，合计 {tokens['total']:,}")
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    payload = ledger.usage(args.days)
    series = payload["series"]
    if not series:
        print("最近没有调用记录")
        return 0
    rows = [
        [
            item["date"],
            str(item["runs"]),
            str(item["topics"]),
            str(item["backtests"]),
            str(item["factors"]),
            f"{item['tokens']:,}",
            f"{item['seconds']:.0f}s",
        ]
        for item in series
    ]
    _table(["日期", "推演", "主题", "回测", "因子", "tokens", "推演耗时"], rows)
    print(f"\n合计 {payload['total_tokens']:,} tokens，{payload['total_tasks']} 个任务")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    payload = data_health.snapshot()
    fingerprint = payload.get("fingerprint") or {}
    print(f"面板截至 {payload.get('as_of')}｜指纹 {fingerprint.get('digest')}｜检查于 {payload.get('checked_at')}\n")
    rows = [
        [
            item["label"],
            item["table"],
            item["last_date"] or "—",
            f"{item['rows']:,}" if item["rows"] else "0",
            "—" if item["lag_days"] is None else str(item["lag_days"]),
            {"ok": "正常", "stale": "滞后", "empty": "空", "missing": "缺表"}.get(item["status"], item["status"]),
        ]
        for item in payload["tables"]
    ]
    _table(["表", "名称", "最新日期", "行数", "滞后(交易日)", "状态"], rows)
    if payload["issues"]:
        print("\n问题：")
        for issue in payload["issues"]:
            print(f"  ! {issue}")
    else:
        print("\n所有关键表都在预期滞后内。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 运维视图")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ledger = sub.add_parser("ledger", help="统一决策账本")
    p_ledger.add_argument("--limit", type=int, default=30)
    p_ledger.add_argument("--kind", default="", choices=["", "推演", "主题", "回测", "成交"])
    p_ledger.set_defaults(func=cmd_ledger)

    p_summary = sub.add_parser("summary", help="账本汇总与 token 总量")
    p_summary.set_defaults(func=cmd_summary)

    p_usage = sub.add_parser("usage", help="按日 token 与任务量")
    p_usage.add_argument("--days", type=int, default=30)
    p_usage.set_defaults(func=cmd_usage)

    p_health = sub.add_parser("health", help="数据健康检查")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
