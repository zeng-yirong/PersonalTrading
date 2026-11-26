"""Tests for the panel backup: the copy must be readable and provably identical."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.config import settings
from app.data import backup, store
from app.db import init_db


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "market.duckdb"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "personal.db"))
    store.init_schema()
    init_db()
    store.upsert_calendar([(date(2026, 1, 2), True)], "test")
    store.upsert_symbols(
        [{"symbol": "600519", "ts_code": "600519.SH", "name": "贵州茅台", "asset_type": "stock"}]
    )
    store.upsert_daily(
        [
            {"symbol": "600519", "trade_date": date(2026, 1, 2), "close": 1500.0, "amount": 1e8},
            {"symbol": "600519", "trade_date": date(2026, 1, 3), "close": 1510.0, "amount": 1.1e8},
        ],
        "test",
    )
    return tmp_path


def test_backup_copies_and_verifies(panel):
    result = backup.run(out_dir=panel / "bk", keep=0, include_app=True)

    assert result["verified"] is True
    assert result["source"] == result["copy"]
    assert result["copy"]["bars"] == 2
    assert result["copy"]["as_of"] == "2026-01-03"
    target = Path(result["target"])
    assert (target / "market.duckdb").exists()
    # SQLite 副本要能通过 integrity_check，否则等于没有备份
    assert result["app"]["integrity"] == "ok"
    assert result["app"]["verified"] is True


def test_backup_prunes_only_its_own_folders(panel):
    root = panel / "bk"
    for stamp in ("2026-01-01_000000", "2026-01-02_000000", "2026-01-03_000000"):
        (root / stamp).mkdir(parents=True)
    (root / "manual-keep-me").mkdir()      # 不是本脚本的命名，不能被删

    result = backup.run(out_dir=root, keep=2, include_app=False)

    names = sorted(item.name for item in root.iterdir() if item.is_dir())
    assert (root / "manual-keep-me").exists()
    assert len([name for name in names if name[:4] == "2026"]) == 2
    assert result["pruned"] and "2026-01-01_000000" in result["pruned"]


def test_backup_reports_a_missing_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "market_db_path", str(tmp_path / "nope.duckdb"))
    with pytest.raises(FileNotFoundError):
        backup.run(out_dir=tmp_path / "bk", keep=0, include_app=False)
