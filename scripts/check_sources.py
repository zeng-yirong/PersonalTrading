"""Drill the data-source fallback chain — especially what happens after Tushare expires.

    python scripts/check_sources.py            # 当前可用性 + 降级演练
    python scripts/check_sources.py --quick    # 只列能力矩阵，不发真实请求

The Tushare package (30 days) is a single point of failure: when it dies, the
panel keeps its history but the daily update must fall back to Sina (no adjustment
factors) and AKShare (calendar only). This script proves that fallback works
*before* it is needed, and prints exactly what would be lost.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.data.providers import (  # noqa: E402
    EastmoneyProvider,
    SinaProvider,
    TushareProvider,
    candidates,
    get_provider,
    list_providers,
)


def _width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(_width(headers[i]), max((_width(row[i]) for row in rows), default=0))
        for i in range(len(headers))
    ]
    print("  ".join(_pad(headers[i], widths[i]) for i in range(len(headers))))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(row))))


def capability_matrix() -> list[dict]:
    """For every capability: who can serve it right now, best first."""
    from app.data.providers import CAPABILITY_ORDER

    output = []
    for capability in CAPABILITY_ORDER:
        names = []
        for name in CAPABILITY_ORDER[capability]:
            try:
                provider = get_provider(name)
            except Exception:  # noqa: BLE001 - a missing optional dependency is fine
                names.append(f"{name}(不可用)")
                continue
            ok, _ = provider.available()
            names.append(name if ok else f"{name}(不可用)")
        try:
            available = [item.name for item in candidates(capability)]
        except Exception:  # noqa: BLE001
            available = []
        output.append(
            {
                "capability": capability,
                "order": " → ".join(names),
                "available": available,
                "primary": available[0] if available else None,
                "fallback": available[1] if len(available) > 1 else None,
            }
        )
    return output


def drill_sina() -> dict:
    """Whole-market snapshot + one symbol's history, without Tushare."""
    provider = SinaProvider()
    ok, reason = provider.available()
    result = {"provider": "sina", "available": ok, "reason": reason}
    if not ok:
        return result
    try:
        snapshot = provider.spot_snapshot()
        result["snapshot_rows"] = len(snapshot)
    except Exception as exc:  # noqa: BLE001 - drill only
        result["snapshot_rows"] = 0
        result["snapshot_error"] = str(exc)[:120]
    try:
        end = date.today()
        bars = provider.daily_bars("600519", end - timedelta(days=40), end)
        result["history_bars"] = len(bars)
        result["history_last"] = bars[-1]["trade_date"] if bars else None
        result["has_factors"] = any(bar.get("adj_factor") for bar in bars)
    except Exception as exc:  # noqa: BLE001
        result["history_bars"] = 0
        result["history_error"] = str(exc)[:120]
    return result


def drill_eastmoney() -> dict:
    provider = EastmoneyProvider()
    ok, reason = provider.available()
    result = {"provider": "eastmoney", "available": ok, "reason": reason}
    if not ok:
        return result
    try:
        end = date.today()
        bars = provider.daily_bars("600519", end - timedelta(days=40), end)
        result["history_bars"] = len(bars)
        result["history_last"] = bars[-1]["trade_date"] if bars else None
        result["has_factors"] = any(bar.get("adj_factor") for bar in bars)
    except Exception as exc:  # noqa: BLE001
        result["history_bars"] = 0
        result["history_error"] = str(exc)[:120]
    return result


def drill_akshare() -> dict:
    try:
        provider = get_provider("akshare")
    except Exception as exc:  # noqa: BLE001
        return {"provider": "akshare", "available": False, "reason": str(exc)[:120]}
    ok, reason = provider.available()
    result = {"provider": "akshare", "available": ok, "reason": reason}
    if not ok:
        return result
    try:
        days = provider.trade_calendar(date.today() - timedelta(days=40), date.today() + timedelta(days=10))
        result["calendar_days"] = len(days)
    except Exception as exc:  # noqa: BLE001
        result["calendar_days"] = 0
        result["calendar_error"] = str(exc)[:120]
    return result


def drill_tushare() -> dict:
    provider = TushareProvider()
    ok, reason = provider.available()
    status = provider.token_status() if ok else None
    return {
        "provider": "tushare",
        "available": ok,
        "reason": reason,
        "expires_at": (status or {}).get("expires_at"),
        "remaining_text": (status or {}).get("remaining_text"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="数据源可用性与降级演练")
    parser.add_argument("--quick", action="store_true", help="只列能力矩阵，不发真实请求")
    args = parser.parse_args()

    print("== 数据源 ==")
    rows = []
    for item in list_providers():
        caps = [key for key, value in item["capabilities"].items() if value]
        rows.append(
            [
                item["label"],
                item["name"],
                "可用" if item["available"] else "不可用",
                (item["reason"] or "—")[:36],
                ",".join(caps) if caps else "—",
            ]
        )
    _table(["名称", "id", "状态", "原因/备注", "能力"], rows)

    matrix = capability_matrix()
    print("\n== 能力矩阵（当前可用性） ==")
    _table(
        ["能力", "候选顺序", "主源", "降级源"],
        [[item["capability"], item["order"], item["primary"] or "无",
          item["fallback"] or "无"] for item in matrix],
    )

    if args.quick:
        return 0

    print("\n== 降级演练（绕开 Tushare） ==")
    tushare = drill_tushare()
    sina = drill_sina()
    eastmoney = drill_eastmoney()
    akshare = drill_akshare()
    print(f"Tushare：{'可用' if tushare['available'] else '不可用'}"
          f"｜到期 {tushare.get('expires_at') or '—'}｜{tushare.get('remaining_text') or ''}")
    print(f"新浪：{'可用' if sina['available'] else '不可用'}｜"
          f"全市场快照 {sina.get('snapshot_rows', 0)} 行｜"
          f"单股历史 {sina.get('history_bars', 0)} 根（复权因子 {sina.get('has_factors')}）")
    print(f"东财：{'可用' if eastmoney['available'] else '不可用'}｜"
          f"单股历史 {eastmoney.get('history_bars', 0)} 根（复权因子 {eastmoney.get('has_factors')}）")
    print(f"AKShare：{'可用' if akshare['available'] else '不可用'}｜"
          f"交易日历 {akshare.get('calendar_days', 0)} 天")

    print("\n== 到期后的影响 ==")
    rows = [
        ["日更快照", "Tushare → 新浪", "可用" if sina.get("snapshot_rows") else "不可用",
         "无复权因子；次日可用"],
        ["单股历史", "Tushare → 新浪 → 东财", "可用" if sina.get("history_bars") else "不可用",
         "新浪约 1023 根；东财可不稳定"],
        ["复权因子", "Tushare → 东财", "可用" if eastmoney.get("has_factors") else "不可用",
         "东财在本机取不到 K 线；到期后无法新增因子，只能保留已下载的历史"],
        ["交易日历", "Tushare → AKShare", "可用" if akshare.get("calendar_days") else "不可用",
         "只影响未来日期"],
        ["全市场估值", "Tushare → 新浪(部分)", "部分", "PE/PB/市值可能缺失"],
    ]
    _table(["能力", "降级路径", "结果", "影响"], rows)
    missing = [row for row in rows if row[2] == "不可用"]
    if missing:
        print(f"\n{len(missing)} 条能力在当前网络下没有可用降级路径，需要提前准备。")
        return 1
    print("\n降级链路可用：到期后日更与历史查询仍可继续，只是缺少复权因子与部分估值字段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
