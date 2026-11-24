"""One ledger of everything this tool concluded, and what happened afterwards.

The four record tables were written at different times and know different things:
a research run has a verdict and (maybe) a T+20 review, a topic report has a
stance, a backtest has metrics, a paper trade is an action. This module only
*reads* them and normalises the columns that matter — it does not add a fifth
source of truth.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.db import connection

KINDS = ("推演", "主题", "回测", "成交")
MAX_LIMIT = 200


def entries(limit: int = 50, kind: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    items: list[dict[str, Any]] = []
    with connection() as conn:
        items.extend(_agent_entries(conn, limit))
        items.extend(_topic_entries(conn, limit))
        items.extend(_backtest_entries(conn, limit))
        items.extend(_trade_entries(conn, limit))

    if kind:
        items = [item for item in items if item["kind"] == kind]
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[:limit]


def _agent_entries(conn, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.id, r.symbol, r.name, r.status, r.started_at, r.verdict,
               r.prompt_tokens, r.completion_tokens, r.calls,
               v.outcome, v.d5_excess, v.d20_excess, v.entry_price
        FROM agent_runs r LEFT JOIN agent_reviews v ON v.run_id = r.id
        ORDER BY r.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    output = []
    for row in rows:
        verdict = _json(row["verdict"])
        decision = verdict.get("decision") or "—"
        position = verdict.get("final_position_pct")
        review = row["outcome"] or "待验证"
        detail = ""
        if row["d20_excess"] is not None:
            detail = f"T+20 超额 {row['d20_excess']:+.2f}%"
        output.append(
            {
                "kind": "推演",
                "ref": f"agent_runs:{row['id']}",
                "id": row["id"],
                "title": f"{row['name'] or ''}（{row['symbol']}）",
                "symbol": row["symbol"],
                "as_of": str(row["started_at"])[:10],
                "stance": decision,
                "score": position,
                "review": review,
                "review_detail": detail,
                "source_run_id": row["id"],
                "tokens": int(row["prompt_tokens"] or 0) + int(row["completion_tokens"] or 0),
                "status": row["status"],
                "created_at": str(row["started_at"]),
            }
        )
    return output


def _topic_entries(conn, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, topic, title, scope, verdict, confidence, as_of, created_at, "
        "prompt_tokens, completion_tokens, source_run_id "
        "FROM topic_reports ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "kind": "主题",
            "ref": f"topic_reports:{row['id']}",
            "id": row["id"],
            "title": row["topic"],
            "symbol": "",
            "as_of": row["as_of"],
            "stance": f"{row['scope']}｜{row['verdict']}",
            "score": row["confidence"],
            "review": "—",
            "review_detail": "",
            "source_run_id": row["source_run_id"],
            "tokens": int(row["prompt_tokens"] or 0) + int(row["completion_tokens"] or 0),
            "status": "ok",
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def _backtest_entries(conn, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, title, spec_json, metrics_json, source_run_id, created_at "
        "FROM backtest_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    output = []
    for row in rows:
        spec = _json(row["spec_json"])
        metrics = _json(row["metrics_json"])
        output.append(
            {
                "kind": "回测",
                "ref": f"backtest_runs:{row['id']}",
                "id": row["id"],
                "title": row["title"] or "选股条件回测",
                "symbol": "",
                "as_of": spec.get("end") or "",
                "stance": f"超额年化 {(metrics.get('excess_cagr_pct') or 0):+.2f}%",
                "score": metrics.get("total_return_pct"),
                "review": f"回撤 {(metrics.get('max_drawdown_pct') or 0):.2f}%",
                "review_detail": f"{metrics.get('periods', 0)} 期",
                "source_run_id": row["source_run_id"],
                "tokens": 0,
                "status": "ok",
                "created_at": str(row["created_at"]),
            }
        )
    return output


def _trade_entries(conn, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.id, t.symbol, t.side, t.shares, t.price, t.amount, t.trade_date, "
        "t.run_id, t.created_at, p.name AS name "
        "FROM paper_trades t LEFT JOIN paper_positions p ON p.id = t.position_id "
        "ORDER BY t.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "kind": "成交",
            "ref": f"paper_trades:{row['id']}",
            "id": row["id"],
            "title": f"{row['side']} {row['name'] or row['symbol']}",
            "symbol": row["symbol"],
            "as_of": row["trade_date"],
            "stance": "买入" if row["side"] == "buy" else "卖出",
            "score": row["amount"],
            "review": f"来自推演 #{row['run_id']}" if row["run_id"] else "手工",
            "review_detail": f"{row['shares']:.0f} 股 @ {row['price']:.2f}",
            "source_run_id": row["run_id"],
            "tokens": 0,
            "status": "ok",
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def chain(run_id: int) -> Optional[dict[str, Any]]:
    """Everything that descends from one research run, in one object.

    The ledger rows each know their own source; this is the view that answers
    "what did we do because of run #7, and how did it end".
    """
    with connection() as conn:
        run = conn.execute(
            "SELECT id, symbol, name, status, model, started_at, finished_at, verdict, "
            "prompt_tokens, completion_tokens FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        review = conn.execute(
            "SELECT * FROM agent_reviews WHERE run_id = ?", (run_id,)
        ).fetchone()
        backtests = conn.execute(
            "SELECT id, title, metrics_json, created_at FROM backtest_runs "
            "WHERE source_run_id = ? ORDER BY id DESC",
            (run_id,),
        ).fetchall()
        topics = conn.execute(
            "SELECT id, topic, verdict, confidence, created_at FROM topic_reports "
            "WHERE source_run_id = ? ORDER BY id DESC",
            (run_id,),
        ).fetchall()
        trades = conn.execute(
            "SELECT id, symbol, side, shares, price, fee, amount, trade_date "
            "FROM paper_trades WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    verdict = _json(run["verdict"])
    return {
        "run": {
            "id": run["id"],
            "symbol": run["symbol"],
            "name": run["name"],
            "status": run["status"],
            "model": run["model"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "decision": verdict.get("decision"),
            "final_position_pct": verdict.get("final_position_pct"),
            "tokens": int(run["prompt_tokens"] or 0) + int(run["completion_tokens"] or 0),
        },
        "review": dict(review) if review else None,
        "backtests": [
            {
                "id": row["id"],
                "title": row["title"],
                **_metrics_brief(row["metrics_json"]),
                "created_at": row["created_at"],
            }
            for row in backtests
        ],
        "topics": [dict(row) for row in topics],
        "trades": [dict(row) for row in trades],
    }


def _metrics_brief(raw: Optional[str]) -> dict[str, Any]:
    metrics = _json(raw)
    return {
        "total_return_pct": metrics.get("total_return_pct"),
        "excess_cagr_pct": metrics.get("excess_cagr_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "periods": metrics.get("periods"),
    }


def summary() -> dict[str, Any]:
    from app.ops import review as review_job

    with connection() as conn:
        counts = {}
        for table in ("agent_runs", "topic_reports", "backtest_runs", "paper_trades"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tokens = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM agent_runs"
        ).fetchone()
        topic_tokens = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM topic_reports"
        ).fetchone()

    return {
        "counts": {
            "runs": int(counts["agent_runs"]),
            "topics": int(counts["topic_reports"]),
            "backtests": int(counts["backtest_runs"]),
            "trades": int(counts["paper_trades"]),
        },
        "reviews": review_job.status(),
        "tokens": {
            "agent": int(tokens[0]) + int(tokens[1]),
            "topic": int(topic_tokens[0]) + int(topic_tokens[1]),
            "total": int(tokens[0] + tokens[1] + topic_tokens[0] + topic_tokens[1]),
        },
    }


def usage(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    with connection() as conn:
        agent_rows = conn.execute(
            "SELECT substr(started_at, 1, 10) AS day, COUNT(*), "
            "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), "
            "COALESCE(SUM(calls), 0), COALESCE(SUM(duration_ms), 0) "
            "FROM agent_runs GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
            (days,),
        ).fetchall()
        topic_rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*), "
            "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM topic_reports GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
            (days,),
        ).fetchall()
        factor_rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM factor_runs "
            "GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
            (days,),
        ).fetchall()
        backtest_rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM backtest_runs "
            "GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
            (days,),
        ).fetchall()

    by_day: dict[str, dict[str, Any]] = {}
    for day, count, prompt, completion, calls, duration in agent_rows:
        item = by_day.setdefault(day, {"date": day, "runs": 0, "topics": 0,
                                       "backtests": 0, "factors": 0,
                                       "tokens": 0, "calls": 0, "seconds": 0.0})
        item["runs"] = int(count)
        item["tokens"] += int(prompt) + int(completion)
        item["calls"] += int(calls)
        item["seconds"] = round(int(duration or 0) / 1000, 1)
    for day, count, prompt, completion in topic_rows:
        item = by_day.setdefault(day, {"date": day, "runs": 0, "topics": 0,
                                       "backtests": 0, "factors": 0,
                                       "tokens": 0, "calls": 0, "seconds": 0.0})
        item["topics"] = int(count)
        item["tokens"] += int(prompt) + int(completion)
    for day, count in factor_rows:
        item = by_day.setdefault(day, {"date": day, "runs": 0, "topics": 0,
                                       "backtests": 0, "factors": 0,
                                       "tokens": 0, "calls": 0, "seconds": 0.0})
        item["factors"] = int(count)
    for day, count in backtest_rows:
        item = by_day.setdefault(day, {"date": day, "runs": 0, "topics": 0,
                                       "backtests": 0, "factors": 0,
                                       "tokens": 0, "calls": 0, "seconds": 0.0})
        item["backtests"] = int(count)

    series = sorted(by_day.values(), key=lambda item: item["date"], reverse=True)
    return {
        "days": days,
        "series": series,
        "total_tokens": sum(item["tokens"] for item in series),
        "total_tasks": sum(item["runs"] + item["topics"] + item["backtests"] + item["factors"]
                           for item in series),
    }


def _json(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}
