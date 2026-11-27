"""每周把全部预设策略跑一遍近一年回测，按超额重排。

    python scripts/preset_backtest.py              # 全部跑一遍并留档
    python scripts/preset_backtest.py --top 5      # 只打印前 5 名
    python scripts/preset_backtest.py --no-save    # 只看不存

12 个预设共用同一段区间（近一年）、同一调仓周期与持仓数，按**超额年化**排序——
只看绝对收益的话，普涨年份所有策略都"很好"。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import init_db  # noqa: E402
from app.screener import ranking  # noqa: E402


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="预设策略全量回测与排名")
    parser.add_argument("--rebalance", type=int, default=20, help="调仓周期（交易日）")
    parser.add_argument("--top-n", type=int, default=10, help="持仓数")
    parser.add_argument("--top", type=int, default=0, help="只打印前 N 名（0 = 全部）")
    parser.add_argument("--no-save", action="store_true", help="不写库，只看结果")
    args = parser.parse_args(argv)

    init_db()
    batch = ranking.run_all(
        rebalance=args.rebalance, top_n=args.top_n, persist=not args.no_save
    )
    rows = batch["rows"][: args.top or None]

    print(
        f"区间 {batch.get('start')} → {batch.get('end')}｜调仓 {batch['rebalance']} 日"
        f"｜持仓 {batch['top_n']}｜按 {ranking.RANK_FIELD} 排序"
    )
    print(f"{'#':<3}{'预设':<12}{'区间收益':>10}{'年化':>9}{'超额年化':>10}{'回撤':>8}{'胜率':>7}{'仓位':>7}")
    for row in rows:
        print(
            f"{row['rank']:<3}{row['label']:<12}"
            f"{_fmt(row['total_return_pct'], 2, '%'):>10}"
            f"{_fmt(row['cagr_pct'], 2, '%'):>9}"
            f"{_fmt(row['excess_cagr_pct'], 2, '%'):>10}"
            f"{_fmt(row['max_drawdown_pct'], 1, '%'):>8}"
            f"{_fmt(row['hit_rate_pct'], 0, '%'):>7}"
            f"{_fmt(row['exposure_pct'], 0, '%'):>7}"
        )
    failed = [row for row in batch["rows"] if row["status"] != "ok"]
    if failed:
        print(f"\n{len(failed)} 个预设没有跑出结果：")
        for row in failed:
            print(f"  - {row['label']}：{row.get('error') or '无数据'}")
    if batch.get("id"):
        print(f"\n已留档批次 #{batch['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
