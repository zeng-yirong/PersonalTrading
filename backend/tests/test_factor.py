"""Tests for factor mining: a planted factor must be found, noise must not.

The synthetic panel gives every symbol a constant PE and a daily return that is
proportional to that PE, so the true 20-day forward return is a strictly increasing
function of PE and the rank correlation must be +1. A second factor (RSI) is a
deterministic avalanche hash, i.e. pure noise, and must stay near zero.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.data import store
from app.db import init_db
from app.factor import evaluate, library, service
from app.factor.evaluate import FactorError, Spec


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()

    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(420)]
    store.upsert_calendar([(day, True) for day in days], "test")
    symbols = [
        {"symbol": f"6000{index:02d}", "ts_code": f"6000{index:02d}.SH",
         "name": f"测试{index}", "industry": "测试行业", "asset_type": "stock"}
        for index in range(40)
    ]
    store.upsert_symbols(symbols)

    daily = []
    basics = []
    factors = []
    for index, symbol in enumerate(symbols):
        growth = 1 + 0.0005 * index
        for step, day in enumerate(days):
            price = round(100 * growth**step, 4)
            daily.append(
                {"symbol": symbol["symbol"], "trade_date": day, "open": price,
                 "high": price, "low": price, "close": price, "amount": 1e8,
                 "volume": 1e6 * (1 + 0.01 * ((step * 7 + index * 3) % 13)),
                 "adj_factor": 1.0}
            )
            basics.append(
                {"symbol": symbol["symbol"], "trade_date": day,
                 "pe_ttm": float(index + 1), "pb": 1.0,
                 # 市值与 index 不单调：给残差检验留出"因子不是市值代理"的空间
                 "total_mv": 1e10 * (1 + ((index * 37) % 7) * 0.1),
                 "turnover_rate": 2.0}
            )
            factors.append(
                {"ts_code": symbol["ts_code"], "trade_date": day,
                 # 异或散列：与 index/step 都不单调，才是真噪音
                 "rsi_12": float(((index * 73856093) ^ (step * 19349663)) % 10000 / 100),
                 "macd": 0.0,
                 "boll_upper": price * 1.1, "boll_lower": price * 0.9,
                 "kdj_k": 50.0, "kdj_d": 45.0, "kdj_j": 60.0, "cci": 10.0}
            )
    store.upsert_daily(daily, "test")

    def table(name, columns, key, values):
        store.ensure_dataset_table(name, columns, key)
        store.write_dataset(name, list(columns), key, values)

    table("daily_basic",
          {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE", "pb": "DOUBLE",
           "total_mv": "DOUBLE", "turnover_rate": "DOUBLE"},
          ("symbol", "trade_date"), basics)
    table("stk_factor",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "rsi_12": "DOUBLE",
           "macd": "DOUBLE", "boll_upper": "DOUBLE", "boll_lower": "DOUBLE",
           "kdj_k": "DOUBLE", "kdj_d": "DOUBLE", "kdj_j": "DOUBLE", "cci": "DOUBLE"},
          ("ts_code", "trade_date"), factors)
    table("moneyflow",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "net_mf_amount": "DOUBLE",
           "buy_elg_amount": "DOUBLE", "sell_elg_amount": "DOUBLE"},
          ("ts_code", "trade_date"), [])
    table("margin_detail",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "rzye": "DOUBLE"},
          ("ts_code", "trade_date"), [])
    table("cyq_perf",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "winner_rate": "DOUBLE"},
          ("ts_code", "trade_date"), [])
    table("limit_list_d",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "limit": "VARCHAR"},
          ("trade_date", "ts_code"), [])
    table("top_list", {"trade_date": "DATE", "ts_code": "VARCHAR"},
          ("trade_date", "ts_code"), [])
    table("hsgt_top10", {"trade_date": "DATE", "ts_code": "VARCHAR"},
          ("trade_date", "ts_code"), [])
    table("ths_hot",
          {"trade_date": "DATE", "ts_code": "VARCHAR", "rank": "BIGINT"},
          ("trade_date", "ts_code"), [])
    table("fina_indicator_vip",
          {"ts_code": "VARCHAR", "ann_date": "DATE", "end_date": "DATE",
           "roe": "DOUBLE", "grossprofit_margin": "DOUBLE",
           "netprofit_margin": "DOUBLE", "debt_to_assets": "DOUBLE",
           "or_yoy": "DOUBLE", "netprofit_yoy": "DOUBLE"},
          ("ts_code", "ann_date", "end_date"), [])
    table("index_daily",
          {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
          ("ts_code", "trade_date"),
          [{"ts_code": "000300.SH", "trade_date": day, "close": 4000 + step * 3}
           for step, day in enumerate(days)])
    return days


def _spec(days, factors):
    return Spec(start=days[0], end=days[-1], rebalance=20, horizon=20, factors=factors)


def test_planted_factor_is_detected(panel):
    result = evaluate.run(_spec(panel, [library.BY_NAME["pe_ttm"]]))
    factor = result["factors"][0]
    assert factor["name"] == "pe_ttm"
    assert factor["ic_mean"] == pytest.approx(1.0, abs=0.02)
    assert factor["positive_ratio"] == 100.0
    assert factor["n_dates"] >= 12
    # 分层必须单调：PE 越高，未来收益越高（这是构造出来的）
    means = [group["mean_pct"] for group in factor["quantiles"]]
    assert means == sorted(means)


def test_noise_factor_stays_near_zero(panel):
    result = evaluate.run(_spec(panel, [library.BY_NAME["rsi12"]]))
    factor = result["factors"][0]
    assert abs(factor["ic_mean"]) < 0.2


def test_spread_matches_top_minus_bottom(panel):
    result = evaluate.run(_spec(panel, [library.BY_NAME["pe_ttm"]]))
    factor = result["factors"][0]
    expected = factor["quantiles"][-1]["mean_pct"] - factor["quantiles"][0]["mean_pct"]
    assert factor["spread_pct"] == pytest.approx(expected, abs=0.01)


def test_library_rejects_unknown_factor(panel):
    with pytest.raises(ValueError, match="未知因子"):
        library.validate(["roe", "magic"])


def test_service_validates_horizon_and_span(panel):
    with pytest.raises(FactorError, match="前瞻期"):
        service.run({"horizon": 15})
    # 只给 200 天：不足一年
    with pytest.raises(FactorError, match="至少需要一年"):
        service.run({"start": panel[0].isoformat(), "end": panel[200].isoformat()})


def test_service_persists_and_lists(panel):
    first = service.run({
        "start": panel[0].isoformat(),
        "end": panel[-1].isoformat(),
        "rebalance": 20,
        "horizon": 20,
        "factors": ["pe_ttm"],
    })
    assert first["id"] >= 1
    assert first["factors"][0]["ic_mean"] == pytest.approx(1.0, abs=0.02)

    listed = service.history(5)
    assert listed[0]["id"] == first["id"]
    assert listed[0]["top_factor"]["name"] == "pe_ttm"

    detail = service.detail(first["id"])
    assert detail["factors"][0]["name"] == "pe_ttm"
    assert detail["config"]["horizon"] == 20


def test_oos_split_passes_the_planted_factor(panel):
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20,
        factors=[library.BY_NAME["pe_ttm"]], oos_months=6,
    )
    result = evaluate.run(spec)
    assert result["lookback"]["end"] == result["out_of_sample_period"]["start"]
    assert result["oos_sections"] >= 5
    factor = result["factors"][0]
    assert factor["passed"] is True
    assert factor["out_of_sample"]["ic_mean"] == pytest.approx(1.0, abs=0.05)
    assert "同号" in factor["gate_reason"]


def test_oos_split_rejects_the_noise_factor(panel):
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20,
        factors=[library.BY_NAME["rsi12"]], oos_months=6,
    )
    factor = evaluate.run(spec)["factors"][0]
    assert factor["passed"] is False


def test_service_rejects_a_short_oos_window(panel):
    with pytest.raises(FactorError, match="样本外"):
        service.run({"oos_months": 2})


def test_alpha158_factor_is_computed_and_ranked(panel):
    """The imported formulas are evaluated on the same cross-section."""
    external = library.registry()["a_roc_20"]
    assert external.source == "alpha158"
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20, factors=[external]
    )
    result = evaluate.run(spec)
    factor = result["factors"][0]
    assert factor["source"] == "alpha158"
    assert factor["coverage"] > 0.9
    # 价格路径是 (1+0.0005·i)^t，20 日涨幅随 i 单调 → 与 PE 的秩相关应接近 1
    assert factor["ic_mean"] > 0.8


def test_incremental_block_has_expected_shape(panel):
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20,
        factors=[library.BY_NAME["pe_ttm"]], incremental=True,
    )
    factor = evaluate.run(spec)["factors"][0]
    incremental = factor["incremental"]
    assert set(incremental) >= {"n_dates", "ic_mean", "icir", "passed", "reason"}
    assert incremental["n_dates"] >= 8
    assert isinstance(incremental["passed"], bool)


def test_residual_ic_strips_a_perfect_size_proxy():
    rng = np.random.default_rng(0)
    size = rng.normal(size=200)
    block = pd.DataFrame({
        "v": size,                 # 因子就是市值本身
        "fwd": size,               # 收益也由市值决定
        "size": size,
        "turnover_rate": rng.normal(size=200),
        "ret20": rng.normal(size=200),
        "industry": "A",
    })
    assert abs(evaluate._residual_ic(block)) < 0.05


def test_residual_ic_finds_an_independent_signal():
    rng = np.random.default_rng(1)
    v = rng.normal(size=200)
    block = pd.DataFrame({
        "v": v,
        "fwd": v + 0.1 * rng.normal(size=200),
        "size": rng.normal(size=200),
        "turnover_rate": rng.normal(size=200),
        "ret20": rng.normal(size=200),
        "industry": "A",
    })
    assert evaluate._residual_ic(block) > 0.5


def test_incremental_ic_rejects_a_size_proxy(panel):
    # 把市值改成与 index 严格单调：PE 就完全变成市值代理，残差不应再有信息
    store.ensure_dataset_table(
        "daily_basic",
        {"symbol": "VARCHAR", "trade_date": "DATE", "pe_ttm": "DOUBLE", "pb": "DOUBLE",
         "total_mv": "DOUBLE", "turnover_rate": "DOUBLE"},
        ("symbol", "trade_date"),
    )
    rows = []
    for index in range(40):
        symbol = f"6000{index:02d}"
        for day in panel:
            rows.append({"symbol": symbol, "trade_date": day, "total_mv": 1e10 * (index + 1),
                         "pe_ttm": float(index + 1), "pb": 1.0, "turnover_rate": 2.0})
    store.write_dataset(
        "daily_basic", ["symbol", "trade_date", "total_mv", "pe_ttm", "pb", "turnover_rate"],
        ("symbol", "trade_date"), rows,
    )
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20,
        factors=[library.BY_NAME["pe_ttm"]], incremental=True,
    )
    factor = evaluate.run(spec)["factors"][0]
    assert factor["incremental"]["passed"] is False


def test_promoted_factor_reads_from_the_panel(panel):
    """The 7 promoted factors live in bt_feat, not in a separate temp table."""
    factor = library.registry()["a_vstd_20"]
    assert factor.source == "panel"
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20, factors=[factor]
    )
    result = evaluate.run(spec)["factors"][0]
    assert result["source"] == "panel"
    assert result["coverage"] > 0.9
    assert result["ic_mean"] is not None


def test_academic_factor_is_computed(panel):
    factor = library.registry()["ac_max_20"]
    assert factor.source == "academic"
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20, factors=[factor]
    )
    result = evaluate.run(spec)["factors"][0]
    assert result["coverage"] > 0.9
    # 单日最大涨幅随 index 单调 → 与 PE 的秩相关应接近 1
    assert result["ic_mean"] > 0.5


def test_gtja_factor_is_computed(panel):
    factor = library.registry()["gtja14"]   # CLOSE - DELAY(CLOSE,5)
    assert factor.source == "gtja"
    spec = evaluate.Spec(
        start=panel[0], end=panel[-1], rebalance=20, horizon=20, factors=[factor]
    )
    result = evaluate.run(spec)["factors"][0]
    assert result["coverage"] > 0.9
    # 价格路径随 index 单调，5 日价差也随 index 单调
    assert result["ic_mean"] > 0.5


def test_http_contract(panel):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    library_payload = client.get("/api/factors/library").json()
    assert len(library_payload["factors"]) >= 30
    assert library_payload["defaults"]["horizon"] == 20

    response = client.post(
        "/api/factors/run",
        json={
            "start": panel[0].isoformat(),
            "end": panel[-1].isoformat(),
            "rebalance": 20,
            "horizon": 20,
            "factors": ["pe_ttm"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert {"id", "config", "factors", "sections"} <= set(body)
    assert set(body["factors"][0]) >= {
        "name", "ic_mean", "icir", "t_stat", "coverage",
        "quantiles", "spread_pct", "ic_series",
    }
    assert client.get(f"/api/factors/runs/{body['id']}").status_code == 200
    assert client.get("/api/factors/runs/99999").status_code == 404
    assert client.post("/api/factors/run", json={"horizon": 15}).status_code == 400
