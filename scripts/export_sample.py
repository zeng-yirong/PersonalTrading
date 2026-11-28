"""导出一份「小面板」，供换机器开发 / 演示使用。

不拷贝 5 GB 的全量面板，只取最近 N 天的日频表 + 全量的维度表，
外加少量财务与事件数据。产物是一个独立的 DuckDB 文件，可以直接
作为 ``PERSONALTRADING_MARKET_DB_PATH`` 使用。

    python scripts/export_sample.py                 # 最近 30 天 → data/samples/market_sample.duckdb
    python scripts/export_sample.py --days 60       # 两个月
    python scripts/export_sample.py --out D:\tmp\m.duckdb

口径：
* 日频表按 ``trade_date >= 全库最新交易日 - days`` 截取（含当天）；
* 财报 / 公告类按 ``ann_date`` 截取，报告期本身不做裁剪；
* 维度表（symbols / calendar / fund_basic / index_basic …）整表拷贝，它们很小；
* 跳过大表里对本项目无用的东西（``dataset_progress``、``stk_surv``）和
  逐段同步日志（``sync_log``）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "market.duckdb"
DEFAULT_OUT = ROOT / "data" / "samples" / "market_sample.duckdb"

# 按 trade_date 截取的日频表（缺失的会被跳过）
DATE_TABLES = (
    "daily",
    "adj_factor",
    "daily_basic",
    "moneyflow",
    "moneyflow_hsgt",
    "stk_limit",
    "cyq_perf",
    "limit_list_d",
    "margin_detail",
    "ths_hot",
    "dc_hot",
    "limit_cpt_list",
    "top_list",
    "top_inst",
    "hsgt_top10",
    "stk_factor",
    "block_trade",
    "fund_adj",
    "sw_daily",
    "index_daily",
    "index_global",
    "screen_snapshot",
    "screen_meta",
)

# 按 ann_date 截取的财报 / 公告类
ANN_TABLES = (
    "income_vip",
    "balancesheet_vip",
    "cashflow_vip",
    "fina_indicator_vip",
    "forecast_vip",
    "express_vip",
    "dividend",
    "share_float",
    "stk_holdernumber",
    "stk_holdertrade",
)

# 维度表整表拷贝
FULL_TABLES = (
    "symbols",
    "calendar",
    "fund_basic",
    "index_basic",
    "new_share",
    "stock_company",
    "broker_recommend",
    "ths_index",
)

SKIP = {"dataset_progress", "stk_surv", "sync_log"}


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "select table_name from information_schema.tables where table_schema = 'main'"
    ).fetchall()
    return {row[0] for row in rows}


def _latest(con: duckdb.DuckDBPyConnection, table: str, column: str) -> dt.date | None:
    value = con.execute(
        f'select max(TRY_CAST("{column}" AS DATE)) from main."{table}"'
    ).fetchone()[0]
    return value


def build(src: Path, out: Path, days: int) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    src_con = duckdb.connect(str(src), read_only=True)
    available = _tables(src_con)
    latest = _latest(src_con, "daily", "trade_date") or dt.date.today()
    cutoff = latest - dt.timedelta(days=days)
    src_con.close()

    con = duckdb.connect(str(out))
    con.execute(f"ATTACH '{src}' AS src (READ_ONLY)")

    copied: dict[str, int] = {}
    for name in DATE_TABLES:
        if name not in available:
            continue
        con.execute(
            f'CREATE TABLE main."{name}" AS '
            f'SELECT * FROM src.main."{name}" '
            f'WHERE TRY_CAST("trade_date" AS DATE) >= DATE \'{cutoff.isoformat()}\''
        )
        copied[name] = con.execute(f'select count(*) from main."{name}"').fetchone()[0]

    for name in ANN_TABLES:
        if name not in available:
            continue
        try:
            con.execute(
                f'CREATE TABLE main."{name}" AS '
                f'SELECT * FROM src.main."{name}" '
                f'WHERE TRY_CAST("ann_date" AS DATE) >= DATE \'{cutoff.isoformat()}\''
            )
        except duckdb.Error as exc:
            print(f"[跳过] {name}: {exc}", file=sys.stderr)
            continue
        copied[name] = con.execute(f'select count(*) from main."{name}"').fetchone()[0]

    for name in FULL_TABLES:
        if name not in available:
            continue
        con.execute(f'CREATE TABLE main."{name}" AS SELECT * FROM src.main."{name}"')
        copied[name] = con.execute(f'select count(*) from main."{name}"').fetchone()[0]

    con.execute(
        f"""
        CREATE TABLE main.sample_meta AS
        SELECT 'PersonalTrading sample panel' AS note,
               DATE '{latest.isoformat()}' AS source_latest_date,
               DATE '{cutoff.isoformat()}' AS cutoff_date,
               {days} AS days,
               CAST(now() AS TIMESTAMP) AS created_at
        """
    )

    con.execute("DETACH src")
    con.close()

    return {
        "out": str(out),
        "latest": latest.isoformat(),
        "cutoff": cutoff.isoformat(),
        "days": days,
        "tables": copied,
        "rows": sum(copied.values()),
        "size_mb": round(out.stat().st_size / 1024 / 1024, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="导出最近 N 天的示例面板")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="源面板（默认 data/market.duckdb）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出文件")
    parser.add_argument("--days", type=int, default=30, help="回溯天数（默认 30）")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"源面板不存在：{src}", file=sys.stderr)
        return 1

    info = build(src, Path(args.out), max(1, args.days))
    print(f"输出：{info['out']}")
    print(f"区间：{info['cutoff']} → {info['latest']}（{info['days']} 天）")
    print(f"表数：{len(info['tables'])}｜行数：{info['rows']:,}｜大小：{info['size_mb']} MB")
    for name, rows in info["tables"].items():
        print(f"  {name:<20} {rows:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
