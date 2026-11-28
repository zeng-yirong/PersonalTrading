"""策略库：种子、状态机、成绩读取。"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db import init_db
from app.strategy import store


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


def test_presets_are_seeded_as_champions_once(app_db):
    champions = store.list_strategies("champion")
    assert len(champions) == 12
    assert {item["id"] for item in champions} >= {"low-valuation", "high-dividend"}
    assert store.seed_from_presets() == 0          # 幂等


def test_status_upgrade_downgrade_and_retire(app_db):
    store.clone("low-valuation", "我的低波")
    candidate = [item for item in store.list_strategies("candidate")][0]

    watch = store.set_status(candidate["id"], "watch", "回测通过")
    assert watch["status"] == "watch"

    champion = store.set_status(candidate["id"], "champion", "观察 8 周")
    assert champion["status"] == "champion" and champion["promoted_at"]

    back = store.set_status(candidate["id"], "watch", "降级观察")
    assert back["status"] == "watch"

    retired = store.set_status(candidate["id"], "retired", "连续 12 周跑输")
    assert retired["status"] == "retired"
    assert retired["retire_reason"] == "连续 12 周跑输" and retired["retired_at"]

    revived = store.set_status(candidate["id"], "watch", "恢复观察")
    assert revived["status"] == "watch"


def test_illegal_transitions_are_rejected(app_db):
    with pytest.raises(store.StrategyError):
        store.set_status("low-valuation", "candidate", "跳过观察期")
    with pytest.raises(store.StrategyError):
        store.set_status("low-valuation", "bogus")
    with pytest.raises(store.StrategyError):
        store.set_status("no-such-strategy", "watch")


def test_clone_copies_the_query_as_candidate(app_db):
    copy = store.clone("high-dividend")
    assert copy["status"] == "candidate"
    assert copy["query"] == store.get("high-dividend")["query"]
    assert copy["id"].startswith("cand-")
    assert "克隆自 high-dividend" in (copy["notes"] or "")


def test_with_metrics_reads_latest_windows_and_trend(app_db):
    for excess in (-2.0, 1.0, 3.5):
        store.save_run("low-valuation", "1y", {"excess_cagr_pct": excess, "periods": 12})
    store.save_run("low-valuation", "2y", {"excess_cagr_pct": 5.5, "periods": 24})

    item = store.with_metrics(store.get("low-valuation"))

    assert item["year1"]["excess_cagr_pct"] == 3.5        # 最新一批
    assert item["year2"]["excess_cagr_pct"] == 5.5
    assert item["trend"] == [-2.0, 1.0, 3.5]              # 旧 → 新
    assert item["last_checked"]

def test_api_lists_and_rejects_illegal_status(app_db):
    import asyncio

    from app.routers import strategy as router

    listing = asyncio.run(router.list_all(status=""))
    assert listing["count"] == 12
    # 同状态内按近一年超额排；没有成绩的排在后面
    assert all(item["status"] == "champion" for item in listing["strategies"])

    try:
        asyncio.run(router.change_status("low-valuation", router.StatusModel(status="bogus")))
    except Exception as exc:  # HTTPException
        assert getattr(exc, "status_code", None) == 400
    else:  # pragma: no cover
        raise AssertionError("非法状态应当被拒绝")

# ------------------------------------------------------------------ horizon
def test_horizon_params_and_gates():
    from app.strategy import horizon

    assert horizon.normalise("短线") == "short"
    assert horizon.normalise("watch") == "long"          # 认不出就按长线处理

    short = horizon.params_for("short")
    long_ = horizon.params_for("long")
    assert short["rebalance"] < long_["rebalance"]
    assert short["top_n"] < long_["top_n"]

    owned = horizon.params_for("short", {"rebalance": 2, "top_n": 3})
    assert owned == {"rebalance": 2, "top_n": 3}


def test_short_gate_rejects_a_losing_scalper():
    from app.strategy import horizon

    metrics = {
        "trades": 765,
        "hit_rate_pct": 42.5,
        "win_loss_ratio": 0.9,
        "fee_drag_pct": 8.2,
        "max_drawdown_pct": -77.9,
    }
    gate = horizon.gate_check("short", metrics)
    assert gate["passed"] is False
    assert any("胜率" in item for item in gate["failures"])
    assert any("盈亏比" in item for item in gate["failures"])


def test_long_gate_passes_a_steady_strategy():
    from app.strategy import horizon

    metrics = {
        "periods": 12,
        "excess_cagr_pct": 16.8,
        "max_drawdown_pct": -17.3,
        "hit_rate_pct": 58.0,
    }
    assert horizon.gate_check("long", metrics)["passed"] is True


def test_seed_keeps_horizon_from_presets(app_db):
    by_id = {item["id"]: item for item in store.list_strategies("")}
    assert by_id["limit-up-pullback"]["horizon"] == "short"
    assert by_id["high-dividend"]["horizon"] == "long"
