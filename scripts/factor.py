"""Rank factors by how well they separate future returns.

    python scripts/factor.py                          # 最近三年、月度、20 日前瞻
    python scripts/factor.py --start 2021-01-01 --horizon 60 --rebalance 10
    python scripts/factor.py --top 12 --detail
    python scripts/factor.py --factor ret20,pe_ttm,mf_net_20d
    python scripts/factor.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import init_db  # noqa: E402
from app.factor import library, service  # noqa: E402
from app.factor.evaluate import FactorError  # noqa: E402


def _width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def print_table(factors: list[dict], top: int) -> None:
    with_oos = any("passed" in item for item in factors)
    with_inc = any(item.get("incremental") for item in factors)
    headers = ["#", "因子", "类别", "覆盖", "IC均值", "ICIR", "t值", "正IC占比", "分层价差"]
    if with_oos:
        headers += ["样本外IC", "样本外ICIR", "结论"]
    if with_inc:
        headers += ["残差IC", "残差ICIR", "增量结论"]
    rows = []
    for index, item in enumerate(factors[:top], start=1):
        row = [
            str(index),
            item["name"],
            item["category"],
            f"{item['coverage'] * 100:.0f}%",
            f"{item['ic_mean']:+.3f}",
            f"{item['icir']:+.3f}",
            f"{item['t_stat']:+.2f}",
            f"{item['positive_ratio']:.0f}%",
            "—" if item["spread_pct"] is None else f"{item['spread_pct']:+.2f}%",
        ]
        if with_oos:
            oos = item.get("out_of_sample") or {}
            row += [
                f"{oos['ic_mean']:+.3f}" if oos else "—",
                f"{oos['icir']:+.3f}" if oos else "—",
                "通过" if item.get("passed") else "未通过",
            ]
        if with_inc:
            inc = item.get("incremental") or {}
            row += [
                f"{inc['ic_mean']:+.3f}" if inc.get("ic_mean") is not None else "—",
                f"{inc['icir']:+.3f}" if inc.get("icir") is not None else "—",
                "有增量" if inc.get("passed") else "无增量",
            ]
        rows.append(row)
    widths = [
        max(_width(headers[i]), max(_width(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    print("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(row))))


def print_detail(factors: list[dict], names: list[str]) -> None:
    for item in factors:
        if names and item["name"] not in names:
            continue
        print()
        print(
            f"{item['label']}（{item['name']}）｜{item['category']}｜"
            f"IC {item['ic_mean']:+.3f}｜ICIR {item['icir']:+.3f}｜"
            f"t {item['t_stat']:+.2f}｜正 IC {item['positive_ratio']:.0f}%｜"
            f"{item['n_dates']} 期｜覆盖 {item['coverage'] * 100:.0f}%｜"
            f"先验 {item['expected']}"
        )
        if not item["quantiles"]:
            print("  分层：该因子取值过少，不做分组（只看秩相关）")
            continue
        print("  分层（第 1 组=因子值最小）：")
        for group in item["quantiles"]:
            bar = "█" * max(0, int(abs(group["mean_pct"]) * 8))
            sign = "+" if group["mean_pct"] >= 0 else "-"
            print(
                f"    Q{group['q']}  平均 {group['mean_pct']:+7.2f}%  "
                f"中位 {group['median_pct']:+7.2f}%  {sign}{bar}"
            )


def print_history(limit: int) -> None:
    runs = service.history(limit)
    if not runs:
        print("还没有因子评估记录")
        return
    for item in runs:
        spec = item["spec"]
        top = item["top_factor"]
        print(
            f"#{item['id']}  {str(item['created_at'])[:16]}  "
            f"{spec.get('start')}~{spec.get('end')}  "
            f"{spec.get('rebalance')}日调仓/{spec.get('horizon')}日前瞻  "
            f"| 最优 {top.get('name')} ICIR {top.get('icir')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 因子挖掘")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD（默认：结束日前三年）")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD（默认：面板最新）")
    parser.add_argument("--rebalance", type=int, default=20, help="截面间隔（交易日，默认 20）")
    parser.add_argument("--horizon", type=int, default=20, help="前瞻期（5/10/20/60）")
    parser.add_argument("--oos-months", type=int, default=0,
                        help="样本外区间（月，0=关闭；如 12 表示最后 12 个月只做验证）")
    parser.add_argument("--factor", help="只评估指定因子，逗号分隔")
    parser.add_argument("--source", default="panel",
                        choices=["panel", "alpha158", "academic", "gtja", "fundamental", "all"],
                        help="因子来源：panel / alpha158 / academic / gtja / fundamental / all")
    parser.add_argument("--incremental", action="store_true",
                        help="额外计算扣除规模/换手/动量/行业后的残差 IC")
    parser.add_argument("--top", type=int, default=15, help="表格显示前 N 个")
    parser.add_argument("--detail", action="store_true", help="打印分层明细")
    parser.add_argument("--detail-factor", help="只打印这些因子的明细（逗号分隔）")
    parser.add_argument("--json-out", help="把完整结果写到文件")
    parser.add_argument("--list", action="store_true", help="列出历史评估")
    parser.add_argument("--limit-history", type=int, default=20)
    args = parser.parse_args()

    init_db()
    if args.list:
        print_history(args.limit_history)
        return 0

    payload: dict = {"rebalance": args.rebalance, "horizon": args.horizon}
    if args.oos_months:
        payload["oos_months"] = args.oos_months
    if args.source != "panel":
        payload["source"] = args.source
    if args.incremental:
        payload["incremental"] = True
    if args.start:
        payload["start"] = args.start
    if args.end:
        payload["end"] = args.end
    if args.factor:
        payload["factors"] = [item.strip() for item in args.factor.split(",") if item.strip()]

    try:
        result = service.run(payload)
    except FactorError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    config = result["config"]
    print(
        f"\n区间 {config['start']} → {config['end']}｜{result['sections']} 个截面｜"
        f"{config['rebalance']} 日间隔｜{config['horizon']} 日前瞻｜"
        f"{result['evaluated_dates']} 期有效"
    )
    if result.get("out_of_sample_period"):
        lookback = result["lookback"]
        period = result["out_of_sample_period"]
        print(
            f"样本内 {lookback['start']} → {lookback['end']}｜"
            f"样本外 {period['start']} → {period['end']}（{result['oos_sections']} 个截面）｜"
            f"通过 {result['gate_passed']} / {len(result['factors'])}"
        )
    if config.get("incremental"):
        passed = [item for item in result["factors"] if (item.get("incremental") or {}).get("passed")]
        print(f"增量检验通过 {len(passed)} / {len(result['factors'])}（残差 |ICIR| ≥ 0.15 且同号）")
    if result.get("out_of_sample_period") and config.get("incremental"):
        both = [
            item for item in result["factors"]
            if item.get("passed") and (item.get("incremental") or {}).get("passed")
        ]
        print(f"同时通过样本外与增量 {len(both)} / {len(result['factors'])}："
              + "、".join(item["name"] for item in both[:10]))
    print("（IC = 因子值与未来收益的秩相关；ICIR = IC 均值/标准差；分层价差 = Q5 − Q1）\n")
    print_table(result["factors"], args.top)
    if args.detail or args.detail_factor:
        names = [item.strip() for item in (args.detail_factor or "").split(",") if item.strip()]
        print_detail(result["factors"], names)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整结果已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
