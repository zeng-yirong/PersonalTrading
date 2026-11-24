"""Tests for the screener: validation, SQL generation, and one end-to-end query."""

from __future__ import annotations

import pytest

from app.config import settings
from app.data import store
from app.screener import query as screen_query
from app.screener.snapshot import COLUMNS, SNAPSHOT_NAME


# --------------------------------------------------------------- validation
def test_from_dict_parses_universe_and_filters():
    parsed = screen_query.ScreenQuery.from_dict(
        {
            "universe": {"asset_type": "stock", "boards": ["创业板"], "industries": ["白酒"]},
            "filters": [{"field": "pe_ttm", "op": "<=", "value": 30}],
            "sort": [{"field": "roe", "desc": True}],
            "limit": 10,
        }
    )
    assert parsed.asset_type == "stock"
    assert parsed.boards == ["创业板"]
    assert parsed.filters[0].field == "pe_ttm"
    assert parsed.limit == 10


def test_limit_is_clamped():
    assert screen_query.ScreenQuery.from_dict({"limit": 9999}).limit == screen_query.MAX_LIMIT
    assert screen_query.ScreenQuery.from_dict({"limit": 0}).limit == 1


def test_unknown_field_is_rejected():
    with pytest.raises(screen_query.QueryError, match="未知字段"):
        screen_query.ScreenQuery.from_dict(
            {"filters": [{"field": "pe_ratio", "op": "<", "value": 1}]}
        ).where()


def test_text_operator_on_numeric_field_is_rejected():
    query = screen_query.ScreenQuery.from_dict(
        {"filters": [{"field": "pe_ttm", "op": "contains", "value": "1"}]}
    )
    with pytest.raises(screen_query.QueryError, match="不支持 contains"):
        query.where()


def test_numeric_operator_on_text_field_is_rejected():
    query = screen_query.ScreenQuery.from_dict(
        {"filters": [{"field": "industry", "op": ">", "value": 1}]}
    )
    with pytest.raises(screen_query.QueryError, match="文本字段"):
        query.where()


def test_between_requires_two_values():
    query = screen_query.ScreenQuery.from_dict(
        {"filters": [{"field": "pe_ttm", "op": "between", "value": [10]}]}
    )
    with pytest.raises(screen_query.QueryError, match="between"):
        query.where()


def test_where_builds_exclude_st_and_boards():
    query = screen_query.ScreenQuery.from_dict(
        {
            "universe": {"boards": ["创业板", "科创板"]},
            "filters": [{"field": "roe", "op": ">=", "value": 15}],
        }
    )
    sql, params = query.where()
    assert "NOT LIKE '%ST%'" in sql
    assert "board IN (?, ?)" in sql
    # asset_type is bound first, then the boards, then filter values.
    assert params[:3] == ["stock", "创业板", "科创板"]
    assert params[-1] == 15


def test_in_operator_expands_placeholders():
    query = screen_query.ScreenQuery.from_dict(
        {"filters": [{"field": "industry", "op": "in", "value": ["白酒", "银行"]}]}
    )
    sql, params = query.where()
    assert "IN (?, ?)" in sql
    assert params[-2:] == ["白酒", "银行"]


def test_order_by_rejects_unknown_sort_field():
    query = screen_query.ScreenQuery.from_dict({"sort": [{"field": "nope", "desc": True}]})
    with pytest.raises(screen_query.QueryError):
        query.order_by()


def test_field_catalog_exposes_operators():
    payload = screen_query.field_catalog()
    names = {item["name"] for item in payload}
    assert {"pe_ttm", "roe", "limit_up_5d", "mf_net_20d"} <= names
    ops = screen_query.describe_ops()
    assert "between" in ops["numeric"] and "contains" in ops["text"]


# ------------------------------------------------------------ end to end
@pytest.fixture()
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    store.init_schema()

    types = {name: ("VARCHAR" if name in {"symbol", "ts_code", "name", "industry", "board", "asset_type"} else "DOUBLE")
             for name in COLUMNS}
    types["trade_date"] = "DATE"
    store.ensure_dataset_table(SNAPSHOT_NAME, types, ("trade_date", "symbol"))

    from datetime import date

    rows = [
        {"trade_date": date(2024, 1, 2), "symbol": "600519", "ts_code": "600519.SH",
         "name": "贵州茅台", "industry": "白酒", "board": "沪市主板", "asset_type": "stock",
         "close": 1500.0, "pe_ttm": 22.0, "roe": 25.0, "mf_net_20d": 1.5e6, "limit_up_5d": 0.0,
         "total_mv": 1.8e12},
        {"trade_date": date(2024, 1, 2), "symbol": "000001", "ts_code": "000001.SZ",
         "name": "平安银行", "industry": "银行", "board": "深市主板", "asset_type": "stock",
         "close": 12.0, "pe_ttm": 5.0, "roe": 7.0, "mf_net_20d": -2.0e4, "limit_up_5d": 1.0,
         "total_mv": 2.3e11},
        {"trade_date": date(2024, 1, 2), "symbol": "300750", "ts_code": "300750.SZ",
         "name": "ST测试", "industry": "电池", "board": "创业板", "asset_type": "stock",
         "close": 200.0, "pe_ttm": 18.0, "roe": 18.0, "mf_net_20d": 3.0e5, "limit_up_5d": 0.0,
         "total_mv": 8.0e11},
    ]
    store.write_dataset(SNAPSHOT_NAME, list(COLUMNS), ("trade_date", "symbol"), rows)
    store.ensure_dataset_table("screen_meta", {"trade_date": "DATE"}, ("trade_date",))
    store.write_dataset("screen_meta", ["trade_date"], ("trade_date",), [{"trade_date": date(2024, 1, 2)}])

    from app.screener import snapshot as snapshot_module

    monkeypatch.setattr(
        snapshot_module,
        "ensure_built",
        lambda: {"trade_date": "2024-01-02", "symbols": 3, "cached": True},
    )
    return store


def test_run_filters_and_excludes_st(snapshot):
    base = {
        "filters": [
            {"field": "pe_ttm", "op": "<=", "value": 30},
            {"field": "mf_net_20d", "op": ">", "value": 0},
            {"field": "limit_up_5d", "op": "==", "value": 0},
        ],
        "sort": [{"field": "roe", "desc": True}],
        "limit": 10,
    }

    # 平安银行 fails (negative flow, recent limit-up); 300750's name contains ST.
    result = screen_query.run(screen_query.ScreenQuery.from_dict(base))
    assert result["total_matched"] == 1
    assert [row["symbol"] for row in result["rows"]] == ["600519"]
    assert result["as_of"] == "2024-01-02"
    assert "pe_ttm" in result["columns"]

    # Turning off the ST filter lets 300750 through, confirming why it was dropped.
    with_st = screen_query.ScreenQuery.from_dict(
        {**base, "universe": {"exclude_st": False}}
    )
    result = screen_query.run(with_st)
    assert result["total_matched"] == 2
    assert [row["symbol"] for row in result["rows"]] == ["600519", "300750"]


def test_run_returns_empty_when_nothing_matches(snapshot):
    query = screen_query.ScreenQuery.from_dict(
        {"filters": [{"field": "pe_ttm", "op": "<", "value": 1}]}
    )
    result = screen_query.run(query)
    assert result["total_matched"] == 0
    assert result["rows"] == []

def test_from_dict_rejects_bad_shapes():
    """LLM 偶尔会给出 limit:"30 条"、universe:"all" 这类值，应该是 400 而不是 500。"""
    with pytest.raises(screen_query.QueryError):
        screen_query.ScreenQuery.from_dict({"limit": "30 条"})
    with pytest.raises(screen_query.QueryError):
        screen_query.ScreenQuery.from_dict({"universe": "all"})
    with pytest.raises(screen_query.QueryError):
        screen_query.ScreenQuery.from_dict(
            {"filters": [{"field": "pe_ttm", "op": "<", "value": 1}] * 13}
        )
