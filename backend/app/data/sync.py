"""Sync orchestration: calendar, universe, whole-market snapshot, history.

Design rules that matter more than speed here:

* A bar is stamped with the **trading day it represents**, never with "today"
  just because the sync ran today. Running a snapshot on a Saturday files the
  data under Friday.
* A snapshot taken while the market is still open is refused by default: a
  half-day bar silently poisons every later factor calculation.
* History sync is resumable and incremental; a failed symbol is reported and
  skipped, never allowed to abort the whole run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable, Optional, Sequence

from app.config import settings
from app.data import store
from app.data.providers import Provider, ProviderError, candidates, provider_for
from app.data.providers.base import board_of
from app.services.symbols import exchange

MARKET_CLOSE = dtime(15, 5)
FLUSH_SYMBOLS = 25
OVERLAP_DAYS = 7
MIN_BARS_FOR_INCREMENTAL = 60
MIN_SPAN_DAYS_FOR_INCREMENTAL = 180
MAX_CALENDAR_STALENESS_DAYS = 15


class SyncError(RuntimeError):
    pass


def _today() -> date:
    return datetime.now().date()


def _date_of(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
    return None


def _open_days(start: Optional[date] = None, end: Optional[date] = None) -> list[date]:
    return store.get_trade_days(start, end)


def resolve_target_date(trade_date: Optional[date] = None) -> date:
    """The trading day a snapshot taken *now* actually represents."""
    today = trade_date or _today()
    open_days = _open_days(end=today)
    if not open_days:
        raise SyncError("交易日历为空，请先运行 `sync_market.py calendar`")
    last_open = open_days[-1]
    if last_open == today:
        return today
    # A stale calendar would file today's prices under an old date and overwrite
    # that bar. Refuse instead of guessing.
    gap = (today - last_open).days
    if gap > MAX_CALENDAR_STALENESS_DAYS:
        raise SyncError(
            f"交易日历最后一天是 {last_open}，距今 {gap} 天，日历可能已过期。"
            "请先运行 `sync_market.py calendar`，否则当日数据会被写到旧日期上。"
        )
    return last_open


def _intraday_guard(target: date, allow_intraday: bool) -> None:
    now = datetime.now()
    if target < now.date():
        return
    if now.time() < MARKET_CLOSE and not allow_intraday:
        raise SyncError(
            f"现在是 {now.strftime('%H:%M')}，A 股尚未收盘（15:05）。"
            "此时抓取会写入半日 K 线并污染后续因子计算；"
            "如确需抓取请加 --allow-intraday。"
        )


# ------------------------------------------------------------------- calendar
def sync_calendar(
    prefer: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Any]:
    started = datetime.now()
    provider = provider_for("calendar", prefer)
    # Bound the window: some providers return every date they know about when
    # asked for "everything", and the panel only needs a few years plus next year.
    today = _today()
    window_start = start or (today - timedelta(days=365 * 6))
    window_end = end or (today + timedelta(days=400))
    rows = provider.trade_calendar(window_start, window_end)
    written = store.upsert_calendar(rows, provider.name)
    open_days = sum(1 for _, is_open in rows if is_open)
    store.log_sync("calendar", started, written, "ok", f"{provider.name} {open_days} 个交易日")
    return {
        "job": "calendar",
        "provider": provider.name,
        "rows": written,
        "open_days": open_days,
        "first": rows[0][0] if rows else None,
        "last": rows[-1][0] if rows else None,
    }


# ------------------------------------------------------------------- universe
def sync_universe(prefer: Optional[str] = None) -> dict[str, Any]:
    started = datetime.now()
    provider = provider_for("universe", prefer)
    symbols = provider.stock_universe()
    written = store.upsert_symbols(symbols)
    store.log_sync("universe", started, written, "ok", provider.name)
    return {"job": "universe", "provider": provider.name, "symbols": written}


# ------------------------------------------------------------------- snapshot
def sync_snapshot(
    prefer: Optional[str] = None,
    trade_date: Optional[date] = None,
    allow_intraday: bool = False,
) -> dict[str, Any]:
    started = datetime.now()
    target = resolve_target_date(trade_date)
    _intraday_guard(target, allow_intraday)
    if trade_date is not None and trade_date != _today():
        raise SyncError("快照接口只提供当日截面，历史数据请用 history 子命令补齐")

    provider = provider_for("spot", prefer)
    rows = provider.spot_snapshot()
    if not rows:
        raise SyncError("快照为空：数据源没有返回任何标的")

    for row in rows:
        row["trade_date"] = target

    symbols = [
        {
            "symbol": row["symbol"],
            "name": row.get("name"),
            "exchange": exchange(row["symbol"]),
            "asset_type": row.get("asset_type", "stock"),
            "board": row.get("board") or board_of(row["symbol"]),
            "is_active": True,
        }
        for row in rows
    ]
    store.upsert_symbols(symbols)

    written = store.upsert_daily(rows, provider.name)["rows"]
    basics = [
        {
            "symbol": row["symbol"],
            "trade_date": target,
            "pe": row.get("pe"),
            "pe_ttm": row.get("pe"),
            "pb": row.get("pb"),
            "total_mv": row.get("total_mv"),
            "circ_mv": row.get("circ_mv"),
            "turnover_rate": row.get("turnover_rate"),
            "source": provider.name,
        }
        for row in rows
    ]
    store.upsert_daily_basic(basics, provider.name)
    store.log_sync("snapshot", started, written, "ok", f"{provider.name} {target}")
    return {
        "job": "snapshot",
        "provider": provider.name,
        "trade_date": target,
        "symbols": len(rows),
        "rows": written,
    }


# -------------------------------------------------------------------- history
def _fetch_one(
    providers: Sequence[Provider],
    symbol: str,
    start: Optional[date],
    end: Optional[date],
    with_adjust: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Try each provider until one returns bars; one bad source must not sink a run."""
    errors: list[str] = []
    for provider in providers:
        try:
            fetch_with_factors = getattr(provider, "daily_with_factors", None)
            if with_adjust and callable(fetch_with_factors):
                bars = fetch_with_factors(symbol, start, end)
            else:
                bars = provider.daily_bars(symbol, start, end)
                if with_adjust and provider.capabilities.adj_factors:
                    try:
                        factors = {
                            item["trade_date"]: item["adj_factor"]
                            for item in provider.adj_factors(symbol, start, end)
                            if item.get("adj_factor")
                        }
                    except Exception:  # noqa: BLE001 - factors are optional, bars are not
                        factors = {}
                    for bar in bars:
                        bar["adj_factor"] = factors.get(bar["trade_date"])
        except Exception as exc:  # noqa: BLE001 - try the next source
            errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
            continue
        if bars:
            return bars, provider.name
        errors.append(f"{provider.name}: 返回空数据")
    raise ProviderError("；".join(errors) or "没有可用的历史数据源")


def sync_history(
    prefer: Optional[str] = None,
    symbols: Optional[Sequence[str]] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    with_adjust: bool = True,
    incremental: bool = False,
    limit: Optional[int] = None,
    concurrency: Optional[int] = None,
    progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    started = datetime.now()
    providers = candidates("history", prefer)
    if not providers:
        raise SyncError("没有可用的历史行情数据源（Tushare token 无效 / 网络不通）")
    store.init_schema()

    if symbols:
        codes = list(dict.fromkeys(symbols))
    else:
        codes = [item["symbol"] for item in store.get_universe(active_only=True)]
    if limit:
        codes = codes[:limit]
    if not codes:
        raise SyncError("没有待同步的标的，请先运行 `sync_market.py universe`")

    last_dates = store.last_bar_dates(codes) if incremental else {}
    spans = store.bar_spans(codes) if incremental else {}
    default_start = start or (_today() - timedelta(days=365 * settings.sync_years))
    plan: list[tuple[str, Optional[date]]] = []
    for code in codes:
        begin = default_start
        # A symbol holding only a few snapshot bars is not "up to date": both the
        # bar count and the covered span must look like real history, otherwise
        # incremental would skip straight past the backfill we actually want.
        if incremental and last_dates.get(code):
            count, first_day = spans.get(code, (0, None))
            covered = (last_dates[code] - first_day).days if first_day else 0
            if count >= MIN_BARS_FOR_INCREMENTAL and covered >= MIN_SPAN_DAYS_FOR_INCREMENTAL:
                begin = max(last_dates[code] - timedelta(days=OVERLAP_DAYS), default_start)
        plan.append((code, begin))

    workers = max(1, min(int(concurrency or settings.sync_concurrency), 16))
    total_rows = 0
    failures: list[tuple[str, str]] = []
    sources: dict[str, int] = {}
    truncated = 0
    buffer: list[dict[str, Any]] = []
    buffer_symbols = 0
    done = 0
    # Sina's history endpoint stops at 1023 bars; a run that asked for more than
    # that would otherwise look complete while silently missing older years.
    partial_day = _today() if datetime.now().time() < MARKET_CLOSE else None

    def flush() -> int:
        nonlocal buffer, buffer_symbols
        if not buffer:
            return 0
        written = store.upsert_daily(buffer, "mixed")["rows"]
        buffer = []
        buffer_symbols = 0
        return written

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(_fetch_one, providers, code, begin, end, with_adjust): (code, begin)
            for code, begin in plan
        }
        for future in as_completed(futures):
            code, begin = futures[future]
            try:
                bars, source = future.result()
            except Exception as exc:  # noqa: BLE001 - one symbol must not abort the run
                failures.append((code, f"{type(exc).__name__}: {exc}"))
                bars = []
            else:
                sources[source] = sources.get(source, 0) + 1
            if bars:
                if partial_day:
                    # Never persist a half-formed bar for the session in progress.
                    bars = [bar for bar in bars if _date_of(bar.get("trade_date")) != partial_day]
                if bars:
                    first_bar = min(
                        (_date_of(bar.get("trade_date")) for bar in bars),
                        default=None,
                    )
                    if begin and first_bar and (first_bar - begin).days > 30:
                        truncated += 1
                    buffer.extend(bars)
                    buffer_symbols += 1
                    if buffer_symbols >= FLUSH_SYMBOLS:
                        total_rows += flush()
            done += 1
            if progress:
                progress(
                    {
                        "done": done,
                        "total": len(plan),
                        "symbol": code,
                        "rows": total_rows + len(buffer),
                        "failed": len(failures),
                    }
                )
        total_rows += flush()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pool.shutdown(wait=False, cancel_futures=True)
        total_rows += flush()
        store.log_sync("history", started, total_rows, "interrupted", "用户中断")
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    status = "ok" if not failures else "partial"
    used = "，".join(f"{name} {count} 只" for name, count in sorted(sources.items())) or "无"
    detail = f"成功 {len(plan) - len(failures)}/{len(plan)}（{used}）"
    if truncated:
        detail += f"；{truncated} 只早于数据源上限，历史被截断"
    if failures:
        detail += "；失败示例：" + "; ".join(f"{code}({err})" for code, err in failures[:5])
    store.log_sync("history", started, total_rows, status, detail)
    return {
        "job": "history",
        "providers": sources,
        "symbols": len(plan),
        "failed": len(failures),
        "truncated": truncated,
        "rows": total_rows,
        "status": status,
        "errors": [{"symbol": code, "error": err} for code, err in failures[:20]],
    }


# ------------------------------------------------------------------ composite
def sync_all(
    prefer: Optional[str] = None,
    allow_intraday: bool = False,
    with_history: bool = True,
    history_limit: Optional[int] = None,
) -> dict[str, Any]:
    store.init_schema()
    result: dict[str, Any] = {}
    result["calendar"] = sync_calendar(prefer)
    result["universe"] = sync_universe(prefer)
    result["snapshot"] = sync_snapshot(prefer, allow_intraday=allow_intraday)
    if with_history:
        result["history"] = sync_history(
            prefer, incremental=True, limit=history_limit
        )
    return result


def status() -> dict[str, Any]:
    coverage = store.get_coverage()
    return {
        "db_path": str(store.db_path()),
        "coverage": {
            key: (value.isoformat() if isinstance(value, date) else value)
            for key, value in coverage.items()
        },
        "recent_sync": [
            {
                "job": item["job"],
                "status": item["status"],
                "rows": item["rows"],
                "finished_at": item["finished_at"].isoformat()
                if isinstance(item["finished_at"], datetime)
                else item["finished_at"],
                "detail": item["detail"],
            }
            for item in store.get_sync_log(5)
        ],
    }
