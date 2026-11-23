"""LangGraph wiring for the research team.

Topology::

    START ─┬─ analyst_fundamental ─┐
           ├─ analyst_technical   ─┤
           ├─ analyst_flow        ─┼─▶ bull ⇄ bear ─▶ research_manager
           └─ analyst_sentiment   ─┘        (N rounds)         │
                                                               ▼
                                            risk_manager ◀─ risk (3 personas) ◀─ trader

Each node is wrapped so that start/finish events reach the SSE stream the moment
they happen: ``astream`` alone only reports completions, which would leave the
UI silent for the whole duration of a 30-second analyst call.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from langgraph.graph import END, START, StateGraph

from app.agents import nodes as n
from app.agents import prompts
from app.agents.llm import ChatClient
from app.agents.nodes import AgentState

NODE_LABELS = {
    **{f"analyst_{role}": prompts.ROLE_LABELS.get(role, role) for role, _, _ in n.ANALYSTS},
    "bull": prompts.ROLE_LABELS["bull"],
    "bear": prompts.ROLE_LABELS["bear"],
    "research_manager": prompts.ROLE_LABELS["research_manager"],
    "trader": prompts.ROLE_LABELS["trader"],
    "risk": "风控委员会（3 位）",
    "risk_manager": prompts.ROLE_LABELS["risk_manager"],
}


def _wrap(
    name: str,
    fn: Callable[[AgentState], Awaitable[dict[str, Any]]],
    queue: Optional[asyncio.Queue],
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    if queue is None:
        return fn

    async def wrapped(state: AgentState) -> dict[str, Any]:
        await queue.put(
            {"type": "node_start", "node": name, "label": NODE_LABELS.get(name, name)}
        )
        try:
            result = await fn(state)
        except Exception as exc:  # noqa: BLE001 - surfaced to the stream, then re-raised
            await queue.put(
                {
                    "type": "node_end",
                    "node": name,
                    "label": NODE_LABELS.get(name, name),
                    "status": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        error = None
        if isinstance(result.get("errors"), list) and result["errors"]:
            error = result["errors"][-1]
        await queue.put(
            {
                "type": "node_end",
                "node": name,
                "label": NODE_LABELS.get(name, name),
                "status": "error" if error else "ok",
                "message": error,
                "payload": _pick(name, result),
            }
        )
        return result

    return wrapped


def _pick(name: str, result: dict[str, Any]) -> Any:
    """Only ship the meaningful slice, not the whole state."""
    if name.startswith("analyst_"):
        role = name.split("_", 1)[1]
        return (result.get("analyses") or {}).get(role)
    if name in {"bull", "bear"}:
        debate = result.get("debate") or []
        return debate[-1].get("payload") if debate else None
    if name == "research_manager":
        return result.get("consensus")
    if name == "trader":
        return result.get("proposal")
    if name == "risk":
        return result.get("risk_reviews")
    if name == "risk_manager":
        return result.get("verdict")
    return None


def build_graph(client: ChatClient, queue: Optional[asyncio.Queue] = None):
    builder = StateGraph(AgentState)

    for role, system, render in n.ANALYSTS:
        builder.add_node(
            f"analyst_{role}",
            _wrap(f"analyst_{role}", n._make_analyst(client, role, system, render), queue),
        )
    builder.add_node("bull", _wrap("bull", n._make_debater(client, "bull", prompts.SYSTEM_BULL), queue))
    builder.add_node("bear", _wrap("bear", n._make_debater(client, "bear", prompts.SYSTEM_BEAR), queue))
    builder.add_node(
        "research_manager", _wrap("research_manager", n._make_manager(client), queue)
    )
    builder.add_node("trader", _wrap("trader", n._make_trader(client), queue))
    builder.add_node("risk", _wrap("risk", n._make_risk(client), queue))
    builder.add_node("risk_manager", _wrap("risk_manager", n._make_risk_manager(client), queue))

    for role, _, _ in n.ANALYSTS:
        builder.add_edge(START, f"analyst_{role}")
        builder.add_edge(f"analyst_{role}", "bull")

    builder.add_edge("bull", "bear")
    builder.add_conditional_edges(
        "bear",
        n.debate_router,
        {"bull": "bull", "bear": "bear", "research_manager": "research_manager"},
    )
    builder.add_edge("research_manager", "trader")
    builder.add_edge("trader", "risk")
    builder.add_edge("risk", "risk_manager")
    builder.add_edge("risk_manager", END)

    return builder.compile()
