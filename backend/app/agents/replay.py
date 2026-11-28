"""Decision-layer replay for memory A/B.

The full graph re-runs analysts, debate and research manager — expensive and
irrelevant to memory, because memory is injected only into the **trader** and
**risk-manager** prompts. This module therefore rebuilds the state from the
stored ``agent_messages`` (analyses / debate / consensus) and replays only those
two layers, once with memory and once without.

It is deliberately narrower than the P2-5 replay engine: it does not re-run the
analyst team, and it cannot replay a context that was never persisted. That is
the right trade-off for answering one question: *does memory change the
decision, and does it help?*
"""

from __future__ import annotations

import random
from typing import Any, Optional

from app.agents import memory
from app.agents import nodes
from app.agents import store as agent_store
from app.agents.llm import ChatClient


def load_state(run_id: int) -> Optional[dict[str, Any]]:
    """Rebuild the graph state of a stored run up to (not including) the trader."""
    run = agent_store.get_run(int(run_id))
    if not run or not run.get("context"):
        return None
    analyses: dict[str, Any] = {}
    debate: list[dict[str, Any]] = []
    consensus: dict[str, Any] = {}
    for message in run.get("messages") or []:
        payload = message.get("payload")
        if not payload:
            continue
        node = str(message.get("node") or "")
        if node.startswith("analyst_"):
            analyses[node.split("_", 1)[1]] = payload
        elif node in {"bull", "bear"}:
            debate.append(
                {"role": node, "round": message.get("round"), "payload": payload}
            )
        elif node == "research_manager":
            consensus = payload
    if not analyses or not consensus:
        return None
    return {
        "run_id": str(run["id"]),
        "symbol": run["symbol"],
        "name": run["name"],
        "rounds": run.get("rounds") or 1,
        "context": run["context"],
        "analyses": analyses,
        "debate": debate,
        "consensus": consensus,
    }


async def replay_decision(
    state: dict[str, Any],
    memory_items: list[dict[str, Any]],
    client: ChatClient,
) -> dict[str, Any]:
    """Run trader → risk (3 personas) → risk manager on a frozen context."""
    context = dict(state["context"])
    context["memory"] = memory_items
    context["memory_ids"] = memory.memory_ids(memory_items)
    current: dict[str, Any] = {
        **state,
        "context": context,
        "model": client.model,
        "proposal": {},
        "risk_reviews": [],
        "verdict": {},
        "errors": [],
    }
    for factory in (nodes._make_trader, nodes._make_risk, nodes._make_risk_manager):
        node = factory(client)
        current.update(await node(current))
    return {
        "proposal": current.get("proposal") or {},
        "verdict": current.get("verdict") or {},
        "errors": current.get("errors") or [],
    }


def _correct(position_pct: Any, d20_return: Optional[float]) -> Optional[bool]:
    if position_pct is None or d20_return is None:
        return None
    try:
        position = float(position_pct)
        ret = float(d20_return)
    except (TypeError, ValueError):
        return None
    return ret > 0 if position > 0 else ret <= 0


def _brier(confidence: Any, correct: Optional[bool]) -> Optional[float]:
    if correct is None:
        return None
    try:
        p = max(0.0, min(1.0, float(confidence) / 100.0))
    except (TypeError, ValueError):
        return None
    y = 1.0 if correct else 0.0
    return (p - y) ** 2


def pair_summary(rows: list[dict[str, Any]], *, seed: int = 7) -> dict[str, Any]:
    """Paired summary: ``on - off`` for Brier (lower better) and excess (higher)."""
    deltas_brier: list[float] = []
    deltas_excess: list[float] = []
    for row in rows:
        on_brier = _brier(row["on"].get("confidence"), row["on"].get("correct"))
        off_brier = _brier(row["off"].get("confidence"), row["off"].get("correct"))
        if on_brier is not None and off_brier is not None:
            deltas_brier.append(on_brier - off_brier)
        if row.get("excess") is not None:
            on_pos = float(row["on"].get("position") or 0.0) / 100.0
            off_pos = float(row["off"].get("position") or 0.0) / 100.0
            deltas_excess.append((on_pos - off_pos) * float(row["excess"]))

    return {
        "n": len(rows),
        "brier": _interval(deltas_brier, seed),
        "excess": _interval(deltas_excess, seed),
        "on_correct": sum(1 for row in rows if row["on"].get("correct")),
        "off_correct": sum(1 for row in rows if row["off"].get("correct")),
    }


def _interval(values: list[float], seed: int = 7, rounds: int = 2000) -> dict[str, Any]:
    if not values:
        return {"mean": None, "low": None, "high": None, "n": 0}
    rng = random.Random(seed)
    means = []
    for _ in range(rounds):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {
        "mean": round(sum(values) / len(values), 4),
        "low": round(means[int(0.025 * len(means))], 4),
        "high": round(means[int(0.975 * len(means)) - 1], 4),
        "n": len(values),
    }


async def run_paired(
    limit: int = 30, model: str = "", client: Optional[ChatClient] = None
) -> dict[str, Any]:
    """Replay the decision layer with memory on/off for already-judged runs."""
    from app.db import connection

    with connection() as conn:
        rows = conn.execute(
            "SELECT r.id, v.d20_return, v.d20_excess "
            "FROM agent_runs r JOIN agent_reviews v ON v.run_id = r.id "
            "WHERE r.status IN ('ok', 'partial') AND r.as_of IS NOT NULL "
            "AND v.d20_return IS NOT NULL ORDER BY r.id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    client = client or ChatClient(model=model, temperature=0.0)
    output: list[dict[str, Any]] = []
    for row in rows:
        state = load_state(int(row["id"]))
        if state is None:
            continue
        symbol = state["symbol"]
        as_of = state["context"].get("as_of")
        on_items = memory.for_symbol(symbol, as_of=as_of)
        off = await replay_decision(state, [], client)
        on = await replay_decision(state, on_items, client)

        def view(result: dict[str, Any]) -> dict[str, Any]:
            verdict = result.get("verdict") or {}
            proposal = result.get("proposal") or {}
            position = verdict.get("final_position_pct")
            correct = _correct(position, row["d20_return"])
            confidence = verdict.get("confidence")
            if confidence is None:
                confidence = proposal.get("conviction")
            return {
                "position": position,
                "confidence": confidence,
                "correct": correct,
                "decision": verdict.get("decision"),
            }

        output.append(
            {
                "run_id": int(row["id"]),
                "symbol": symbol,
                "as_of": as_of,
                "d20_return": row["d20_return"],
                "excess": row["d20_excess"],
                "on": view(on),
                "off": view(off),
                "memory_ids": memory.memory_ids(on_items),
            }
        )
    return {"rows": output, "summary": pair_summary(output), "calls": client.usage.as_dict()}
