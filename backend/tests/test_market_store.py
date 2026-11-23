"""Tests for the DuckDB market panel (no network access)."""

from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.data import store


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    return store


def _bar(symbol: str, day: str, close: float, factor: float | None = None, **extra):
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
        "adj_factor": factor,
    }
    payload.update(extra)
    return payload


def test_init_and_coverage(panel):
    assert panel.is_ready() is False
    panel.upsert_symbols([{"symbol": "000001", "name": "平安银行", "exchange": "SZ"}])
    panel.upsert_daily([_bar("000001", "2024-01-02", 10.0)], "test")
    coverage = panel.get_coverage()
    assert coverage["ready"] is True
    assert coverage["symbols"] == 1
    assert coverage["bars"] == 1
    assert coverage["first_date"] == date(2024, 1, 2)


def test_daily_upsert_keeps_existing_adj_factor(panel):
    panel.upsert_daily([_bar("000001", "2024-01-02", 10.0, factor=2.0)], "test")
    # A later raw-price refresh carries no factor and must not erase it.
    panel.upsert_daily([_bar("000001", "2024-01-02", 11.0)], "test")
    bars = panel.get_daily("000001")
    assert len(bars) == 1
    assert bars[0]["close"] == 11.0
    assert bars[0]["adj_factor"] == 2.0


def test_adjustment_math(panel):
    panel.upsert_daily(
        [
            _bar("000001", "2024-01-02", 10.0, factor=1.0),
            _bar("000001", "2024-01-03", 10.0, factor=2.0),
        ],
        "test",
    )
    hfq = panel.get_daily("000001", adjust="hfq")
    assert [bar["close"] for bar in hfq] == [10.0, 20.0]

    # 前复权 anchors on the latest factor: the newest bar stays at raw price.
    qfq = panel.get_daily("000001", adjust="qfq")
    assert [bar["close"] for bar in qfq] == [5.0, 10.0]

    raw = panel.get_daily("000001", adjust="none")
    assert [bar["close"] for bar in raw] == [10.0, 10.0]


def test_symbol_merge_never_blanks_fields(panel):
    panel.upsert_symbols(
        [
            {
                "symbol": "000001",
                "name": "平安银行",
                "exchange": "SZ",
                "board": "深市主板",
                "industry": "银行",
                "list_date": date(1991, 4, 3),
                "is_active": True,
            }
        ]
    )
    # A snapshot knows the name and board only.
    panel.upsert_symbols([{"symbol": "000001", "name": "平安银行", "board": "深市主板"}])
    item = panel.get_symbol("000001")
    assert item["industry"] == "银行"
    assert item["list_date"] == date(1991, 4, 3)
    assert item["exchange"] == "SZ"


def test_calendar_and_trade_days(panel):
    panel.upsert_calendar(
        [
            (date(2024, 1, 1), False),
            (date(2024, 1, 2), True),
            (date(2024, 1, 3), True),
        ],
        "test",
    )
    days = panel.get_trade_days()
    assert days == [date(2024, 1, 2), date(2024, 1, 3)]
    assert len(panel.get_calendar()) == 3


def test_snapshot_joins_symbols_and_basic(panel):
    panel.upsert_symbols(
        [{"symbol": "600000", "name": "浦发银行", "exchange": "SH", "asset_type": "stock"}]
    )
    panel.upsert_daily(
        [_bar("600000", "2024-01-02", 7.5, factor=1.0, amount=5e8)], "test"
    )
    panel.upsert_daily_basic(
        [{"symbol": "600000", "trade_date": "2024-01-02", "pe_ttm": 5.1, "pb": 0.4}],
        "test",
    )
    rows = panel.get_snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "浦发银行"
    assert row["pe_ttm"] == 5.1
    assert row["pb"] == 0.4
    assert row["trade_date"] == date(2024, 1, 2)


def test_last_bar_dates_for_incremental_sync(panel):
    panel.upsert_daily(
        [
            _bar("000001", "2024-01-02", 10.0),
            _bar("000001", "2024-01-05", 10.5),
            _bar("000002", "2024-01-03", 8.0),
        ],
        "test",
    )
    dates = panel.last_bar_dates()
    assert dates["000001"] == date(2024, 1, 5)
    assert dates["000002"] == date(2024, 1, 3)
    assert panel.last_bar_dates(["000002"]) == {"000002": date(2024, 1, 3)}
