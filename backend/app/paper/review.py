"""Review past agent runs against what actually happened.

The point is to answer one question weeks later: **was the team right?** For each
run we freeze the entry price on its own as-of date, then measure T+5 and T+20
returns plus the excess over 沪深300. A "观望" that avoided a drop counts as
correct, which is why the classification looks at both the call and the outcome.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.data import store
from app.db import connection, now_iso

HORIZONS = (5, 20)
NEUTRAL_BAND = 1.0  # |return| under 1% counts as "no move either way"


def _runs_with_context(limit: int = 200) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, symbol, name, status, context_json, verdict FROM agent_runs "
            "WHERE status IN ('ok', 'partial') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    import json

    output = []
    for row in rows:
        context = json.loads(row["context_json"]) if row["context_json"] else {}
        verdict = json.loads(row["verdict"]) if row["verdict"] else {}
        output.append(
            {
                "run_id": row["id"],
                "symbol": row["symbol"],
                "name": row["name"],
                "as_of": context.get("as_of"),
                "verdict": verdict,
            }
        )
    return output


def _close_on(symbol: str, day: date) -> Optional[float]:
    bars = store.get_daily(symbol, start=day, end=day, limit=1)
    return float(bars[-1]["close"]) if bars else None


def _benchmark_on(day: date) -> Optional[float]:
    from app.data.store import session

    try:
        with session(read_only=True) as conn:
            row = conn.execute(
                "SELECT close FROM index_daily WHERE ts_code = '000300.SH' "
                "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                [day],
            ).fetchone()
    except Exception:  # noqa: BLE001 - index table may be missing
        return None
    return float(row[0]) if row and row[0] else None


def _classify(decision: Optional[str], position_pct: Optional[float]) -> str:
    """Stance implied by the risk manager's final call.

    The account follows the verdict, not the trader's wish, so a "批准 0%" is a
    flat stance and a "否决" is a deliberate no-position call.
    """
    pct = position_pct or 0
    if decision in {"批准", "调整后批准"} and pct > 0:
        return "看多"
    if decision == "否决":
        return "否决"
    if pct == 0:
        return "空仓"
    return "未定"


def _judge(stance: str, ret20: Optional[float]) -> str:
    if ret20 is None:
        return "待验证"
    if abs(ret20) < NEUTRAL_BAND:
        return "中性区间"
    if stance == "看多":
        return "正确" if ret20 > 0 else "错误"
    if stance in {"否决", "空仓"}:
        return "正确" if ret20 < 0 else "错误"
    return "未定"


def refresh(limit: int = 200) -> dict[str, Any]:
    """Recompute T+5/T+20 outcomes for every recorded run."""
    runs = _runs_with_context(limit)
    days = store.get_trade_days()
    index_of = {day: index for index, day in enumerate(days)}
    stamped = now_iso()
    written = 0
    skipped = 0

    with connection() as conn:
        for run in runs:
            as_of = run.get("as_of")
            if not as_of:
                skipped += 1
                continue
            entry_date = date.fromisoformat(as_of)
            entry_price = _close_on(run["symbol"], entry_date)
            if entry_price is None:
                skipped += 1
                continue
            base_index = index_of.get(entry_date)
            if base_index is None:
                skipped += 1
                continue
            benchmark_entry = _benchmark_on(entry_date)

            verdict = run["verdict"] or {}
            decision = verdict.get("decision")
            position_pct = verdict.get("final_position_pct")
            stance = _classify(decision, position_pct)

            values: dict[str, Any] = {}
            for horizon in HORIZONS:
                target_index = base_index + horizon
                if target_index >= len(days):
                    values[f"d{horizon}_date"] = None
                    values[f"d{horizon}_return"] = None
                    values[f"d{horizon}_excess"] = None
                    continue
                target_date = days[target_index]
                price = _close_on(run["symbol"], target_date)
                if price is None:
                    values[f"d{horizon}_date"] = target_date.isoformat()
                    values[f"d{horizon}_return"] = None
                    values[f"d{horizon}_excess"] = None
                    continue
                ret = (price / entry_price - 1) * 100
                benchmark_now = _benchmark_on(target_date)
                excess = None
                if benchmark_entry and benchmark_now:
                    excess = ret - (benchmark_now / benchmark_entry - 1) * 100
                values[f"d{horizon}_date"] = target_date.isoformat()
                values[f"d{horizon}_return"] = round(ret, 4)
                values[f"d{horizon}_excess"] = round(excess, 4) if excess is not None else None

            conn.execute(
                "INSERT OR REPLACE INTO agent_reviews("
                "run_id, symbol, name, action, position_pct, conviction, entry_date, entry_price, "
                "d5_date, d5_return, d5_excess, d20_date, d20_return, d20_excess, outcome, updated_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["run_id"], run["symbol"], run["name"], decision, position_pct,
                    verdict.get("confidence"), entry_date.isoformat(), round(entry_price, 4),
                    values.get("d5_date"), values.get("d5_return"), values.get("d5_excess"),
                    values.get("d20_date"), values.get("d20_return"), values.get("d20_excess"),
                    f"{stance}/{_judge(stance, values.get('d20_return'))}",
                    stamped,
                ),
            )
            written += 1

    return {"runs": len(runs), "written": written, "skipped": skipped}


def list_reviews(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_reviews ORDER BY entry_date DESC, run_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def stats() -> dict[str, Any]:
    """Aggregate hit rate, split by whether the call was bullish or flat/short."""
    reviews = [row for row in list_reviews(500) if row["d20_return"] is not None]
    total = len(reviews)
    if not total:
        return {"total": 0, "message": "还没有满 20 个交易日的推演可供复盘"}

    correct = sum(1 for row in reviews if (row["outcome"] or "").endswith("正确"))
    avg_ret = sum(row["d20_return"] for row in reviews) / total
    avg_excess = sum(row["d20_excess"] or 0 for row in reviews) / total

    by_stance: dict[str, dict[str, Any]] = {}
    for row in reviews:
        stance = (row["outcome"] or "").split("/")[0] or "未定"
        bucket = by_stance.setdefault(stance, {"count": 0, "correct": 0, "return": 0.0})
        bucket["count"] += 1
        bucket["return"] += row["d20_return"]
        if (row["outcome"] or "").endswith("正确"):
            bucket["correct"] += 1
    for bucket in by_stance.values():
        bucket["avg_return"] = round(bucket["return"] / bucket["count"], 4)
        bucket["hit_rate"] = round(bucket["correct"] / bucket["count"] * 100, 2)
        bucket.pop("return")

    return {
        "total": total,
        "hit_rate": round(correct / total * 100, 2),
        "avg_return_20d": round(avg_ret, 4),
        "avg_excess_20d": round(avg_excess, 4),
        "by_stance": by_stance,
    }
