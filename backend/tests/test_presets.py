"""预设策略与每周全量回测排名。"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.db import init_db
from app.screener import presets as presets_module
from app.screener import ranking


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    init_db()


def test_presets_are_loaded_and_valid():
    items = presets_module.PRESETS
    assert len(items) == 12
    assert len({item.id for item in items}) == 12
    for item in items:
        assert item.label and item.text
        assert item.query.get("filters"), item.id


def test_bad_preset_file_is_rejected(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text(
        json.dumps({"presets": [{"id": "x", "label": "坏", "text": "t",
                                 "query": {"filters": [{"field": "no_such", "op": "<", "value": 1}]}}]}),
        encoding="utf-8",
    )
    with pytest.raises(presets_module.PresetError, match="不合法"):
        presets_module._load(path)


def test_ranking_sorts_by_excess_and_keeps_failures_last():
    rows = [
        {"status": "ok", "excess_cagr_pct": -5.0},
        {"status": "failed", "excess_cagr_pct": None},
        {"status": "ok", "excess_cagr_pct": 3.0},
    ]
    ordered = sorted(rows, key=ranking._sort_key)
    assert [row.get("excess_cagr_pct") for row in ordered] == [3.0, -5.0, None]


def test_run_all_ranks_and_persists(app_db, monkeypatch):
    from app.backtest import service as backtest_service

    excess = {"低波动": 12.0, "高成长": -8.0}

    def fake_run(payload, persist=True):
        label = payload["title"]
        if label == "涨停回踩":
            raise RuntimeError("命中太少")
        return {
            "spec": {"start": "2025-11-27", "end": "2026-11-27"},
            "metrics": {
                "periods": 12,
                "total_return_pct": 6.0,
                "cagr_pct": 6.2,
                "excess_cagr_pct": excess.get(label, 0.0),
                "max_drawdown_pct": -17.0,
                "hit_rate_pct": 58.0,
                "exposure_pct": 98.0,
                "trades": 40,
                "empty_periods": 0,
            },
            "warnings": [],
        }

    monkeypatch.setattr(backtest_service, "run", fake_run)

    batch = ranking.run_all(persist=True)

    assert batch["id"]
    assert len(batch["rows"]) == 12
    assert batch.get("grouped") is True

    # 分组排名：rank 是组内名次，不跨组混排
    groups: dict[str, list[dict]] = {}
    for row in batch["rows"]:
        groups.setdefault(row["horizon"], []).append(row)
    assert set(groups) == {"short", "mid", "long"}
    for rows in groups.values():
        ranks = [row["rank"] for row in rows if row["rank"] is not None]
        assert ranks == list(range(1, len(ranks) + 1))

    # 短线用短线的参数（3 日调仓），长线用 20 日
    assert groups["short"][0]["params"]["rebalance"] == 3
    assert groups["long"][0]["params"]["rebalance"] == 20
    # 失败的那条留在本组内并排最后
    failed = [row for row in groups["short"] if row["status"] == "failed"]
    assert failed and failed[0]["rank"] is None
    # 每条都带门槛结果
    assert all("gate" in row for row in batch["rows"])

    latest = ranking.latest()
    assert latest and latest["id"] == batch["id"]
    assert len(latest["rows"]) == 12
    assert ranking.describe()["rank_field"] == "excess_cagr_pct"
