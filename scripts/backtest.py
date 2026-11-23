"""Backtest a screening condition over the local panel.

    python scripts/backtest.py "市盈率低于30、ROE大于15%" --start 2025-01-01
    python scripts/backtest.py --json '{"filters":[{"field":"pe_ttm","op":"<=","value":20}]}'
    python scripts/backtest.py --list          # 历史回测
    python scripts/backtest.py "股息率超过4%" --rebalance 60 --top 5 --detail
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.backtest import service  # noqa: E402
from app.backtest.spec import BacktestError  # noqa: E402
from app.db import init_db  # noqa: E402
from app.screener import nl  # noqa: E402


def _width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def print_summary(result: dict) -> None:
    metrics = result["metrics"]
    spec = result["spec"]
    query = spec.get("query") or {}
    print()
    print(f"条件：{query.get('title') or '—'}  ——  {query.get('explanation') or ''}")
    print(
        f"区间：{metrics['start']} → {metrics['end']}"
        f"（{metrics['trading_days']} 个交易日，{metrics['periods']} 期，"
        f"{spec['rebalance']} 日调仓，最多 {spec['top_n']} 只）"
    )
    if result.get("id"):
        print(f"运行编号：#{result['id']}（--list 可回看）")
    print()
    print(f"  策略收益    {metrics['total_return_pct']:>10.2f}%   年化 {metrics['cagr_pct']:>8.2f}%")
    print(f"  基准收益    {metrics['benchmark_return_pct']:>10.2f}%   年化 {metrics['benchmark_cagr_pct']:>8.2f}%")
    print(f"  超额年化    {metrics['excess_cagr_pct']:>10.2f}%   胜率 {metrics['hit_rate_pct']:>6.1f}%")
    print(f"  最大回撤    {metrics['max_drawdown_pct']:>10.2f}%   夏普 {metrics['sharpe']:>8.2f}")
    print(
        f"  波动率      {metrics['volatility_pct']:>10.2f}%   "
        f"仓位 {metrics['exposure_pct']:>6.1f}%   "
        f"平均持仓 {metrics['avg_positions']:>4.1f} 只"
    )
    print(
        f"  成交 {metrics['trades']:>4} 笔   费用 {metrics['fees']:>10,.0f} 元   "
        f"空仓期 {metrics['empty_periods']} 个"
    )
    for warning in result.get("warnings") or []:
        print(f"  ! {warning}")


def print_periods(result: dict) -> None:
    periods = result.get("periods") or []
    if not periods:
        return
    headers = ["#", "调仓日", "建仓日", "平仓日", "持仓", "本期", "基准", "超额"]
    rows = [
        [
            str(item["index"]),
            item["signal_date"],
            item["entry_date"],
            item["exit_date"],
            str(item["positions"]),
            f"{item['return_pct']:+.2f}%",
            f"{item['benchmark_pct']:+.2f}%",
            f"{item['excess_pct']:+.2f}%",
        ]
        for item in periods
    ]
    widths = [
        max(_width(headers[i]), max(_width(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    print()
    print("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(row))))

    missing = [item for item in periods if item.get("note")]
    if missing:
        print()
        for item in missing[:5]:
            print(f"  #{item['index']} {item['entry_date']}：{item['note']}")


def print_history(limit: int = 20) -> None:
    runs = service.history(limit)
    if not runs:
        print("还没有回测记录")
        return
    headers = ["#", "时间", "条件", "区间", "收益", "基准", "超额年化", "期数"]
    rows = []
    for item in runs:
        metrics = item["metrics"]
        spec = item["spec"]
        rows.append(
            [
                str(item["id"]),
                str(item["created_at"])[:16],
                (item["title"] or "—")[:16],
                f"{spec.get('start')}~{spec.get('end')}",
                f"{metrics.get('total_return_pct') or 0:+.2f}%",
                f"{metrics.get('benchmark_return_pct') or 0:+.2f}%",
                f"{metrics.get('excess_cagr_pct') or 0:+.2f}%",
                str(metrics.get("periods")),
            ]
        )
    widths = [max(_width(headers[i]), max(_width(r[i]) for r in rows)) for i in range(len(headers))]
    print("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(row))))


async def build_query(args: argparse.Namespace) -> dict:
    if args.json_file:
        # utf-8-sig: PowerShell's Set-Content writes a BOM that json 无法直接解析
        return json.loads(Path(args.json_file).read_text(encoding="utf-8-sig"))
    if args.json:
        return json.loads(args.json)
    text = " ".join(args.text or []).strip()
    if not text:
        raise BacktestError("请给出筛选条件（自然语言或 --json/--json-file），或用 --list 查看历史")
    parsed = await nl.parse(text)
    return parsed.to_dict()


async def main_async(args: argparse.Namespace) -> int:
    init_db()
    if args.list:
        print_history(args.limit_history)
        return 0
    try:
        query = await build_query(args)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    payload = {"query": query}
    for key, value in (
        ("start", args.start),
        ("end", args.end),
        ("rebalance", args.rebalance),
        ("top_n", args.top),
        ("initial_cash", args.cash),
        ("slippage_bps", args.slippage),
    ):
        if value is not None:
            payload[key] = value

    try:
        result = service.run(payload, persist=not args.no_persist)
    except BacktestError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"完整结果已写入 {args.json_out}")
    print_summary(result)
    if args.detail:
        print_periods(result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 选股条件回测")
    parser.add_argument("text", nargs="*", help="自然语言筛选条件")
    parser.add_argument("--json", help="结构化查询 JSON（与选股器一致）")
    parser.add_argument("--json-file", help="从文件读取结构化查询 JSON（Windows 下免去引号转义）")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD（默认：结束日前两年）")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD（默认：面板最新日期）")
    parser.add_argument("--rebalance", type=int, help="调仓周期（交易日，默认 20）")
    parser.add_argument("--top", type=int, help="每期持仓数量（默认 10）")
    parser.add_argument("--cash", type=float, help="初始资金（默认 100 万）")
    parser.add_argument("--slippage", type=float, help="滑点（基点，默认 5）")
    parser.add_argument("--detail", action="store_true", help="打印每期明细")
    parser.add_argument("--json-out", help="把完整结果写到文件")
    parser.add_argument("--no-persist", action="store_true", help="不写历史记录")
    parser.add_argument("--list", action="store_true", help="列出历史回测")
    parser.add_argument("--limit-history", type=int, default=20)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
