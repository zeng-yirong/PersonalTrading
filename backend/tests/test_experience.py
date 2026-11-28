"""P8-0 经验记忆：抽取、去重、as-of、影子召回不注入。"""

from __future__ import annotations

import copy
import json

import pytest

from app.agents import experience
from app.config import settings
from app.db import connection, init_db


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


def _context(
    day: str,
    *,
    industry: str = "白酒",
    index_20d: float = -5.0,
    rsi: float = 55.0,
    gap: float = -1.0,
) -> dict:
    return {
        "as_of": day,
        "profile": {
            "symbol": "600519",
            "industry": industry,
            "board": "主板",
            "asset_type": "stock",
        },
        "market": {
            "benchmarks": [
                {"name": "沪深300", "ts_code": "000300.SH", "return_20d": index_20d}
            ]
        },
        "technical": {"rsi14": rsi, "close_vs_ma20_pct": gap},
        "price": {"drawdown_from_high": -8.0, "return_20d": -2.0},
        "regime": {"breadth": {"limit_up": 30, "limit_down": 10}},
    }


def _seed(
    conn,
    run_id: int,
    symbol: str,
    day: str,
    *,
    position: float,
    ret20: float,
    outcome: str,
    ctx: dict,
    d20_date: str = "2026-12-18",
) -> None:
    conn.execute(
        "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, as_of, "
        "context_json, verdict) VALUES(?, ?, ?, 'ok', 'fake', 1, ?, ?, ?, ?)",
        (
            run_id,
            symbol,
            symbol,
            f"{day}T18:00:00+08:00",
            day,
            json.dumps(ctx, ensure_ascii=False),
            json.dumps(
                {"decision": "批准", "final_position_pct": position}, ensure_ascii=False
            ),
        ),
    )
    conn.execute(
        "INSERT INTO agent_reviews(run_id, symbol, name, action, position_pct, conviction, "
        "entry_date, entry_price, d20_date, d20_return, d20_excess, outcome, updated_at) "
        "VALUES(?, ?, ?, '批准', ?, 60, ?, 10.0, ?, ?, ?, ?, ?)",
        (
            run_id,
            symbol,
            symbol,
            position,
            day,
            d20_date,
            ret20,
            ret20 - 1,
            outcome,
            "2026-12-18T18:00:00+08:00",
        ),
    )


def test_extract_merges_same_pattern_and_is_idempotent(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
        _seed(conn, 2, "000001", "2026-11-21", position=25, ret20=-3, outcome="看多/错误", ctx=_context("2026-11-21"))
        _seed(conn, 3, "000858", "2026-11-22", position=40, ret20=-6, outcome="看多/错误", ctx=_context("2026-11-22"))

    result = experience.extract()
    assert result == {"created": 1, "merged": 2, "skipped": 0}

    cards = experience.list_experiences()
    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "candidate"
    assert card["scope"] == "market"
    assert card["evidence_count"] == 3
    assert card["confidence"] == 70
    assert card["as_of"] == "2026-12-18"

    again = experience.extract()
    assert again == {"created": 0, "merged": 0, "skipped": 3}
    assert experience.list_experiences()[0]["evidence_count"] == 3


def test_recall_is_as_of_bounded_and_candidates_are_not_used(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
    experience.extract()

    features = experience.features_from_context(_context("2026-12-20"))
    assert features["index_20d"] == -5.0

    # 候选卡永不进入正常召回
    assert experience.recall(features, as_of="2026-12-20") == []
    assert len(experience.recall(features, as_of="2026-12-20", include_candidate=True)) == 1
    # 经验在 12-18 才学到，12-17 不可见
    assert experience.recall(features, as_of="2026-12-17", include_candidate=True) == []


def test_shadow_recall_logs_without_touching_context(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
    experience.extract()
    with connection() as conn:
        conn.execute("UPDATE experiences SET status = 'active'")

    ctx = _context("2026-12-20")
    before = copy.deepcopy(ctx)
    assert experience.shadow_recall(99, ctx) == 1
    assert ctx == before

    rows = experience.recalls(run_id=99)
    assert len(rows) == 1
    assert rows[0]["injected"] == 0
    assert rows[0]["status"] == "active"

    # 重复影子召回不产生重复行
    experience.shadow_recall(99, ctx)
    assert len(experience.recalls(run_id=99)) == 1


def test_strong_tape_and_overbought_patterns(app_db):
    with connection() as conn:
        _seed(
            conn,
            1,
            "600519",
            "2026-11-20",
            position=0,
            ret20=4,
            outcome="空仓/错误",
            ctx=_context("2026-11-20", index_20d=5.0),
        )
        _seed(
            conn,
            2,
            "000001",
            "2026-11-21",
            position=20,
            ret20=-4,
            outcome="看多/错误",
            ctx=_context("2026-11-21", index_20d=0.0, rsi=70.0, gap=4.0),
        )
    result = experience.extract()
    assert result["created"] == 2
    scopes = {(item["scope"], item["scope_key"]) for item in experience.list_experiences()}
    assert scopes == {("market", "strong_tape_miss"), ("decision", "chase_overbought")}


def test_match_requires_every_feature(app_db):
    assert experience.match({"index_20d_lte": -3}, {"index_20d": -5})
    assert not experience.match({"index_20d_lte": -3}, {})
    assert not experience.match({"rsi14_gte": 65, "close_vs_ma20_gte": 3}, {"rsi14": 70})
    assert experience.match(
        {"rsi14_gte": 65, "close_vs_ma20_gte": 3}, {"rsi14": 70, "close_vs_ma20": 4}
    )


def test_status_gate_requires_evidence_and_reason(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
        _seed(conn, 2, "000001", "2026-11-21", position=25, ret20=-3, outcome="看多/错误", ctx=_context("2026-11-21"))
    experience.extract()
    card = experience.list_experiences()[0]

    with pytest.raises(ValueError, match="证据不足"):
        experience.change_status(card["id"], "active", "人工确认")

    with connection() as conn:
        _seed(conn, 3, "000858", "2026-11-22", position=40, ret20=-6, outcome="看多/错误", ctx=_context("2026-11-22"))
    experience.extract()
    card = experience.list_experiences()[0]
    assert card["evidence_count"] == 3 and card["can_activate"] is True

    with pytest.raises(ValueError, match="必须写理由"):
        experience.change_status(card["id"], "active", "")

    active = experience.change_status(card["id"], "active", "3 条独立复盘")
    assert active is not None and active["status"] == "active"

    features = experience.features_from_context(_context("2026-12-20"))
    assert len(experience.recall(features, as_of="2026-12-20")) == 1

    with pytest.raises(ValueError, match="必须写理由"):
        experience.change_status(card["id"], "retired", "")
    retired = experience.change_status(card["id"], "retired", "后续两次反证")
    assert retired is not None and retired["status"] == "retired"
    assert experience.recall(features, as_of="2026-12-20") == []


def test_status_transition_whitelist(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
    experience.extract()
    card = experience.list_experiences()[0]

    with pytest.raises(ValueError, match="未知状态"):
        experience.change_status(card["id"], "bogus", "x")
    assert experience.change_status(card["id"], "candidate", "noop") is not None

    with connection() as conn:
        conn.execute("UPDATE experiences SET status = 'retired'")
    with pytest.raises(ValueError, match="不允许"):
        experience.change_status(card["id"], "active", "想复活但证据不够")


def test_details_include_evidence(app_db):
    with connection() as conn:
        _seed(conn, 1, "600519", "2026-11-20", position=30, ret20=-5, outcome="看多/错误", ctx=_context("2026-11-20"))
    experience.extract()
    card = experience.list_experiences()[0]

    detail = experience.details(card["id"])
    assert detail is not None
    assert detail["lesson"] == card["lesson"]
    assert detail["evidence"][0]["ref_id"] == 1
    assert detail["evidence"][0]["kind"] == "review"


def test_render_labels_caps_and_keeps_conflicts(app_db):
    cards = [
        {
            "id": index,
            "as_of": "2026-12-18",
            "evidence_count": 3,
            "lesson": "逆风期应降仓" if index == 1 else "逆风期可加仓",
        }
        for index in (1, 2)
    ]
    block = experience.render(cards, max_chars=300)
    assert block.startswith("【经验（不是事实）】")
    assert "逆风期应降仓" in block and "逆风期可加仓" in block
    assert len(block) <= 300

    many = [
        {"id": i, "as_of": "2026-12-18", "evidence_count": 3, "lesson": "教训" * 60}
        for i in range(8)
    ]
    capped = experience.render(many, max_chars=200)
    assert len(capped) <= 200
    assert "【经验（不是事实）】" in capped


def test_shadow_precision_and_graduation(app_db):
    with connection() as conn:
        for run_id, day in ((1, "2026-11-20"), (2, "2026-11-21"), (3, "2026-11-22")):
            _seed(
                conn,
                run_id,
                f"60000{run_id}",
                day,
                position=30,
                ret20=-5,
                outcome="看多/错误",
                ctx=_context(day),
            )
    experience.extract()
    card = experience.list_experiences()[0]
    experience.change_status(card["id"], "active", "3 条独立复盘")

    for run_id in (1, 2, 3):
        experience.shadow_recall(run_id, _context("2026-12-20"))

    payload = experience.evaluation()
    assert payload["recalls"] == 3
    assert payload["judged"] == 3
    assert payload["hits"] == 3
    assert payload["precision_pct"] == 100.0
    assert payload["coverage_pct"] == 100.0

    retired = experience.graduate(card["id"], "guard", "连续 5 次验证有效")
    assert retired is not None
    assert retired["status"] == "retired"
    assert retired["graduated_to"] == "guard"

    with connection() as conn:
        _seed(
            conn,
            4,
            "600519",
            "2026-11-23",
            position=0,
            ret20=4,
            outcome="空仓/错误",
            ctx=_context("2026-11-23", index_20d=5.0),
        )
    experience.extract()
    candidate = next(item for item in experience.list_experiences() if item["id"] != card["id"])
    with pytest.raises(ValueError, match="只有 active"):
        experience.graduate(candidate["id"], "guard", "x")
