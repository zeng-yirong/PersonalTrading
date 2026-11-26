"""Thematic research from the command line.

    python scripts/topic.py "市场情绪现在处于什么位置"
    python scripts/topic.py "白酒行业还能拿吗" --md data/baijiu.md
    python scripts/topic.py "白酒" --context          # 只看数据快照，不调模型
    python scripts/topic.py --today                   # 当日「一键复盘」（每日 18:20 定时跑）
    python scripts/topic.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.agents.llm import LLMError  # noqa: E402
from app.db import init_db  # noqa: E402
from app.topic import service  # noqa: E402
from app.topic.service import TopicError  # noqa: E402


def print_history(limit: int) -> None:
    items = service.history(limit)
    if not items:
        print("还没有主题报告")
        return
    print(f"{'#':<4}{'时间':<18}{'主题':<24}{'裁定':<8}{'信心':<6}标题")
    for item in items:
        print(
            f"{item['id']:<4}{str(item['created_at'])[:16]:<18}"
            f"{(item['topic'] or '')[:22]:<24}{(item['verdict'] or ''):<8}"
            f"{item['confidence']:<6}{item['title'] or ''}"
        )


def print_preview(payload: dict) -> None:
    print(f"数据截至：{payload['as_of']}")
    summary = payload["summary"]
    breadth = summary.get("breadth") or {}
    print(
        f"股票 {breadth.get('stocks')} 只，上涨 {breadth.get('up')} / 下跌 {breadth.get('down')}，"
        f"站上 MA20 {breadth.get('above_ma20_ratio')}%"
    )
    turnover = summary.get("turnover") or {}
    print(
        f"成交额 {turnover.get('amount_yi')} 亿（20 日均 {turnover.get('amount_20d_avg_yi')} 亿，"
        f"{turnover.get('ratio_to_20d_avg')} 倍）"
    )
    limits = summary.get("limits") or {}
    print(f"涨跌停：{limits.get('limit_up')} / {limits.get('limit_down')}（{limits.get('date')}）")
    print(f"匹配行业：{('、'.join(summary.get('matched_industries') or []) or '无')}")
    print(f"匹配题材：{('、'.join(summary.get('matched_concepts') or []) or '无')}")
    detail = payload.get("industry_detail") or []
    for item in detail:
        stats = item.get("stats") or {}
        print(
            f"  {item['industry']}：{item['count']} 只，ret5 {stats.get('ret5')}%、"
            f"ret20 {stats.get('ret20')}%、ret60 {stats.get('ret60')}%、"
            f"PE中位 {stats.get('pe_median')}、20日资金 {stats.get('mf_net_20d')} 亿"
        )
        for leader in (item.get("leaders") or [])[:3]:
            print(f"    强：{leader['name']}({leader['symbol']}) ret20 {leader['ret20']}%")
        for laggard in (item.get("laggards") or [])[:2]:
            print(f"    弱：{laggard['name']}({laggard['symbol']}) ret20 {laggard['ret20']}%")
    for gap in payload.get("data_gaps") or []:
        print(f"  ! {gap}")


async def main_async(args: argparse.Namespace) -> int:
    init_db()
    if args.list:
        print_history(args.limit_history)
        return 0

    if getattr(args, "today", False):
        return await run_today(args)

    topic = " ".join(args.topic or []).strip()
    if not topic:
        print("请给出研究主题，例如：python scripts/topic.py \"市场情绪怎么样\"", file=sys.stderr)
        return 1

    if args.context:
        payload = service.preview(topic)
        print_preview(payload)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"完整快照已写入 {args.json_out}")
        return 0

    print(f"正在研究「{topic}」：读取面板快照 → 生成报告（约 30-90 秒）…", file=sys.stderr)
    try:
        report = await service.research(
        topic, model=args.model or "", source_run_id=args.source_run
    )
    except (TopicError, LLMError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    if args.md:
        Path(args.md).write_text(report["markdown"], encoding="utf-8")
        print(f"报告已写入 {args.md}")
    if args.json_out:
        serialisable = {key: value for key, value in report.items() if key != "markdown"}
        Path(args.json_out).write_text(
            json.dumps(serialisable, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print()
    print(report["markdown"])
    print(
        f"—— #{report['id']}｜{report['model']}｜"
        f"tokens {report['usage']['prompt_tokens']}→{report['usage']['completion_tokens']}｜"
        f"{report['created_at']}"
    )
    return 0


async def run_today(args: argparse.Namespace) -> int:
    """Generate (or reuse) the daily brief — the headless twin of the button.

    Scheduled right after the 18:00 sync so every trading day has a brief; the
    brief's `watching` list is what the next day's reconciliation scores.
    """
    try:
        result = await service.daily_brief(force=args.force, model=args.model or "")
    except (TopicError, LLMError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    report = result["report"]
    if result.get("cached"):
        print(
            f"今天（{result.get('as_of')}）的复盘已存在，未重新生成："
            f"#{report['id']} {report.get('title') or ''}"
        )
        return 0

    if args.md:
        Path(args.md).write_text(report["markdown"], encoding="utf-8")
        print(f"报告已写入 {args.md}")
    print(report["markdown"])
    report_body = report.get("report") or {}
    print(
        f"—— #{report['id']}｜{report.get('model')}｜"
        f"阶段 {report_body.get('stage')}｜立场 {report_body.get('stance')}｜"
        f"tokens {report['usage']['prompt_tokens']}→{report['usage']['completion_tokens']}｜"
        f"{report['created_at']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 主题研究")
    parser.add_argument("topic", nargs="*", help="研究主题（大盘/行业/题材/情绪）")
    parser.add_argument("--md", help="把 markdown 报告写到文件")
    parser.add_argument("--json-out", help="把快照或报告 JSON 写到文件")
    parser.add_argument("--context", action="store_true", help="只看数据快照，不调用模型")
    parser.add_argument("--today", action="store_true", help="生成当日「一键复盘」（写入库，非交易日也可跑）")
    parser.add_argument("--force", action="store_true", help="配合 --today：忽略当天缓存，强制重算")
    parser.add_argument("--model", help="覆盖默认模型")
    parser.add_argument("--source-run", type=int, help="关联的推演编号")
    parser.add_argument("--list", action="store_true", help="列出历史报告")
    parser.add_argument("--limit-history", type=int, default=20)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
