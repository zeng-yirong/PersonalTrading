"""每日轮转的选股规则：确定性、可跳过、不重复。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents import daily as daily_mod
from app.config import settings
from app.db import connection, init_db


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


POOL = [{"symbol": f"60000{index}", "name": f"票{index}"} for index in range(5)]


def test_pick_is_deterministic_for_the_same_day():
    day = date(2026, 11, 27)
    first = daily_mod.pick(POOL, day, set())
    assert daily_mod.pick(POOL, day, set()) == first


def test_rotation_covers_every_name_across_days():
    picked = {
        daily_mod.pick(POOL, date(2026, 11, 27) + timedelta(days=offset), set())["symbol"]
        for offset in range(len(POOL))
    }
    assert picked == {item["symbol"] for item in POOL}


def test_recent_names_are_skipped():
    day = date(2026, 11, 27)
    first = daily_mod.pick(POOL, day, set())
    second = daily_mod.pick(POOL, day, {first["symbol"]})
    assert second["symbol"] != first["symbol"]


def test_everything_recent_falls_back_to_the_scheduled_one():
    day = date(2026, 11, 27)
    everything = {item["symbol"] for item in POOL}
    assert daily_mod.pick(POOL, day, everything) == daily_mod.pick(POOL, day, set())


def test_empty_pool_is_an_error():
    with pytest.raises(LookupError):
        daily_mod.pick([], date(2026, 11, 27), set())


def test_choose_with_an_explicit_symbol(app_db, monkeypatch):
    monkeypatch.setattr(
        daily_mod.store, "get_symbol", lambda symbol: {"symbol": symbol, "name": "贵州茅台"}
    )
    target = daily_mod.choose("600519")
    assert target.symbol == "600519"
    assert target.name == "贵州茅台"
    assert target.source == "指定"
    assert "600519" in target.line()


def test_runs_today_only_counts_today(app_db):
    today = date.today()
    yesterday = today - timedelta(days=1)
    with connection() as conn:
        for run_id, day in ((1, yesterday.isoformat()), (2, today.isoformat())):
            conn.execute(
                "INSERT INTO agent_runs(id, symbol, status, started_at) VALUES(?, ?, 'ok', ?)",
                (run_id, "600519", f"{day}T18:30:00+08:00"),
            )

    assert daily_mod.runs_today(today) == 1
    assert daily_mod.recent_symbols(days=3, today=today) == {"600519"}
