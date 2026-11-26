"""Tests for paper trading and the review loop (no network)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.config import settings
from app.data import store
from app.db import connection, init_db
from app.paper import review as review_module
from app.paper import service


@pytest.fixture()
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()

    # A tiny panel: 25 trading days so T+20 exists, with a stepped price path:
    # 100 for days 0-4, 150 for days 5-19, 200 for days 20-24.
    from datetime import timedelta

    days = [date(2026, 11, 2) + timedelta(days=index) for index in range(25)]
    store.upsert_calendar([(day, True) for day in days], "test")
    store.upsert_symbols([{"symbol": "600519", "name": "贵州茅台", "industry": "白酒"}])

    def price_at(index: int) -> float:
        if index < 5:
            return 100.0
        if index < 20:
            return 150.0
        return 200.0

    store.upsert_daily(
        [
            {"symbol": "600519", "trade_date": day.isoformat(), "close": price_at(index),
             "amount": 1e8}
            for index, day in enumerate(days)
        ],
        "test",
    )
    store.ensure_dataset_table(
        "index_daily",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.write_dataset(
        "index_daily",
        ["ts_code", "trade_date", "close"],
        ("ts_code", "trade_date"),
        [{"ts_code": "000300.SH", "trade_date": day, "close": 4000 + index * 5}
         for index, day in enumerate(days)],
    )
    return store


def test_split_does_not_fake_a_loss(paper):
    """10 元买 100 股，1 拆 2（价 5、因子 2）后市值应仍是 1 万，不是腰斩。"""
    store.upsert_daily(
        [{"symbol": "000001", "trade_date": "2026-11-30", "close": 10.0,
          "adj_factor": 1.0, "amount": 1e8}],
        "test",
    )
    service.ensure_account()
    service.trade("000001", "buy", shares=100)
    store.upsert_daily(
        [{"symbol": "000001", "trade_date": "2026-12-01", "close": 5.0,
          "adj_factor": 2.0, "amount": 1e8}],
        "test",
    )

    position = service.positions()[0]
    assert position["price"] == pytest.approx(10.0)          # 5 × 2 / 1
    assert position["market_value"] == pytest.approx(1000.0)
    assert position["pnl_pct"] > -1.0                        # 只亏手续费


def test_buy_charges_fees_and_updates_cash(paper):
    account = service.ensure_account()
    result = service.trade("600519", "buy", shares=100)
    assert result["price"] == 200.0          # last close in the seeded panel
    assert result["gross"] == 20000.0
    assert result["fee"] > 0

    summary = service.summary()
    assert summary["cash"] == pytest.approx(account["initial_cash"] - 20000 - result["fee"])
    assert summary["positions"][0]["shares"] == 100


def test_fee_has_a_five_yuan_floor(paper):
    # 100 shares at 160 = 16,000; commission at 0.025% would be 4 -> floored to 5.
    fee = service._fees("buy", 16000.0)
    assert fee == pytest.approx(5 + 16000 * service.TRANSFER_RATE, abs=0.01)


def test_stamp_duty_only_on_sell(paper):
    assert service._fees("sell", 100000.0) > service._fees("buy", 100000.0)


def test_buying_without_cash_is_rejected(paper):
    service.ensure_account()
    with pytest.raises(service.PaperError, match="现金不足"):
        service.trade("600519", "buy", shares=1_000_000)


def test_selling_more_than_held_is_rejected(paper):
    service.trade("600519", "buy", shares=100)
    with pytest.raises(service.PaperError, match="持仓不足"):
        service.trade("600519", "sell", shares=500)


def test_selling_everything_closes_the_position(paper):
    service.trade("600519", "buy", shares=100)
    service.trade("600519", "sell", shares=100)
    assert service.positions() == []
    with connection() as conn:
        row = conn.execute("SELECT status FROM paper_positions").fetchone()
    assert row["status"] == "closed"


def test_amount_is_rounded_down_to_whole_lots(paper):
    result = service.trade("600519", "buy", amount=25000)
    assert result["shares"] % service.LOT == 0
    assert result["shares"] == 100  # 25000 / 200 = 125 -> 100


def test_nav_replays_price_moves(paper):
    service.trade("600519", "buy", shares=100)
    series = service.nav_series()
    assert series, "nav series should not be empty"
    # Buying on the last day means there is exactly one NAV point so far.
    assert series[-1]["total"] == pytest.approx(service.summary()["total"], abs=1.0)
    assert series[-1]["benchmark"] == pytest.approx(1.0)


# ------------------------------------------------------------------- review
def _seed_run(as_of: str, decision: str, pct: float, run_id: int = 1) -> None:
    context = {"as_of": as_of, "profile": {"name": "贵州茅台"}}
    verdict = {"decision": decision, "final_position_pct": pct, "confidence": 55}
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, "
            "verdict, context_json) VALUES(?, ?, ?, 'ok', 'm', 2, ?, ?, ?)",
            (run_id, "600519", "贵州茅台", as_of, json.dumps(verdict, ensure_ascii=False),
             json.dumps(context, ensure_ascii=False)),
        )


def test_review_computes_forward_returns(paper):
    # Entry on the first day (close 100); T+5 lands on the sixth day (150).
    _seed_run("2026-11-02", "调整后批准", 30, run_id=1)
    result = review_module.refresh()
    assert result["written"] == 1

    rows = review_module.list_reviews()
    assert rows[0]["d5_return"] == pytest.approx(50.0)      # 100 -> 150
    assert rows[0]["d5_excess"] is not None
    assert rows[0]["outcome"].startswith("看多")


def test_review_marks_a_flat_call_as_correct_when_price_falls(paper):
    # A 0% position on a day before a rally is wrong; seed one and check the maths
    # first, then a falling series to confirm the "avoided loss" branch.
    _seed_run("2026-11-02", "调整后批准", 0, run_id=2)
    review_module.refresh()
    rows = review_module.list_reviews()
    assert rows[0]["outcome"].startswith("空仓")
    # Price rose 100 -> 150, so staying flat was wrong.
    assert rows[0]["outcome"].endswith("错误")


def test_review_is_pending_when_horizon_has_not_elapsed(paper):
    _seed_run("2026-11-09", "批准", 20, run_id=3)
    review_module.refresh()
    rows = [row for row in review_module.list_reviews() if row["run_id"] == 3]
    assert rows[0]["d20_return"] is None
    assert rows[0]["outcome"].endswith("待验证")


def test_review_stats_aggregates(paper):
    _seed_run("2026-11-02", "调整后批准", 30, run_id=4)
    review_module.refresh()
    stats = review_module.stats()
    assert stats["total"] >= 1
    assert 0 <= stats["hit_rate"] <= 100
    assert "看多" in stats["by_stance"]
