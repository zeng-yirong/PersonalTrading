"""Tests for the market / sector / event / attention context builders.

These cover the failure modes found against real data: a half-published margin
file, a float `rank`, and a JSON-encoded `concept` column.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.agents import context_events, context_market
from app.data import store


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()
    # Tables the agent layer reads but that the core schema does not create.
    store.ensure_dataset_table(
        "index_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.ensure_dataset_table(
        "margin_detail",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "rzye": "DOUBLE"},
        ("trade_date", "ts_code"),
    )
    store.ensure_dataset_table(
        "sw_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "name": "VARCHAR", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.ensure_dataset_table(
        "ths_hot",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "data_type": "VARCHAR",
         "rank": "DOUBLE", "hot": "DOUBLE", "concept": "VARCHAR"},
        ("trade_date", "ts_code", "data_type"),
    )
    store.ensure_dataset_table(
        "dc_hot",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "data_type": "VARCHAR",
         "rank": "DOUBLE", "hot": "DOUBLE", "concept": "VARCHAR"},
        ("trade_date", "ts_code", "data_type"),
    )
    store.ensure_dataset_table(
        "limit_cpt_list",
        {"trade_date": "DATE", "ts_code": "VARCHAR", "name": "VARCHAR",
         "up_nums": "DOUBLE", "cons_nums": "VARCHAR", "pct_chg": "DOUBLE", "rank": "DOUBLE"},
        ("trade_date", "ts_code"),
    )
    store.ensure_dataset_table(
        "stk_holdernumber",
        {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE", "holder_num": "DOUBLE"},
        ("ts_code", "end_date"),
    )
    store.ensure_dataset_table(
        "forecast_vip",
        {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE", "type": "VARCHAR",
         "p_change_min": "DOUBLE", "p_change_max": "DOUBLE"},
        ("ts_code", "end_date", "ann_date"),
    )
    store.ensure_dataset_table(
        "dividend",
        {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE", "div_proc": "VARCHAR",
         "cash_div_tax": "DOUBLE", "stk_div": "DOUBLE", "ex_date": "DATE"},
        ("ts_code", "ann_date", "end_date"),
    )
    store.ensure_dataset_table(
        "share_float",
        # Upstream keeps this as 'YYYYMMDD' text; mirror that here.
        {"ts_code": "VARCHAR", "float_date": "VARCHAR", "float_share": "DOUBLE"},
        ("ts_code", "float_date"),
    )
    store.ensure_dataset_table(
        "stk_holdertrade",
        {"ts_code": "VARCHAR", "ann_date": "DATE", "in_de": "VARCHAR",
         "holder_name": "VARCHAR", "change_vol": "DOUBLE"},
        ("ts_code", "ann_date", "holder_name", "in_de", "change_vol"),
    )
    store.ensure_dataset_table(
        "block_trade",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "amount": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.ensure_dataset_table(
        "stk_surv",
        {"ts_code": "VARCHAR", "surv_date": "DATE", "rece_org": "VARCHAR"},
        ("ts_code", "surv_date"),
    )
    return store


def test_latest_complete_date_skips_a_half_published_day(panel):
    """The exact bug: Tushare's newest margin file has ~45% of the rows."""
    rows = []
    for day in (date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)):
        for index in range(10):
            rows.append({"trade_date": day, "ts_code": f"00000{index}.SZ", "rzye": 100.0})
    # Newest date is only partially published.
    rows.append({"trade_date": date(2026, 11, 20), "ts_code": "000001.SZ", "rzye": 100.0})
    store.write_dataset(
        "margin_detail", ["trade_date", "ts_code", "rzye"], ("trade_date", "ts_code"), rows
    )

    assert context_market._latest_complete_date("margin_detail") == date(2026, 11, 19)


def test_market_regime_reads_the_complete_margin_day(panel):
    rows = []
    for day in (date(2026, 10, 20), date(2026, 11, 19)):
        for index in range(10):
            rows.append({"trade_date": day, "ts_code": f"00000{index}.SZ", "rzye": 1000.0})
    store.write_dataset(
        "margin_detail", ["trade_date", "ts_code", "rzye"], ("trade_date", "ts_code"), rows
    )
    store.write_dataset(
        "margin_detail",
        ["trade_date", "ts_code", "rzye"],
        ("trade_date", "ts_code"),
        [{"trade_date": date(2026, 11, 20), "ts_code": "000001.SZ", "rzye": 10.0}],
    )
    store.upsert_daily(
        [{"symbol": "000001", "trade_date": "2026-11-19", "close": 10.0, "amount": 1e8}],
        "test",
    )

    regime = context_market.build_market_regime()
    assert regime["margin"] is not None
    # 10 rows of 1000, not the partial 10 on the 20th.
    assert regime["margin"]["balance"] == pytest.approx(10000.0)
    assert regime["margin"]["as_of"] == "2026-11-19"


def test_attention_parses_rank_and_concept(panel):
    store.write_dataset(
        "ths_hot",
        ["trade_date", "ts_code", "data_type", "rank", "hot", "concept"],
        ("trade_date", "ts_code", "data_type"),
        [
            {
                "trade_date": date(2026, 11, 20),
                "ts_code": "600519.SH",
                "data_type": "A股",
                "rank": 74.0,
                "hot": 96571.0,
                "concept": '["超级品牌", "白酒概念"]',
            }
        ],
    )
    store.write_dataset(
        "stk_holdernumber",
        ["ts_code", "end_date", "holder_num"],
        ("ts_code", "end_date"),
        [
            {"ts_code": "600519.SH", "end_date": date(2026, 9, 30), "holder_num": 278364.0},
            {"ts_code": "600519.SH", "end_date": date(2026, 6, 30), "holder_num": 296400.0},
        ],
    )

    attention = context_events.build_attention("600519.SH", "600519")
    text = context_events.render_attention(attention)
    assert "第 74 名" in text and "74.0" not in text
    assert "超级品牌、白酒概念" in text
    assert attention["holder_number"]["change_pct"] == pytest.approx(-6.085, abs=0.01)


def test_events_render_reads_each_table(panel):
    store.write_dataset(
        "forecast_vip",
        ["ts_code", "ann_date", "end_date", "type", "p_change_min", "p_change_max"],
        ("ts_code", "end_date", "ann_date"),
        [{"ts_code": "600519.SH", "ann_date": date(2025, 1, 3), "end_date": date(2024, 12, 31),
          "type": "略增", "p_change_min": 14.67, "p_change_max": 14.67}],
    )
    store.write_dataset(
        "dividend",
        ["ts_code", "ann_date", "end_date", "div_proc", "cash_div_tax", "stk_div", "ex_date"],
        ("ts_code", "ann_date", "end_date"),
        [{"ts_code": "600519.SH", "ann_date": date(2026, 6, 1), "end_date": date(2025, 12, 31),
          "div_proc": "实施", "cash_div_tax": 20.0, "stk_div": 0.0,
          "ex_date": date(2026, 6, 20)}],
    )
    store.write_dataset(
        "block_trade",
        ["ts_code", "trade_date", "amount"],
        ("ts_code", "trade_date"),
        [{"ts_code": "600519.SH", "trade_date": date(2026, 10, 10), "amount": 5e7}],
    )
    store.write_dataset(
        "share_float",
        ["ts_code", "float_date", "float_share"],
        ("ts_code", "float_date"),
        [{"ts_code": "600519.SH", "float_date": "20261010", "float_share": 1.2e8}],
    )
    store.write_dataset(
        "stk_holdertrade",
        ["ts_code", "ann_date", "in_de", "holder_name", "change_vol"],
        ("ts_code", "ann_date", "holder_name", "in_de", "change_vol"),
        [
            {"ts_code": "600519.SH", "ann_date": date(2026, 11, 12), "in_de": "DE",
             "holder_name": "机构专用", "change_vol": 16900.0},
            {"ts_code": "600519.SH", "ann_date": date(2026, 10, 12), "in_de": "IN",
             "holder_name": "高管", "change_vol": 1000.0},
        ],
    )

    events = context_events.build_events("600519.SH")
    text = context_events.render_events(events)
    assert "略增" in text
    assert "每股派现" in text
    assert "大宗交易" in text
    assert events["insider"]["count"] == 2
    assert events["insider"]["sell_count"] == 1
    assert events["insider"]["buy_count"] == 1
    # 1.2e8 shares unlocked inside the 90-day window, parsed from 'YYYYMMDD' text.
    assert events["lockup_90d"]["count"] == 1
    assert events["lockup_90d"]["shares"] == pytest.approx(1.2e8)


def test_sector_peer_stats(panel):
    store.upsert_symbols(
        [
            {"symbol": "600519", "name": "贵州茅台", "industry": "白酒"},
            {"symbol": "000858", "name": "五粮液", "industry": "白酒"},
        ]
    )
    store.upsert_daily(
        [
            {"symbol": "600519", "trade_date": "2026-11-20", "close": 1500.0, "pre_close": 1490.0},
            {"symbol": "000858", "trade_date": "2026-11-20", "close": 100.0, "pre_close": 101.0},
        ],
        "test",
    )
    store.upsert_daily_basic(
        [
            {"symbol": "600519", "trade_date": "2026-11-20", "pe_ttm": 22.0, "pb": 6.8,
             "total_mv": 1.8e12},
            {"symbol": "000858", "trade_date": "2026-11-20", "pe_ttm": 15.0, "pb": 3.0,
             "total_mv": 6e11},
        ],
        "test",
    )
    store.write_dataset(
        "index_daily",
        ["ts_code", "trade_date", "close"],
        ("ts_code", "trade_date"),
        [{"ts_code": "000300.SH", "trade_date": date(2026, 11, 20), "close": 3950.0}],
    )

    sector = context_market.build_sector("白酒", "600519")
    peer = sector["peer"]
    assert peer["count"] == 2
    assert peer["median_pe_ttm"] == pytest.approx(18.5)
    assert peer["up_ratio"] == pytest.approx(0.5)
    text = context_market.render_sector(sector)
    assert "同业对比·白酒" in text


def test_sector_without_industry_degrades(panel):
    sector = context_market.build_sector(None, "600519")
    assert sector["peer"] is None
    assert "行业分类缺失" in context_market.render_sector(sector)
