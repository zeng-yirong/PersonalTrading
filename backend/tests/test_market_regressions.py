"""Regression tests for the defects found in review of the P0 data layer."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from app.config import settings
from app.data import store, sync
from app.data.providers import base as provider_base


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    return store


def _bar(symbol: str, day: str, close: float = 10.0, **extra):
    payload = {
        "symbol": symbol,
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "volume": 100.0,
        "amount": 1000.0,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------- lock retry
def test_session_retries_a_busy_file(panel, monkeypatch):
    real_connect = store.connect
    attempts = {"n": 0}

    def flaky_connect(read_only: bool = False):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise store.StoreLocked("市场数据库被其他进程占用")
        return real_connect(read_only=read_only)

    monkeypatch.setattr(store, "connect", flaky_connect)
    monkeypatch.setattr(store.time, "sleep", lambda _seconds: None)

    with store.session() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert attempts["n"] == 3


def test_store_locked_detected_from_windows_message():
    exc = duckdb.IOException(
        'Cannot open file "x.duckdb": 另一个程序正在使用此文件，进程无法访问。'
    )
    assert store._is_locked(exc)
    assert not store._is_locked(duckdb.IOException("syntax error"))


# ------------------------------------------------------- adjustment anchoring
def test_qfq_anchor_does_not_depend_on_window(panel):
    bars = [
        _bar("000001", "2024-01-02", 5.0, adj_factor=1.0),
        _bar("000001", "2024-01-03", 7.5, adj_factor=1.5),
        _bar("000001", "2024-01-04", 10.0, adj_factor=2.0),
    ]
    panel.upsert_daily(bars, "test")

    full = panel.get_daily("000001", adjust="qfq")
    assert [bar["close"] for bar in full] == [2.5, 5.625, 10.0]

    # Same date through a narrower window must be rebased identically.
    windowed = panel.get_daily("000001", end=date(2024, 1, 3), adjust="qfq")
    assert [bar["close"] for bar in windowed] == [2.5, 5.625]

    limited = panel.get_daily("000001", adjust="qfq", limit=10)
    assert [bar["close"] for bar in limited] == [2.5, 5.625, 10.0]


# ----------------------------------------------------------- no field erosion
def test_raw_reinsert_keeps_amount_and_factor(panel):
    panel.upsert_daily(
        [_bar("000001", "2024-01-02", 10.0, amount=7.1e6, adj_factor=2.0)], "test"
    )
    # A provider that only knows prices (Sina history) must not null out the rest.
    panel.upsert_daily(
        [_bar("000001", "2024-01-02", 11.0, amount=None, adj_factor=None)], "test"
    )
    bar = panel.get_daily("000001")[0]
    assert bar["close"] == 11.0
    assert bar["amount"] == 7.1e6
    assert bar["adj_factor"] == 2.0


def test_upsert_adj_factors_never_writes_null(panel):
    panel.upsert_daily([_bar("000001", "2024-01-02", 10.0, adj_factor=2.0)], "test")
    panel.upsert_adj_factors(
        [{"symbol": "000001", "trade_date": "2024-01-02", "adj_factor": None}], "test"
    )
    assert panel.get_daily("000001")[0]["adj_factor"] == 2.0

    panel.upsert_adj_factors(
        [{"symbol": "000001", "trade_date": "2024-01-02", "adj_factor": 3.0}], "test"
    )
    assert panel.get_daily("000001")[0]["adj_factor"] == 3.0


# --------------------------------------------------------------- empty schema
def test_coverage_on_uninitialised_file(tmp_path, monkeypatch):
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()  # file exists, no tables
    monkeypatch.setattr(settings, "market_db_path", str(path))
    coverage = store.get_coverage()
    assert coverage["ready"] is False
    assert coverage["bars"] == 0
    assert store.is_ready() is False


# ------------------------------------------------------------ stale calendar
def test_stale_calendar_is_refused(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 8), True)], "test")
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 3, 15))
    with pytest.raises(sync.SyncError, match="日历可能已过期"):
        sync.resolve_target_date()


def test_small_calendar_gap_is_tolerated(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 5), True)], "test")
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 6))
    assert sync.resolve_target_date() == date(2024, 1, 5)


# --------------------------------------------------- intraday history guard
def test_history_skips_the_bar_of_the_open_session(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 8), True)], "test")
    store.upsert_symbols([{"symbol": "600000", "name": "浦发银行"}])

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, 8, 10, 30)

    class Stub(provider_base.Provider):
        name = "stub"
        label = "stub"
        capabilities = provider_base.Capabilities(history=True)

        def available(self):
            return True, ""

        def daily_bars(self, symbol, start=None, end=None, adjust="none"):
            return [
                _bar("600000", "2024-01-05", 7.0),
                _bar("600000", "2024-01-08", 7.5),  # in-progress session
            ]

    monkeypatch.setattr(sync, "datetime", FakeDatetime)
    monkeypatch.setattr(sync, "candidates", lambda capability, prefer=None: [Stub()])

    result = sync.sync_history(start=date(2024, 1, 1))
    assert result["status"] == "ok"
    stored = [bar["trade_date"].isoformat() for bar in store.get_daily("600000")]
    assert stored == ["2024-01-05"]


# --------------------------------------------------- incremental heuristics
def test_snapshot_bars_do_not_count_as_history(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 8), True)], "test")
    store.upsert_symbols([{"symbol": "000001", "name": "平安银行"}])
    # 100 bars, but only one month of span: still not real history.
    start = date(2023, 12, 1)
    panel.upsert_daily(
        [
            _bar("000001", (start + timedelta(days=i)).isoformat())
            for i in range(0, 30)
        ],
        "test",
    )

    requested: list[object] = []

    class Recording(provider_base.Provider):
        name = "rec"
        label = "rec"
        capabilities = provider_base.Capabilities(history=True)

        def available(self):
            return True, ""

        def daily_bars(self, symbol, start=None, end=None, adjust="none"):
            requested.append(start)
            return []

    monkeypatch.setattr(sync, "candidates", lambda capability, prefer=None: [Recording()])
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 8))
    sync.sync_history(incremental=True, start=date(2021, 1, 1))
    assert requested == [date(2021, 1, 1)]


def test_deep_history_uses_incremental_window(panel, monkeypatch):
    store.upsert_calendar([(date(2024, 1, 8), True)], "test")
    store.upsert_symbols([{"symbol": "000001", "name": "平安银行"}])
    deep_start = date(2023, 1, 1)
    panel.upsert_daily(
        [_bar("000001", (deep_start + timedelta(days=i)).isoformat()) for i in range(0, 200)],
        "test",
    )

    requested: list[object] = []

    class Recording(provider_base.Provider):
        name = "rec"
        label = "rec"
        capabilities = provider_base.Capabilities(history=True)

        def available(self):
            return True, ""

        def daily_bars(self, symbol, start=None, end=None, adjust="none"):
            requested.append(start)
            return []

    monkeypatch.setattr(sync, "candidates", lambda capability, prefer=None: [Recording()])
    monkeypatch.setattr(sync, "_today", lambda: date(2024, 1, 8))
    sync.sync_history(incremental=True, start=date(2021, 1, 1))
    last = deep_start + timedelta(days=199)
    assert requested == [last - timedelta(days=sync.OVERLAP_DAYS)]
