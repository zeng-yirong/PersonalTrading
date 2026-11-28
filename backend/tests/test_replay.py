"""配对评测的纯函数与状态重建（不调用模型）。"""

from __future__ import annotations

import json

import pytest

from app.agents import replay
from app.config import settings
from app.db import connection, init_db


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


def test_correct_rule_handles_long_and_flat():
    assert replay._correct(30, 2.0) is True
    assert replay._correct(30, -2.0) is False
    assert replay._correct(0, -2.0) is True       # 空仓躲过下跌也算对
    assert replay._correct(0, 2.0) is False
    assert replay._correct(None, 2.0) is None


def test_pair_summary_reports_on_minus_off():
    rows = [
        {
            "on": {"confidence": 80, "position": 50, "correct": True},
            "off": {"confidence": 50, "position": 0, "correct": False},
            "excess": 2.0,
        },
        {
            "on": {"confidence": 40, "position": 0, "correct": False},
            "off": {"confidence": 60, "position": 30, "correct": True},
            "excess": -1.0,
        },
    ]
    summary = replay.pair_summary(rows)
    assert summary["n"] == 2
    assert summary["on_correct"] == 1 and summary["off_correct"] == 1
    assert summary["brier"]["mean"] == pytest.approx(-0.105, abs=1e-6)
    assert summary["excess"]["mean"] == pytest.approx(0.65, abs=1e-6)


def test_bootstrap_interval_is_deterministic():
    first = replay._interval([1.0, 2.0, 3.0], seed=7)
    second = replay._interval([1.0, 2.0, 3.0], seed=7)
    assert first == second
    assert first["low"] <= first["mean"] <= first["high"]
    assert replay._interval([]) == {"mean": None, "low": None, "high": None, "n": 0}


def test_load_state_rebuilds_from_stored_messages(app_db):
    context = {"as_of": "2026-11-26", "price": {"last_close": 10}}
    with connection() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, "
            "as_of, context_json) VALUES(1, '600519', '贵州茅台', 'ok', 'fake', 1, "
            "'2026-11-26T18:00:00+08:00', '2026-11-26', ?)",
            (json.dumps(context, ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO agent_messages(run_id, node, label, role, round, status, payload_json, "
            "created_at) VALUES(1, 'analyst_market', '市场', 'market', 1, 'ok', ?, "
            "'2026-11-26T18:01:00+08:00')",
            (json.dumps({"stance": "看多", "score": 30}, ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO agent_messages(run_id, node, label, role, round, status, payload_json, "
            "created_at) VALUES(1, 'research_manager', '研究主管', NULL, NULL, 'ok', ?, "
            "'2026-11-26T18:02:00+08:00')",
            (json.dumps({"verdict": "偏多"}, ensure_ascii=False),),
        )

    state = replay.load_state(1)
    assert state is not None
    assert state["context"]["as_of"] == "2026-11-26"
    assert state["analyses"]["market"]["stance"] == "看多"
    assert state["consensus"]["verdict"] == "偏多"

    with connection() as conn:
        conn.execute("DELETE FROM agent_messages WHERE run_id = 1")
    assert replay.load_state(1) is None
