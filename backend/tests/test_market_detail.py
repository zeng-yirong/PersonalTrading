"""Tests for the market overview and the per-stock detail composition."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config import settings
from app.data import intraday, store
from app.db import init_db
from app.market import overview
from app.services import detail as detail_module


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()
    store.upsert_symbols(
        [
            {"symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台",
             "industry": "白酒", "asset_type": "stock"},
            {"symbol": "000001", "ts_code": "000001.SZ", "name": "平安银行",
             "industry": "银行", "asset_type": "stock"},
        ]
    )
    days = [date(2026, 11, 17) + timedelta(days=index) for index in range(5)]
    rows = []
    for step, day in enumerate(days):
        for index, (symbol, ts_code) in enumerate((("600519", "600519.SH"), ("000001", "000001.SZ"))):
            close = 1400 + step * 10 + index * 5
            rows.append(
                {
                    "symbol": symbol, "trade_date": day, "open": close - 3, "high": close + 5,
                    "low": close - 6, "close": close, "pre_close": close - 4,
                    "volume": 1e6, "amount": 1e9, "adj_factor": 1.0,
                }
            )
    store.upsert_daily(rows, "test")

    def table(name, columns, key, values):
        store.ensure_dataset_table(name, columns, key)
        store.write_dataset(name, list(columns), key, values)

    table("daily_basic",
          {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE", "pb": "DOUBLE",
           "ps_ttm": "DOUBLE", "dv_ttm": "DOUBLE", "total_mv": "DOUBLE",
           "circ_mv": "DOUBLE", "turnover_rate": "DOUBLE", "volume_ratio": "DOUBLE"},
          ("symbol", "trade_date"),
          [{"symbol": "600519", "trade_date": days[-1], "pe_ttm": 25.0, "pb": 8.0,
            "ps_ttm": 10.0, "dv_ttm": 1.5, "total_mv": 1.8e12, "circ_mv": 1.8e12,
            "turnover_rate": 0.30, "volume_ratio": 1.1}])
    table("screen_snapshot",
          {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE",
           "a_vstd_20": "DOUBLE", "fd_pead": "DOUBLE"},
          ("symbol", "trade_date"),
          [{"symbol": "600519", "trade_date": days[-1], "pe_ttm": 25.0,
            "a_vstd_20": 0.35, "fd_pead": 1.2}])
    table("fina_indicator_vip",
          {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE", "roe": "DOUBLE",
           "netprofit_margin": "DOUBLE", "grossprofit_margin": "DOUBLE",
           "debt_to_assets": "DOUBLE", "or_yoy": "DOUBLE", "netprofit_yoy": "DOUBLE"},
          ("ts_code", "ann_date", "end_date"),
          [{"ts_code": "600519.SH", "ann_date": days[-1], "end_date": days[-1],
            "roe": 30.0, "netprofit_margin": 50.0, "grossprofit_margin": 90.0,
            "debt_to_assets": 20.0, "or_yoy": 9.0, "netprofit_yoy": 12.0}])
    table("index_daily",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE",
           "pct_chg": "DOUBLE", "amount": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": "000300.SH", "trade_date": days[-1], "close": 4000.0,
            "pct_chg": 0.8, "amount": 3e11}])
    table("limit_list_d",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "limit": "VARCHAR"},
          ("trade_date", "ts_code"),
          [{"trade_date": days[-1], "ts_code": "600519.SH", "limit": "U"},
           {"trade_date": days[-1], "ts_code": "000001.SZ", "limit": "D"}])
    table("moneyflow",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "net_mf_amount": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": "600519.SH", "trade_date": days[-1], "net_mf_amount": 1234.5}])
    table("sw_daily",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "name": "VARCHAR",
           "pct_change": "DOUBLE", "amount": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": f"8010{index:02d}.SI", "trade_date": days[-1], "name": name,
            "pct_change": pct, "amount": 1e10}
           for index, (name, pct) in enumerate(
               [("农林牧渔", 2.5), ("电子", 1.8), ("计算机", 1.2), ("电力设备", 0.6),
                ("食品饮料", 0.2), ("医药生物", -0.5), ("煤炭", -1.2), ("银行", -2.0)])])
    table("ths_hot",
          {"trade_date": "DATE", "data_type": "VARCHAR", "ts_code": "VARCHAR",
           "ts_name": "VARCHAR", "rank": "INTEGER", "pct_change": "DOUBLE",
           "hot": "DOUBLE"},
          ("trade_date", "data_type", "ts_code"),
          [{"trade_date": days[-1], "data_type": "hot", "ts_code": "600519.SH",
            "ts_name": "贵州茅台", "rank": 1, "pct_change": 1.5, "hot": 98888.0}])
    return days


def test_overview_shape(panel):
    payload = overview.overview()
    assert payload["as_of"] == panel[-1].isoformat()
    assert payload["indices"][0]["label"] == "沪深300"
    assert payload["breadth"]["up"] == 2
    assert payload["limit"] == {"up": 1, "down": 1, "date": panel[-1]}
    assert payload["money"]["net_mf_amount"] == pytest.approx(1234.5)
    assert payload["sectors"]["top"][0]["name"] == "农林牧渔"
    assert payload["sectors"]["bottom"][0]["name"] == "银行"
    assert payload["hot"][0]["name"] == "贵州茅台"


def test_detail_composes_everything(panel, monkeypatch):
    monkeypatch.setattr(detail_module.intraday, "latest_or_cached", lambda symbol: None)
    payload = detail_module.detail("600519")
    assert payload["info"]["name"] == "贵州茅台"
    assert payload["quote"]["close"] == pytest.approx(1440.0)
    assert payload["quote"]["pct_chg"] == pytest.approx((1440 / 1436 - 1) * 100, abs=1e-6)
    assert len(payload["chart"]) == 5
    assert payload["chart"][0]["date"] < payload["chart"][-1]["date"]
    assert payload["factors"]["a_vstd_20"] == pytest.approx(0.35)
    assert payload["financial"]["roe"] == pytest.approx(30.0)
    assert payload["watching"] is False
    assert payload["runs"] == []


def test_detail_unknown_symbol(panel):
    assert detail_module.detail("999999") is None


def test_intraday_parse_and_cache(tmp_path, monkeypatch):
    payload = {
        "data": {
            "code": "600519",
            "name": "贵州茅台",
            "preClose": 1439.0,
            "trends": [
                "2026-11-24 09:31,1439.00,1440.00,1441.00,1438.00,100,144000,1439.50",
                "2026-11-24 09:32,1440.00,1442.00,1443.00,1439.00,200,288400,1440.20",
            ],
        }
    }
    parsed = intraday._parse(payload)
    assert parsed["trade_date"] == "2026-11-24"
    assert [point["time"] for point in parsed["points"]] == ["09:31", "09:32"]
    assert parsed["points"][1]["price"] == pytest.approx(1442.0)
    assert parsed["pre_close"] == pytest.approx(1439.0)


def test_detail_http_contract(panel, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(detail_module.intraday, "latest_or_cached", lambda symbol: None)
    client = TestClient(app)
    response = client.get("/api/stocks/600519/detail")
    assert response.status_code == 200
    body = response.json()
    assert {"info", "quote", "chart", "factors", "financial", "intraday", "runs", "watching"} <= set(body)
    assert client.get("/api/stocks/999999/detail").status_code == 404
    market = client.get("/api/market/overview").json()
    assert market["as_of"] == panel[-1].isoformat()
