"""Tests for the operator views: ledger, usage, data health and the fingerprint."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.config import settings
from app.data import fingerprint, store
from app.db import connection, init_db
from app.ops import health, ledger
from app.ops import review as review_job


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()

    days = [date(2026, 1, 2) + timedelta(days=index) for index in range(10)]
    store.upsert_calendar([(day, True) for day in days], "test")
    store.upsert_symbols([
        {"symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台", "asset_type": "stock"},
        {"symbol": "000001", "ts_code": "000001.SZ", "name": "平安银行", "asset_type": "stock"},
    ])
    store.upsert_daily(
        [
            {"symbol": symbol, "trade_date": day, "close": 10.0 + index, "amount": 1e8}
            for symbol in ("600519", "000001")
            for index, day in enumerate(days)
        ],
        "test",
    )
    # 最后一天只有 1 只两融标的：残缺日
    store.ensure_dataset_table(
        "margin_detail",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "rzye": "DOUBLE"},
        ("ts_code", "trade_date"),
    )
    store.write_dataset(
        "margin_detail",
        ["ts_code", "trade_date", "rzye"],
        ("ts_code", "trade_date"),
        [{"ts_code": ts_code, "trade_date": day, "rzye": 1e9}
         for day in days[:-1] for ts_code in ("600519.SH", "000001.SZ")]
        + [{"ts_code": "600519.SH", "trade_date": days[-1], "rzye": 1e9}],
    )
    return days


# ------------------------------------------------------------------ fingerprint
def test_fingerprint_is_stable_and_short(panel):
    first = fingerprint.current(force=True)
    assert len(first["digest"]) == 12
    assert first["as_of"] == panel[-1].isoformat()
    assert first["bars"] == 20
    second = fingerprint.current(force=True)
    assert second["digest"] == first["digest"]


# ------------------------------------------------------------------- data health
def test_health_flags_a_partial_day(panel):
    payload = health.snapshot()
    assert payload["as_of"] == panel[-1].isoformat()
    assert payload["ok"] is False
    assert any("两融" in issue for issue in payload["issues"])
    assert payload["partial_days"][0]["date"] == panel[-1].isoformat()
    daily = next(item for item in payload["tables"] if item["table"] == "daily")
    assert daily["status"] == "ok" and daily["lag_days"] == 0
    assert "digest" in payload["fingerprint"]


def test_health_flags_a_stale_heat_table(panel):
    """A table that silently stops updating must surface as an issue.

    `dc_hot` sat at 2026-11-20 for four trading days while the sentiment agent
    kept reading it, and health only watched 13 tables so nobody noticed.
    """
    store.ensure_dataset_table(
        "dc_hot",
        {"ts_code": "VARCHAR", "trade_date": "DATE", "data_type": "VARCHAR"},
        ("ts_code", "trade_date", "data_type"),
    )
    store.write_dataset(
        "dc_hot",
        ["ts_code", "trade_date", "data_type"],
        ("ts_code", "trade_date", "data_type"),
        [{"ts_code": "600519.SH", "trade_date": panel[0], "data_type": "hot"}],
    )
    payload = health.snapshot()
    row = next(item for item in payload["tables"] if item["table"] == "dc_hot")
    assert row["status"] == "stale"
    assert any("东财热榜" in issue for issue in payload["issues"])


def test_health_reports_how_far_the_calendar_reaches(panel):
    """日历耗尽会让 T+20 复盘集体卡住，所以它必须出现在健康检查里。"""
    payload = health.snapshot()
    assert payload["calendar"]["last"] == panel[-1].isoformat()
    assert payload["calendar"]["open_days_left"] == 0
    assert any("交易日历" in issue for issue in payload["issues"])


# ------------------------------------------------------------------ repair
def test_repair_rejects_unknown_or_code_mode_datasets(panel):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.post("/api/data/repair?dataset=nope").status_code == 400
    assert client.post("/api/data/repair?dataset=sw_daily").status_code == 400   # 按代码拉取


def test_repair_runs_the_backfill_for_recent_days(panel, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import ops as ops_router

    seen: dict = {}

    def fake_run(names, years=None, workers=4, force=False, max_parts=None, progress=None, dates=None):
        seen.update({"names": names, "dates": dates})
        return [{"dataset": names[0], "parts": len(dates or []), "rows": 42, "status": "ok"}]

    monkeypatch.setattr(ops_router, "run_backfill", fake_run)
    today = date.today()
    store.upsert_calendar([(today - timedelta(days=offset), True) for offset in range(10)], "test")
    client = TestClient(app)
    body = client.post("/api/data/repair?dataset=margin_detail&days=3").json()

    assert seen["names"] == ["margin_detail"]
    assert len(seen["dates"]) == 3
    assert body["rows"] == 42 and body["parts"] == 3 and body["label"] == "融资融券"


# ----------------------------------------------------------------------- ledger
def _seed_records() -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, "
            "prompt_tokens, completion_tokens, calls, verdict) "
            "VALUES(1, '600519', '贵州茅台', 'ok', 'm', 2, ?, 1000, 500, 12, ?)",
            ("2026-11-20T10:00:00+08:00",
             json.dumps({"decision": "批准", "final_position_pct": 20}, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO agent_reviews(run_id, symbol, name, outcome, d20_return, d20_excess, "
            "updated_at) VALUES(1, '600519', '贵州茅台', '看多/正确', 5.0, 3.2, ?)",
            ("2026-11-23T10:00:00+08:00",),
        )
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "prompt_tokens, completion_tokens, created_at) "
            "VALUES('白酒', '白酒缩圈', '行业', '中性', 58, 'm', '2026-11-23', 5000, 3000, ?)",
            ("2026-11-23T11:00:00+08:00",),
        )
        conn.execute(
            "INSERT INTO backtest_runs(title, spec_json, metrics_json, nav_json, periods_json, "
            "created_at) VALUES('低估值', ?, ?, '[]', '[]', ?)",
            (json.dumps({"end": "2026-11-21"}), json.dumps({"total_return_pct": -3.2,
                                                           "excess_cagr_pct": 1.5,
                                                           "max_drawdown_pct": -12.0,
                                                           "periods": 12}),
             "2026-11-23T12:00:00+08:00"),
        )
        conn.execute(
            "INSERT INTO paper_positions(id, symbol, name, shares, avg_cost, opened_at, "
            "status) VALUES(1, '600519', '贵州茅台', 100, 1500, '2026-11-20', 'open')"
        )
        conn.execute(
            "INSERT INTO paper_trades(position_id, symbol, side, shares, price, fee, amount, "
            "trade_date, run_id, created_at) VALUES(1, '600519', 'buy', 100, 1500, 5, 150000, "
            "'2026-11-20', 1, '2026-11-20T10:05:00+08:00')"
        )


def test_ledger_unifies_all_four_sources(panel):
    _seed_records()
    items = ledger.entries(20)
    kinds = {item["kind"] for item in items}
    assert kinds == {"推演", "主题", "回测", "成交"}
    run = next(item for item in items if item["kind"] == "推演")
    assert run["stance"] == "批准" and run["score"] == 20
    assert run["review"] == "看多/正确"
    assert run["tokens"] == 1500
    backtest = next(item for item in items if item["kind"] == "回测")
    assert "超额年化 +1.50%" in backtest["stance"]

    only_topics = ledger.entries(20, "主题")
    assert len(only_topics) == 1 and only_topics[0]["kind"] == "主题"


def test_ledger_summary_and_usage(panel):
    _seed_records()
    summary = ledger.summary()
    assert summary["counts"] == {"runs": 1, "topics": 1, "backtests": 1, "trades": 1}
    assert summary["reviews"]["hit_rate_pct"] == 100.0
    assert summary["tokens"]["total"] == 1500 + 8000

    usage = ledger.usage(30)
    assert usage["total_tokens"] == 9500
    assert usage["series"][0]["date"] == "2026-11-23"


def test_review_job_writes_and_counts_pending(panel):
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, "
            "context_json, verdict) VALUES(2, '600519', '贵州茅台', 'ok', 'm', 1, ?, ?, ?)",
            (
                "2026-01-02T10:00:00+08:00",
                json.dumps({"as_of": "2026-01-02"}),
                json.dumps({"decision": "批准", "final_position_pct": 20}),
            ),
        )
    result = review_job.run()
    assert result["refreshed"]["written"] == 1
    assert result["reviews"] == 1
    assert result["pending"] == 1          # 面板只有 10 个交易日，T+20 还没到
    assert result["judged"] == 0
    assert result["hit_rate_pct"] is None
    assert result["last_refreshed_at"]
    # 幂等：再跑一次不会多出记录
    again = review_job.run()
    assert again["reviews"] == 1


def test_review_http_endpoints(panel):
    from fastapi.testclient import TestClient

    from app.main import app

    _seed_records()
    client = TestClient(app)
    status = client.get("/api/ledger/review").json()
    assert status["reviews"] == 1
    assert status["judged"] == 1
    refreshed = client.post("/api/ledger/review/refresh").json()
    assert "refreshed" in refreshed


def test_chain_endpoint_links_derived_records(panel):
    from fastapi.testclient import TestClient

    from app.main import app

    _seed_records()
    with connection() as conn:
        conn.execute(
            "INSERT INTO backtest_runs(title, spec_json, metrics_json, nav_json, periods_json, "
            "source_run_id, created_at) VALUES('链测试', '{}', ?, '[]', '[]', 1, ?)",
            (
                json.dumps({"total_return_pct": 1.5, "excess_cagr_pct": 2.5,
                            "max_drawdown_pct": -3.0, "periods": 12}),
                "2026-11-23T10:00:00+08:00",
            ),
        )
    client = TestClient(app)
    payload = client.get("/api/ledger/chain/1").json()
    assert payload["run"]["id"] == 1
    assert payload["backtests"][0]["title"] == "链测试"
    assert payload["backtests"][0]["excess_cagr_pct"] == 2.5
    assert payload["review"]["outcome"] == "看多/正确"
    assert client.get("/api/ledger/chain/999").status_code == 404


def test_migration_is_idempotent(panel):
    from app.db import init_db

    init_db()
    init_db()
    with connection() as conn:
        for table in ("backtest_runs", "topic_reports"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "source_run_id" in columns


def test_http_contract(panel):
    from fastapi.testclient import TestClient

    from app.main import app

    _seed_records()
    client = TestClient(app)
    ledger_payload = client.get("/api/ledger?limit=10").json()
    assert ledger_payload["count"] == 4
    assert client.get("/api/ledger/summary").json()["counts"]["runs"] == 1
    assert client.get("/api/usage?days=7").json()["total_tokens"] == 9500
    health_payload = client.get("/api/data/health").json()
    assert health_payload["as_of"] == panel[-1].isoformat()
    fingerprint_payload = client.get("/api/data/fingerprint").json()
    assert len(fingerprint_payload["digest"]) == 12
