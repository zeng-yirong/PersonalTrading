"""Daily review job: refresh T+5/T+20 outcomes, then report coverage.

``paper.review`` does the per-run arithmetic; this module is the scheduled entry
point around it: run the refresh, then answer "how much of the ledger has been
judged, and when was it last refreshed" in one dict.
"""

from __future__ import annotations

from typing import Any

from app.db import connection
from app.paper import review as paper_review


def status() -> dict[str, Any]:
    with connection() as conn:
        runs = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE status IN ('ok', 'partial')"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT outcome, d20_return, updated_at FROM agent_reviews"
        ).fetchall()

    total = len(rows)
    pending = sum(1 for row in rows if row["d20_return"] is None)
    judged = total - pending
    correct = sum(
        1 for row in rows
        if row["d20_return"] is not None and (row["outcome"] or "").endswith("正确")
    )
    wrong = sum(
        1 for row in rows
        if row["d20_return"] is not None and (row["outcome"] or "").endswith("错误")
    )
    stars = [row["updated_at"] for row in rows if row["updated_at"]]
    return {
        "runs": int(runs or 0),
        "reviews": total,
        "covered_pct": round(total / runs * 100, 1) if runs else 0.0,
        "runs_without_review": max(0, int(runs or 0) - total),
        "pending": pending,
        "judged": judged,
        "correct": correct,
        "wrong": wrong,
        "hit_rate_pct": round(correct / judged * 100, 1) if judged else None,
        "last_refreshed_at": max(stars) if stars else None,
    }


def run(limit: int = 200) -> dict[str, Any]:
    refreshed = paper_review.refresh(limit)
    return {"refreshed": refreshed, **status()}
