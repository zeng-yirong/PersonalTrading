"""Tests for the Tushare dataset backfill engine (no network access)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.config import settings
from app.data import backfill, store
from app.data.datasets import DatasetSpec, quarter_ends


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    return store


def _daily_payload(ts_code: str, day: str, close: float = 10.0) -> dict:
    return {
        "ts_code": ts_code,
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "vol": 1000.0,          # 手
        "amount": 5000.0,       # 千元
    }


# ------------------------------------------------------------- unit handling
def test_daily_units_are_converted_once():
    rows = backfill.to_core_rows("daily", [_daily_payload("000001.SZ", "20240102", 9.5)])
    assert len(rows) == 1
    bar = rows[0]
    assert bar["symbol"] == "000001"
    assert bar["trade_date"] == date(2024, 1, 2)
    assert bar["volume"] == pytest.approx(100_000.0)      # 手 -> 股
    assert bar["amount"] == pytest.approx(5_000_000.0)    # 千元 -> 元
    assert bar["source"] == "tushare"


def test_daily_basic_market_cap_units():
    rows = backfill.to_core_rows(
        "daily_basic",
        [
            {
                "ts_code": "600000.SH",
                "trade_date": "20240102",
                "pe_ttm": 5.0,
                "pb": 0.4,
                "total_mv": 1000.0,   # 万元
                "circ_mv": 900.0,
                "turnover_rate": 0.8,
            }
        ],
    )
    assert rows[0]["total_mv"] == pytest.approx(10_000_000.0)
    assert rows[0]["circ_mv"] == pytest.approx(9_000_000.0)
    assert rows[0]["symbol"] == "600000"


def test_core_write_lands_in_panel(panel):
    written = backfill.write_core("daily", [_daily_payload("000001.SZ", "20240102")])
    assert written == 1
    bar = store.get_daily("000001")[0]
    assert bar["close"] == 10.0
    assert bar["volume"] == pytest.approx(100_000.0)


# ------------------------------------------------------------------- schema
def test_schema_inference_types():
    spec = DatasetSpec(name="moneyflow", api="moneyflow", label="资金", mode="date")
    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "net_mf_amount": 12.5, "note": "x"},
        {"ts_code": "000002.SZ", "trade_date": "20240103", "net_mf_amount": 3.0, "note": "y"},
    ]
    columns, types = backfill.infer_schema(rows, spec)
    assert columns == ["ts_code", "trade_date", "net_mf_amount", "note"]
    assert types["ts_code"] == "VARCHAR"
    assert types["trade_date"] == "DATE"
    assert types["net_mf_amount"] == "DOUBLE"
    assert types["note"] == "VARCHAR"


def test_normalize_rows_parses_dates_and_nulls():
    spec = DatasetSpec(name="x", api="x", label="x", mode="date")
    columns = ["ts_code", "trade_date", "value"]
    types = {"ts_code": "VARCHAR", "trade_date": "DATE", "value": "DOUBLE"}
    rows = backfill.normalize_rows(
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "value": "1.5"}], columns, types
    )
    assert rows[0]["trade_date"] == date(2024, 1, 2)
    assert rows[0]["value"] == 1.5

    rows = backfill.normalize_rows(
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "value": "n/a"}], columns, types
    )
    assert rows[0]["value"] is None


def test_adds_columns_on_a_widened_field_set(panel):
    store.ensure_dataset_table("probe", {"ts_code": "VARCHAR", "value": "DOUBLE"}, ("ts_code",))
    store.write_dataset("probe", ["ts_code", "value"], ("ts_code",), [{"ts_code": "a", "value": 1.0}])
    # A later response introduces a new field.
    store.ensure_dataset_table(
        "probe", {"ts_code": "VARCHAR", "value": "DOUBLE", "extra": "VARCHAR"}, ("ts_code",)
    )
    store.write_dataset(
        "probe",
        ["ts_code", "value", "extra"],
        ("ts_code",),
        [{"ts_code": "b", "value": 2.0, "extra": "new"}],
    )
    with store.session(read_only=True) as conn:
        rows = conn.execute("SELECT ts_code, value, extra FROM probe ORDER BY ts_code").fetchall()
    assert rows == [("a", 1.0, None), ("b", 2.0, "new")]


# -------------------------------------------------------------------- parts
def test_date_parts_follow_the_calendar(panel):
    store.upsert_calendar(
        [(date(2024, 1, 2), True), (date(2024, 1, 3), True), (date(2024, 1, 6), False)],
        "test",
    )
    spec = DatasetSpec(name="daily", api="daily", label="日线", mode="date", start_year=2020)
    parts = backfill.build_parts(spec, years=10, today=date(2024, 1, 8))
    assert [part.key for part in parts] == ["2024-01-02", "2024-01-03"]
    assert parts[0].params == {"trade_date": "20240102"}


def test_period_parts_are_quarter_ends():
    spec = DatasetSpec(
        name="income_vip",
        api="income_vip",
        label="利润表",
        mode="period",
        date_param="period",
        date_field="end_date",
        start_year=2023,
    )
    parts = backfill.build_parts(spec, years=10, today=date(2024, 8, 1))
    keys = [part.key for part in parts]
    assert keys[0] == "2023-03-31"
    assert keys[-1] == "2024-06-30"
    assert len(keys) == 6


def test_quarter_ends_for_a_year():
    assert quarter_ends(2024, today=date(2024, 12, 31)) == [
        date(2024, 3, 31),
        date(2024, 6, 30),
        date(2024, 9, 30),
        date(2024, 12, 31),
    ]


# ------------------------------------------------------------------- resume
class StubProvider:
    name = "stub"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def query(self, api, params, fields=""):
        self.calls.append(params)
        return [_daily_payload("000001.SZ", params["trade_date"])]

    def available(self):
        return True, ""


def test_backfill_is_resumable(panel):
    store.upsert_calendar(
        [(date(2024, 1, 2), True), (date(2024, 1, 3), True)], "test"
    )
    store.mark_progress("daily", [("2024-01-02", 1, True)])

    spec = backfill.DatasetSpec(name="daily", api="daily", label="日线", mode="date")
    parts = backfill.build_parts(spec, years=10, today=date(2024, 1, 8))
    provider = StubProvider()

    result = backfill._run_dataset(spec, parts, provider, workers=1, progress=None)
    assert result["status"] == "ok"
    assert provider.calls == [{"trade_date": "20240103"}]  # finished part skipped
    assert store.dataset_progress("daily")["2024-01-03"][1] is True


def test_update_today_refuses_before_the_close(monkeypatch):
    """Tushare publishes the daily bar after close; a midday fetch is a partial bar."""

    class Midday:
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, 8, 10, 30)

    monkeypatch.setattr(backfill, "datetime", Midday)
    with pytest.raises(backfill.BackfillError, match="尚未收盘"):
        backfill.update_today()


def test_update_today_skips_a_non_trading_day(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 5), True), (date(2024, 1, 6), False)], "test")

    class Saturday:
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, 6, 16, 0)

    monkeypatch.setattr(backfill, "datetime", Saturday)
    result = backfill.update_today(date(2024, 1, 6))
    assert result[0]["status"] == "non-trading-day"


def test_reserved_word_columns_are_quoted(panel):
    """`limit_list_d` really has a column named ``limit``: quote identifiers."""
    store.ensure_dataset_table(
        "limit_list_d",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "limit": "VARCHAR", "up_stat": "VARCHAR"},
        ("ts_code", "trade_date"),
    )
    rows = [
        {"ts_code": "000001.SZ", "trade_date": date(2024, 1, 2), "limit": "U", "up_stat": "2/3"},
    ]
    assert store.write_dataset(
        "limit_list_d", ["ts_code", "trade_date", "limit", "up_stat"], ("ts_code", "trade_date"), rows
    ) == 1
    # Upsert path (ON CONFLICT) must also quote the reserved column.
    rows[0]["limit"] = "D"
    store.write_dataset(
        "limit_list_d", ["ts_code", "trade_date", "limit", "up_stat"], ("ts_code", "trade_date"), rows
    )
    with store.session(read_only=True) as conn:
        got = conn.execute('SELECT "limit", up_stat FROM limit_list_d').fetchall()
    assert got == [("D", "2/3")]


class QuotaProvider:
    """Fails like the gateway does once the daily request cap is reached."""

    name = "quota"
    calls = 0

    def query(self, api, params, fields=""):
        QuotaProvider.calls += 1
        from app.data.providers.tushare import QuotaExceeded

        raise QuotaExceeded("Tushare daily 返回 42901：已超 token 每日请求上限")

    def available(self):
        return True, ""


def test_quota_exhaustion_stops_the_dataset(panel):
    store.upsert_calendar(
        [(date(2024, 1, 2), True), (date(2024, 1, 3), True), (date(2024, 1, 4), True)],
        "test",
    )
    spec = backfill.DatasetSpec(name="daily", api="daily", label="日线", mode="date")
    parts = backfill.build_parts(spec, years=10, today=date(2024, 1, 8))
    provider = QuotaProvider()
    QuotaProvider.calls = 0

    result = backfill._run_dataset(spec, parts, provider, workers=1, progress=None)
    assert result["status"] == "quota-exhausted"
    # One failed request is enough: the rest must not be attempted.
    assert QuotaProvider.calls < len(parts)
    # Nothing is checkpointed as done, so a later run retries these parts.
    assert store.dataset_progress("daily") == {}


def test_backfill_is_idempotent(panel):
    store.upsert_calendar([(date(2024, 1, 2), True)], "test")
    spec = backfill.DatasetSpec(name="daily", api="daily", label="日线", mode="date")
    parts = backfill.build_parts(spec, years=10, today=date(2024, 1, 8))
    provider = StubProvider()

    backfill._run_dataset(spec, parts, provider, workers=1, progress=None)
    store.clear_progress("daily")
    backfill._run_dataset(spec, parts, provider, workers=1, progress=None)

    with store.session(read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
    assert count == 1  # upserted on (symbol, trade_date), not duplicated
