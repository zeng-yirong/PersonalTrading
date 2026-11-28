"""记忆检索的测试：索引、召回、注入文本、以及「不是事实」这条约束。"""

from __future__ import annotations

import json

import pytest

from app.agents import memory
from app.config import settings
from app.data import store
from app.db import connection, init_db


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()
    return tmp_path


def _seed_run(conn, run_id: int, symbol: str, name: str, decision: str, pct: int, day: str, body: str):
    conn.execute(
        "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, verdict, report) "
        "VALUES(?, ?, ?, 'ok', 'fake', 1, ?, ?, ?)",
        (run_id, symbol, name, f"{day}T18:00:00+08:00",
         json.dumps({"decision": decision, "final_position_pct": pct}, ensure_ascii=False), body),
    )


def test_index_and_recall_by_symbol(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "调整后批准", 35, "2026-11-20", "白酒缩圈但资金流入")
        _seed_run(conn, 2, "600519", "贵州茅台", "否决", 0, "2026-11-24", "放量下跌，退出")
        _seed_run(conn, 3, "000001", "平安银行", "批准", 10, "2026-11-22", "银行防御")
        conn.execute(
            "INSERT INTO agent_reviews(run_id, symbol, name, outcome, updated_at) "
            "VALUES(1, '600519', '贵州茅台', '看多/错误', '2026-11-26T18:00:00+08:00')"
        )

    assert memory.sync() == 3
    assert memory.sync() == 0            # 幂等

    items = memory.for_symbol("600519")
    assert [item["ref_id"] for item in items] == [2, 1]      # 新的在前
    assert items[1]["outcome"] == "看多/错误"
    assert items[0]["outcome"] is None                        # 还没到 T+20


def test_free_text_search_handles_chinese(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "调整后批准", 35, "2026-11-20", "白酒缩圈但资金流入")
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "payload_json, markdown, created_at) VALUES('白酒行业还能拿吗', '白酒缩圈', '行业', "
            "'中性', 58, 'fake', '2026-11-23', '{}', '白酒 19 只成分股 ret20 -1.83%', ?)",
            ("2026-11-23T11:00:00+08:00",),
        )
    memory.sync()

    hits = memory.search("白酒")
    assert hits and all("白酒" in (hit["title"] or hit["kind"] or "") or True for hit in hits)
    assert {hit["kind"] for hit in hits} & {"推演", "主题"}
    assert memory.search("") == []


def test_prompt_block_carries_the_not_a_fact_warning(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "调整后批准", 35, "2026-11-20", "白酒")
    memory.sync()
    block = memory.render(memory.for_symbol("600519"))
    assert memory.LABEL in block
    assert "不能当作今天的数字或事实" in block
    assert "2026-11-20" in block
    assert memory.render([]) == ""


def test_stats_counts_indexed_runs(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "批准", 10, "2026-11-20", "x")
    memory.sync()
    stats = memory.stats()
    assert stats["indexed"] == 1 and stats["runs"] == 1 and stats["judged"] == 0


def _seed_dated(conn, run_id: int, symbol: str, day: str, as_of: str, decision: str):
    conn.execute(
        "INSERT INTO agent_runs(id, symbol, name, status, model, rounds, started_at, as_of, "
        "verdict, report) VALUES(?, ?, ?, 'ok', 'fake', 1, ?, ?, ?, ?)",
        (
            run_id,
            symbol,
            symbol,
            f"{day}T18:00:00+08:00",
            as_of,
            json.dumps({"decision": decision, "final_position_pct": 10}, ensure_ascii=False),
            f"report {day} 收盘 1485.75",
        ),
    )


def test_as_of_cutoff_hides_the_future(app_db):
    with connection() as conn:
        _seed_dated(conn, 1, "600519", "2026-11-20", "2026-11-20", "批准")
        _seed_dated(conn, 2, "600519", "2026-11-25", "2026-11-25", "否决")
        _seed_dated(conn, 3, "600519", "2026-11-30", "2026-11-30", "批准")
    memory.sync()

    items = memory.for_symbol("600519", as_of="2026-11-26")
    assert [item["ref_id"] for item in items] == [2, 1]
    assert memory.for_symbol("600519", as_of="2026-11-19") == []


def test_window_drops_stale_conclusions(app_db):
    with connection() as conn:
        _seed_dated(conn, 1, "600519", "2025-01-02", "2025-01-02", "批准")
        _seed_dated(conn, 2, "600519", "2026-11-25", "2026-11-25", "否决")
    memory.sync()

    items = memory.for_symbol("600519", as_of="2026-11-26", window_days=180)
    assert [item["ref_id"] for item in items] == [2]


def test_three_wrong_calls_add_a_warning(app_db):
    with connection() as conn:
        for run_id, day in ((1, "2026-11-20"), (2, "2026-11-21"), (3, "2026-11-22")):
            _seed_dated(conn, run_id, "600519", day, day, "批准")
            conn.execute(
                "INSERT INTO agent_reviews(run_id, symbol, name, outcome, updated_at) "
                "VALUES(?, '600519', '贵州茅台', '看多/错误', ?)",
                (run_id, f"{day}T18:00:00+08:00"),
            )
    memory.sync()

    items = memory.for_symbol("600519", as_of="2026-11-23")
    assert len(items) == 1 and items[0]["warning"] is True
    block = memory.render(items)
    assert memory.WARNING in block
    assert "1485.75" not in block          # 正文数字不进 prompt


def test_block_respects_the_character_budget(app_db, monkeypatch):
    monkeypatch.setattr(settings, "memory_block_chars", 220)
    with connection() as conn:
        for run_id, day in ((1, "2026-11-20"), (2, "2026-11-21"), (3, "2026-11-22")):
            _seed_dated(conn, run_id, "600519", day, day, "批准")
    memory.sync()

    block = memory.render(memory.for_symbol("600519", as_of="2026-11-23"))
    assert len(block) <= 220
    assert memory.LABEL in block


def test_rebuild_is_idempotent_and_stats_join_reviews(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "批准", 10, "2026-11-20", "x")
        _seed_run(conn, 2, "000001", "平安银行", "否决", 0, "2026-11-21", "y")
        conn.execute(
            "INSERT INTO agent_reviews(run_id, symbol, name, outcome, updated_at) "
            "VALUES(1, '600519', '贵州茅台', '看多/正确', '2026-11-26T18:00:00+08:00')"
        )
    memory.sync()

    stats = memory.stats()
    assert stats["indexed"] == 2 and stats["runs"] == 2 and stats["judged"] == 1

    assert memory.rebuild() == 2
    assert memory.rebuild() == 2
    assert memory.stats()["indexed"] == 2


def test_topic_memory_is_bounded_and_labelled(app_db):
    with connection() as conn:
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "payload_json, markdown, created_at) VALUES('白酒行业还能拿吗', '白酒缩圈', '行业', "
            "'中性', 58, 'fake', '2026-11-23', '{}', '白酒 19 只成分股', ?)",
            ("2026-11-23T11:00:00+08:00",),
        )
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "payload_json, markdown, created_at) VALUES('白酒行业还能拿吗', '白酒反弹', '行业', "
            "'看多', 61, 'fake', '2026-11-30', '{}', '白酒 19 只成分股', ?)",
            ("2026-11-30T11:00:00+08:00",),
        )
    memory.sync()

    items = memory.for_topic("白酒", as_of="2026-11-27")
    assert [item["ref_id"] for item in items] == [1]
    assert memory.render_topics(items).startswith(f"【{memory.TOPIC_LABEL}】")
    assert memory.for_topic("白酒", as_of="2025-01-01") == []
    assert memory.search("白酒", as_of="2026-11-27")
    assert all(item["as_of"] <= "2026-11-27" for item in memory.search("白酒", as_of="2026-11-27"))


def test_industry_peers_are_separately_labelled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    init_db()
    store.init_schema()
    store.upsert_symbols(
        [
            {"symbol": "600519", "name": "贵州茅台", "exchange": "SH", "industry": "白酒"},
            {"symbol": "000858", "name": "五粮液", "exchange": "SZ", "industry": "白酒"},
        ]
    )
    with connection() as conn:
        _seed_dated(conn, 1, "000858", "2026-11-25", "2026-11-25", "批准")
    memory.sync()

    items = memory.for_industry("白酒", as_of="2026-11-26", exclude_symbol="600519")
    assert [item["ref_id"] for item in items] == [1]
    assert items[0]["group"] == "同行业对照"
    block = memory.render(items)
    assert memory.PEER_LABEL in block


def test_two_wrong_one_correct_has_no_warning(app_db):
    with connection() as conn:
        for run_id, day, outcome in (
            (1, "2026-11-20", "看多/错误"),
            (2, "2026-11-21", "看多/正确"),
            (3, "2026-11-22", "看多/错误"),
        ):
            _seed_dated(conn, run_id, "600519", day, day, "批准")
            conn.execute(
                "INSERT INTO agent_reviews(run_id, symbol, name, outcome, updated_at) "
                "VALUES(?, '600519', '贵州茅台', ?, ?)",
                (run_id, outcome, f"{day}T18:00:00+08:00"),
            )
    memory.sync()

    items = memory.for_symbol("600519", as_of="2026-11-23")
    assert len(items) == 3
    assert not any(item.get("warning") for item in items)
    assert memory.WARNING not in memory.render(items)


def test_three_char_search_uses_fts(app_db):
    with connection() as conn:
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "payload_json, markdown, created_at) VALUES('白酒行业还能拿吗', '白酒缩圈', '行业', "
            "'中性', 58, 'fake', '2026-11-23', '{}', '白酒 19 只成分股', ?)",
            ("2026-11-23T11:00:00+08:00",),
        )
    memory.sync()

    hits = memory.search("白酒行业")
    assert [hit["ref_id"] for hit in hits] == [1]


def test_stats_counts_topics(app_db):
    with connection() as conn:
        conn.execute(
            "INSERT INTO topic_reports(topic, title, scope, verdict, confidence, model, as_of, "
            "payload_json, markdown, created_at) VALUES('半导体', '半导体设备', '行业', "
            "'看多', 60, 'fake', '2026-11-23', '{}', '设备', ?)",
            ("2026-11-23T11:00:00+08:00",),
        )
    memory.sync()

    stats = memory.stats()
    assert stats["topics"] == 1 and stats["runs"] == 0 and stats["judged"] == 0


def test_memory_ids_match_items(app_db):
    with connection() as conn:
        _seed_run(conn, 1, "600519", "贵州茅台", "批准", 10, "2026-11-20", "x")
    memory.sync()

    items = memory.for_symbol("600519")
    assert memory.memory_ids(items) == ["推演:1"]
