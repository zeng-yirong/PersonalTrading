"""面板前置检查：18:20 生成复盘前，先确认日更已经把指数与行业写完。"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.data import store
from app.market import freshness


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    store.upsert_calendar(
        [(date(2026, 11, 26), True), (date(2026, 11, 27), True)], "test"
    )
    store.upsert_symbols(
        [
            {"symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台", "asset_type": "stock"},
            {"symbol": "000001", "ts_code": "000001.SZ", "name": "平安银行", "asset_type": "stock"},
        ]
    )
    return store


def _bar(symbol: str, day: str, close: float = 10.0) -> dict:
    return {"symbol": symbol, "trade_date": day, "close": close, "amount": 1e8}


def _write_index(day: str) -> None:
    store.ensure_dataset_table(
        "index_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.write_dataset(
        "index_daily",
        ["ts_code", "trade_date", "close"],
        ("ts_code", "trade_date"),
        [{"ts_code": "000001.SH", "trade_date": day, "close": 3900.0}],
    )


def _write_sw(day: str) -> None:
    store.ensure_dataset_table(
        "sw_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.write_dataset(
        "sw_daily",
        ["ts_code", "trade_date", "close"],
        ("ts_code", "trade_date"),
        [{"ts_code": "801010.SI", "trade_date": day, "close": 100.0}],
    )


def test_ready_only_when_every_table_reaches_the_panel_date(panel):
    store.upsert_daily(
        [_bar("600519", "2026-11-26"), _bar("000001", "2026-11-26"),
         _bar("600519", "2026-11-27"), _bar("000001", "2026-11-27")],
        "test",
    )
    _write_index("2026-11-27")
    _write_sw("2026-11-26")                      # 申万还停在昨天

    state = freshness.panel_freshness()

    assert state["ready"] is False
    assert state["panel"] == "2026-11-27"
    assert any("sw_daily" in item for item in state["missing"])
    assert not any("index_daily" in item for item in state["missing"])


def test_mid_write_daily_is_caught_by_the_row_ratio(panel):
    store.upsert_daily(
        [_bar("600519", "2026-11-26"), _bar("000001", "2026-11-26")]
        + [_bar("600519", "2026-11-27")],        # 只写了一半
        "test",
    )
    _write_index("2026-11-27")
    _write_sw("2026-11-27")

    state = freshness.panel_freshness()

    assert state["ready"] is False
    assert any("daily" in item and "未写完" in item for item in state["missing"])


def test_ready_when_everything_lines_up(panel):
    store.upsert_daily(
        [_bar("600519", "2026-11-26"), _bar("000001", "2026-11-26"),
         _bar("600519", "2026-11-27"), _bar("000001", "2026-11-27")],
        "test",
    )
    _write_index("2026-11-27")
    _write_sw("2026-11-27")

    state = freshness.panel_freshness()

    assert state["ready"] is True
    assert state["missing"] == []
    assert state["rows"] == 2


def test_wait_retries_until_ready(panel):
    store.upsert_daily(
        [_bar("600519", "2026-11-26"), _bar("600519", "2026-11-27")], "test"
    )
    _write_index("2026-11-27")
    _write_sw("2026-11-26")                      # 第一次检查：未就绪

    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        _write_sw("2026-11-27")                  # 睡完之后日更补上了

    state = freshness.wait_until_fresh(tries=3, interval=1.0, sleep=fake_sleep)

    assert state["ready"] is True
    assert state["attempts"] == 1
    assert state["waited_seconds"] == 1
    assert slept == [1.0]


def test_wait_gives_up_after_tries(panel):
    store.upsert_daily(
        [_bar("600519", "2026-11-26"), _bar("600519", "2026-11-27")], "test"
    )
    _write_index("2026-11-27")
    _write_sw("2026-11-26")

    state = freshness.wait_until_fresh(tries=2, interval=1.0, sleep=lambda _: None)

    assert state["ready"] is False
    assert state["attempts"] == 2
    assert state["missing"]
