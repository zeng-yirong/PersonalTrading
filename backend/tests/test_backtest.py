"""Tests for the backtest: spec validation, as-of features, fills and the run loop.

The engine tests build their own dictionaries, so they say exactly what the rules
are (next-open fill, limit-up skip, limit-down deferral, fees) without a database.
The panel fixture is only used where the SQL matters.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.backtest import engine, features, service
from app.backtest.spec import BacktestError, Spec
from app.config import settings
from app.data import store
from app.db import init_db
from app.screener import query as screen_query


# ------------------------------------------------------------------- fixtures
def _spec(**overrides) -> Spec:
    query = screen_query.ScreenQuery.from_dict(
        overrides.pop("query", {"filters": [{"field": "pe_ttm", "op": "<=", "value": 100}]})
    )
    return Spec(
        query=query,
        start=date(2026, 1, 5),
        end=date(2026, 2, 5),
        rebalance=overrides.pop("rebalance", 5),
        top_n=overrides.pop("top_n", 1),
        initial_cash=overrides.pop("initial_cash", 100_000.0),
        slippage_bps=overrides.pop("slippage_bps", 0.0),
    )


def _bars(price_map: dict[str, list[float]], days: list[date], adj: float = 1.0):
    output: dict[str, dict[date, dict[str, float]]] = {}
    for symbol, prices in price_map.items():
        output[symbol] = {
            day: {"open": price, "close": price, "adj": adj}
            for day, price in zip(days, prices)
        }
    return output


def _picks(day: date, symbol: str) -> dict[date, list[dict]]:
    return {
        day: [
            {
                "symbol": symbol,
                "ts_code": f"{symbol}.SH",
                "name": symbol,
                "close": 10.0,
            }
        ]
    }


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()

    days = [date(2026, 1, 2) + timedelta(days=index) for index in range(80)]
    store.upsert_calendar([(day, True) for day in days], "test")
    store.upsert_symbols([
        {"symbol": "600001", "ts_code": "600001.SH", "name": "甲公司",
         "industry": "白酒", "asset_type": "stock"},
        {"symbol": "600002", "ts_code": "600002.SH", "name": "乙公司",
         "industry": "银行", "asset_type": "stock"},
        {"symbol": "600003", "ts_code": "600003.SH", "name": "丙公司",
         "industry": "电池", "asset_type": "stock"},
    ])

    price_paths = {
        "600001": [10.0 * (1 + 0.01 * index) for index in range(len(days))],
        "600002": [20.0 + 0.5 * index for index in range(len(days))],
        "600003": [30.0 * (1 - 0.01 * index) for index in range(len(days))],
    }
    rows = []
    for symbol, prices in price_paths.items():
        for day, price in zip(days, prices):
            rows.append({"symbol": symbol, "trade_date": day, "open": price,
                         "high": price * 1.01, "low": price * 0.99, "close": price,
                         "amount": 1e8, "adj_factor": 1.0})
    store.upsert_daily(rows, "test")

    def table(name, columns, key, values):
        store.ensure_dataset_table(name, columns, key)
        store.write_dataset(name, list(columns), key, values)

    # rsi/macd 由 stk_factor 提供（按 ts_code 关联）
    table(
        "stk_factor",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "rsi_12": "DOUBLE",
         "macd": "DOUBLE", "boll_upper": "DOUBLE", "boll_lower": "DOUBLE",
         "kdj_k": "DOUBLE", "kdj_d": "DOUBLE", "kdj_j": "DOUBLE", "cci": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": f"{symbol}.SH", "trade_date": day, "rsi_12": 55.0, "macd": 0.2,
          "boll_upper": price * 1.1, "boll_lower": price * 0.9,
          "kdj_k": 50.0, "kdj_d": 45.0, "kdj_j": 60.0, "cci": 10.0}
         for symbol, prices in price_paths.items() for day, price in zip(days, prices)],
    )
    table(
        "daily_basic",
        {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE", "pb": "DOUBLE",
         "ps_ttm": "DOUBLE", "dv_ttm": "DOUBLE", "total_mv": "DOUBLE",
         "circ_mv": "DOUBLE", "turnover_rate": "DOUBLE", "volume_ratio": "DOUBLE"},
        ("symbol", "trade_date"),
        [{"symbol": symbol, "trade_date": day, "pe_ttm": 10.0 + index % 3, "pb": 2.0,
          "ps_ttm": 3.0, "dv_ttm": 1.5, "total_mv": 1e10, "circ_mv": 8e9,
          "turnover_rate": 2.0, "volume_ratio": 1.1}
         for symbol in price_paths for index, day in enumerate(days)],
    )
    table(
        "moneyflow",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "net_mf_amount": "DOUBLE",
         "buy_elg_amount": "DOUBLE", "sell_elg_amount": "DOUBLE"},
        ("ts_code", "trade_date"),
        [{"ts_code": f"{symbol}.SH", "trade_date": day, "net_mf_amount": 1000.0,
          "buy_elg_amount": 500.0, "sell_elg_amount": 100.0}
         for symbol in price_paths for day in days],
    )
    table("margin_detail",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "rzye": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": f"{symbol}.SH", "trade_date": day, "rzye": 1e6}
           for symbol in price_paths for day in days])
    table("cyq_perf",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "winner_rate": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": f"{symbol}.SH", "trade_date": day, "winner_rate": 0.5}
           for symbol in price_paths for day in days])
    table("limit_list_d",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "limit": "VARCHAR"},
          ("trade_date", "ts_code"),
          [{"trade_date": days[5], "ts_code": "600002.SH", "limit": "U"}])
    table("top_list", {"trade_date": "DATE", "ts_code": "VARCHAR"},
          ("trade_date", "ts_code"),
          [{"trade_date": days[6], "ts_code": "600001.SH"}])
    table("hsgt_top10", {"trade_date": "DATE", "ts_code": "VARCHAR"},
          ("trade_date", "ts_code"), [])
    table("ths_hot",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "rank": "BIGINT"},
          ("trade_date", "ts_code"),
          [{"trade_date": day, "ts_code": "600001.SH", "rank": 3} for day in days])
    # 财报公布日 = 第 20 个交易日：之前的回测日期必须看不到这条数据。
    table(
        "fina_indicator_vip",
        {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE", "roe": "DOUBLE",
         "grossprofit_margin": "DOUBLE", "netprofit_margin": "DOUBLE",
         "debt_to_assets": "DOUBLE", "or_yoy": "DOUBLE", "netprofit_yoy": "DOUBLE"},
        ("ts_code", "ann_date", "end_date"),
        [{"ts_code": f"{symbol}.SH", "ann_date": days[20], "end_date": days[19],
          "roe": 20.0, "grossprofit_margin": 40.0, "netprofit_margin": 25.0,
          "debt_to_assets": 30.0, "or_yoy": 12.0, "netprofit_yoy": 15.0}
         for symbol in price_paths],
    )
    table("index_daily",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": "000300.SH", "trade_date": day, "close": 4000 + index * 8}
           for index, day in enumerate(days)])
    table("stk_limit",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "up_limit": "DOUBLE",
           "down_limit": "DOUBLE"},
          ("trade_date", "ts_code"),
          [{"trade_date": day, "ts_code": f"{symbol}.SH",
            "up_limit": price * 1.1, "down_limit": price * 0.9}
           for symbol, prices in price_paths.items() for day, price in zip(days, prices)])
    return days


# ---------------------------------------------------------------------- spec
def test_spec_rejects_unknown_field():
    with pytest.raises(BacktestError, match="未知字段"):
        service.run({"query": {"filters": [{"field": "pe_ratio", "op": "<", "value": 1}]}},
                    persist=False)


def test_spec_rejects_unsupported_field():
    from app.backtest import spec as spec_module

    with pytest.raises(BacktestError, match="回测不支持字段 pe_pct_3y"):
        spec_module.from_dict(
            {"query": {"filters": [{"field": "pe_pct_3y", "op": "<", "value": 30}]}}
        )


def test_spec_rejects_inverted_dates():
    from app.backtest import spec as spec_module

    with pytest.raises(BacktestError, match="必须早于"):
        spec_module.from_dict({"start": "2026-03-01", "end": "2026-01-01"})


def test_spec_rejects_short_window():
    from app.backtest import spec as spec_module

    with pytest.raises(BacktestError, match="太短"):
        spec_module.from_dict({"start": "2026-01-01", "end": "2026-01-20"})


def test_spec_clamps_to_panel_and_defaults():
    from app.backtest import spec as spec_module

    spec, warnings = spec_module.from_dict(
        {"start": "2020-01-01", "end": "2026-03-20"},
        panel_start=date(2026, 1, 2),
        panel_end=date(2026, 3, 20),
    )
    assert spec.start == date(2026, 1, 2)
    assert any("最早" in item for item in warnings)
    assert spec.rebalance == spec_module.DEFAULT_REBALANCE
    assert spec.top_n == spec_module.DEFAULT_TOP_N


def test_spec_bounds_rebalance_and_positions():
    from app.backtest import spec as spec_module

    bounds = {"panel_start": date(2026, 1, 2), "panel_end": date(2026, 6, 30)}
    with pytest.raises(BacktestError, match="调仓周期"):
        spec_module.from_dict({"rebalance": 0}, **bounds)
    with pytest.raises(BacktestError, match="持仓数量"):
        spec_module.from_dict({"top_n": 99}, **bounds)
    with pytest.raises(BacktestError, match="滑点"):
        spec_module.from_dict({"slippage_bps": 500}, **bounds)


# ------------------------------------------------------------------ features
def test_features_respect_announcement_date(panel):
    days = panel
    with store.session() as conn:
        features.build(conn, [days[10], days[30]], days[-1])
        rows = conn.execute(
            "SELECT trade_date, roe FROM bt_feat WHERE symbol = '600001' ORDER BY 1"
        ).fetchall()
        adjusted = conn.execute(
            "SELECT ret5 FROM bt_feat WHERE symbol = '600001' AND trade_date = ?",
            [days[10]],
        ).fetchone()[0]
    assert rows[0][1] is None          # 公布日之前：看不到这条财报
    assert rows[1][1] == pytest.approx(20.0)
    # 5 日收益按 1%/天的路径：约 +5%
    assert adjusted == pytest.approx(4.7619, abs=0.05)


def test_features_returns_use_adjusted_prices(panel):
    days = panel
    # 在第 25 天附近做 2:1 拆股：价格减半、因子加倍，后复权收益应连续。
    with store.session() as conn:
        conn.execute(
            "UPDATE daily SET adj_factor = 2.0 WHERE symbol = '600002' AND trade_date >= ?",
            [days[25]],
        )
        conn.execute(
            "UPDATE daily SET close = close / 2, open = open / 2 "
            "WHERE symbol = '600002' AND trade_date >= ?",
            [days[25]],
        )
        features.build(conn, [days[10], days[20], days[29]], days[-1])
        rows = conn.execute(
            "SELECT trade_date, close, ret5 FROM bt_feat WHERE symbol = '600002' ORDER BY 1"
        ).fetchall()
    # 未复权价会显示 -46% 的假跌；后复权后 5 日收益仍是真实的 +7.8%
    assert rows[2][1] == pytest.approx((20.0 + 0.5 * 29) / 2, abs=0.01)
    assert rows[2][2] == pytest.approx(7.8125, abs=0.05)


# -------------------------------------------------------------------- engine
def test_engine_fills_at_next_open_and_charges_fees():
    days = [date(2026, 3, 2) + timedelta(days=index) for index in range(6)]
    spec = _spec()
    bars = _bars({"600001": [10.0] * 6}, days)
    result = engine.simulate(spec, days, _picks(days[0], "600001"), bars, {}, {}, [])

    period = result["periods"][0]
    assert period["entry_date"] == days[1].isoformat()
    assert period["holdings"][0]["entry_price"] == pytest.approx(10.0)
    assert period["holdings"][0]["return_pct"] < 0  # 只有费用，没有行情
    # 100 股一手：10 万现金 / 10 元 = 1 万股，再扣掉手续费那一点点
    assert result["nav"][-1]["nav"] < 1.0
    assert result["metrics"]["fees"] > 0


def test_engine_skips_a_limit_up_open():
    days = [date(2026, 3, 2) + timedelta(days=index) for index in range(6)]
    spec = _spec()
    bars = _bars({"600001": [10.0] * 6}, days)
    limits = {("600001.SH", days[1]): (10.0, 9.0)}
    result = engine.simulate(spec, days, _picks(days[0], "600001"), bars, limits, {}, [])
    assert result["periods"][0]["holdings"] == []
    assert "涨停" in result["periods"][0]["note"]


def test_engine_defers_a_limit_down_sell():
    days = [date(2026, 3, 2) + timedelta(days=index) for index in range(6)]
    spec = _spec(rebalance=2)
    bars = _bars({"600001": [10.0] * 6}, days)
    picks = {days[0]: [{"symbol": "600001", "ts_code": "600001.SH", "name": "甲", "close": 10.0}],
             days[2]: []}
    # 第 3 天跌停：卖不出去，只能顺延到第 4 天
    limits = {("600001.SH", days[3]): (11.0, 10.0)}
    result = engine.simulate(spec, days, picks, bars, limits, {}, [])
    holding = result["periods"][0]["holdings"][0]
    assert holding["exit_date"] == days[4].isoformat()


def test_schedule_drops_an_uninvestable_tail():
    days = [date(2026, 1, 2) + timedelta(days=index) for index in range(244)]
    signals = engine.schedule(days, 20)
    assert len(signals) == 12
    assert days.index(signals[-1]) <= len(days) - 4


# ------------------------------------------------------------------- service
def test_run_end_to_end_on_a_small_panel(panel):
    result = service.run(
        {
            "query": {"filters": [{"field": "pe_ttm", "op": "<=", "value": 100}],
                      "sort": [{"field": "ret20", "desc": True}]},
            "start": panel[0].isoformat(),
            "end": panel[-1].isoformat(),
            "rebalance": 5,
            "top_n": 2,
        },
        persist=False,
    )
    metrics = result["metrics"]
    assert metrics["periods"] >= 3
    assert len(result["nav"]) == len(panel)
    assert result["coverage"]["rows"] > 0
    assert result["spec"]["rebalance"] == 5
    assert {"total_return_pct", "excess_cagr_pct", "max_drawdown_pct"} <= set(metrics)
    # 净值曲线每个点都应能对上下一天的现金 + 市值
    assert all(point["nav"] > 0 for point in result["nav"])


def test_run_persists_and_lists(panel):
    payload = {
        "query": {"filters": [{"field": "pe_ttm", "op": "<=", "value": 100}]},
        "start": panel[0].isoformat(),
        "end": panel[-1].isoformat(),
        "rebalance": 10,
        "top_n": 1,
    }
    first = service.run(payload)
    assert first["id"] >= 1
    listed = service.history(5)
    assert listed[0]["id"] == first["id"]
    assert listed[0]["metrics"]["periods"] == first["metrics"]["periods"]
    detail = service.detail(first["id"])
    assert detail["periods"] == first["periods"]


def test_run_links_back_to_a_research_run(panel):
    from app.db import connection

    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at) "
            "VALUES(7, '600519', '贵州茅台', 'ok', 'm', 1, '2026-01-02T10:00:00+08:00')"
        )
    payload = {
        "query": {"filters": [{"field": "pe_ttm", "op": "<=", "value": 100}]},
        "start": panel[0].isoformat(),
        "end": panel[-1].isoformat(),
        "rebalance": 10,
        "top_n": 1,
        "source_run_id": 7,
    }
    result = service.run(payload)
    assert result["source_run_id"] == 7
    assert service.history(1)[0]["source_run_id"] == 7

    bad = dict(payload, source_run_id=999)
    with pytest.raises(BacktestError):
        service.run(bad, persist=False)


def test_http_contract(panel):
    """The response shape the UI is coded against; a rename here breaks the panel."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    meta = client.get("/api/backtest/meta").json()
    assert {"defaults", "limits", "panel", "fees", "rebalance_choices"} <= set(meta)
    assert meta["defaults"]["rebalance"] == 20

    response = client.post(
        "/api/backtest/run",
        json={
            "query": {"filters": [{"field": "pe_ttm", "op": "<=", "value": 100}]},
            "start": panel[0].isoformat(),
            "end": panel[-1].isoformat(),
            "rebalance": 5,
            "top_n": 2,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert {"id", "spec", "metrics", "nav", "periods", "trades", "warnings"} <= set(data)
    assert set(data["nav"][0]) == {"trade_date", "nav", "benchmark", "invested_ratio"}
    assert set(data["periods"][0]) >= {
        "index", "signal_date", "entry_date", "exit_date", "return_pct",
        "benchmark_pct", "excess_pct", "cash_weight", "holdings",
    }
    assert set(data["metrics"]) >= {
        "total_return_pct", "cagr_pct", "excess_cagr_pct", "max_drawdown_pct",
        "sharpe", "hit_rate_pct", "periods", "trades", "fees", "exposure_pct",
    }
    assert client.get(f"/api/backtest/runs/{data['id']}").status_code == 200
    assert client.get("/api/backtest/runs/999999").status_code == 404
    assert client.post(
        "/api/backtest/run",
        json={"query": {"filters": [{"field": "pe_pct_3y", "op": "<", "value": 30}]}},
    ).status_code == 400
