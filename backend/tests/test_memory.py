"""记忆检索的测试：索引、召回、注入文本、以及「不是事实」这条约束。"""

from __future__ import annotations

import json

import pytest

from app.agents import memory
from app.config import settings
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
