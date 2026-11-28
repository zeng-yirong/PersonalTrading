"""自选股的行情指标：现价、今日、近 5 日、添加后涨跌幅。

口径与面板一致：

* 今日涨跌幅优先用实时行情（交易所给的 pre_close 已除权），取不到时用面板
  复权收盘计算；
* 近 5 日与添加后涨跌幅都用 ``close × adj_factor``，分红送转不会被当成下跌；
* 添加自选时把「添加那一刻的价格」记进 ``watchlist.added_close`` + ``added_adj``，
  之后重算不需要再猜基准。旧数据（本功能上线前加入）在第一次读指标时按
  ``created_at`` 从面板回填一次。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from app.data import store
from app.db import list_watchlist, set_watchlist_baseline
from app.services.market import get_quotes


def _iso_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    text = str(value).strip()
    return text[:10] or None


def _created_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _pct(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return round((float(numerator) / float(denominator) - 1) * 100, 2)


def _panel_ready() -> bool:
    try:
        return store.db_path().exists()
    except Exception:
        return False


def _ensure_baseline(item: dict[str, Any]) -> dict[str, Any]:
    """旧自选没有添加时基准价时，按 created_at 从面板回填一次。"""
    if item.get("added_close") is not None or not _panel_ready():
        return item
    try:
        end = _created_date(item.get("created_at"))
        bars = store.get_daily(item["symbol"], end=end, limit=1)
    except Exception:
        return item
    if not bars:
        return item
    bar = bars[-1]
    trade_date = _iso_date(bar.get("trade_date"))
    adj = bar.get("adj_factor") or 1.0
    close = bar.get("close")
    if close is None:
        return item
    try:
        set_watchlist_baseline(
            item["symbol"], trade_date, float(close), float(adj), "panel_backfill"
        )
    except Exception:
        pass
    return {
        **item,
        "added_trade_date": trade_date,
        "added_close": float(close),
        "added_adj": float(adj),
        "added_source": "panel_backfill",
    }


def _metric(
    item: dict[str, Any],
    bars: list[dict[str, Any]],
    quote: Optional[dict[str, Any]],
) -> dict[str, Any]:
    item = _ensure_baseline(item)
    last = bars[-1] if bars else None
    panel_date = _iso_date(last.get("trade_date")) if last else None

    quote_price = quote.get("price") if quote else None
    quote_date = _iso_date(quote.get("trade_date")) if quote else None
    # 行情日期早于面板（例如盘前/收盘后取到旧报价）时不用它，避免倒退回旧价。
    usable_quote = bool(
        quote_price
        and quote_price > 0
        and (not panel_date or not quote_date or quote_date >= panel_date)
    )
    quote_newer = bool(usable_quote and quote_date and panel_date and quote_date > panel_date)

    if usable_quote:
        price: Optional[float] = float(quote_price)
    elif last:
        price = last.get("close")
    else:
        price = None

    adj_now = (
        (last.get("adj_factor") if last else None)
        or item.get("added_adj")
        or 1.0
    )

    pct_chg = quote.get("pct_chg") if usable_quote else None
    if pct_chg is None and last:
        if len(bars) >= 2:
            prev = bars[-2]
            pct_chg = _pct(
                (last.get("close") or 0) * (last.get("adj_factor") or 1.0),
                (prev.get("close") or 0) * (prev.get("adj_factor") or 1.0),
            )
        elif last.get("pre_close"):
            pct_chg = _pct(last.get("close"), last.get("pre_close"))

    pct_5d = None
    need = 5 if quote_newer else 6
    if last and price and len(bars) >= need:
        base = bars[-need]
        pct_5d = _pct(
            price * adj_now,
            (base.get("close") or 0) * (base.get("adj_factor") or 1.0),
        )

    pct_since_add = None
    added_close = item.get("added_close")
    if price and added_close:
        pct_since_add = _pct(
            price * adj_now, float(added_close) * (item.get("added_adj") or 1.0)
        )

    return {
        "symbol": item["symbol"],
        "name": item.get("name") or item["symbol"],
        "asset_type": item.get("asset_type") or "stock",
        "price": round(price, 4) if price is not None else None,
        "pct_chg": pct_chg,
        "pct_5d": pct_5d,
        "pct_since_add": pct_since_add,
        "added_trade_date": item.get("added_trade_date"),
        "added_close": item.get("added_close"),
        "as_of": quote_date if usable_quote else panel_date,
        "quote_source": (quote or {}).get("source") if usable_quote else ("panel" if last else "none"),
    }


async def build_metrics() -> list[dict[str, Any]]:
    items = list_watchlist()
    if not items:
        return []
    codes = [item["symbol"] for item in items]
    quotes = await get_quotes(codes)
    bars_map: dict[str, list[dict[str, Any]]] = {}
    if _panel_ready():
        try:
            bars_map = store.get_recent_bars(codes, limit=8)
        except Exception:
            bars_map = {}
    return [
        _metric(item, bars_map.get(item["symbol"], []), quotes.get(item["symbol"]))
        for item in items
    ]


async def baseline_for(symbol: str) -> dict[str, Any]:
    """加入自选时冻结基准：优先用此刻的实时价，取不到再用面板收盘价。"""
    panel = None
    if _panel_ready():
        try:
            bars = store.get_daily(symbol, limit=1)
            panel = bars[-1] if bars else None
        except Exception:
            panel = None

    adj = float(panel.get("adj_factor") or 1.0) if panel else 1.0
    quote = (await get_quotes([symbol])).get(symbol)
    if quote and quote.get("price"):
        trade_date = _iso_date(quote.get("trade_date")) or _iso_date(
            panel.get("trade_date") if panel else None
        )
        return {
            "added_trade_date": trade_date,
            "added_close": float(quote["price"]),
            "added_adj": adj,
            "added_source": "quote",
        }
    if panel and panel.get("close") is not None:
        return {
            "added_trade_date": _iso_date(panel.get("trade_date")),
            "added_close": float(panel["close"]),
            "added_adj": adj,
            "added_source": "panel",
        }
    return {"added_source": None}
