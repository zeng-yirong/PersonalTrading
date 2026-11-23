"""Paper trading from the command line.

    python scripts/paper.py summary
    python scripts/paper.py buy 600519 --amount 100000
    python scripts/paper.py sell 600519 --shares 400
    python scripts/paper.py from-run 8
    python scripts/paper.py nav
    python scripts/paper.py review
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.paper import review as review_module  # noqa: E402
from app.paper import service  # noqa: E402


def cmd_summary(_: argparse.Namespace) -> int:
    data = service.summary()
    account = data["account"]
    print(
        f"账户：{account['name']}  初始 {account['initial_cash']:,.0f} 元\n"
        f"现金 {data['cash']:,.2f} ｜ 持仓市值 {data['market_value']:,.2f} ｜ "
        f"总资产 {data['total']:,.2f} 元\n"
        f"盈亏 {data['pnl']:+,.2f} 元（{data['pnl_pct']:+.2f}%）\n"
    )
    if not data["positions"]:
        print("（空仓）")
        return 0
    print(f"{'代码':<8}{'名称':<10}{'股数':>8}{'成本':>10}{'现价':>10}{'市值':>14}{'盈亏%':>9}")
    for item in data["positions"]:
        print(
            f"{item['symbol']:<8}{(item['name'] or ''):<10}{item['shares']:>8,.0f}"
            f"{item['avg_cost']:>10.2f}{(item['price'] or 0):>10.2f}"
            f"{(item['market_value'] or 0):>14,.0f}{(item['pnl_pct'] or 0):>9.2f}"
        )
    return 0


def cmd_trade(args: argparse.Namespace) -> int:
    result = service.trade(
        args.symbol, args.side, shares=args.shares, amount=args.amount, note=args.note or ""
    )
    print(
        f"{result['trade_date']} {result['side']} {result['name']}（{result['symbol']}）"
        f" {result['shares']:,.0f} 股 @ {result['price']:.2f}，"
        f"金额 {result['gross']:,.2f}，费用 {result['fee']:,.2f}"
    )
    return cmd_summary(args)


def cmd_from_run(args: argparse.Namespace) -> int:
    result = service.trade_from_run(args.run_id, args.fraction)
    print(
        f"按推演 #{args.run_id} 建仓：{result['name']}（{result['symbol']}）"
        f" {result['shares']:,.0f} 股 @ {result['price']:.2f}，费用 {result['fee']:,.2f}"
    )
    return cmd_summary(args)


def cmd_nav(args: argparse.Namespace) -> int:
    series = service.nav_series()
    if not series:
        print("还没有交易，净值曲线为空")
        return 0
    print(f"{'日期':<12}{'净值':>8}  {'总资产':>14}  {'基准':>8}{'超额%':>9}")
    for item in series[-args.limit:]:
        excess = (
            (item["nav"] / item["benchmark"] - 1) * 100
            if item.get("benchmark")
            else None
        )
        print(
            f"{item['trade_date']:<12}{item['nav']:>8.4f}  {item['total']:>14,.0f}  "
            f"{(item['benchmark'] or 0):>8.4f}{(excess or 0):>9.2f}"
        )
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    result = review_module.refresh()
    print(f"复盘刷新：{result['written']} 条（跳过 {result['skipped']}）\n")
    rows = review_module.list_reviews(args.limit)
    if not rows:
        print("（暂无复盘记录）")
        return 0
    print(f"{'推演':<6}{'标的':<10}{'入场':<12}{'T+5':>9}{'T+20':>9}{'超额':>9}  {'结论'}")
    for row in rows:
        print(
            f"#{row['run_id']:<5}{row['symbol']:<10}{row['entry_date']:<12}"
            f"{(row['d5_return'] if row['d5_return'] is not None else 0):>9.2f}"
            f"{(row['d20_return'] if row['d20_return'] is not None else 0):>9.2f}"
            f"{(row['d20_excess'] if row['d20_excess'] is not None else 0):>9.2f}"
            f"  {row['outcome']}"
        )
    stats = review_module.stats()
    print()
    if stats.get("total"):
        print(
            f"统计：{stats['total']} 条已满 20 日，命中率 {stats['hit_rate']}%，"
            f"平均收益 {stats['avg_return_20d']}%，平均超额 {stats['avg_excess_20d']}%"
        )
        for stance, bucket in stats["by_stance"].items():
            print(
                f"  {stance}: {bucket['count']} 条，命中 {bucket['hit_rate']}%，"
                f"平均 {bucket['avg_return']}%"
            )
    else:
        print(stats.get("message", ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 模拟持仓")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="账户概览")

    buy = sub.add_parser("buy", help="买入")
    buy.add_argument("symbol")
    buy.add_argument("--amount", type=float, help="按金额买入（自动取整到 100 股）")
    buy.add_argument("--shares", type=float, help="按股数买入")
    buy.add_argument("--note", default="")
    buy.set_defaults(side="buy")

    sell = sub.add_parser("sell", help="卖出")
    sell.add_argument("symbol")
    sell.add_argument("--shares", type=float, help="卖出股数")
    sell.add_argument("--amount", type=float, help="按金额卖出")
    sell.add_argument("--note", default="")
    sell.set_defaults(side="sell")

    from_run = sub.add_parser("from-run", help="按推演裁定建仓")
    from_run.add_argument("run_id", type=int)
    from_run.add_argument("--fraction", type=float, default=1.0, help="只建建议仓位的一部分")

    nav = sub.add_parser("nav", help="净值曲线")
    nav.add_argument("--limit", type=int, default=20)

    rev = sub.add_parser("review", help="复盘与命中率")
    rev.add_argument("--limit", type=int, default=20)

    reset = sub.add_parser("reset", help="重置账户")
    reset.add_argument("--cash", type=float, default=service.DEFAULT_CASH)

    args = parser.parse_args()
    try:
        if args.command == "summary":
            return cmd_summary(args)
        if args.command in {"buy", "sell"}:
            return cmd_trade(args)
        if args.command == "from-run":
            return cmd_from_run(args)
        if args.command == "nav":
            return cmd_nav(args)
        if args.command == "review":
            return cmd_review(args)
        if args.command == "reset":
            service.reset(args.cash)
            print(f"账户已重置，初始资金 {args.cash:,.0f} 元")
            return 0
    except service.PaperError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
