"""Generic dataset backfill: pull every permitted interface into DuckDB.

The engine turns a :class:`~app.data.datasets.DatasetSpec` into "parts" (one per
trade date, symbol, report period, or a single reference pull), fetches them
through the shared rate limiter, and upserts the result.

Three properties matter and are explicit in the code:

* **Resumable** – every finished part is checkpointed in ``dataset_progress``,
  so an interrupted multi-hour backfill continues where it stopped.
* **Idempotent** – re-running a part upserts on the primary key.
* **Unit-correct** – Tushare quotes volume in 手 and amount in 千元; the core
  panel stores shares and yuan, so the conversion happens exactly once, here.

The price datasets (:data:`CORE_WRITERS`) write into the existing ``daily`` /
``daily_basic`` panel tables so every existing query and API keeps working;
everything else lands in a table named after the dataset.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Callable, Optional, Sequence

from app.config import settings
from app.data import store
from app.data.datasets import DATASETS, DatasetSpec, quarter_ends, specs_for
from app.data.providers import ProviderError
from app.data.providers.tushare import QuotaExceeded, TushareProvider, get_rate_limiter
from app.services.symbols import exchange, normalize_code

DATE_PATTERN = re.compile(r"^\d{8}$")
FLUSH_PARTS = 8
MARKET_CLOSE = dtime(15, 5)
# 剩余交易日少于这个数就顺手刷新日历：20 个交易日是 T+20 复盘的窗口。
CALENDAR_MIN_DAYS = 25

# Datasets that belong in the core panel rather than in their own table.
CORE_WRITERS = {
    "daily": "daily",
    "fund_daily": "daily",
    "daily_basic": "daily_basic",
}

# Factors land in their own table first and are materialised into ``daily`` once
# per dataset: 2400 batched UPDATEs against a 13M-row table would dominate the
# whole backfill.
ADJ_DATASETS = {"adj_factor", "fund_adj"}

SW_LEVEL1 = (
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
)
GLOBAL_INDICES = (
    "HSI", "HSCEI", "HSTECH", "SPX", "IXIC", "DJI", "N225", "KS11", "TWII",
    "AS51", "DAX", "FTSE", "CAC40", "SENSEX", "RTS",
)


class BackfillError(RuntimeError):
    pass


@dataclass
class Part:
    key: str
    params: dict[str, str]


# --------------------------------------------------------------------- schema
def _as_date_value(field_name: str, value: Any) -> Optional[date]:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _looks_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _float_or_none(value: Any) -> Optional[float]:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def infer_schema(
    rows: Sequence[dict[str, Any]], spec: DatasetSpec
) -> tuple[list[str], dict[str, str]]:
    """Column order + DuckDB types, inferred from real response values."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    types: dict[str, str] = {}
    for column in columns:
        if column == "ts_code" or (column in spec.keys and column != spec.date_field):
            types[column] = "VARCHAR"
            continue
        if column == spec.date_field or column in {"ann_date", "end_date", "trade_date"}:
            types[column] = "DATE"
            continue
        sample = [row.get(column) for row in rows if row.get(column) not in (None, "")]
        if sample and all(_looks_numeric(value) for value in sample):
            types[column] = "DOUBLE"
        elif sample and all(DATE_PATTERN.match(str(value)) for value in sample):
            types[column] = "DATE"
        else:
            types[column] = "VARCHAR"
    return columns, types


def normalize_rows(
    rows: Sequence[dict[str, Any]], columns: Sequence[str], types: dict[str, str]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for column in columns:
            value = row.get(column)
            kind = types[column]
            if kind == "DATE":
                item[column] = _as_date_value(column, value)
            elif kind == "DOUBLE":
                item[column] = _float_or_none(value)
            else:
                item[column] = None if value is None else str(value)
        output.append(item)
    return output


# --------------------------------------------------------- core panel writers
def to_core_rows(dataset: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a Tushare payload into the core panel's dict shape and units."""
    target = CORE_WRITERS[dataset]
    output: list[dict[str, Any]] = []
    for row in rows:
        code = normalize_code(row.get("ts_code"))
        if not code:
            continue
        if target == "daily":
            day = _as_date_value("trade_date", row.get("trade_date"))
            if day is None:
                continue
            volume = _float_or_none(row.get("vol"))
            amount = _float_or_none(row.get("amount"))
            output.append(
                {
                    "symbol": code,
                    "trade_date": day,
                    "open": _float_or_none(row.get("open")),
                    "high": _float_or_none(row.get("high")),
                    "low": _float_or_none(row.get("low")),
                    "close": _float_or_none(row.get("close")),
                    "pre_close": _float_or_none(row.get("pre_close")),
                    # 手 -> 股, 千元 -> 元
                    "volume": volume * 100.0 if volume is not None else None,
                    "amount": amount * 1000.0 if amount is not None else None,
                    "source": "tushare",
                }
            )
        elif target == "daily_basic":
            day = _as_date_value("trade_date", row.get("trade_date"))
            if day is None:
                continue
            total_mv = _float_or_none(row.get("total_mv"))
            circ_mv = _float_or_none(row.get("circ_mv"))
            output.append(
                {
                    "symbol": code,
                    "trade_date": day,
                    "pe": _float_or_none(row.get("pe")),
                    "pe_ttm": _float_or_none(row.get("pe_ttm")),
                    "pb": _float_or_none(row.get("pb")),
                    "ps_ttm": _float_or_none(row.get("ps_ttm")),
                    "dv_ttm": _float_or_none(row.get("dv_ttm")),
                    # 万元 -> 元
                    "total_mv": total_mv * 10000.0 if total_mv else None,
                    "circ_mv": circ_mv * 10000.0 if circ_mv else None,
                    "turnover_rate": _float_or_none(row.get("turnover_rate")),
                    "volume_ratio": _float_or_none(row.get("volume_ratio")),
                    "source": "tushare",
                }
            )
    return output


def write_core(dataset: str, rows: Sequence[dict[str, Any]]) -> int:
    converted = to_core_rows(dataset, rows)
    if not converted:
        return 0
    target = CORE_WRITERS[dataset]
    if target == "daily":
        return store.upsert_daily(converted, "tushare")["rows"]
    return store.upsert_daily_basic(converted, "tushare")


# ----------------------------------------------------------------------- parts
def _codes_for_source(source: str) -> list[str]:
    if source == "stocks":
        return [
            f"{item['symbol']}.{exchange(item['symbol'])}"
            for item in store.get_universe(asset_type="stock", active_only=True)
        ]
    if source == "funds":
        try:
            with store.session(read_only=True) as conn:
                rows = conn.execute("SELECT ts_code FROM fund_basic").fetchall()
            if rows:
                return [row[0] for row in rows]
        except Exception:  # noqa: BLE001 - table may not exist yet
            pass
        return [
            f"{item['symbol']}.{exchange(item['symbol'])}"
            for item in store.get_universe(asset_type="etf", active_only=True)
        ]
    if source == "index":
        try:
            with store.session(read_only=True) as conn:
                # Exchange-published indices only. CSI lists ~6000 bespoke
                # strategy indices; pulling all of them would dwarf the stock
                # panel with series nobody screens on.
                rows = conn.execute(
                    "SELECT ts_code FROM index_basic "
                    "WHERE market IN ('SSE', 'SZSE', 'BSE', 'CNI') ORDER BY ts_code"
                ).fetchall()
            if rows:
                return [row[0] for row in rows]
        except Exception:  # noqa: BLE001 - table may not exist yet
            pass
        return ["000001.SH", "399001.SZ", "000300.SH", "000905.SH", "000852.SH"]
    if source == "sw":
        try:
            with store.session(read_only=True) as conn:
                rows = conn.execute(
                    "SELECT ts_code FROM index_basic WHERE market = 'SW' ORDER BY ts_code"
                ).fetchall()
            if rows:
                return [row[0] for row in rows]
        except Exception:  # noqa: BLE001 - table may not exist yet
            pass
        return list(SW_LEVEL1)
    if source == "global":
        return list(GLOBAL_INDICES)
    raise BackfillError(f"未知的 code_source：{source}")


def build_parts(
    spec: DatasetSpec,
    years: int,
    today: Optional[date] = None,
) -> list[Part]:
    today = today or date.today()
    start = max(today - timedelta(days=365 * years), date(spec.start_year, 1, 1))

    if spec.mode == "once":
        return [Part("all", dict(spec.fixed))]

    if spec.mode == "date":
        days = store.get_trade_days(start, today)
        if not days:
            raise BackfillError("交易日历为空，请先运行 `sync_market.py calendar`")
        return [
            Part(day.isoformat(), {spec.date_param: day.strftime("%Y%m%d")}) for day in days
        ]

    if spec.mode == "period":
        return [
            Part(period.isoformat(), {spec.date_param: period.strftime("%Y%m%d")})
            for period in quarter_ends(max(spec.start_year, start.year), today)
        ]

    if spec.mode == "month":
        parts: list[Part] = []
        cursor = date(max(spec.start_year, start.year), 1, 1)
        while cursor <= today:
            parts.append(Part(cursor.strftime("%Y-%m"), {spec.date_param: cursor.strftime("%Y%m")}))
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        return parts

    if spec.mode == "code":
        codes = _codes_for_source(spec.code_source)
        params_base = dict(spec.fixed)
        if spec.keys == ("ts_code",):
            return [Part(code, {"ts_code": code, **params_base}) for code in codes]
        return [
            Part(
                code,
                {
                    "ts_code": code,
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": today.strftime("%Y%m%d"),
                    **params_base,
                },
            )
            for code in codes
        ]

    raise BackfillError(f"未知的 mode：{spec.mode}")


# --------------------------------------------------------------------- engine
def _drop_rows_missing_keys(
    rows: Sequence[dict[str, Any]], keys: Sequence[str]
) -> tuple[list[dict[str, Any]], int]:
    """Drop rows that cannot satisfy the primary key.

    Some interfaces return index/aggregate lines with an empty ``ts_code``; one
    such row would abort a whole dataset on a NOT NULL constraint, so they are
    filtered and counted instead.
    """
    kept = [row for row in rows if all(row.get(key) not in (None, "") for key in keys)]
    return kept, len(rows) - len(kept)


def _run_dataset(
    spec: DatasetSpec,
    parts: list[Part],
    provider: TushareProvider,
    workers: int,
    progress: Optional[Callable[[dict[str, Any]], None]],
) -> dict[str, Any]:
    started = datetime.now()
    done_map = store.dataset_progress(spec.name)
    pending = [part for part in parts if not done_map.get(part.key, (0, False))[1]]
    if not pending:
        return {
            "dataset": spec.name,
            "label": spec.label,
            "parts": 0,
            "skipped": len(parts),
            "rows": 0,
            "status": "up-to-date",
        }

    is_core = spec.name in CORE_WRITERS
    total_rows = 0
    dropped_rows = 0
    errors: list[str] = []
    columns: Optional[list[str]] = None
    types: Optional[dict[str, str]] = None
    buffer: list[dict[str, Any]] = []
    marks: list[tuple[str, int, bool]] = []

    def flush() -> int:
        nonlocal buffer, marks
        # Checkpoint first: a part that legitimately yields no rows still must be
        # recorded, otherwise it is refetched on every run (and looks untouched).
        if marks:
            store.mark_progress(spec.name, marks)
            marks = []
        if not buffer:
            return 0
        if is_core:
            written = write_core(spec.name, buffer)
        elif columns is not None and types is not None:
            written = store.write_dataset(spec.name, columns, spec.keys, buffer)
        else:  # pragma: no cover - defensive
            written = 0
        buffer = []
        return written

    quota_hit = False
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {
            pool.submit(provider.query, spec.api, part.params, spec.fields): part
            for part in pending
        }
        for index, future in enumerate(as_completed(futures), start=1):
            part = futures[future]
            try:
                rows = future.result()
            except QuotaExceeded as exc:
                # Stop immediately: every remaining part would fail the same way.
                quota_hit = True
                errors.append(f"{part.key}: {exc}")
                break
            except ProviderError as exc:
                errors.append(f"{part.key}: {exc}")
                continue
            if not rows:
                marks.append((part.key, 0, True))
                continue
            rows, dropped = _drop_rows_missing_keys(rows, spec.keys)
            dropped_rows += dropped
            if not rows:
                marks.append((part.key, 0, True))
                continue
            if is_core:
                buffer.extend(rows)
            else:
                if columns is None:
                    columns, types = infer_schema(rows, spec)
                    store.ensure_dataset_table(spec.name, types, spec.keys)
                buffer.extend(normalize_rows(rows, columns, types or {}))
            marks.append((part.key, len(rows), True))
            if len(marks) >= FLUSH_PARTS:
                total_rows += flush()
            if progress:
                progress(
                    {
                        "dataset": spec.name,
                        "label": spec.label,
                        "done": index,
                        "total": len(pending),
                        "rows": total_rows + len(buffer),
                        "errors": len(errors),
                    }
                )
    finally:
        pool.shutdown(wait=not quota_hit, cancel_futures=quota_hit)
    total_rows += flush()

    if quota_hit:
        status = "quota-exhausted"
    else:
        status = "ok" if not errors else "partial"
    store.log_sync(
        f"backfill:{spec.name}",
        started,
        total_rows,
        status,
        f"{len(pending) - len(errors)}/{len(pending)} parts"
        + (f"；{errors[0]}" if errors else ""),
    )
    return {
        "dataset": spec.name,
        "label": spec.label,
        "parts": len(pending),
        "skipped": len(parts) - len(pending),
        "rows": total_rows,
        "dropped": dropped_rows,
        "status": status,
        "errors": errors[:5],
    }


def run(
    names: Optional[Sequence[str]] = None,
    years: Optional[int] = None,
    workers: int = 4,
    force: bool = False,
    max_parts: Optional[int] = None,
    progress: Optional[Callable[[dict[str, Any]], None]] = None,
    dates: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    years = years or settings.sync_years
    specs = specs_for(list(names) if names else None)
    provider = TushareProvider()
    ok, reason = provider.available()
    if not ok:
        raise BackfillError(f"Tushare 不可用：{reason}")

    store.init_schema()
    get_rate_limiter()
    summary: list[dict[str, Any]] = []
    wanted = {str(item) for item in dates} if dates else None

    for spec in specs:
        parts = build_parts(spec, years)
        if wanted is not None:
            # Targeted repair: only the named dates, and force a refetch by
            # marking them pending instead of clearing the whole dataset.
            parts = [part for part in parts if part.key in wanted]
            if not parts:
                summary.append(
                    {
                        "dataset": spec.name,
                        "label": spec.label,
                        "status": "no-matching-part",
                        "parts": 0,
                        "skipped": 0,
                        "rows": 0,
                    }
                )
                continue
            store.mark_progress(spec.name, [(part.key, 0, False) for part in parts])
        elif force:
            store.clear_progress(spec.name)
        if max_parts and wanted is None:
            done_map = store.dataset_progress(spec.name)
            parts = [p for p in parts if not done_map.get(p.key, (0, False))[1]][:max_parts]
        result = _run_dataset(spec, parts, provider, workers, progress)
        if spec.name in ADJ_DATASETS and result["status"] != "quota-exhausted":
            # Factors live in their own table; flattening into ``daily`` is one
            # pass instead of thousands of small updates.
            result["adj_applied"] = store.apply_adj_factors()
        summary.append(result)
        if result["status"] == "quota-exhausted":
            # Everything after this point would fail identically; the remaining
            # datasets stay unchecked and resume on the next run.
            summary.append(
                {
                    "dataset": "(其余数据集)",
                    "label": "已停止",
                    "status": "quota-exhausted",
                    "rows": 0,
                    "parts": 0,
                    "skipped": 0,
                }
            )
            break
    return summary


def plan(
    names: Optional[Sequence[str]] = None, years: Optional[int] = None
) -> list[dict[str, Any]]:
    """What a backfill would fetch, without fetching it."""
    years = years or settings.sync_years
    store.init_schema()
    output = []
    for spec in specs_for(list(names) if names else None):
        try:
            parts = build_parts(spec, years)
        except BackfillError as exc:
            output.append({"dataset": spec.name, "error": str(exc)})
            continue
        done = store.dataset_progress(spec.name)
        finished = sum(1 for part in parts if done.get(part.key, (0, False))[1])
        output.append(
            {
                "dataset": spec.name,
                "label": spec.label,
                "mode": spec.mode,
                "parts": len(parts),
                "finished": finished,
                "pending": len(parts) - finished,
                "daily": spec.daily,
            }
        )
    return output


DAILY_INDICES = (
    # 大盘 / 中小盘
    "000001.SH", "399001.SZ", "000300.SH", "000905.SH", "000852.SH", "399006.SZ",
    # 风格与板块
    "000016.SH",   # 上证50（大盘）
    "399303.SZ",   # 国证2000（小盘）
    "000688.SH",   # 科创50
    "000680.SH",   # 科创综指
    "399102.SZ",   # 创业板综
    "899050.BJ",   # 北证50
)


def refresh_indices(trade_date: Optional[date] = None) -> list[dict[str, Any]]:
    """Keep the benchmark indices current — the panel's daily update is per-date,
    but ``index_daily`` is pulled per code, so it would otherwise lag."""
    from app.data.datasets import BY_NAME

    spec = BY_NAME["index_daily"]
    provider = TushareProvider()
    target = trade_date or date.today()
    start = target - timedelta(days=10)
    results = []
    for code in DAILY_INDICES:
        try:
            rows = provider.query(
                spec.api,
                {
                    "ts_code": code,
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": target.strftime("%Y%m%d"),
                },
                spec.fields,
            )
        except ProviderError as exc:
            results.append({"index": code, "rows": 0, "status": "error", "error": str(exc)})
            continue
        if not rows:
            results.append({"index": code, "rows": 0, "status": "empty"})
            continue
        columns, types = infer_schema(rows, spec)
        store.ensure_dataset_table(spec.name, types, spec.keys)
        written = store.write_dataset(
            spec.name, columns, spec.keys, normalize_rows(rows, columns, types)
        )
        results.append({"index": code, "rows": written, "status": "ok"})
    return results


def daily_specs() -> list[DatasetSpec]:
    """Datasets the ``day`` command pulls for a single trade date.

    ``daily`` marks intent, but the pull shape still has to support a one-date
    request: ``date`` / ``period`` modes take a single date param, and
    ``daily_date`` marks code-mode specs the API can also serve per date.
    """
    return [
        spec
        for spec in specs_for()
        if spec.daily and (spec.mode in {"date", "period"} or spec.daily_date)
    ]


def _fill_late_publisher(
    spec: DatasetSpec,
    target: date,
    provider,
    days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Re-ask the previous trading days when today's pull came back empty.

    ``margin_detail`` is published T+1 and ``ths_hot`` / ``dc_hot`` often land
    after the evening job. Without this, the ``day`` command — which only ever
    asks for "today" — would leave them one day behind forever.
    """
    count = days if days is not None else spec.retry_days
    if count <= 0:
        return []
    recent = store.get_trade_days(target - timedelta(days=count * 3 + 5), target - timedelta(days=1))
    filled: list[dict[str, Any]] = []
    for day in list(recent)[-count:]:
        params = {spec.date_param: day.strftime("%Y%m%d")}
        try:
            rows = provider.query(spec.api, params, spec.fields)
        except ProviderError:
            continue
        if not rows:
            continue
        rows, _dropped = _drop_rows_missing_keys(rows, spec.keys)
        if not rows:
            continue
        if spec.name in CORE_WRITERS:
            written = write_core(spec.name, rows)
        else:
            columns, types = infer_schema(rows, spec)
            store.ensure_dataset_table(spec.name, types, spec.keys)
            written = store.write_dataset(
                spec.name, columns, spec.keys, normalize_rows(rows, columns, types)
            )
        store.log_sync(f"daily:{spec.name}", datetime.now(), written, "ok", f"{day} 回补")
        filled.append({"date": day.isoformat(), "rows": written})
    return filled


def update_today(
    trade_date: Optional[date] = None,
    progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> list[dict[str, Any]]:
    """Fetch only today's slice of every dataset marked ``daily``."""
    target = trade_date or date.today()
    if target >= date.today() and datetime.now().time() < MARKET_CLOSE:
        # Tushare publishes the daily bar after the close; before that the day is
        # either missing or half-formed, and a partial bar would poison factors.
        raise BackfillError(
            f"现在是 {datetime.now().strftime('%H:%M')}，A 股尚未收盘（15:05）。"
            "Tushare 的当日日线要收盘后才发布，请收盘后再执行。"
        )
    # Weekends and holidays: nothing to fetch, and a scheduled job should say so
    # instead of flooding the sync log with empty results.
    if not store.get_trade_days(target, target):
        return [{"dataset": "calendar", "rows": 0, "status": "non-trading-day"}]
    provider = TushareProvider()
    ok, reason = provider.available()
    if not ok:
        raise BackfillError(f"Tushare 不可用：{reason}")
    store.init_schema()

    # 交易日历只覆盖到某一天时，T+5/T+20 复盘会从那天起集体卡住。日更顺手把它
    # 续上（Tushare 一般在 12 月发布次年日历），比等人工发现便宜得多。
    remaining = store.get_trade_days(target, date(target.year + 1, 12, 31))
    if len(remaining) < CALENDAR_MIN_DAYS:
        from app.data import sync as sync_module

        try:
            sync_module.sync_calendar()
        except Exception as exc:  # noqa: BLE001 - 日历刷新失败不该挡住日更
            store.log_sync("daily:calendar", datetime.now(), 0, "error", str(exc)[:200])

    specs = daily_specs()
    results: list[dict[str, Any]] = []
    for spec in specs:
        started = datetime.now()
        params = {spec.date_param: target.strftime("%Y%m%d")}
        try:
            rows = provider.query(spec.api, params, spec.fields)
        except ProviderError as exc:
            store.log_sync(f"daily:{spec.name}", started, 0, "error", str(exc)[:500])
            results.append(
                {"dataset": spec.name, "rows": 0, "status": "error", "error": str(exc)}
            )
            continue
        if not rows:
            results.append(
                {
                    "dataset": spec.name,
                    "rows": 0,
                    "status": "empty",
                    "filled": _fill_late_publisher(spec, target, provider),
                }
            )
            continue
        rows, _dropped = _drop_rows_missing_keys(rows, spec.keys)
        if not rows:
            results.append({"dataset": spec.name, "rows": 0, "status": "empty"})
            continue
        if spec.name in CORE_WRITERS:
            written = write_core(spec.name, rows)
        else:
            columns, types = infer_schema(rows, spec)
            store.ensure_dataset_table(spec.name, types, spec.keys)
            written = store.write_dataset(
                spec.name, columns, spec.keys, normalize_rows(rows, columns, types)
            )
        store.log_sync(f"daily:{spec.name}", started, written, "ok", str(target))
        results.append({"dataset": spec.name, "rows": written, "status": "ok"})
        if progress:
            progress({"dataset": spec.name, "rows": written})
    for item in refresh_indices(target):
        results.append(
            {"dataset": f"index_daily:{item['index']}", "rows": item["rows"], "status": item["status"]}
        )
    if any(item["dataset"] in ADJ_DATASETS for item in results):
        store.apply_adj_factors(target)
    # New ETF / BJ codes show up in ``daily`` before any universe pull sees them;
    # register them so breadth, detail and the screener do not ignore them.
    added = store.backfill_symbols_from_daily()
    if added:
        results.append({"dataset": "symbols", "rows": added, "status": "ok"})
    return results
