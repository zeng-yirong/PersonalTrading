"""Back up the local panel + app database, then read the copy back to verify.

    python scripts/backup_panel.py                     # 默认目录（优先另一块盘）
    python scripts/backup_panel.py --out D:\\backups   # 指定目录
    python scripts/backup_panel.py --keep 3            # 只保留最近 3 份
    python scripts/backup_panel.py --dry-run           # 只看目标目录与源数据规模

5 GB 的面板只存在这一份：备份本身不难，难的是确认备份能用。脚本会打开拷贝，
比对表数、日线行数与最新交易日，不一致就报失败。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.data import backup  # noqa: E402


def _gb(size: float) -> str:
    return f"{size / 1024 ** 3:.2f} GB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 面板备份")
    parser.add_argument("--out", help="备份根目录（默认优先 E:\\backups\\PersonalTrading）")
    parser.add_argument("--keep", type=int, default=5, help="保留最近 N 份，0 表示不清理")
    parser.add_argument("--no-app", action="store_true", help="只备份市场面板，不备份 SQLite")
    parser.add_argument("--dry-run", action="store_true", help="只看目标目录与源数据规模")
    args = parser.parse_args(argv)

    target = Path(args.out) if args.out else backup.default_target()
    if args.dry_run:
        stats = backup._stats(Path(backup.settings.market_db_path).resolve())
        print(f"备份目标：{target}")
        print(f"源面板：{stats['tables']} 张表｜{stats['bars']:,} 根日线｜{stats['symbols']} 只标的｜截至 {stats['as_of']}")
        return 0

    try:
        result = backup.run(Path(args.out) if args.out else None, keep=args.keep, include_app=not args.no_app)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"备份目录：{result['target']}")
    for item in result["files"]:
        print(f"  {item['file']:<28}{_gb(item['bytes']):>12}")
    source, copy = result["source"], result["copy"]
    print(
        f"源：{source['tables']} 表 / {source['bars']:,} 行 / 截至 {source['as_of']}　"
        f"拷贝：{copy['tables']} 表 / {copy['bars']:,} 行 / 截至 {copy['as_of']}"
    )
    if result["app"]:
        app = result["app"]
        print(f"SQLite：integrity {app['integrity']}｜推演 {app['counts'].get('agent_runs')} 条｜成交 {app['counts'].get('paper_trades')} 笔")
    if result["pruned"]:
        print(f"已清理旧备份：{'、'.join(result['pruned'])}")
    print(f"合计 {_gb(result['bytes'])}｜校验 {'通过' if result['verified'] else '不一致'}")
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
