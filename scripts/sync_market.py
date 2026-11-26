"""Build and update the local market panel.

Examples
--------
    python scripts/sync_market.py status
    python scripts/sync_market.py calendar
    python scripts/sync_market.py universe
    python scripts/sync_market.py snapshot
    python scripts/sync_market.py history --limit 20 --start 2020-01-01
    python scripts/sync_market.py history --symbols 600000,000001 --incremental
    python scripts/sync_market.py all
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.data import store, sync  # noqa: E402
from app.data.providers import list_providers  # noqa: E402


def _parse_date(text: str | None) -> date | None:
    return datetime.strptime(text, "%Y-%m-%d").date() if text else None


def _print_rows(payload: dict) -> None:
    for key, value in payload.items():
        if key == "errors" and value:
            print(f"  {key}:")
            for item in value:
                print(f"    - {item['symbol']}: {item['error']}")
            continue
        print(f"  {key}: {value}")


def cmd_status(args: argparse.Namespace) -> int:
    payload = sync.status()
    print(f"市场数据库: {payload['db_path']}")
    coverage = payload["coverage"]
    print("覆盖情况:")
    for key, value in coverage.items():
        print(f"  {key}: {value}")
    print("最近同步:")
    if not payload["recent_sync"]:
        print("  （无记录）")
    for item in payload["recent_sync"]:
        print(
            f"  {item['finished_at']}  {item['job']:<9} {item['status']:<11} "
            f"rows={item['rows']:<8} {item['detail']}"
        )
    print("数据源:")
    for item in list_providers():
        flag = "可用" if item["available"] else f"不可用（{item.get('reason', '')}）"
        print(f"  {item['name']:<10} {item['label']:<12} {flag}")
    return 0


def cmd_calendar(args: argparse.Namespace) -> int:
    store.init_schema()
    print("同步交易日历…")
    _print_rows(sync.sync_calendar(args.provider))
    return 0


def cmd_universe(args: argparse.Namespace) -> int:
    store.init_schema()
    print("同步股票列表…")
    _print_rows(sync.sync_universe(args.provider))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    store.init_schema()
    print("抓取全市场快照…")
    _print_rows(
        sync.sync_snapshot(args.provider, allow_intraday=args.allow_intraday)
    )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    store.init_schema()
    symbols = [item.strip() for item in (args.symbols or "").split(",") if item.strip()] or None

    def progress(info: dict) -> None:
        if info["done"] % 20 == 0 or info["done"] == info["total"]:
            print(
                f"  [{info['done']}/{info['total']}] 最新 {info['symbol']} "
                f"累计 {info['rows']} 行 失败 {info['failed']}",
                flush=True,
            )

    print(f"同步历史行情（{'增量' if args.incremental else '全量'}）…")
    payload = sync.sync_history(
        prefer=args.provider,
        symbols=symbols,
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        with_adjust=not args.no_adjust,
        incremental=args.incremental,
        limit=args.limit,
        concurrency=args.concurrency,
        progress=progress,
    )
    _print_rows(payload)
    return 0 if payload["status"] == "ok" else 1


def cmd_all(args: argparse.Namespace) -> int:
    store.init_schema()
    print("同步日历…")
    _print_rows(sync.sync_calendar(args.provider))
    print("同步股票列表…")
    _print_rows(sync.sync_universe(args.provider))
    print("抓取全市场快照…")
    _print_rows(sync.sync_snapshot(args.provider, allow_intraday=args.allow_intraday))
    if args.no_history:
        return 0
    print("补齐历史行情（增量）…")
    payload = sync.sync_history(prefer=args.provider, incremental=True, limit=args.limit)
    _print_rows(payload)
    return 0 if payload["status"] == "ok" else 1


def cmd_backfill(args: argparse.Namespace) -> int:
    from app.data import backfill

    store.init_schema()
    datasets = [item.strip() for item in (args.datasets or "").split(",") if item.strip()] or None
    if args.dry_run:
        print("回填计划：")
        for item in backfill.plan(datasets, args.years):
            if "error" in item:
                print(f"  {item['dataset']:<22} 无法规划：{item['error']}")
                continue
            flag = "日更" if item["daily"] else "   "
            print(
                f"  {item['dataset']:<22} {item['mode']:<7} {flag} "
                f"共 {item['parts']:>5} 段，已完成 {item['finished']:>5}，待拉 {item['pending']:>5}"
            )
        return 0

    def progress(info: dict) -> None:
        if info["done"] % 50 == 0 or info["done"] == info["total"]:
            print(
                f"  [{info['dataset']}] {info['done']}/{info['total']} "
                f"行数 {info['rows']} 错误 {info['errors']}",
                flush=True,
            )

    print(f"开始回填（{args.years} 年，{args.workers} 并发）…")
    summary = backfill.run(
        names=datasets,
        years=args.years,
        workers=args.workers,
        force=args.force,
        max_parts=args.max_parts,
        progress=progress,
        dates=[item.strip() for item in (args.date or "").split(",") if item.strip()] or None,
    )
    failures = 0
    print("\n回填结果：")
    for item in summary:
        if item["status"] == "up-to-date":
            print(f"  {item['dataset']:<22} 已最新（跳过 {item['skipped']} 段）")
            continue
        if item["status"] == "quota-exhausted":
            print(
                f"  {item['dataset']:<22} 每日配额已用完，本轮停止。"
                "已完成的分段已落库，次日重跑同一命令即可续传。"
            )
            failures += 1
            continue
        flag = "" if item["status"] == "ok" else "  ← 有错误"
        print(
            f"  {item['dataset']:<22} {item['parts']:>5} 段 "
            f"{item['rows']:>9} 行 {item['status']}{flag}"
        )
        for error in item.get("errors", []):
            print(f"      {error}")
        failures += 0 if item["status"] == "ok" else 1
    return 0 if failures == 0 else 1


def cmd_day(args: argparse.Namespace) -> int:
    from app.data import backfill

    target = _parse_date(args.date) or datetime.now().date()
    print(f"更新 {target} 的当日数据…")
    store.init_schema()
    results = backfill.update_today(target)
    total = 0
    backfilled = 0
    for item in results:
        total += item["rows"]
        note = f"  ← {item['error']}" if item.get("error") else ""
        filled = item.get("filled") or []
        if filled:
            backfilled += sum(entry["rows"] for entry in filled)
            note = "  ← 今日未发布；已回补 " + "、".join(
                f"{entry['date']} {entry['rows']} 行" for entry in filled
            )
        print(f"  {item['dataset']:<22} {item['rows']:>7} 行 {item['status']}{note}")
    suffix = f"（另回补前几日 {backfilled} 行）" if backfilled else ""
    print(f"合计 {total} 行{suffix}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from app.data import backfill

    for item in backfill.plan(None, args.years):
        if "error" in item:
            print(f"  {item['dataset']:<22} 无法规划：{item['error']}")
            continue
        print(
            f"  {item['dataset']:<22} {item['mode']:<7} "
            f"共 {item['parts']:>5} 段，待拉 {item['pending']:>5}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--provider",
        default="auto",
        help="auto / tushare / sina / eastmoney / akshare",
    )

    parser = argparse.ArgumentParser(description="PersonalTrading 市场数据同步", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看数据库覆盖与数据源状态", parents=[common])

    sub.add_parser("calendar", help="同步交易日历", parents=[common])
    sub.add_parser("universe", help="同步股票 / ETF 列表", parents=[common])

    snap = sub.add_parser("snapshot", help="抓取全市场当日截面", parents=[common])
    snap.add_argument("--allow-intraday", action="store_true", help="允许盘中抓取（不推荐）")

    hist = sub.add_parser("history", help="同步单只或多只标的历史 K 线", parents=[common])
    hist.add_argument("--symbols", help="逗号分隔的代码，默认全部")
    hist.add_argument("--start", help="起始日期 YYYY-MM-DD")
    hist.add_argument("--end", help="结束日期 YYYY-MM-DD")
    hist.add_argument("--limit", type=int, help="只同步前 N 只（调试用）")
    hist.add_argument("--incremental", action="store_true", help="从已有数据的最后日期继续")
    hist.add_argument("--no-adjust", action="store_true", help="不抓取复权因子")
    hist.add_argument("--concurrency", type=int, help="并发线程数，默认 4")

    every = sub.add_parser("all", help="日历 + 列表 + 快照 + 增量历史", parents=[common])
    every.add_argument("--allow-intraday", action="store_true")
    every.add_argument("--no-history", action="store_true")
    every.add_argument("--limit", type=int)

    fill = sub.add_parser("backfill", help="从 Tushare 批量下载有权限的全部历史", parents=[common])
    fill.add_argument("--datasets", help="逗号分隔的数据集名，默认全部")
    fill.add_argument("--years", type=int, default=10, help="回溯年数，默认 10")
    fill.add_argument("--workers", type=int, default=4, help="并发请求数，默认 4")
    fill.add_argument("--max-parts", type=int, help="每个数据集只拉前 N 段（调试用）")
    fill.add_argument("--force", action="store_true", help="忽略断点，重拉已完成的分段")
    fill.add_argument("--date", help="只补指定日期（YYYY-MM-DD，可逗号分隔），用于修复残缺日")
    fill.add_argument("--dry-run", action="store_true", help="只打印计划，不实际拉取")

    sub.add_parser("plan", help="查看回填计划与进度", parents=[common]).add_argument(
        "--years", type=int, default=10
    )

    day = sub.add_parser("day", help="只追加某一天的日更数据", parents=[common])
    day.add_argument("--date", help="YYYY-MM-DD，默认今天")

    return parser


COMMANDS = {
    "status": cmd_status,
    "calendar": cmd_calendar,
    "universe": cmd_universe,
    "snapshot": cmd_snapshot,
    "history": cmd_history,
    "all": cmd_all,
    "backfill": cmd_backfill,
    "plan": cmd_plan,
    "day": cmd_day,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = COMMANDS[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\n已中断（已完成的数据已落库，可重复运行续传）")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"\n[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
