"""面板"写完了没有"的前置检查。

18:00 的日更是**逐表写**的：`daily` 先落库，`index_daily` / `sw_daily` 要等指数
刷新那几步跑完。18:20 生成复盘时如果抢在中间，快照里指数为空、行业占比为 null，
模型会把这些**同步竞态**当成"数据缺口"写进报告——2026-11-27 的 #22 就是这样。

所以在生成之前先问一句：面板写完了吗？没写完就等（默认 3 次 × 30 秒）。判据三条：

1. `index_daily` 的最新日期 = `daily` 的最新日期；
2. `sw_daily` 的同样相等；
3. 当日 `daily` 行数不少于前一交易日的 90%（防止 daily 自己还没写完）。
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Callable, Optional

from app.data import store

MIN_ROW_RATIO = 0.9
DEFAULT_TRIES = 3
DEFAULT_INTERVAL = 30.0


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, date) else (str(value) if value else None)


def _max_date(conn, table: str) -> Optional[date]:
    if not store.table_exists(conn, table):
        return None
    row = conn.execute(f'SELECT MAX(trade_date) FROM "{table}"').fetchone()
    return row[0] if row else None


def panel_freshness() -> dict[str, Any]:
    """面板是否"写完"：返回各表的最新日期与未就绪的原因。"""
    with store.session(read_only=True) as conn:
        panel = conn.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
        if panel is None:
            return {"ready": False, "panel": None, "missing": ["面板为空"]}
        index = _max_date(conn, "index_daily")
        sw = _max_date(conn, "sw_daily")
        today_rows = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE trade_date = ?", [panel]
        ).fetchone()[0]
        prev_row = conn.execute(
            "SELECT COUNT(*) FROM daily WHERE trade_date = "
            "(SELECT MAX(trade_date) FROM daily WHERE trade_date < ?)",
            [panel],
        ).fetchone()
        prev_rows = int(prev_row[0] or 0) if prev_row else 0

    missing: list[str] = []
    if index is None or index < panel:
        missing.append(f"index_daily（{_iso(index) or '缺表'} < 面板 {_iso(panel)}）")
    if sw is None or sw < panel:
        missing.append(f"sw_daily（{_iso(sw) or '缺表'} < 面板 {_iso(panel)}）")
    if prev_rows and today_rows < prev_rows * MIN_ROW_RATIO:
        missing.append(f"daily 当日只有 {today_rows} 行（前一日 {prev_rows}），可能未写完")

    return {
        "ready": not missing,
        "panel": _iso(panel),
        "index_daily": _iso(index),
        "sw_daily": _iso(sw),
        "rows": int(today_rows),
        "missing": missing,
    }


def wait_until_fresh(
    tries: int = DEFAULT_TRIES,
    interval: float = DEFAULT_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """最多等 ``tries`` 次；每次检查前先睡 ``interval`` 秒（第一次也睡）。"""
    state = panel_freshness()
    attempts = 0
    while not state["ready"] and attempts < tries:
        sleep(interval)
        attempts += 1
        state = panel_freshness()
    state["attempts"] = attempts
    state["waited_seconds"] = int(attempts * interval)
    return state
