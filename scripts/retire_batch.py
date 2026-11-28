"""批量淘汰策略：把"明确没戏"的从库里下架（不删除，标 retired + 理由）。

    python scripts/retire_batch.py --horizon short --status candidate --reason "短线组费用后无 edge"
    python scripts/retire_batch.py --ids a13-pe-percentile,a33-low-launch --reason "条件选不出票"
    python scripts/retire_batch.py --horizon short --dry-run

规则：
* 只处理 ``candidate`` / ``template``；``watch`` / ``champion`` 必须先降级（``--force`` 可越权，但要写理由）；
* 必须给 ``--reason``，它写进 ``retire_reason`` 并显示在策略库里；
* 不删除行——淘汰是可查的历史。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db import init_db  # noqa: E402
from app.strategy import store  # noqa: E402


def select(horizon: str = "", status: str = "", ids: str = "") -> list[dict]:
    wanted = {item.strip() for item in ids.split(",") if item.strip()}
    rows = store.list_strategies(status)
    picked = []
    for row in rows:
        if wanted and row["id"] not in wanted:
            continue
        if horizon and (row.get("horizon") or "long") != horizon:
            continue
        picked.append(row)
    return picked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量淘汰策略")
    parser.add_argument("--horizon", default="", help="short / mid / long")
    parser.add_argument("--status", default="candidate", help="默认只动 candidate")
    parser.add_argument("--ids", default="", help="逗号分隔的策略 id")
    parser.add_argument("--reason", default="", help="淘汰理由（必填，会留档）")
    parser.add_argument("--force", action="store_true", help="允许淘汰 watch / champion")
    parser.add_argument("--dry-run", action="store_true", help="只看会动谁")
    args = parser.parse_args(argv)

    if not args.reason.strip() and not args.dry_run:
        raise SystemExit("必须给 --reason：淘汰理由会留在库里，半年后靠它才知道为什么下架")

    init_db()
    rows = select(args.horizon, args.status, args.ids)
    if not rows:
        print("没有匹配的策略")
        return 0

    print(f"匹配 {len(rows)} 条：")
    retired = 0
    for row in rows:
        label = f"{row['id']}（{row['name']}，{row['horizon']}，{row['status']}）"
        if row["status"] in {"watch", "champion"} and not args.force:
            print(f"  跳过 {label}：观察中/正式策略，先降级或加 --force")
            continue
        if args.dry_run:
            print(f"  将淘汰 {label}")
            continue
        try:
            store.set_status(row["id"], "retired", args.reason)
        except store.StrategyError as exc:
            print(f"  失败 {label}：{exc}")
            continue
        print(f"  已淘汰 {label}")
        retired += 1

    if not args.dry_run:
        print(f"共淘汰 {retired} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
