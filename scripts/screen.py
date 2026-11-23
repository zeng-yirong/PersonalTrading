"""Run a stock screen from the command line.

    python scripts/screen.py "市盈率低于行业中位数、ROE>15%、20日主力净流入为正"
    python scripts/screen.py --json '{"filters":[{"field":"pe_ttm","op":"<=","value":20}]}'
    python scripts/screen.py --fields
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

from app.screener import nl  # noqa: E402
from app.screener import query as screen_query  # noqa: E402
from app.screener.fields import FIELDS, BY_NAME  # noqa: E402


def _fmt(value, field: str) -> str:
    if value is None:
        return "—"
    spec = BY_NAME.get(field)
    if not isinstance(value, (int, float)):
        return str(value)
    if spec is None:
        return f"{value:,.2f}"
    if spec.kind == "money":
        # Fields quoted in 万元 must be converted before scaling, or 157.3 万
        # reads as 157 万元 instead of 15.7 亿.
        amount = value * 1e4 if spec.unit == "万元" else value
        if abs(amount) >= 1e8:
            return f"{amount / 1e8:,.2f} 亿"
        if abs(amount) >= 1e4:
            return f"{amount / 1e4:,.1f} 万"
        return f"{amount:,.0f}"
    if spec.kind == "ratio":
        suffix = "%" if spec.unit == "%" else ""
        return f"{value:,.2f}{suffix}"
    if spec.kind in {"count", "rank"}:
        return f"{value:,.0f}"
    return str(value)


DISPLAY_COLUMNS = (
    "symbol", "name", "industry", "close", "pct_chg", "ret20",
    "pe_ttm", "roe", "mf_net_20d", "rsi12",
)

HEADERS = {
    "symbol": "代码",
    "name": "名称",
    "industry": "行业",
    "board": "板块",
}


def _label(field: str) -> str:
    if field in HEADERS:
        return HEADERS[field]
    return BY_NAME[field].label if field in BY_NAME else field


def _width(text: str) -> int:
    """CJK glyphs occupy two terminal columns."""
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def print_result(result: dict) -> None:
    query = result.get("query") or {}
    print(f"\n条件：{query.get('title') or '—'}  ——  {query.get('explanation') or ''}")
    print(
        f"数据截至 {result['as_of']}，命中 {result['total_matched']:,} 只，"
        f"展示前 {result['returned']} 只\n"
    )
    rows = result.get("rows") or []
    if not rows:
        print("（无符合条件的标的）")
        return

    headers = list(DISPLAY_COLUMNS)
    cells = []
    for row in rows:
        cells.append([_fmt(row.get(name), name) for name in headers])

    labels = [_label(name) for name in headers]
    widths = [
        max(_width(labels[index]), max((_width(line[index]) for line in cells), default=0))
        for index in range(len(headers))
    ]
    print("  ".join(_pad(label, widths[i]) for i, label in enumerate(labels)))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for line in cells:
        print("  ".join(_pad(text, widths[i]) for i, text in enumerate(line)))


async def main_async(args: argparse.Namespace) -> int:
    if args.fields:
        for spec in FIELDS:
            unit = f"（{spec.unit}）" if spec.unit else ""
            print(f"{spec.name:<16}{spec.label}{unit}：{spec.description}")
        return 0

    if args.json:
        try:
            payload = json.loads(args.json)
        except json.JSONDecodeError as exc:
            print(f"JSON 解析失败：{exc}", file=sys.stderr)
            return 1
        try:
            parsed = screen_query.ScreenQuery.from_dict(payload)
        except screen_query.QueryError as exc:
            print(f"查询无效：{exc}", file=sys.stderr)
            return 1
        result = screen_query.run(parsed)
        result["query"] = parsed.to_dict()
    else:
        text = " ".join(args.text or [])
        if not text:
            print("请提供筛选条件，或用 --fields 查看可用字段", file=sys.stderr)
            return 1
        try:
            result = await nl.parse_and_run(text, limit=args.limit)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            print(f"[失败] {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"结果已写入 {args.json_out}")
    print_result(result)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PersonalTrading 选股器")
    parser.add_argument("text", nargs="*", help="自然语言筛选条件")
    parser.add_argument("--json", help="直接给结构化查询 JSON")
    parser.add_argument("--json-out", help="把完整结果写到文件")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--fields", action="store_true", help="列出可用字段")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
