"""自选指标：今日 / 近 5 日 / 添加后，以及旧数据的基准回填。"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.data import store
from app.db import add_watchlist, connection, init_db, list_watchlist
from app.services import market
from app.services import watchlist as wl


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    init_db()
    store.init_schema()
    return tmp_path


def _bar(symbol: str, day: str, close: float, adj: float = 1.0) -> dict:
    return {
        "symbol": symbol,
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "volume": 100.0,
        "amount": 1000.0,
        "adj_factor": adj,
    }


def _panel(symbol: str, rows: list[tuple[str, float, float]]) -> None:
    store.upsert_daily([_bar(symbol, day, close, adj) for day, close, adj in rows], "test")


def _no_quotes(monkeypatch):
    async def fake(codes):
        return {}

    monkeypatch.setattr(wl, "get_quotes", fake)


def _quotes(monkeypatch, mapping):
    async def fake(codes):
        return {code: mapping[code] for code in codes if code in mapping}

    monkeypatch.setattr(wl, "get_quotes", fake)


def test_panel_fallback_computes_5d_and_since_add(env, monkeypatch):
    _panel(
        "600519",
        [
            ("2024-01-02", 10.0, 1.0),
            ("2024-01-03", 10.5, 1.0),
            ("2024-01-04", 11.0, 1.0),
            ("2024-01-05", 11.5, 1.0),
            ("2024-01-08", 12.0, 1.0),
            ("2024-01-09", 12.5, 1.0),
        ],
    )
    add_watchlist(
        "600519",
        "贵州茅台",
        "stock",
        added_trade_date="2024-01-03",
        added_close=10.5,
        added_adj=1.0,
        added_source="panel",
    )
    _no_quotes(monkeypatch)

    row = asyncio.run(wl.build_metrics())[0]

    assert row["price"] == 12.5
    assert row["pct_chg"] == 4.17           # 12.5 / 12.0 - 1
    assert row["pct_5d"] == 25.0            # 12.5 / 10.0 - 1
    assert row["pct_since_add"] == 19.05    # 12.5 / 10.5 - 1
    assert row["as_of"] == "2024-01-09"
    assert row["quote_source"] == "panel"


def test_live_quote_adds_today_and_shifts_5d_window(env, monkeypatch):
    _panel(
        "600519",
        [
            ("2024-01-02", 10.0, 1.0),
            ("2024-01-03", 10.5, 1.0),
            ("2024-01-04", 11.0, 1.0),
            ("2024-01-05", 11.5, 1.0),
            ("2024-01-08", 12.0, 1.0),
            ("2024-01-09", 12.5, 1.0),
        ],
    )
    add_watchlist(
        "600519", "贵州茅台", "stock",
        added_trade_date="2024-01-03", added_close=10.0, added_adj=1.0,
    )
    _quotes(monkeypatch, {
        "600519": {
            "symbol": "600519",
            "price": 13.0,
            "pct_chg": 4.0,
            "trade_date": "2024-01-10",
            "source": "sina",
        }
    })

    row = asyncio.run(wl.build_metrics())[0]

    assert row["price"] == 13.0
    assert row["pct_chg"] == 4.0
    # 行情比面板新一天：13.0 / 10.5（5 个交易日前）- 1
    assert row["pct_5d"] == 23.81
    assert row["pct_since_add"] == 30.0
    assert row["as_of"] == "2024-01-10"
    assert row["quote_source"] == "sina"


def test_adjustment_factor_prevents_dividend_fake_drop(env, monkeypatch):
    _panel(
        "600519",
        [
            ("2024-01-02", 10.0, 1.0),
            ("2024-01-03", 10.0, 1.0),
            ("2024-01-04", 10.0, 1.0),
            ("2024-01-05", 10.0, 1.0),
            ("2024-01-08", 10.0, 1.0),
            ("2024-01-09", 5.0, 2.0),   # 10 送 10：原始价腰斩，复权价不变
        ],
    )
    add_watchlist(
        "600519", "贵州茅台", "stock",
        added_trade_date="2024-01-02", added_close=10.0, added_adj=1.0,
    )
    _no_quotes(monkeypatch)

    row = asyncio.run(wl.build_metrics())[0]

    assert row["price"] == 5.0
    assert row["pct_chg"] == 0.0
    assert row["pct_5d"] == 0.0
    assert row["pct_since_add"] == 0.0


def test_missing_panel_still_uses_quote(env, monkeypatch):
    add_watchlist(
        "600519", "贵州茅台", "stock",
        added_trade_date="2024-01-02", added_close=9.0, added_adj=1.0,
    )
    _quotes(monkeypatch, {
        "600519": {
            "symbol": "600519",
            "price": 10.0,
            "pct_chg": 2.0,
            "trade_date": "2024-01-09",
            "source": "sina",
        }
    })

    row = asyncio.run(wl.build_metrics())[0]

    assert row["price"] == 10.0
    assert row["pct_chg"] == 2.0
    assert row["pct_5d"] is None
    assert row["pct_since_add"] == 11.11     # 10 / 9 - 1
    assert row["as_of"] == "2024-01-09"


def test_legacy_watchlist_backfills_baseline_from_created_at(env, monkeypatch):
    _panel(
        "600519",
        [
            ("2024-01-02", 10.0, 1.0),
            ("2024-01-03", 10.5, 1.0),
            ("2024-01-04", 11.5, 1.0),
            ("2024-01-05", 11.8, 1.0),
            ("2024-01-08", 12.0, 1.0),
            ("2024-01-09", 12.5, 1.0),
        ],
    )
    add_watchlist("600519", "贵州茅台", "stock")
    with connection() as conn:
        conn.execute(
            "UPDATE watchlist SET created_at=? WHERE symbol=?",
            ("2024-01-04T10:00:00+08:00", "600519"),
        )
    _no_quotes(monkeypatch)

    row = asyncio.run(wl.build_metrics())[0]

    assert row["added_trade_date"] == "2024-01-04"
    assert row["pct_since_add"] == 8.7      # 12.5 / 11.5 - 1
    saved = list_watchlist()[0]
    assert saved["added_close"] == 11.5
    assert saved["added_source"] == "panel_backfill"


def test_re_adding_does_not_reset_the_baseline(env):
    add_watchlist(
        "600519", "贵州茅台", "stock",
        added_trade_date="2024-01-02", added_close=10.0, added_adj=1.0, added_source="panel",
    )
    add_watchlist(
        "600519", "贵州茅台", "stock",
        added_trade_date="2024-01-09", added_close=12.0, added_adj=1.0, added_source="quote",
    )
    saved = list_watchlist()[0]
    assert saved["added_close"] == 10.0
    assert saved["added_trade_date"] == "2024-01-02"


def test_sina_batch_quote_parses_every_line(monkeypatch):
    market._quote_cache.clear()
    fields = ["贵州茅台", "1500", "1480", "1505", "1510", "1470", "0", "0", "12345", "678900"]
    fields += ["0"] * 20
    fields += ["2024-01-05", "15:00:00"]
    payload = ",".join(fields)
    text = (
        f'var hq_str_sh600519="{payload}";\n'
        f'var hq_str_sz000001="{payload}";\n'
    )

    async def fake_get_text(url, headers, encoding="utf-8", timeout=None):
        assert "sh600519" in url and "sz000001" in url
        return text

    monkeypatch.setattr(market, "_get_text", fake_get_text)

    quotes = asyncio.run(market.get_quotes(["600519", "000001"]))

    assert set(quotes) == {"600519", "000001"}
    assert quotes["600519"]["price"] == 1505.0
    assert quotes["600519"]["pct_chg"] == round((1505 - 1480) / 1480 * 100, 4)
    assert quotes["600519"]["trade_date"] == "2024-01-05"
