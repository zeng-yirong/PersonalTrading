"""Tests for sync bookkeeping and provider parsing (no network access)."""

from __future__ import annotations

from datetime import date, datetime, time as dtime

import pytest

from app.config import settings
from app.data import store, sync
from app.data.providers import base as provider_base
from app.data.providers.eastmoney import EastmoneyProvider
from app.data.providers.sina import _parse_payload


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    store.upsert_calendar(
        [
            (date(2024, 1, 5), True),   # Friday
            (date(2024, 1, 6), False),  # Saturday
            (date(2024, 1, 8), True),   # Monday
        ],
        "test",
    )
    return store


class StubSpotProvider(provider_base.Provider):
    name = "stub"
    label = "stub"
    capabilities = provider_base.Capabilities(spot=True)

    def available(self):
        return True, ""

    def spot_snapshot(self):
        return [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "asset_type": "stock",
                "board": "沪市主板",
                "trade_date": date(2024, 1, 6),  # provider's "today"; sync must override
                "open": 7.0,
                "high": 7.2,
                "low": 6.9,
                "close": 7.1,
                "pre_close": 7.0,
                "volume": 1e6,
                "amount": 7e6,
                "pe": 5.0,
                "pb": 0.4,
                "total_mv": 1e10,
                "circ_mv": 9e9,
                "turnover_rate": 1.2,
                "source": "stub",
            }
        ]


def test_resolve_target_uses_last_open_day(panel):
    # Saturday has no session: the snapshot belongs to Friday.
    assert sync.resolve_target_date(date(2024, 1, 6)) == date(2024, 1, 5)
    assert sync.resolve_target_date(date(2024, 1, 8)) == date(2024, 1, 8)


def test_intraday_guard(panel, monkeypatch):
    today = date(2024, 1, 8)

    class Midday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, 8, 10, 30)

    monkeypatch.setattr(sync, "datetime", Midday)
    with pytest.raises(sync.SyncError, match="尚未收盘"):
        sync._intraday_guard(today, allow_intraday=False)
    sync._intraday_guard(today, allow_intraday=True)

    # A past target is always safe.
    sync._intraday_guard(date(2024, 1, 5), allow_intraday=False)


def test_snapshot_stamps_trading_day_not_calendar_day(panel, monkeypatch):
    monkeypatch.setattr(sync, "provider_for", lambda capability, prefer=None: StubSpotProvider())
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 6))

    result = sync.sync_snapshot()
    assert result["trade_date"] == date(2024, 1, 5)
    bars = store.get_daily("600000")
    assert len(bars) == 1
    assert bars[0]["trade_date"] == date(2024, 1, 5)
    assert store.get_symbol("600000")["board"] == "沪市主板"


def test_snapshot_rejects_historical_date(panel, monkeypatch):
    monkeypatch.setattr(sync, "provider_for", lambda capability, prefer=None: StubSpotProvider())
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 8))
    with pytest.raises(sync.SyncError, match="只提供当日截面"):
        sync.sync_snapshot(trade_date=date(2024, 1, 5))


def test_incremental_ignores_shallow_history(panel, monkeypatch):
    """A symbol holding just a snapshot bar must be refetched from scratch."""
    panel.upsert_symbols(
        [
            {"symbol": "000001", "name": "平安银行"},
            {"symbol": "000002", "name": "万科A"},
        ]
    )
    panel.upsert_daily(
        [{"symbol": "000001", "trade_date": f"2023-{m:02d}-01", "close": 10.0} for m in range(1, 7)],
        "test",
    )
    panel.upsert_daily([{"symbol": "000001", "trade_date": "2023-12-01", "close": 10.0}], "test")
    panel.upsert_daily([{"symbol": "000002", "trade_date": "2024-01-05", "close": 8.0}], "test")

    captured: list[tuple[str, object]] = []

    class Recording(provider_base.Provider):
        name = "rec"
        label = "rec"
        capabilities = provider_base.Capabilities(history=True)

        def available(self):
            return True, ""

        def daily_bars(self, symbol, start=None, end=None, adjust="none"):
            captured.append((symbol, start))
            return []

    monkeypatch.setattr(sync, "candidates", lambda capability, prefer=None: [Recording()])
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 8))
    sync.sync_history(incremental=True, start=date(2020, 1, 1))

    starts = dict(captured)
    # 000001 has 7 bars (< MIN_BARS_FOR_INCREMENTAL) -> full history requested.
    assert starts["000001"] == date(2020, 1, 1)
    assert starts["000002"] == date(2020, 1, 1)


def test_sina_payload_accepts_unquoted_keys():
    raw = '[{symbol:"sh600000",code:"600000",name:"浦发银行",trade:"7.10"}]'
    rows = _parse_payload(raw)
    assert rows[0]["code"] == "600000"
    assert rows[0]["trade"] == "7.10"
    assert _parse_payload("null") == []
    assert _parse_payload("") == []


def test_eastmoney_row_units_and_pre_close():
    row = "2024-01-02,9.39,9.21,9.42,9.21,1158366,1075742252.45,2.24,-1.92,-0.18,0.60"
    bar = EastmoneyProvider._row_to_bar("000001", row)
    assert bar["trade_date"] == "2024-01-02"
    assert bar["open"] == 9.39
    assert bar["close"] == 9.21
    assert bar["high"] == 9.42
    # 手 -> 股
    assert bar["volume"] == pytest.approx(115836600.0)
    assert bar["pre_close"] == pytest.approx(9.39)
